"""SQLite-backed local trial server. Run: python3 -m server.app --db trials.sqlite.

Set RICE_ADMIN_TOKEN and RICE_STATION_TOKENS (JSON station-id/token map).
Bind localhost by default; put TLS in front before using an untrusted network.
Offline restore (server stopped): python3 server/app.py --db trials.sqlite --restore backup.sqlite
"""
import argparse
import csv
import hashlib
import hmac
import io
import json
import math
import os
import re
import shutil
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

try:
    from server import field_v2, penalties_v2, scoring
except ImportError:  # executed as server/app.py or frozen with PyInstaller
    import field_v2
    import penalties_v2
    import scoring

ROOT = Path(__file__).resolve().parent.parent
ID = re.compile(r'^[A-Za-z0-9_.-]{1,80}$')
HEARTBEAT_TTL = timedelta(seconds=90)
SCHEMA_VERSION = 4
SYNC_ERROR_LIMIT = 1000          # rows kept in sync_errors (oldest pruned)
DETECTOR_MAX_BYTES = 4096        # serialized payload.detector
SYNC_MAX_BYTES = 2048            # serialized payload.sync
VALID_STATUSES = ('valid', 'VALID')
REQUIRED_TABLES = {'sessions', 'pilots', 'assignments', 'assignment_history', 'laps', 'reviews', 'calibrations'}
STATIC = {'/admin': 'admin.html', '/tv': 'tv.html', '/admin.js': 'admin.js', '/tv.js': 'tv.js',
          '/v2/field': 'field-v2.html', '/field-v2.js': 'field-v2.js',
          '/admin.css': 'admin.css', '/tv.css': 'tv.css'}
CONTENT_TYPES = {'.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8'}


