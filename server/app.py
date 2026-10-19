"""SQLite-backed local trial server. Run: python3 -m server.app --db trials.sqlite.

Set RICE_ADMIN_TOKEN and RICE_STATION_TOKENS (JSON station-id/token map).
Bind localhost by default; put TLS in front before using an untrusted network.
"""
import argparse
import csv
import hmac
import io
import json
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

try:
    from server import scoring
except ImportError:  # executed as server/app.py or frozen with PyInstaller
    import scoring

ROOT = Path(__file__).resolve().parent.parent
ID = re.compile(r'^[A-Za-z0-9_.-]{1,80}$')
HEARTBEAT_TTL = timedelta(seconds=90)


class Problem(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message


def identifier(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise Problem(400, 'invalid identifier')
    return value


class Store:
    def __init__(self, path, calibrated_configs=(), scoring_armed=False):
        self.path = Path(path)
        # Production is unarmed: no in-game physical commissioning was performed.
        # The switch is for isolated scoring contract tests, never the CLI.
        self.scoring_armed = scoring_armed
        # No track is trusted by default: client self-attestation is insufficient.
        self.calibrated_configs = frozenset(calibrated_configs)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS sessions (session_id TEXT PRIMARY KEY, name TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS pilots (pilot_id TEXT PRIMARY KEY, name TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS assignments (station_id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions, pilot_id TEXT NOT NULL REFERENCES pilots);
                CREATE TABLE IF NOT EXISTS assignment_history (revision INTEGER PRIMARY KEY AUTOINCREMENT, station_id TEXT NOT NULL, session_id TEXT NOT NULL, pilot_id TEXT NOT NULL, created_utc TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS heartbeats (station_id TEXT PRIMARY KEY, seen_utc TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS laps (station_id TEXT NOT NULL, lap_id TEXT NOT NULL, session_id TEXT NOT NULL, pilot_id TEXT NOT NULL, event TEXT NOT NULL, raw_ms INTEGER NOT NULL, corrected_ms INTEGER, events TEXT NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL, created_utc TEXT NOT NULL, PRIMARY KEY(station_id, lap_id));
                CREATE TABLE IF NOT EXISTS calibrations (station_id TEXT NOT NULL, track_config TEXT NOT NULL, reference TEXT NOT NULL, operator TEXT NOT NULL, active INTEGER NOT NULL, updated_utc TEXT NOT NULL, PRIMARY KEY(station_id, track_config));
                CREATE TABLE IF NOT EXISTS reviews (id INTEGER PRIMARY KEY AUTOINCREMENT, station_id TEXT NOT NULL, lap_id TEXT NOT NULL, session_id TEXT NOT NULL, reviewer TEXT NOT NULL, decision TEXT NOT NULL, reason TEXT NOT NULL, before_status TEXT NOT NULL, after_status TEXT NOT NULL, created_utc TEXT NOT NULL, calibration_reference TEXT);
            ''')
            if 'calibration_reference' not in {row['name'] for row in db.execute('PRAGMA table_info(laps)')}:
                db.execute('ALTER TABLE laps ADD COLUMN calibration_reference TEXT')
            if 'revision' not in {row['name'] for row in db.execute('PRAGMA table_info(assignments)')}:
                db.execute('ALTER TABLE assignments ADD COLUMN revision INTEGER')

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA busy_timeout=10000')
        return db

    def register(self, table, key, data):
        value, name = identifier(data.get(key)), data.get('name')
        if not isinstance(name, str) or not name.strip() or len(name) > 120:
            raise Problem(400, 'invalid name')
        with self.connect() as db:
            db.execute(f'INSERT INTO {table} ({key}, name) VALUES (?, ?) ON CONFLICT({key}) DO UPDATE SET name=excluded.name', (value, name.strip()))
        return {key: value, 'name': name.strip()}

    def assign(self, data, stations):
        station = identifier(data.get('station_id'))
        session = identifier(data.get('session_id'))
        pilot = identifier(data.get('pilot_id'))
        if station not in stations:
            raise Problem(400, 'unknown station')
        with self.connect() as db:
            try:
                db.execute('BEGIN IMMEDIATE')
                db.execute('INSERT INTO assignments (station_id,session_id,pilot_id) VALUES (?, ?, ?) ON CONFLICT(station_id) DO UPDATE SET session_id=excluded.session_id, pilot_id=excluded.pilot_id', (station, session, pilot))
                cursor = db.execute('INSERT INTO assignment_history (station_id,session_id,pilot_id,created_utc) VALUES (?,?,?,?)', (station, session, pilot, datetime.now(timezone.utc).isoformat()))
                revision = cursor.lastrowid
                db.execute('UPDATE assignments SET revision=? WHERE station_id=?', (revision, station))
            except sqlite3.IntegrityError:
                raise Problem(400, 'unknown session or pilot') from None
        return {'station_id': station, 'session_id': session, 'pilot_id': pilot, 'assignment_revision': revision}

    def assignment(self, station):
        with self.connect() as db:
            row = db.execute('SELECT session_id, pilot_id, revision FROM assignments WHERE station_id=?', (station,)).fetchone()
            seen = db.execute('SELECT seen_utc FROM heartbeats WHERE station_id=?', (station,)).fetchone()
        online = False
        if seen:
            try:
                timestamp = datetime.fromisoformat(seen['seen_utc'])
                online = timestamp.tzinfo is not None and timedelta(0) <= datetime.now(timezone.utc) - timestamp <= HEARTBEAT_TTL
            except ValueError:
                pass
        return {'station_id': station, 'session_id': row['session_id'] if row else None,
                'pilot_id': row['pilot_id'] if row else None,
                'assignment_revision': row['revision'] if row else None, 'online': online,
                'last_seen_utc': seen['seen_utc'] if seen else None}

    def heartbeat(self, station):
        seen = datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            db.execute('INSERT INTO heartbeats VALUES (?, ?) ON CONFLICT(station_id) DO UPDATE SET seen_utc=excluded.seen_utc', (station, seen))
        return self.assignment(station)

    def calibrate(self, data, stations):
        station, track = identifier(data.get('station_id')), identifier(data.get('track_config'))
        reference, operator = data.get('reference'), data.get('operator')
        if station not in stations or not all(isinstance(v, str) and v.strip() and len(v) <= 200 for v in (reference, operator)) or type(data.get('active', True)) is not bool:
            raise Problem(400, 'invalid calibration provenance')
        active = data.get('active', True)
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            db.execute('INSERT INTO calibrations VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(station_id, track_config) DO UPDATE SET reference=excluded.reference, operator=excluded.operator, active=excluded.active, updated_utc=excluded.updated_utc', (station, track, reference.strip(), operator.strip(), active, now))
        return {'station_id': station, 'track_config': track, 'reference': reference.strip(), 'operator': operator.strip(), 'active': active, 'updated_utc': now}

    def review(self, data):
        station, lap_id = identifier(data.get('station_id')), identifier(data.get('lap_id'))
        decision, reviewer, reason = data.get('decision'), data.get('reviewer'), data.get('reason')
        if decision not in ('approve', 'reject') or not all(isinstance(v, str) and v.strip() and len(v) <= 200 for v in (reviewer, reason)):
            raise Problem(400, 'invalid review')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM laps WHERE station_id=? AND lap_id=?', (station, lap_id)).fetchone()
            if not row:
                raise Problem(404, 'lap not found')
            if row['event'] != 'endurance' or (decision == 'approve' and row['status'] != 'PENDING_REVIEW') or (decision == 'reject' and row['status'] not in ('PENDING_REVIEW', 'VALID')):
                raise Problem(409, 'lap cannot be reviewed')
            calibration = None
            corrected = None
            if decision == 'approve':
                if not self.scoring_armed:
                    raise Problem(409, 'official scoring not commissioned')
                payload = json.loads(row['payload'])
                if payload.get('detector_ok') is not True or payload.get('calibration_verified') is not True or not isinstance(payload.get('track_config'), str):
                    raise Problem(409, 'verified detector and calibration required')
                calibration = db.execute('SELECT reference FROM calibrations WHERE station_id=? AND track_config=? AND active=1', (station, payload['track_config'])).fetchone()
                if not calibration:
                    raise Problem(409, 'admin calibration required')
                events = json.loads(row['events'])
                corrected = scoring.corrected_ms('endurance', row['raw_ms'], events)
                if corrected is None:
                    raise Problem(409, 'stored events do not score under current rules')
            status = 'VALID' if decision == 'approve' else 'INVALID'
            reference = calibration['reference'] if calibration else row['calibration_reference']
            now = datetime.now(timezone.utc).isoformat()
            db.execute('UPDATE laps SET status=?, corrected_ms=?, calibration_reference=? WHERE station_id=? AND lap_id=?', (status, corrected, reference, station, lap_id))
            db.execute('INSERT INTO reviews (station_id,lap_id,session_id,reviewer,decision,reason,before_status,after_status,created_utc,calibration_reference) VALUES (?,?,?,?,?,?,?,?,?,?)', (station, lap_id, row['session_id'], reviewer.strip(), decision, reason.strip(), row['status'], status, now, reference))
        return {'station_id': station, 'lap_id': lap_id, 'status': status, 'corrected_ms': corrected, 'calibration_reference': reference}

    def reviews(self, session):
        with self.connect() as db:
            return {'reviews': [dict(row) for row in db.execute('SELECT * FROM reviews WHERE session_id=? ORDER BY id', (identifier(session),))]}

    def submit(self, data, station):
        if type(data.get('protocol_version')) is not int or data['protocol_version'] != 1:
            raise Problem(400, 'unsupported protocol_version')
        for field in ('station_id', 'lap_id', 'session_id', 'pilot_id'):
            identifier(data.get(field))
        if data['station_id'] != station:
            raise Problem(403, 'station mismatch')
        raw = data.get('raw_ms')
        if type(raw) is not int or not 0 < raw <= 3600000:
            raise Problem(400, 'invalid raw_ms')
        event, status, events = data.get('event'), data.get('status'), data.get('events')
        if event not in ('autocross', 'skidpad', 'endurance') or status not in ('valid', 'dnf', 'invalid', 'VALID', 'INVALID', 'OUTLAP', 'PENDING_REVIEW', 'DETECTOR_ERROR'):
            raise Problem(400, 'invalid event or status')
        allowed = {'cone', 'off_course', 'slalom'} if event == 'endurance' else {'doo', 'oc', 'right_ms', 'left_ms'}
        if not isinstance(events, dict) or set(events) - allowed:
            raise Problem(400, 'invalid events')
        for key in (('cone', 'off_course') if event == 'endurance' else ('doo', 'oc')):
            if type(events.get(key, 0)) is not int or not 0 <= events.get(key, 0) <= 100:
                raise Problem(400, 'invalid penalty count')
        if event == 'endurance':
            slalom = events.get('slalom', [])
            if not isinstance(slalom, list) or len(slalom) > 100 or any(not isinstance(v, str) or not ID.fullmatch(v) for v in slalom) or len(set(slalom)) != len(slalom):
                raise Problem(400, 'invalid slalom identifiers')
            if type(data.get('scoring_version')) is not int or data['scoring_version'] != 1:
                raise Problem(400, 'unsupported scoring_version')
            corrected = scoring.corrected_ms(event, raw, events)
            status = scoring.resolve_status(status, data.get('detector_ok'))
            if status != 'VALID':
                corrected = None
        elif event == 'skidpad':
            if 'oc' in events and events['oc'] or not all(type(events.get(k)) is int and 0 < events[k] <= 3600000 for k in ('right_ms', 'left_ms')):
                raise Problem(400, 'skidpad requires right_ms and left_ms and no off-course')
            corrected = scoring.corrected_ms(event, raw, events)
        else:
            if 'right_ms' in events or 'left_ms' in events:
                raise Problem(400, 'autocross does not accept skidpad splits')
            corrected = scoring.corrected_ms(event, raw, events)
        if event != 'endurance' and status != 'valid':
            corrected = None
        payload = json.dumps(data, sort_keys=True, separators=(',', ':'))
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute('SELECT payload, corrected_ms, status FROM laps WHERE station_id=? AND lap_id=?', (station, data['lap_id'])).fetchone()
            if existing:
                if existing['payload'] != payload:
                    raise Problem(409, 'lap_id already used with different payload')
                return {'lap_id': data['lap_id'], 'corrected_ms': existing['corrected_ms'], 'status': existing['status'], 'duplicate': True}
            assignment = db.execute('SELECT session_id, pilot_id, revision FROM assignments WHERE station_id=?', (station,)).fetchone()
            revision = data.get('assignment_revision')
            if revision is not None:
                if type(revision) is not int or revision < 1:
                    raise Problem(400, 'invalid assignment_revision')
                historical = db.execute('SELECT session_id,pilot_id FROM assignment_history WHERE revision=? AND station_id=?', (revision, station)).fetchone()
                if not historical or historical['session_id'] != data['session_id'] or historical['pilot_id'] != data['pilot_id']:
                    raise Problem(409, 'assignment revision mismatch')
                if not assignment or assignment['revision'] != revision:
                    # Accept historical offline evidence, but never rank it automatically.
                    status, corrected = 'PENDING_REVIEW', None
            elif not assignment or assignment['session_id'] != data['session_id'] or assignment['pilot_id'] != data['pilot_id']:
                raise Problem(409, 'station assignment mismatch')
            calibration = None
            if event == 'endurance' and status == 'VALID':
                track = data.get('track_config')
                if isinstance(track, str):
                    calibration = db.execute('SELECT reference FROM calibrations WHERE station_id=? AND track_config=? AND active=1', (station, track)).fetchone()
                if not calibration or data.get('calibration_verified') is not True:
                    status, corrected = 'PENDING_REVIEW', None
            if not self.scoring_armed and status in ('VALID', 'valid'):
                status, corrected = 'PENDING_REVIEW', None
                calibration = None
            db.execute('INSERT INTO laps (station_id, lap_id, session_id, pilot_id, event, raw_ms, corrected_ms, events, status, payload, created_utc, calibration_reference) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', (station, data['lap_id'], data['session_id'], data['pilot_id'], event, raw, corrected, json.dumps(events, sort_keys=True), status, payload, datetime.now(timezone.utc).isoformat(), calibration['reference'] if calibration and status == 'VALID' else None))
        return {'lap_id': data['lap_id'], 'corrected_ms': corrected, 'status': status, 'duplicate': False}

    def laps(self, session):
        with self.connect() as db:
            rows = db.execute('SELECT station_id, lap_id, session_id, pilot_id, event, raw_ms, corrected_ms, events, status, created_utc, calibration_reference FROM laps WHERE session_id=? ORDER BY created_utc, lap_id', (identifier(session),)).fetchall()
            return [dict(row) for row in rows]

    def leaderboard(self, session):
        laps = self.laps(session)
        leaders = {}
        counts = {}
        for lap in laps:
            counts[lap['status']] = counts.get(lap['status'], 0) + 1
            if self.scoring_armed and lap['corrected_ms'] is not None and lap['status'] in ('valid', 'VALID'):
                key = (lap['event'], lap['pilot_id'])
                leaders[key] = min(leaders.get(key, lap['corrected_ms']), lap['corrected_ms'])
        with self.connect() as db:
            names = {row['pilot_id']: row['name'] for row in db.execute('SELECT pilot_id, name FROM pilots')}
            stations = [self.assignment(row['station_id']) for row in db.execute('SELECT station_id FROM assignments WHERE session_id=? ORDER BY station_id', (session,))]
        return {'session_id': session, 'laps': laps, 'leaders': [{'event': e, 'pilot_id': p, 'pilot_name': names.get(p, p), 'best_ms': t} for (e, p), t in sorted(leaders.items(), key=lambda item: (item[0][0], item[1], item[0][1]))],
                'diagnostics': {'status_counts': counts, 'stations': stations, 'scoring_armed': self.scoring_armed}}

    def backup(self):
        destination = self.path.parent / 'backups' / (self.path.stem + '-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.sqlite')
        destination.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as source, sqlite3.connect(destination) as target:
            source.backup(target)
        return str(destination)


def make_handler(store, admin_token, station_tokens):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def send(self, code, data, content_type='application/json; charset=utf-8'):
            body = (json.dumps(data) if content_type.startswith('application/json') else data).encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'")
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def authenticated(self, token):
            given = self.headers.get('Authorization', '').removeprefix('Bearer ')
            if not token or not hmac.compare_digest(given, token):
                raise Problem(401, 'authentication required')

        def dispatch(self):
            url = urlsplit(self.path)
            path = url.path
            args = parse_qs(url.query)
            if path == '/api/station/assignment' and self.command == 'GET':
                return self.send(200, store.assignment(self.station_identity()))
            if self.command == 'GET' and path in ('/admin', '/tv', '/admin.js', '/tv.js'):
                filename = {'/admin': 'admin.html', '/tv': 'tv.html', '/admin.js': 'admin.js', '/tv.js': 'tv.js'}[path]
                return self.send(200, (ROOT / 'leaderboard' / filename).read_text(), 'text/javascript; charset=utf-8' if path.endswith('.js') else 'text/html; charset=utf-8')
            if self.command == 'GET' and path == '/api/leaderboard':
                return self.send(200, store.leaderboard(args.get('session_id', [''])[0]))
            if self.command == 'GET' and path == '/api/admin/reviews':
                self.authenticated(admin_token)
                return self.send(200, store.reviews(args.get('session_id', [''])[0]))
            if self.command == 'GET' and path == '/api/admin/reviews.csv':
                self.authenticated(admin_token)
                output = io.StringIO()
                columns = ['id', 'station_id', 'lap_id', 'session_id', 'reviewer', 'decision', 'reason', 'before_status', 'after_status', 'created_utc', 'calibration_reference']
                writer = csv.DictWriter(output, fieldnames=columns)
                writer.writeheader()
                writer.writerows(store.reviews(args.get('session_id', [''])[0])['reviews'])
                return self.send(200, output.getvalue(), 'text/csv; charset=utf-8')
            if self.command == 'GET' and path == '/api/admin/export.csv':
                self.authenticated(admin_token)
                output = io.StringIO()
                columns = ['station_id', 'lap_id', 'session_id', 'pilot_id', 'event', 'raw_ms', 'corrected_ms', 'events', 'status', 'created_utc', 'calibration_reference']
                writer = csv.DictWriter(output, fieldnames=columns)
                writer.writeheader()
                writer.writerows(store.laps(args.get('session_id', [''])[0]))
                return self.send(200, output.getvalue(), 'text/csv; charset=utf-8')
            if self.command != 'POST':
                raise Problem(404, 'not found')
            try:
                length = int(self.headers.get('Content-Length', '0'))
            except ValueError:
                raise Problem(400, 'invalid body length') from None
            if not 0 < length <= 65536:
                raise Problem(400, 'invalid body length')
            try:
                data = json.loads(self.rfile.read(length))
            except (ValueError, UnicodeDecodeError):
                raise Problem(400, 'invalid JSON') from None
            if not isinstance(data, dict):
                raise Problem(400, 'JSON object required')
            if path == '/api/laps':
                station = identifier(data.get('station_id'))
                self.authenticated(station_tokens.get(station))
                return self.send(200, store.submit(data, station))
            if path == '/api/station/heartbeat':
                station = identifier(data.get('station_id'))
                self.authenticated(station_tokens.get(station))
                if type(data.get('protocol_version')) is not int or data['protocol_version'] != 1:
                    raise Problem(400, 'unsupported protocol_version')
                return self.send(200, store.heartbeat(station))
            if path.startswith('/api/admin/'):
                self.authenticated(admin_token)
                if path == '/api/admin/sessions':
                    return self.send(200, store.register('sessions', 'session_id', data))
                if path == '/api/admin/pilots':
                    return self.send(200, store.register('pilots', 'pilot_id', data))
                if path == '/api/admin/assignments':
                    return self.send(200, store.assign(data, station_tokens))
                if path == '/api/admin/calibrations':
                    return self.send(200, store.calibrate(data, station_tokens))
                if path == '/api/admin/reviews':
                    return self.send(200, store.review(data))
                if path == '/api/admin/backup':
                    return self.send(200, {'path': store.backup()})
            raise Problem(404, 'not found')

        def station_identity(self):
            given = self.headers.get('Authorization', '').removeprefix('Bearer ')
            for station, token in station_tokens.items():
                if token and hmac.compare_digest(given, token):
                    return station
            raise Problem(401, 'authentication required')

        def do_GET(self):
            self.handle_request()

        def do_POST(self):
            self.handle_request()

        def handle_request(self):
            try:
                self.dispatch()
            except Problem as error:
                self.send(error.code, {'error': error.message})
            except (OSError, sqlite3.Error, ValueError):
                self.send(500, {'error': 'server error'})
    return Handler


def main():
    parser = argparse.ArgumentParser(description='Rice Racing local trials server')
    parser.add_argument('--db', default='trials.sqlite')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8080)
    args = parser.parse_args()
    admin = os.environ.get('RICE_ADMIN_TOKEN', '')
    stations = json.loads(os.environ.get('RICE_STATION_TOKENS', '{}'))
    if not admin or not isinstance(stations, dict) or not stations or not all(isinstance(k, str) and isinstance(v, str) and v and v != admin for k, v in stations.items()):
        parser.error('set RICE_ADMIN_TOKEN and nonempty RICE_STATION_TOKENS JSON with distinct station secrets')
    with ThreadingHTTPServer((args.host, args.port), make_handler(Store(args.db), admin, stations)) as server:
        print(f'Listening on {args.host}:{args.port}', flush=True)
        server.serve_forever()


if __name__ == '__main__':
    main()
