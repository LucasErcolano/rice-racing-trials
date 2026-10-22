"""Independent field OR channels; no geometry analysis or official scoring.

Structurally valid penalty events/settings expose server-recomputed arithmetic
previews even without calibration or native receipt. These are NOT verified
times: review reasons remain visible and official stays false. An explicit
administrator attestation can set evidence_verified for the exact payload,
never from client flags. corrected_ms/final_ms remain null. Identity/message conflicts suppress all
previews; native-only records expose raw_ms with null penalties and previews.
"""
import json
import hashlib
from datetime import datetime, timezone
import re
from typing import Any

try:
    from server import scoring
except ImportError:
    import scoring


class FieldError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


EXPORT_COLUMNS = ['station_id', 'session_id', 'lap_id', 'pilot_id', 'track_config', 'raw_ms',
                  'status', 'native_received', 'penalty_received', 'penalty_ms', 'corrected_preview_ms',
                  'corrected_ms', 'final_ms', 'official', 'evidence_verified', 'review_reasons',
                  'preview_ms', 'penalty_evidence_state', 'disabled_penalties',
                  'evidence', 'native_payload', 'penalty_payload', 'conflicts', 'verification']


def compact(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS field_v2_laps (
        station_id TEXT NOT NULL, session_id TEXT NOT NULL, lap_id TEXT NOT NULL,
        pilot_id TEXT NOT NULL, track_config TEXT NOT NULL, raw_ms INTEGER NOT NULL,
        native_payload TEXT, penalty_payload TEXT, review_json TEXT NOT NULL DEFAULT '[]',
        PRIMARY KEY(station_id,session_id,lap_id))''')
    if 'verification_json' not in {r[1] for r in db.execute('PRAGMA table_info(field_v2_laps)')}:
        db.execute('ALTER TABLE field_v2_laps ADD COLUMN verification_json TEXT')
    db.execute('''CREATE TABLE IF NOT EXISTS field_v2_reviews (
        id INTEGER PRIMARY KEY AUTOINCREMENT, station_id TEXT NOT NULL,
        session_id TEXT NOT NULL, lap_id TEXT NOT NULL, payload TEXT NOT NULL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS field_v2_messages (
        station_id TEXT NOT NULL, message_id TEXT NOT NULL, payload TEXT NOT NULL,
        PRIMARY KEY(station_id,message_id))''')


def validate(data, station, channel):
    if data.get('protocol') != 'rice-lap-or-v2' or data.get('channel') != channel:
        raise FieldError(400, 'unsupported field protocol/channel')
    for name in ('message_id', 'station_id', 'session_id', 'pilot_id', 'track_config', 'lap_id'):
        value = data.get(name)
        if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', value):
            raise FieldError(400, 'invalid ' + name)
    if data['station_id'] != station:
        raise FieldError(403, 'station mismatch')
    if type(data.get('raw_ms')) is not int or not 0 < data['raw_ms'] <= 3600000:
        raise FieldError(400, 'invalid raw_ms')
    if type(data.get('rules_version')) is not int or data['rules_version'] != 1:
        raise FieldError(400, 'unsupported rules_version')
    if 'native_valid' in data and type(data['native_valid']) is not bool:
        raise FieldError(400, 'native_valid must be boolean when supplied')
    if data.get('assignment_state') == 'UNASSIGNED':
        if channel != 'native' or data.get('session_id') != 'UNASSIGNED' or data.get('pilot_id') != 'UNASSIGNED':
            raise FieldError(400, 'invalid unassigned native identity')
        if data.get('review_reason') != 'no_authenticated_assignment':
            raise FieldError(400, 'unassigned review reason required')
    elif data.get('session_id') == 'UNASSIGNED' or data.get('pilot_id') == 'UNASSIGNED':
        raise FieldError(400, 'unassigned identity marker required')
    try:
        compact(data)
    except (TypeError, ValueError):
        raise FieldError(400, 'invalid finite JSON') from None
    if channel == 'penalty':
        events, settings, evidence = data.get('events'), data.get('settings'), data.get('evidence')
        if not isinstance(evidence, dict):
            raise FieldError(400, 'evidence object required')
        if scoring.corrected_ms_v2(data['raw_ms'], events, settings) is None:
            raise FieldError(400, 'invalid events/settings')
        for event in events:
            kind = event.get('type')
            if kind not in ('cone_contact', 'off_course', 'slalom_violation', 'off_course_unresolved', 'slalom_gate_crossing'):
                raise FieldError(400, 'unknown event type')
            if kind in ('cone_contact', 'off_course', 'slalom_violation'):
                name = 'cone_id' if kind == 'cone_contact' else 'incident_id'
                identity = event.get(name)
                if (type(event.get('penalizable')) is not bool or not isinstance(identity, str)
                        or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,120}', identity)):
                    raise FieldError(400, 'invalid event identity/penalizable')


def evidence_reasons(data):
    evidence = data['evidence']
    reasons = []
    for name, expected in (('source', 'rice-client-v2'), ('detector_ok', True),
                           ('calibration_verified', True), ('track_config', data['track_config'])):
        value = evidence.get(name)
        if name == 'track_config' and isinstance(value, str) and isinstance(expected, str):
            # AC reports the content folder name, which Windows matches case-insensitively,
            # while calibration files use the canonical spelling. Only letter case may differ.
            value, expected = value.casefold(), expected.casefold()
        if value != expected or type(value) is not type(expected):
            reasons.append('evidence.' + name)
    if not isinstance(evidence.get('detector'), dict) or not evidence['detector']:
        reasons.append('evidence.detector')
    if evidence.get('review_required') or evidence.get('review_reasons'):
        reasons.append('evidence.review_required')
    if any(event['type'] == 'off_course_unresolved' or event.get('review_required') for event in data['events']):
        reasons.append('event.review_required')
    return reasons


def render(row):
    item: dict[str, Any] = dict(row)
    verified = json.loads(item.pop('verification_json', None) or 'null')
    item['verification'] = verified
    item['native_payload'] = json.loads(item['native_payload']) if item['native_payload'] else None
    item['penalty_payload'] = json.loads(item['penalty_payload']) if item['penalty_payload'] else None
    reviews = json.loads(item.pop('review_json'))
    item['conflicts'] = [entry for entry in reviews if entry.get('kind') != 'review']
    item['review_reasons'] = sorted({entry['reason'] for entry in reviews})
    item.update(native_received=item['native_payload'] is not None,
                penalty_received=item['penalty_payload'] is not None,
                status='PENALTIES_PENDING', penalty_ms=None, final_ms=None,
                corrected_ms=None, corrected_preview_ms=None, official=False,
                evidence_verified=False)
    if item['native_received'] and item['native_payload'].get('native_valid') is not True:
        item['review_reasons'].append('native_validity_unconfirmed')
    if item['penalty_received']:
        item['status'] = 'PENDING_REVIEW' if item['native_received'] else 'NATIVE_PENDING'
        penalty = item['penalty_payload']
        item['review_reasons'] += evidence_reasons(penalty)
        preview = scoring.corrected_ms_v2(item['raw_ms'], penalty.get('events'), penalty.get('settings'))
        if not item['conflicts'] and preview is not None:
            item['corrected_preview_ms'] = preview
            item['penalty_ms'] = preview - item['raw_ms']
    if item['conflicts']:
        item['status'] = 'PENDING_REVIEW'
    if (verified and verified.get('approved') is True and item['penalty_received']
            and not item['conflicts'] and item['corrected_preview_ms'] is not None
            and verified.get('penalty_sha256') == hashlib.sha256(
                compact(item['penalty_payload']).encode('utf-8')).hexdigest()):
        item['evidence_verified'] = True
        item['status'] = 'VERIFIED_PROVISIONAL'
    item['preview_ms'] = item['corrected_preview_ms']
    item['evidence'] = item['penalty_payload']['evidence'] if item['penalty_received'] else None
    item['disabled_penalties'], item['penalty_evidence_state'] = evidence_state(item)
    return item


DISABLE_FLAGS = (('enable_cones', 'cones', 'cone_contact'), ('enable_off_track', 'off_track', 'off_course'),
                 ('enable_slalom', 'slalom', 'slalom_violation'))


def evidence_state(item):
    """Never let absent or incomplete evidence read as zero penalties."""
    if item['conflicts']:
        settings = (item['penalty_payload'] or {}).get('settings') or {}
        return [name for key, name, _ in DISABLE_FLAGS if settings.get(key) is False], 'CONFLICT'
    if not item['penalty_received']:
        return [], 'ABSENT'
    penalty = item['penalty_payload']
    settings = penalty.get('settings') or {}
    disabled = [name for key, name, _ in DISABLE_FLAGS if settings.get(key) is False]
    if item['corrected_preview_ms'] is None or evidence_reasons(penalty):
        return disabled, 'INCOMPLETE'
    if item['penalty_ms']:
        return disabled, 'PENALIZED'
    kinds = {event.get('type') for event in penalty.get('events') or [] if event.get('penalizable') is True}
    if any(kind in kinds for key, _, kind in DISABLE_FLAGS if settings.get(key) is False):
        return disabled, 'PENALTIES_DISABLED'
    return disabled, 'ZERO_INCIDENTS'


def receive(store, data, station, channel):
    if not store.penalties_v2_enabled:
        raise FieldError(404, 'V2 ingestion is disabled')
    # Recognize altered accepted message IDs even when their new body is malformed.
    if not isinstance(data.get('message_id'), str):
        raise FieldError(400, 'invalid message_id')
    try:
        payload = compact(data)
    except (TypeError, ValueError):
        raise FieldError(400, 'invalid finite JSON') from None
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        old = db.execute('SELECT payload FROM field_v2_messages WHERE station_id=? AND message_id=?',
                         (station, data['message_id'])).fetchone()
        conflict = None
        if old and old['payload'] != payload:
            original = json.loads(old['payload'])
            key = (station, original['session_id'], original['lap_id'])
            conflict = 'message_id_payload_conflict'
        else:
            validate(data, station, channel)
            key = (station, data['session_id'], data['lap_id'])
        row = db.execute('SELECT * FROM field_v2_laps WHERE station_id=? AND session_id=? AND lap_id=?', key).fetchone()
        if row and not conflict:
            other_name = 'penalty_payload' if channel == 'native' else 'native_payload'
            if row[other_name]:
                other = json.loads(row[other_name])
                for field_name in ('pilot_id','track_config','assignment_revision'):
                    if other.get(field_name) != data.get(field_name):
                        conflict = 'assignment_revision_conflict' if field_name == 'assignment_revision' else 'lap_identity_conflict'
                        break
        # A penalty-only raw claim cannot replace the independent native clock.
        # Preserve the discrepant original evidence and withhold any preview.
        if (not conflict and row and channel == 'native' and not row['native_payload']
                and row['pilot_id'] == data['pilot_id'] and row['track_config'] == data['track_config']
                and row['raw_ms'] != data['raw_ms']):
            reviews = json.loads(row['review_json'])
            reviews.append({'reason': 'lap_identity_conflict', 'channel': 'penalty',
                            'payload': json.loads(row['penalty_payload'])})
            db.execute('UPDATE field_v2_laps SET raw_ms=?,review_json=? WHERE station_id=? AND session_id=? AND lap_id=?',
                       (data['raw_ms'], compact(reviews)) + key)
            row = db.execute('SELECT * FROM field_v2_laps WHERE station_id=? AND session_id=? AND lap_id=?', key).fetchone()
        if row and any(row[name] != data.get(name) for name in ('pilot_id', 'track_config', 'raw_ms')):
            conflict = conflict or 'lap_identity_conflict'
        if row and row[channel + '_payload'] and not conflict:
            previous = json.loads(row[channel + '_payload'])
            if previous != data:
                conflict = 'channel_payload_conflict'
        if conflict:
            conflicts = json.loads(row['review_json'])
            entry = {'reason': conflict, 'payload': data}
            if entry not in conflicts:
                conflicts.append(entry)
            db.execute('UPDATE field_v2_laps SET review_json=? WHERE station_id=? AND session_id=? AND lap_id=?',
                       (compact(conflicts),) + key)
        elif not row:
            unassigned = data.get('assignment_state') == 'UNASSIGNED'
            assignment = db.execute('SELECT session_id,pilot_id FROM assignments WHERE station_id=?', (station,)).fetchone()
            reviews = [{'reason': 'unassigned', 'kind': 'review'}] if unassigned else []
            if not unassigned:
                revision = data.get('assignment_revision')
                if revision is not None and (type(revision) is not int or revision < 1):
                    raise FieldError(400, 'invalid assignment_revision')
                if revision is not None:
                    historical = db.execute('SELECT session_id,pilot_id FROM assignment_history WHERE station_id=? AND revision=?',
                                            (station, revision)).fetchone()
                    if not historical or (historical['session_id'],historical['pilot_id']) != (data['session_id'],data['pilot_id']):
                        raise FieldError(409, 'assignment revision mismatch')
                    current_revision = assignment['revision'] if assignment and 'revision' in assignment.keys() else None
                    if assignment and current_revision is None:
                        latest = db.execute('SELECT revision,session_id,pilot_id FROM assignment_history WHERE station_id=? ORDER BY revision DESC LIMIT 1', (station,)).fetchone()
                        if latest and (latest['session_id'],latest['pilot_id']) == (assignment['session_id'],assignment['pilot_id']):
                            current_revision = latest['revision']
                    if not assignment or current_revision != revision:
                        reviews.append({'reason':'historical_assignment','kind':'review'})
                elif not assignment or (assignment['session_id'], assignment['pilot_id']) != (data['session_id'], data['pilot_id']):
                    historical = db.execute('SELECT 1 FROM assignment_history WHERE station_id=? AND session_id=? AND pilot_id=?',
                                            (station, data['session_id'], data['pilot_id'])).fetchone()
                    if not historical:
                        raise FieldError(409, 'station assignment mismatch')
                    reviews.append({'reason': 'historical_assignment', 'kind': 'review'})
            db.execute(f'INSERT INTO field_v2_laps (station_id,session_id,lap_id,pilot_id,track_config,raw_ms,{channel}_payload,review_json) VALUES (?,?,?,?,?,?,?,?)',
                       key + (data['pilot_id'], data['track_config'], data['raw_ms'], payload, compact(reviews)))
        elif not old:
            db.execute(f'UPDATE field_v2_laps SET {channel}_payload=? WHERE station_id=? AND session_id=? AND lap_id=?', (payload,) + key)
        if not old and not conflict:
            db.execute('INSERT INTO field_v2_messages VALUES (?,?,?)', (station, data['message_id'], payload))
        row = db.execute('SELECT * FROM field_v2_laps WHERE station_id=? AND session_id=? AND lap_id=?', key).fetchone()
    if conflict:
        # Raise only after commit: the rejected evidence remains available to administrators.
        raise FieldError(409, conflict)
    return dict(render(row), duplicate=old is not None)


def assign_unassigned(store, data):
    """Manual attribution of an unassigned native-only lap; always remains provisional."""
    for name in ('station_id', 'lap_id', 'session_id', 'pilot_id', 'reviewer'):
        if not isinstance(data.get(name), str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', data[name]):
            raise FieldError(400, 'invalid ' + name)
    reason = data.get('reason')
    if not isinstance(reason, str) or not 10 <= len(reason.strip()) <= 1000:
        raise FieldError(400, 'manual attribution reason required')
    old = (data['station_id'], 'UNASSIGNED', data['lap_id'])
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM field_v2_laps WHERE station_id=? AND session_id=? AND lap_id=?', old).fetchone()
        if not row or row['pilot_id'] != 'UNASSIGNED' or not row['native_payload'] or row['penalty_payload']:
            raise FieldError(409, 'unassigned native-only lap not found')
        if not db.execute('SELECT 1 FROM sessions WHERE session_id=?', (data['session_id'],)).fetchone():
            raise FieldError(400, 'unknown session')
        if not db.execute('SELECT 1 FROM pilots WHERE pilot_id=?', (data['pilot_id'],)).fetchone():
            raise FieldError(400, 'unknown pilot')
        target = (data['station_id'], data['session_id'], data['lap_id'])
        if db.execute('SELECT 1 FROM field_v2_laps WHERE station_id=? AND session_id=? AND lap_id=?', target).fetchone():
            raise FieldError(409, 'target lap identity already exists')
        audit = {'kind':'manual_unassigned_attribution','reviewer':data['reviewer'],
                 'reason':reason.strip(),'from_session_id':'UNASSIGNED','from_pilot_id':'UNASSIGNED',
                 'to_session_id':data['session_id'],'to_pilot_id':data['pilot_id'],
                 'created_utc':datetime.now(timezone.utc).isoformat(),'official':False}
        reviews = json.loads(row['review_json'])
        reviews.append({'reason':'manual_attribution_review_required','kind':'review'})
        db.execute('UPDATE field_v2_laps SET session_id=?,pilot_id=?,review_json=? WHERE station_id=? AND session_id=? AND lap_id=?',
                   (data['session_id'],data['pilot_id'],compact(reviews))+old)
        db.execute('INSERT INTO field_v2_reviews(station_id,session_id,lap_id,payload) VALUES(?,?,?,?)',
                   (data['station_id'],data['session_id'],data['lap_id'],compact(audit)))
    with store.connect() as db:
        result = db.execute('SELECT * FROM field_v2_laps WHERE station_id=? AND session_id=? AND lap_id=?', target).fetchone()
    return render(result)


def review(store, data):
    """Explicit administrator attestation of captured evidence, NOT official approval.

    Does not claim a physical survey or auto-trust client flags. The operator must
    inspect the exported evidence; audit history and the exact penalty hash persist.
    """
    if not store.penalties_v2_enabled:
        raise FieldError(404, 'V2 ingestion is disabled')
    for name in ('station_id', 'session_id', 'lap_id', 'reviewer'):
        if not isinstance(data.get(name), str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', data[name]):
            raise FieldError(400, 'invalid ' + name)
    if type(data.get('approved')) is not bool:
        raise FieldError(400, 'approved must be boolean')
    if not isinstance(data.get('reason'), str) or not 10 <= len(data['reason'].strip()) <= 1000:
        raise FieldError(400, 'review reason required (10-1000 characters)')
    key = tuple(data[k] for k in ('station_id', 'session_id', 'lap_id'))
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM field_v2_laps WHERE station_id=? AND session_id=? AND lap_id=?', key).fetchone()
        if not row:
            raise FieldError(404, 'field lap not found')
        current = render(row)
        if data['approved'] and (not current['penalty_received'] or current['conflicts']):
            raise FieldError(409, 'penalty evidence required and conflicts must remain under review')
        entry = dict(approved=data['approved'], reviewer=data['reviewer'], reason=data['reason'].strip(),
                     created_utc=datetime.now(timezone.utc).isoformat(),
                     penalty_sha256=hashlib.sha256((row['penalty_payload'] or '').encode('utf-8')).hexdigest(),
                     kind='manual_field_attestation', official=False)
        payload = compact(entry)
        db.execute('INSERT INTO field_v2_reviews(station_id,session_id,lap_id,payload) VALUES(?,?,?,?)', key + (payload,))
        db.execute('UPDATE field_v2_laps SET verification_json=? WHERE station_id=? AND session_id=? AND lap_id=?', (payload,) + key)
        row = db.execute('SELECT * FROM field_v2_laps WHERE station_id=? AND session_id=? AND lap_id=?', key).fetchone()
    return render(row)


def review_history(store, station, session, lap):
    with store.connect() as db:
        rows = db.execute('SELECT id,payload FROM field_v2_reviews WHERE station_id=? AND session_id=? AND lap_id=? ORDER BY id',
                          (station, session, lap)).fetchall()
    return [dict(json.loads(row['payload']), id=row['id']) for row in rows]


def list_laps(store, session=None):
    with store.connect() as db:
        rows = db.execute('SELECT * FROM field_v2_laps' + (' WHERE session_id=?' if session else '') +
                          ' ORDER BY session_id,station_id,lap_id', (session,) if session else ()).fetchall()
    return [render(row) for row in rows]