class Problem(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def identifier(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise Problem(400, 'invalid identifier')
    return value


def now_utc():
    return datetime.now(timezone.utc).isoformat()


def text_field(value, limit=200):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= limit


def compact(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'))


class _Connection(sqlite3.Connection):
    """Commit/rollback like sqlite3, then close: no connection leaks across threads."""
    def __exit__(self, kind, value, traceback):
        try:
            return super().__exit__(kind, value, traceback)
        finally:
            self.close()


def validate_events(event, events):
    """Shape checks shared by station submissions and admin penalty review."""
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


def validate_optional(data):
    """Optional client v1 metadata: typed and bounded, stored verbatim in payload."""
    if data.get('lap_number') is not None and (type(data['lap_number']) is not int or not 0 <= data['lap_number'] <= 100000):
        raise Problem(400, 'invalid lap_number')
    if data.get('rules_version') is not None and (type(data['rules_version']) is not int or data['rules_version'] != scoring.RULES_VERSION):
        raise Problem(400, 'unsupported rules_version')
    calibration = data.get('calibration_version')
    if calibration is not None and not ((type(calibration) is int and 0 <= calibration <= 10**9) or (isinstance(calibration, str) and 0 < len(calibration) <= 120)):
        raise Problem(400, 'invalid calibration_version')
    stamp = data.get('client_timestamp')
    if stamp is not None and not ((isinstance(stamp, str) and len(stamp) <= 64) or (type(stamp) in (int, float) and math.isfinite(stamp))):
        raise Problem(400, 'invalid client_timestamp')
    for key, limit in (('detector', DETECTOR_MAX_BYTES), ('sync', SYNC_MAX_BYTES)):
        value = data.get(key)
        if value is None:
            continue
        # Lua JSON encoders may emit an empty table as []: accept lists, never scalars.
        if not isinstance(value, (dict, list)):
            raise Problem(400, 'invalid ' + key)
        if len(compact(value).encode('utf-8')) > limit:
            raise Problem(400, key + ' too large')
    for key in ('track_config',):
        if data.get(key) is not None and (not isinstance(data[key], str) or len(data[key]) > 120):
            raise Problem(400, 'invalid ' + key)


def reported_corrected(data, raw):
    """Final time computed by the station (raw + its configured penalties). Shape check only:
    an integer ms, never below the raw time. Anything else is ignored and the server falls back."""
    value = data.get('corrected_ms')
    if type(value) is int and raw <= value <= raw + 3600000:
        return value
    return None


PAYLOAD_EXTRAS = ('lap_number', 'assignment_revision', 'rules_version', 'scoring_version', 'calibration_version',
                  'track_config', 'detector_ok', 'calibration_verified', 'client_timestamp', 'detector', 'sync')


class Store:
    def __init__(self, path, calibrated_configs=(), scoring_armed=False, penalties_v2_enabled=False,
                 field_v2_only=False):
        self.path = Path(path)
        # Production is unarmed: no in-game physical commissioning was performed.
        # The switch is for isolated scoring contract tests, never the CLI.
        self.scoring_armed = scoring_armed
        # V2 has a separate opt-in endpoint; V1 submission and status logic stay unchanged.
        self.penalties_v2_enabled = penalties_v2_enabled is True
        # Dedicated field deployments reject synchronous geometry work, including replays.
        self.field_v2_only = field_v2_only is True
        # No track is trusted by default: client self-attestation is insufficient.
        self.calibrated_configs = frozenset(calibrated_configs)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        migrate(self.path)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10, factory=_Connection)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA busy_timeout=10000')
        return db

    # ---- sessions, pilots, assignments -------------------------------------------------
    def register(self, table, key, data):
        if table == 'sessions':
            return self.create_session(data)
        value, name = identifier(data.get(key)), data.get('name')
        if not text_field(name, 120):
            raise Problem(400, 'invalid name')
        with self.connect() as db:
            db.execute(f'INSERT INTO {table} ({key}, name) VALUES (?, ?) ON CONFLICT({key}) DO UPDATE SET name=excluded.name', (value, name.strip()))
        return {key: value, 'name': name.strip()}

    def create_session(self, data):
        session, name, active = identifier(data.get('session_id')), data.get('name'), data.get('active', False)
        if not text_field(name, 120) or type(active) is not bool:
            raise Problem(400, 'invalid name')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT INTO sessions (session_id, name, created_utc, active) VALUES (?, ?, ?, 0) ON CONFLICT(session_id) DO UPDATE SET name=excluded.name', (session, name.strip(), now_utc()))
            if active:
                db.execute('UPDATE sessions SET active = (session_id = ?)', (session,))
        return self.session(session)

    def set_active(self, data):
        session = identifier(data.get('session_id'))
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT 1 FROM sessions WHERE session_id=?', (session,)).fetchone():
                raise Problem(404, 'session not found')
            db.execute('UPDATE sessions SET active = (session_id = ?)', (session,))
        return self.session(session)

    def session(self, session):
        with self.connect() as db:
            row = db.execute('SELECT session_id, name, created_utc, active FROM sessions WHERE session_id=?', (session,)).fetchone()
        return {**dict(row), 'active': bool(row['active'])} if row else None

    def sessions(self):
        with self.connect() as db:
            rows = db.execute('SELECT session_id, name, created_utc, active FROM sessions ORDER BY created_utc, session_id').fetchall()
        return [{**dict(r), 'active': bool(r['active'])} for r in rows]

    def active_session(self):
        with self.connect() as db:
            row = db.execute('SELECT session_id FROM sessions WHERE active=1 ORDER BY session_id LIMIT 1').fetchone()
        return row['session_id'] if row else None

    def pilots(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute('SELECT pilot_id, name FROM pilots ORDER BY pilot_id')]

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
                cursor = db.execute('INSERT INTO assignment_history (station_id,session_id,pilot_id,created_utc) VALUES (?,?,?,?)', (station, session, pilot, now_utc()))
                revision = cursor.lastrowid
                db.execute('UPDATE assignments SET revision=? WHERE station_id=?', (revision, station))
            except sqlite3.IntegrityError:
                raise Problem(400, 'unknown session or pilot') from None
        return {'station_id': station, 'session_id': session, 'pilot_id': pilot, 'assignment_revision': revision}

    def assignment_history(self, station=None):
        query = 'SELECT h.*, p.name AS pilot_name FROM assignment_history h LEFT JOIN pilots p USING (pilot_id)'
        args = ()
        if station:
            query, args = query + ' WHERE h.station_id=?', (identifier(station),)
        with self.connect() as db:
            rows = [dict(r) for r in db.execute(query + ' ORDER BY h.revision', args)]
        previous = {}
        for row in rows:
            before = previous.get(row['station_id'])
            row['previous_pilot_id'] = before['pilot_id'] if before else None
            row['previous_session_id'] = before['session_id'] if before else None
            row['reassignment'] = bool(before) and (before['pilot_id'], before['session_id']) != (row['pilot_id'], row['session_id'])
            previous[row['station_id']] = row
        return rows

    def current_assignments(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute('SELECT a.station_id, a.session_id, a.pilot_id, a.revision AS assignment_revision, p.name AS pilot_name FROM assignments a LEFT JOIN pilots p USING (pilot_id) ORDER BY a.station_id')]

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
        seen = now_utc()
        with self.connect() as db:
            db.execute('INSERT INTO heartbeats VALUES (?, ?) ON CONFLICT(station_id) DO UPDATE SET seen_utc=excluded.seen_utc', (station, seen))
        return self.assignment(station)

    def stations(self, station_ids):
        """Every configured station: connectivity, assignment, last lap, detector summary, sync errors."""
        result = []
        with self.connect() as db:
            for station in sorted(station_ids):
                last = db.execute('SELECT lap_id, status, created_utc, payload FROM laps WHERE station_id=? ORDER BY created_utc DESC, lap_id DESC LIMIT 1', (station,)).fetchone()
                errors = db.execute('SELECT COUNT(*) AS n, MAX(created_utc) AS last FROM sync_errors WHERE station_id=?', (station,)).fetchone()
                detector_errors = db.execute("SELECT COUNT(*) AS n FROM laps WHERE station_id=? AND status='DETECTOR_ERROR'", (station,)).fetchone()
                payload = json.loads(last['payload']) if last else {}
                result.append({**self.assignment(station), 'offline': None,
                               'last_lap_id': last['lap_id'] if last else None,
                               'last_lap_status': last['status'] if last else None,
                               'last_lap_utc': last['created_utc'] if last else None,
                               'detector_ok': payload.get('detector_ok'), 'detector': payload.get('detector'),
                               'calibration_version': payload.get('calibration_version'),
                               'detector_error_laps': detector_errors['n'],
                               'sync_error_count': errors['n'], 'last_sync_error_utc': errors['last']})
        for item in result:
            item['offline'] = not item['online']
        return result

    # ---- synchronization errors --------------------------------------------------------
    def record_sync_error(self, station, path, code, cause, lap_id=None):
        with self.connect() as db:
            cursor = db.execute('INSERT INTO sync_errors (station_id, path, http_status, cause, lap_id, created_utc) VALUES (?,?,?,?,?,?)',
                                (station, path[:80], code, cause[:200], lap_id, now_utc()))
            db.execute('DELETE FROM sync_errors WHERE id <= ?', (cursor.lastrowid - SYNC_ERROR_LIMIT,))

    def sync_errors(self, station=None, limit=200):
        query, args = 'SELECT * FROM sync_errors', ()
        if station:
            query, args = query + ' WHERE station_id=?', (identifier(station),)
        with self.connect() as db:
            return [dict(r) for r in db.execute(query + ' ORDER BY id DESC LIMIT ?', args + (limit,))]

    # ---- calibration and reviews --------------------------------------------------------
    def calibrate(self, data, stations):
        station, track = identifier(data.get('station_id')), identifier(data.get('track_config'))
        reference, operator = data.get('reference'), data.get('operator')
        if station not in stations or not all(text_field(v) for v in (reference, operator)) or type(data.get('active', True)) is not bool:
            raise Problem(400, 'invalid calibration provenance')
        active = data.get('active', True)
        now = now_utc()
        with self.connect() as db:
            db.execute('INSERT INTO calibrations VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(station_id, track_config) DO UPDATE SET reference=excluded.reference, operator=excluded.operator, active=excluded.active, updated_utc=excluded.updated_utc', (station, track, reference.strip(), operator.strip(), active, now))
        return {'station_id': station, 'track_config': track, 'reference': reference.strip(), 'operator': operator.strip(), 'active': active, 'updated_utc': now}

    def calibrations(self):
        with self.connect() as db:
            return [{**dict(r), 'active': bool(r['active'])} for r in db.execute('SELECT * FROM calibrations ORDER BY station_id, track_config')]

    def review(self, data):
        station, lap_id = identifier(data.get('station_id')), identifier(data.get('lap_id'))
        decision, reviewer, reason = data.get('decision'), data.get('reviewer'), data.get('reason')
        if decision not in ('approve', 'reject') or not all(text_field(v) for v in (reviewer, reason)):
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
                corrected = reported_corrected(payload, row['raw_ms'])
                if corrected is None:
                    corrected = scoring.corrected_ms('endurance', row['raw_ms'], events)
                if corrected is None:
                    raise Problem(409, 'stored events do not score under current rules')
            status = 'VALID' if decision == 'approve' else 'INVALID'
            reference = calibration['reference'] if calibration else row['calibration_reference']
            db.execute('UPDATE laps SET status=?, corrected_ms=?, calibration_reference=? WHERE station_id=? AND lap_id=?', (status, corrected, reference, station, lap_id))
            db.execute('INSERT INTO reviews (station_id,lap_id,session_id,reviewer,decision,reason,before_status,after_status,created_utc,calibration_reference,kind,before_events,after_events,before_corrected_ms,after_corrected_ms) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       (station, lap_id, row['session_id'], reviewer.strip(), decision, reason.strip(), row['status'], status, now_utc(), reference, 'status', row['events'], row['events'], row['corrected_ms'], corrected))
        return {'station_id': station, 'lap_id': lap_id, 'status': status, 'corrected_ms': corrected, 'calibration_reference': reference}

    def review_penalties(self, data):
        """Admin adjusts penalty counts with a reason; server recalculates, status never changes here."""
        station, lap_id = identifier(data.get('station_id')), identifier(data.get('lap_id'))
        reviewer, reason, events = data.get('reviewer'), data.get('reason'), data.get('events')
        if not all(text_field(v) for v in (reviewer, reason)):
            raise Problem(400, 'invalid review')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM laps WHERE station_id=? AND lap_id=?', (station, lap_id)).fetchone()
            if not row:
                raise Problem(404, 'lap not found')
            before = json.loads(row['events'])
            merged = dict(before)
            penalty_keys = {'cone', 'off_course', 'slalom'} if row['event'] == 'endurance' else {'doo', 'oc'}
            if not isinstance(events, dict) or not events or set(events) - penalty_keys:
                raise Problem(400, 'invalid events')
            merged.update(events)
            validate_events(row['event'], merged)
            preview = scoring.corrected_ms(row['event'], row['raw_ms'], merged)
            if preview is None:
                raise Problem(400, 'events do not score under current rules')
            # Only an already official lap keeps an official corrected time; never promotes status.
            corrected = preview if row['status'] in VALID_STATUSES else None
            after_json = compact(merged)
            db.execute('UPDATE laps SET events=?, corrected_ms=? WHERE station_id=? AND lap_id=?', (after_json, corrected, station, lap_id))
            db.execute('INSERT INTO reviews (station_id,lap_id,session_id,reviewer,decision,reason,before_status,after_status,created_utc,calibration_reference,kind,before_events,after_events,before_corrected_ms,after_corrected_ms) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       (station, lap_id, row['session_id'], reviewer.strip(), 'penalties', reason.strip(), row['status'], row['status'], now_utc(), row['calibration_reference'], 'penalties', compact(before), after_json, row['corrected_ms'], corrected))
        return {'station_id': station, 'lap_id': lap_id, 'status': row['status'], 'events_before': before, 'events': merged,
                'corrected_ms': corrected, 'corrected_preview_ms': preview, 'official': corrected is not None and self.scoring_armed}

    def reviews(self, session=None):
        query, args = 'SELECT * FROM reviews', ()
        if session:
            query, args = query + ' WHERE session_id=?', (identifier(session),)
        with self.connect() as db:
            return {'reviews': [dict(row) for row in db.execute(query + ' ORDER BY id', args)]}

    def review_penalties_v2(self, data):
        station, lap_id = identifier(data.get('station_id')), identifier(data.get('lap_id'))
        reviewer, reason, counts = data.get('reviewer'), data.get('reason'), data.get('counts')
        if (not text_field(reviewer) or not text_field(reason) or not isinstance(counts, dict)
                or set(counts) != {'touched_cones', 'off_course_events', 'missed_slaloms'}
                or any(type(value) is not int or not 0 <= value <= 100 for value in counts.values())):
            raise Problem(400, 'invalid V2 penalty review')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            lap = db.execute('SELECT * FROM laps WHERE station_id=? AND lap_id=?', (station, lap_id)).fetchone()
            replay = db.execute('SELECT settings_json FROM penalties_v2_replays WHERE station_id=? AND lap_id=?', (station, lap_id)).fetchone()
            if not lap or not replay or lap['event'] != 'endurance':
                raise Problem(404, 'V2 lap/replay not found')
            settings = json.loads(replay['settings_json'])
            evidence = []
            for i in range(counts['touched_cones']):
                evidence.append({'type': 'cone_contact', 'cone_id': f'review-cone-{i}', 'penalizable': True})
            for kind, count in (('off_course', counts['off_course_events']), ('slalom_violation', counts['missed_slaloms'])):
                for i in range(count):
                    evidence.append({'type': kind, 'incident_id': f'review-{kind}-{i}', 'penalizable': True})
            preview = scoring.corrected_ms_v2(lap['raw_ms'], evidence, settings)
            if preview is None:
                raise Problem(400, 'V2 penalties cannot be scored with stored settings')
            before = json.loads(lap['events'])
            after = dict(counts)
            corrected = preview if lap['status'] == 'VALID' and self.scoring_armed else None
            db.execute('UPDATE laps SET corrected_ms=? WHERE station_id=? AND lap_id=?', (corrected, station, lap_id))
            db.execute('INSERT INTO reviews (station_id,lap_id,session_id,reviewer,decision,reason,before_status,after_status,created_utc,calibration_reference,kind,before_events,after_events,before_corrected_ms,after_corrected_ms) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       (station, lap_id, lap['session_id'], reviewer.strip(), 'penalties', reason.strip(), lap['status'], lap['status'], now_utc(), lap['calibration_reference'], 'penalties_v2', compact(before), compact({'counts': after, 'corrected_preview_ms': preview}), lap['corrected_ms'], corrected))
        return {'station_id': station, 'lap_id': lap_id, 'status': lap['status'], 'counts': after,
                'corrected_ms': corrected, 'corrected_preview_ms': preview, 'official': corrected is not None}

    def reviews_v2(self, station, lap_id):
        station, lap_id = identifier(station), identifier(lap_id)
        with self.connect() as db:
            rows = db.execute('SELECT * FROM reviews WHERE station_id=? AND lap_id=? AND kind=? ORDER BY id',
                              (station, lap_id, 'penalties_v2')).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            after = json.loads(item.pop('after_events'))
            item['counts'] = after['counts']
            item['corrected_preview_ms'] = after['corrected_preview_ms']
            result.append(item)
        return {'reviews': result}

    def configure_penalties_v2(self, data):
        station, track_config = identifier(data.get('station_id')), identifier(data.get('track_config'))
        reference, config, settings = data.get('reference'), data.get('config'), data.get('settings')
        if not text_field(reference, 200) or not isinstance(config, dict) or not isinstance(settings, dict):
            raise Problem(400, 'invalid V2 calibration')
        if config.get('schema') != penalties_v2.CONFIG_SCHEMA or config.get('track') != track_config:
            raise Problem(400, 'V2 geometry track must match track_config')
        try:
            normalized = penalties_v2._settings(settings)
            penalties_v2.validate_config(config, normalized)
            if len(compact(config).encode('utf-8')) > 4_000_000:
                raise Problem(413, 'V2 geometry configuration too large')
        except penalties_v2.TrajectoryError as error:
            raise Problem(400, str(error)) from None
        config_json, settings_json = compact(config), compact(normalized)
        now = now_utc()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT INTO penalties_v2_configs (station_id,track_config,config_json,settings_json,reference,active,updated_utc) VALUES (?,?,?,?,?,1,?) ON CONFLICT(station_id,track_config) DO UPDATE SET config_json=excluded.config_json, settings_json=excluded.settings_json, reference=excluded.reference, active=1, updated_utc=excluded.updated_utc',
                       (station, track_config, config_json, settings_json, reference.strip(), now))
            db.execute('INSERT INTO calibrations (station_id,track_config,reference,operator,active,updated_utc) VALUES (?,?,?,?,1,?) ON CONFLICT(station_id,track_config) DO UPDATE SET reference=excluded.reference, operator=excluded.operator, active=1, updated_utc=excluded.updated_utc',
                       (station, track_config, reference.strip(), 'v2-admin', now))
        return {'station_id': station, 'track_config': track_config, 'reference': reference.strip(),
                'active': True, 'updated_utc': now}

    def submit_v2(self, data, station):
        if self.field_v2_only:
            raise Problem(404, 'trajectory ingestion is disabled in field-only mode')
        if not self.penalties_v2_enabled:
            raise Problem(404, 'V2 ingestion is disabled')
        if type(data.get('protocol_version')) is not int or data['protocol_version'] != 2:
            raise Problem(400, 'unsupported protocol_version')
        for field in ('station_id', 'session_id', 'pilot_id', 'lap_id', 'track_config'):
            identifier(data.get(field))
        if data['station_id'] != station or data.get('event', 'endurance') != 'endurance':
            raise Problem(400, 'V2 requires the authenticated endurance station')
        raw_ms = data.get('raw_ms')
        if type(raw_ms) is not int or not 0 < raw_ms <= 3600000:
            raise Problem(400, 'invalid raw_ms')
        trace = data.get('trace')
        if not isinstance(trace, dict):
            raise Problem(400, 'trajectory trace required')
        lap = trace.get('lap')
        expected = {key: data[key] for key in ('station_id', 'session_id', 'lap_id')}
        if not isinstance(lap, dict) or any(lap.get(key) != value for key, value in expected.items()) or lap.get('raw_ms') != raw_ms:
            raise Problem(400, 'trajectory identity does not match submission')
        if len(compact(data).encode('utf-8')) > 8_000_000:
            raise Problem(413, 'V2 submission too large')
        try:
            identifier(data.get('track_config'))
        except Problem:
            raise Problem(400, 'invalid track_config') from None
        with self.connect() as db:
            calibration = db.execute('SELECT * FROM penalties_v2_configs WHERE station_id=? AND track_config=? AND active=1',
                                     (station, data['track_config'])).fetchone()
            assignment = db.execute('SELECT session_id,pilot_id,revision FROM assignments WHERE station_id=?', (station,)).fetchone()
        if not calibration:
            raise Problem(409, 'no active V2 geometry calibration')
        if not assignment or (assignment['session_id'], assignment['pilot_id']) != (data['session_id'], data['pilot_id']):
            raise Problem(409, 'station assignment mismatch')
        try:
            config = json.loads(calibration['config_json'])
            settings = json.loads(calibration['settings_json'])
            result = penalties_v2.analyze_trajectory(trace, config, settings)
        except (penalties_v2.TrajectoryError, ValueError) as error:
            raise Problem(400, str(error)) from None
        status = 'VALID' if self.scoring_armed and not result['review_required'] else 'PENDING_REVIEW'
        corrected = result['final_ms'] if status == 'VALID' else None
        events = {'cone': result['penalty_counts']['touched_cones'],
                  'off_course': result['penalty_counts']['off_course_events'], 'slalom': []}
        payload, events_json = compact(data), compact(events)
        digest = hashlib.sha256(compact(result).encode('utf-8')).hexdigest()
        created = now_utc()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute('SELECT payload,corrected_ms,status FROM laps WHERE station_id=? AND lap_id=?',
                                  (station, data['lap_id'])).fetchone()
            if existing:
                if existing['payload'] != payload:
                    raise Problem(409, 'lap_id already used with different payload')
                return {'lap_id': data['lap_id'], 'corrected_ms': existing['corrected_ms'],
                        'status': existing['status'], 'duplicate': True}
            current = db.execute('SELECT session_id,pilot_id,revision FROM assignments WHERE station_id=?', (station,)).fetchone()
            if not current or (current['session_id'], current['pilot_id']) != (data['session_id'], data['pilot_id']):
                raise Problem(409, 'station assignment changed during submission')
            if not self.scoring_armed:
                status = 'PENDING_REVIEW'
            db.execute('INSERT INTO laps (station_id,lap_id,session_id,pilot_id,event,raw_ms,corrected_ms,events,status,payload,created_utc,calibration_reference,lap_number) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       (station, data['lap_id'], data['session_id'], data['pilot_id'], 'endurance', raw_ms,
                        corrected, events_json, status, payload, created, calibration['reference'], data.get('lap_number')))
            db.execute('INSERT INTO penalties_v2_replays (station_id,lap_id,trace_json,config_json,settings_json,result_json,result_sha256,created_utc) VALUES (?,?,?,?,?,?,?,?)',
                       (station, data['lap_id'], compact(trace), calibration['config_json'], calibration['settings_json'],
                        compact(result), digest, created))
        return {'lap_id': data['lap_id'], 'corrected_ms': corrected, 'status': status, 'duplicate': False,
                'penalty_ms': result['penalty_ms'], 'analysis': result}

    def replay_penalties_v2(self, station, lap_id):
        if self.field_v2_only:
            raise Problem(404, 'trajectory replay is disabled in field-only mode')
        station, lap_id = identifier(station), identifier(lap_id)
        with self.connect() as db:
            row = db.execute('SELECT * FROM penalties_v2_replays WHERE station_id=? AND lap_id=?', (station, lap_id)).fetchone()
        if not row:
            raise Problem(404, 'V2 replay not found')
        trace, config, settings = json.loads(row['trace_json']), json.loads(row['config_json']), json.loads(row['settings_json'])
        result = penalties_v2.analyze_trajectory(trace, config, settings)
        digest = hashlib.sha256(compact(result).encode('utf-8')).hexdigest()
        return {'station_id': station, 'lap_id': lap_id, 'trace': trace, 'config': config, 'settings': settings,
                'result': result, 'replay_matches': digest == row['result_sha256'],
                'stored_sha256': row['result_sha256'], 'replayed_sha256': digest}

    # ---- laps -------------------------------------------------------------------------
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
        validate_events(event, events)
        validate_optional(data)
        if event == 'endurance':
            if type(data.get('scoring_version')) is not int or data['scoring_version'] != 1:
                raise Problem(400, 'unsupported scoring_version')
            # The station computes the final time (its penalty settings are editable in-game).
            # A well-formed value is stored as sent; otherwise the fixed FSAE rules are used.
            reported = reported_corrected(data, raw)
            corrected = reported if reported is not None else scoring.corrected_ms(event, raw, events)
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
        payload = compact(data)
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
            if not self.scoring_armed and status in VALID_STATUSES:
                status, corrected = 'PENDING_REVIEW', None
                calibration = None
            # A pending lap keeps the station's final time for display; ranking still needs
            # VALID status and armed scoring.
            if event == 'endurance' and corrected is None and status == 'PENDING_REVIEW':
                corrected = reported_corrected(data, raw)
            lap_number = data.get('lap_number')
            db.execute('INSERT INTO laps (station_id, lap_id, session_id, pilot_id, event, raw_ms, corrected_ms, events, status, payload, created_utc, calibration_reference, lap_number) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', (station, data['lap_id'], data['session_id'], data['pilot_id'], event, raw, corrected, json.dumps(events, sort_keys=True), status, payload, now_utc(), calibration['reference'] if calibration and status == 'VALID' else None, lap_number))
        return {'lap_id': data['lap_id'], 'corrected_ms': corrected, 'status': status, 'duplicate': False}

    def laps(self, session):
        with self.connect() as db:
            rows = db.execute('SELECT station_id, lap_id, session_id, pilot_id, event, raw_ms, corrected_ms, events, status, created_utc, calibration_reference, lap_number FROM laps WHERE session_id=? ORDER BY created_utc, lap_id', (identifier(session),)).fetchall()
            return [dict(row) for row in rows]

    def laps_detail(self, session=None):
        """Admin view: every stored lap with client metadata from the verbatim payload."""
        query, args = 'SELECT * FROM laps', ()
        if session:
            query, args = query + ' WHERE session_id=?', (identifier(session),)
        with self.connect() as db:
            rows = [dict(r) for r in db.execute(query + ' ORDER BY session_id, station_id, created_utc, lap_id', args)]
        for row in rows:
            payload = json.loads(row.pop('payload'))
            row['events'] = json.loads(row['events'])
            for key in PAYLOAD_EXTRAS:
                row.setdefault(key, None)
                if row[key] is None:
                    row[key] = payload.get(key)
            row['client_status'] = payload.get('status')
            row['client_corrected_ms_ignored'] = payload.get('corrected_ms')
        return rows

    def leaderboard(self, session=None):
        if not session:
            session = self.active_session()
        info = self.session(session) if session else None
        laps = self.laps(session) if session else []
        best, counts, valid_counts = {}, {}, {}
        for lap in laps:
            counts[lap['status']] = counts.get(lap['status'], 0) + 1
            if self.scoring_armed and lap['corrected_ms'] is not None and lap['status'] in VALID_STATUSES:
                key = (lap['event'], lap['pilot_id'])
                valid_counts[key] = valid_counts.get(key, 0) + 1
                order = (lap['corrected_ms'], lap['raw_ms'], lap['created_utc'], lap['lap_id'])
                if key not in best or order < best[key][0]:
                    best[key] = (order, lap)
        with self.connect() as db:
            names = {row['pilot_id']: row['name'] for row in db.execute('SELECT pilot_id, name FROM pilots')}
            stations = [self.assignment(row['station_id']) for row in db.execute('SELECT station_id FROM assignments WHERE session_id=? ORDER BY station_id', (session,))] if session else []
        # Deterministic: corrected asc, raw asc, earlier lap first, pilot_id.
        ordered = sorted(best.items(), key=lambda item: (item[0][0], item[1][0], item[0][1]))
        ranking = {}
        for (event, pilot), (_, lap) in ordered:
            rows = ranking.setdefault(event, [])
            events = json.loads(lap['events'])
            clean = {k: v for k, v in events.items() if k in ('right_ms', 'left_ms')}
            base = scoring.corrected_ms(event, lap['raw_ms'], clean)
            rows.append({'rank': len(rows) + 1, 'event': event, 'pilot_id': pilot, 'pilot_name': names.get(pilot, pilot),
                         'best_corrected_ms': lap['corrected_ms'], 'raw_ms': lap['raw_ms'], 'penalties': events,
                         'penalty_ms': lap['corrected_ms'] - base if base is not None else None,
                         'gap_ms': lap['corrected_ms'] - rows[0]['best_corrected_ms'] if rows else 0,
                         'lap_id': lap['lap_id'], 'station_id': lap['station_id'], 'lap_utc': lap['created_utc'],
                         'valid_laps': valid_counts[(event, pilot)]})
        return {'session_id': session, 'session_name': info['name'] if info else None, 'active': bool(info and info['active']),
                'scoring_armed': self.scoring_armed, 'rules_version': scoring.RULES_VERSION, 'generated_utc': now_utc(),
                'laps': laps, 'ranking': ranking,
                'leaders': [{'event': e, 'pilot_id': p, 'pilot_name': names.get(p, p), 'best_ms': lap['corrected_ms']} for (e, p), (_, lap) in sorted(ordered, key=lambda item: item[0][0])],
                'diagnostics': {'status_counts': counts, 'stations': stations, 'scoring_armed': self.scoring_armed,
                                'pending_review': counts.get('PENDING_REVIEW', 0) + counts.get('DETECTOR_ERROR', 0)}}

    # ---- export, backup, diagnostics ----------------------------------------------------
    def export(self, session=None):
        data = {'exported_utc': now_utc(), 'schema_version': SCHEMA_VERSION, 'rules_version': scoring.RULES_VERSION,
                'penalties_ms': scoring.PENALTIES, 'scoring_armed': self.scoring_armed, 'session_filter': session,
                'sessions': self.sessions(), 'pilots': self.pilots(), 'laps': self.laps_detail(session),
                'reviews': self.reviews(session)['reviews'], 'assignments': self.current_assignments(),
                'assignment_history': self.assignment_history(), 'calibrations': self.calibrations(),
                'sync_errors': self.sync_errors(limit=SYNC_ERROR_LIMIT)}
        if session:
            data['sessions'] = [s for s in data['sessions'] if s['session_id'] == session]
            data['assignment_history'] = [a for a in data['assignment_history'] if a['session_id'] == session]
            data['assignments'] = [a for a in data['assignments'] if a['session_id'] == session]
        return data

    def diagnostics(self, station_ids):
        with self.connect() as db:
            counts = {r['status']: r['n'] for r in db.execute('SELECT status, COUNT(*) AS n FROM laps GROUP BY status')}
            version = db.execute('PRAGMA user_version').fetchone()[0]
            journal = db.execute('PRAGMA journal_mode').fetchone()[0]
        return {'scoring_armed': self.scoring_armed, 'rules_version': scoring.RULES_VERSION, 'penalties_ms': scoring.PENALTIES,
                'schema_version': version, 'journal_mode': journal, 'database': str(self.path), 'status_counts': counts,
                'active_session': self.active_session(), 'stations': self.stations(station_ids),
                'heartbeat_ttl_s': int(HEARTBEAT_TTL.total_seconds()), 'backups': self.backups()[:20]}

    def backup(self):
        destination = self.path.parent / 'backups' / (self.path.stem + '-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.sqlite')
        destination.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as source, sqlite3.connect(destination, factory=_Connection) as target:
            source.backup(target)
        check_integrity(destination)
        return str(destination)

    def backups(self):
        folder = self.path.parent / 'backups'
        if not folder.is_dir():
            return []
        return [{'path': str(p), 'bytes': p.stat().st_size} for p in sorted(folder.glob('*.sqlite'), reverse=True)]


def migrate(path):
    """Idempotent migrations keyed by PRAGMA user_version; WAL for crash recovery."""
    db = sqlite3.connect(path, timeout=10)
    try:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA busy_timeout=10000')
        db.execute('PRAGMA journal_mode=WAL')
        version = db.execute('PRAGMA user_version').fetchone()[0]
        if version > SCHEMA_VERSION:
            raise RuntimeError(f'database schema {version} is newer than server schema {SCHEMA_VERSION}')
        columns = lambda table: {row['name'] for row in db.execute(f'PRAGMA table_info({table})')}
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
        # v1 (legacy, unversioned) column additions.
        if 'calibration_reference' not in columns('laps'):
            db.execute('ALTER TABLE laps ADD COLUMN calibration_reference TEXT')
        if 'revision' not in columns('assignments'):
            db.execute('ALTER TABLE assignments ADD COLUMN revision INTEGER')
        # v2: active session, lap numbering, penalty review trail, sync errors.
        for table, column, ddl in (('sessions', 'active', 'INTEGER NOT NULL DEFAULT 0'), ('sessions', 'created_utc', 'TEXT'),
                                   ('laps', 'lap_number', 'INTEGER'), ('reviews', 'kind', "TEXT NOT NULL DEFAULT 'status'"),
                                   ('reviews', 'before_events', 'TEXT'), ('reviews', 'after_events', 'TEXT'),
                                   ('reviews', 'before_corrected_ms', 'INTEGER'), ('reviews', 'after_corrected_ms', 'INTEGER')):
            if column not in columns(table):
                db.execute(f'ALTER TABLE {table} ADD COLUMN {column} {ddl}')
        db.executescript('''
            CREATE TABLE IF NOT EXISTS sync_errors (id INTEGER PRIMARY KEY AUTOINCREMENT, station_id TEXT NOT NULL, path TEXT NOT NULL, http_status INTEGER NOT NULL, cause TEXT NOT NULL, lap_id TEXT, created_utc TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS penalties_v2_configs (station_id TEXT NOT NULL, track_config TEXT NOT NULL, config_json TEXT NOT NULL, settings_json TEXT NOT NULL, reference TEXT NOT NULL, active INTEGER NOT NULL, updated_utc TEXT NOT NULL, PRIMARY KEY(station_id, track_config));
            CREATE TABLE IF NOT EXISTS penalties_v2_replays (station_id TEXT NOT NULL, lap_id TEXT NOT NULL, trace_json TEXT NOT NULL, config_json TEXT NOT NULL, settings_json TEXT NOT NULL, result_json TEXT NOT NULL, result_sha256 TEXT NOT NULL, created_utc TEXT NOT NULL, PRIMARY KEY(station_id, lap_id), FOREIGN KEY(station_id, lap_id) REFERENCES laps(station_id, lap_id) ON DELETE CASCADE);
            CREATE INDEX IF NOT EXISTS laps_session ON laps(session_id, created_utc);
            CREATE INDEX IF NOT EXISTS sync_errors_station ON sync_errors(station_id, id);
        ''')
        field_v2.initialize(db)
        db.execute(f'PRAGMA user_version={SCHEMA_VERSION}')
        db.commit()
    finally:
        db.close()


def check_integrity(path):
    """Raise unless path is a readable SQLite trials database that passes integrity_check."""
    path = Path(path)
    if not path.is_file():
        raise RuntimeError(f'not a file: {path}')
    try:
        db = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
        try:
            result = [row[0] for row in db.execute('PRAGMA integrity_check')]
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            version = db.execute('PRAGMA user_version').fetchone()[0]
        finally:
            db.close()
    except sqlite3.DatabaseError as error:
        raise RuntimeError(f'not a valid SQLite database: {error}') from None
    if result != ['ok']:
        raise RuntimeError('integrity_check failed: ' + '; '.join(result[:5]))
    missing = REQUIRED_TABLES - tables
    if missing:
        raise RuntimeError('missing tables: ' + ', '.join(sorted(missing)))
    if version > SCHEMA_VERSION:
        raise RuntimeError(f'backup schema {version} is newer than server schema {SCHEMA_VERSION}')
    return {'integrity': 'ok', 'schema_version': version}


def restore(db_path, source):
    """Offline restore: validate source, keep a pre-restore copy, copy via SQLite backup API, migrate."""
    db_path, source = Path(db_path), Path(source)
    check_integrity(source)
    if db_path.resolve() == source.resolve():
        raise RuntimeError('source and destination are the same file')
    db_path.parent.mkdir(parents=True, exist_ok=True)
    previous = None
    if db_path.exists():
        previous = db_path.parent / 'backups' / (db_path.stem + '-pre-restore-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.sqlite')
        previous.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(db_path, factory=_Connection) as current, sqlite3.connect(previous, factory=_Connection) as copy:
            current.backup(copy)
        check_integrity(previous)
    staging = db_path.with_name(db_path.name + '.restoring')
    staging.unlink(missing_ok=True)
    with sqlite3.connect(f'file:{source}?mode=ro', uri=True, factory=_Connection) as src, sqlite3.connect(staging, factory=_Connection) as dst:
        src.backup(dst)
    check_integrity(staging)
    for suffix in ('-wal', '-shm'):
        Path(str(db_path) + suffix).unlink(missing_ok=True)
    os.replace(staging, db_path)
    migrate(db_path)
    result = check_integrity(db_path)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    return {'restored_from': str(source), 'sha256': digest, 'database': str(db_path),
            'pre_restore_copy': str(previous) if previous else None, **result}


def csv_text(rows, columns):
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=columns, extrasaction='ignore')
    writer.writeheader()
    for row in rows:
        clean = {}
        for key in columns:
            value = row.get(key)
            if isinstance(value, (dict, list)):
                value = compact(value)
            if isinstance(value, str) and value[:1] in ('=', '+', '-', '@', '\t', '\r'):
                value = "'" + value  # spreadsheet formula injection guard
            clean[key] = value
        writer.writerow(clean)
    return output.getvalue()


LAP_COLUMNS = ['session_id', 'station_id', 'lap_id', 'lap_number', 'pilot_id', 'event', 'raw_ms', 'corrected_ms', 'events', 'status',
               'client_status', 'created_utc', 'calibration_reference', 'assignment_revision', 'rules_version', 'scoring_version',
               'calibration_version', 'track_config', 'detector_ok', 'calibration_verified', 'client_timestamp', 'detector', 'sync']
REVIEW_COLUMNS = ['id', 'station_id', 'lap_id', 'session_id', 'reviewer', 'decision', 'reason', 'before_status', 'after_status', 'created_utc',
                  'calibration_reference', 'kind', 'before_events', 'after_events', 'before_corrected_ms', 'after_corrected_ms']
EXPORT_TABLES = {
    'laps': LAP_COLUMNS,
    'reviews': REVIEW_COLUMNS,
    'assignment_history': ['revision', 'station_id', 'session_id', 'pilot_id', 'pilot_name', 'created_utc', 'previous_pilot_id', 'previous_session_id', 'reassignment'],
    'assignments': ['station_id', 'session_id', 'pilot_id', 'pilot_name', 'assignment_revision'],
    'calibrations': ['station_id', 'track_config', 'reference', 'operator', 'active', 'updated_utc'],
    'sessions': ['session_id', 'name', 'created_utc', 'active'],
    'pilots': ['pilot_id', 'name'],
    'sync_errors': ['id', 'station_id', 'path', 'http_status', 'cause', 'lap_id', 'created_utc'],
}


def make_handler(store, admin_token, station_tokens):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def send(self, code, data, content_type='application/json; charset=utf-8', filename=None):
            body = (json.dumps(data) if content_type.startswith('application/json') else data).encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'")
            if filename:
                self.send_header('Content-Disposition', f'attachment; filename="{filename}"')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def authenticated(self, token):
            given = self.headers.get('Authorization', '').removeprefix('Bearer ')
            if token and hmac.compare_digest(given, token):
                return
            # 403: a genuine credential of another role/station; 401: missing or unknown.
            known = [admin_token, *station_tokens.values()]
            if given and any(k and hmac.compare_digest(given, k) for k in known):
                raise Problem(403, 'credential not authorized for this resource')
            raise Problem(401, 'authentication required')

        def arg(self, args, name):
            value = args.get(name, [''])[0]
            return identifier(value) if value else None

        def dispatch(self):
            url = urlsplit(self.path)
            path = url.path
            args = parse_qs(url.query)
            if path == '/api/station/assignment' and self.command == 'GET':
                return self.send(200, store.assignment(self.station_identity()))
            if self.command == 'GET' and path in STATIC:
                filename = STATIC[path]
                return self.send(200, (ROOT / 'leaderboard' / filename).read_text(encoding='utf-8'), CONTENT_TYPES[Path(filename).suffix])
            if self.command == 'GET' and path == '/api/leaderboard':
                return self.send(200, store.leaderboard(self.arg(args, 'session_id')))
            if self.command == 'GET' and path == '/api/sessions':
                return self.send(200, {'sessions': store.sessions(), 'active_session': store.active_session(), 'scoring_armed': store.scoring_armed})
            if self.command == 'GET' and path.startswith('/api/admin/'):
                self.authenticated(admin_token)
                session = self.arg(args, 'session_id')
                if path == '/api/admin/v2/field-reviews':
                    return self.send(200, {'reviews': field_v2.review_history(store,
                        self.arg(args, 'station_id'), session, self.arg(args, 'lap_id'))})
                if path == '/api/admin/v2/field-laps':
                    return self.send(200, {'laps': field_v2.list_laps(store, session)})
                if path == '/api/admin/v2/field-export.json':
                    return self.send(200, {'protocol': 'rice-lap-or-v2', 'provisional': True,
                                          'official': False, 'session_filter': session,
                                          'laps': field_v2.list_laps(store, session)}, filename='rice-field-v2.json')
                if path == '/api/admin/v2/field-export.csv':
                    return self.send(200, csv_text(field_v2.list_laps(store, session), field_v2.EXPORT_COLUMNS),
                                     'text/csv; charset=utf-8', 'rice-field-v2.csv')
                if path == '/api/admin/reviews':
                    return self.send(200, store.reviews(identifier(args.get('session_id', [''])[0])))
                if path == '/api/admin/v2/replay':
                    station = identifier(args.get('station_id', [''])[0])
                    lap_id = identifier(args.get('lap_id', [''])[0])
                    return self.send(200, store.replay_penalties_v2(station, lap_id))
                if path == '/api/admin/v2/reviews':
                    station = identifier(args.get('station_id', [''])[0])
                    lap_id = identifier(args.get('lap_id', [''])[0])
                    return self.send(200, store.reviews_v2(station, lap_id))
                if path == '/api/admin/reviews.csv':
                    rows = store.reviews(identifier(args.get('session_id', [''])[0]))['reviews']
                    return self.send(200, csv_text(rows, REVIEW_COLUMNS), 'text/csv; charset=utf-8', 'rice-reviews.csv')
                if path == '/api/admin/export.csv':
                    table = args.get('table', ['laps'])[0]
                    if table not in EXPORT_TABLES:
                        raise Problem(400, 'unknown table')
                    data = store.export(session)
                    return self.send(200, csv_text(data[table], EXPORT_TABLES[table]), 'text/csv; charset=utf-8', f'rice-{table}.csv')
                if path == '/api/admin/export.json':
                    return self.send(200, store.export(session), filename='rice-export.json')
                if path == '/api/admin/laps':
                    return self.send(200, {'laps': store.laps_detail(session)})
                if path == '/api/admin/stations':
                    return self.send(200, {'stations': store.stations(station_tokens), 'heartbeat_ttl_s': int(HEARTBEAT_TTL.total_seconds())})
                if path == '/api/admin/sync-errors':
                    return self.send(200, {'sync_errors': store.sync_errors(self.arg(args, 'station_id'))})
                if path == '/api/admin/assignments':
                    return self.send(200, {'current': store.current_assignments(), 'history': store.assignment_history(self.arg(args, 'station_id'))})
                if path == '/api/admin/calibrations':
                    return self.send(200, {'calibrations': store.calibrations()})
                if path == '/api/admin/pilots':
                    return self.send(200, {'pilots': store.pilots()})
                if path == '/api/admin/sessions':
                    return self.send(200, {'sessions': store.sessions(), 'active_session': store.active_session()})
                if path == '/api/admin/diagnostics':
                    return self.send(200, store.diagnostics(station_tokens))
                raise Problem(404, 'not found')
            if self.command != 'POST':
                raise Problem(404, 'not found')
            try:
                length = int(self.headers.get('Content-Length', '0'))
            except ValueError:
                raise Problem(400, 'invalid body length') from None
            if not 0 < length <= (8_000_000 if path in ('/api/v2/laps', '/api/admin/v2/config') else 65536):
                raise Problem(400, 'invalid body length')
            try:
                data = json.loads(self.rfile.read(length))
            except (ValueError, UnicodeDecodeError):
                raise Problem(400, 'invalid JSON') from None
            if not isinstance(data, dict):
                raise Problem(400, 'JSON object required')
            if path in ('/api/laps', '/api/v2/laps', '/api/station/heartbeat'):
                claimed, lap = data.get('station_id'), data.get('lap_id')
                self.claimed = (claimed if isinstance(claimed, str) and claimed in station_tokens else None,
                                lap if isinstance(lap, str) and ID.fullmatch(lap) else None)
            if path in ('/api/v2/native-laps', '/api/v2/penalty-laps'):
                station = identifier(data.get('station_id'))
                self.authenticated(station_tokens.get(station))
                try:
                    result = field_v2.receive(store, data, station, 'native' if path == '/api/v2/native-laps' else 'penalty')
                except field_v2.FieldError as error:
                    raise Problem(error.code, error.message) from None
                return self.send(200, result)
            if path == '/api/v2/laps':
                station = identifier(data.get('station_id'))
                self.authenticated(station_tokens.get(station))
                return self.send(200, store.submit_v2(data, station))
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
                if path == '/api/admin/v2/field-review':
                    try:
                        return self.send(200, field_v2.review(store, data))
                    except field_v2.FieldError as error:
                        raise Problem(error.code, error.message) from None
                if path == '/api/admin/v2/config':
                    if data.get('station_id') not in station_tokens:
                        raise Problem(400, 'unknown station')
                    return self.send(200, store.configure_penalties_v2(data))
                if path == '/api/admin/v2/penalties':
                    return self.send(200, store.review_penalties_v2(data))
                if path == '/api/admin/sessions':
                    return self.send(200, store.create_session(data))
                if path == '/api/admin/sessions/active':
                    return self.send(200, store.set_active(data))
                if path == '/api/admin/pilots':
                    return self.send(200, store.register('pilots', 'pilot_id', data))
                if path == '/api/admin/assignments':
                    return self.send(200, store.assign(data, station_tokens))
                if path == '/api/admin/calibrations':
                    return self.send(200, store.calibrate(data, station_tokens))
                if path == '/api/admin/reviews':
                    return self.send(200, store.review(data))
                if path == '/api/admin/penalties':
                    return self.send(200, store.review_penalties(data))
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
            self.claimed = (None, None)
            try:
                self.dispatch()
            except Problem as error:
                path = urlsplit(self.path).path
                if 400 <= error.code < 500 and path in ('/api/laps', '/api/v2/laps', '/api/station/heartbeat', '/api/station/assignment'):
                    try:
                        store.record_sync_error(self.claimed[0] or 'unknown', path, error.code, error.message, self.claimed[1])
                    except sqlite3.Error:
                        pass
                self.send(error.code, {'error': error.message})
            except (OSError, sqlite3.Error, ValueError):
                self.send(500, {'error': 'server error'})
    return Handler


def main(argv=None):
    parser = argparse.ArgumentParser(description='Rice Racing local trials server')
    parser.add_argument('--db', default='trials.sqlite')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8080)
    parser.add_argument('--enable-penalties-v2', action='store_true',
                        help='opt in to authenticated V2 ingestion; V1 remains unchanged')
    parser.add_argument('--field-v2-only', action='store_true',
                        help='disable synchronous trajectory ingestion/replay for responsive field OR channels (requires --enable-penalties-v2)')
    parser.add_argument('--restore', metavar='BACKUP', help='offline: validate BACKUP, copy current DB aside, restore into --db and exit')
    parser.add_argument('--check', action='store_true', help='offline: run integrity_check on --db and exit')
    args = parser.parse_args(argv)
    if args.restore or args.check:
        try:
            result = restore(args.db, args.restore) if args.restore else check_integrity(args.db)
        except RuntimeError as error:
            print(f'ERROR: {error}', file=sys.stderr)
            return 2
        print(json.dumps(result, indent=2))
        return 0
    admin = os.environ.get('RICE_ADMIN_TOKEN', '')
    stations = json.loads(os.environ.get('RICE_STATION_TOKENS', '{}'))
    if not admin or not isinstance(stations, dict) or not stations or not all(isinstance(k, str) and isinstance(v, str) and v and v != admin for k, v in stations.items()):
        parser.error('set RICE_ADMIN_TOKEN and nonempty RICE_STATION_TOKENS JSON with distinct station secrets')
    with ThreadingHTTPServer((args.host, args.port), make_handler(
            Store(args.db, penalties_v2_enabled=args.enable_penalties_v2,
                  field_v2_only=args.field_v2_only), admin, stations)) as server:
        print(f'Listening on {args.host}:{args.port}', flush=True)
        server.serve_forever()
    return 0


if __name__ == '__main__':
    sys.exit(main())
