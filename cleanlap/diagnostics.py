"""Read-only diagnostic evidence; missing is never interpreted as zero."""
import hashlib
import json
import math
from pathlib import Path
from urllib.request import Request, urlopen
from cleanlap.planning import Vehicle, footprint, obstacles
from cleanlap.validation import slalom_evidence
from cleanlap.control import wrap
from shapely.ops import unary_union
from track.geometry import _segment_hit

IDENTITY = ('station_id','session_id','lap_id','pilot_id','track_config')


def receipt(row, expected):
    # Read the real schema/validator, not a parallel unofficial V2 protocol.
    from server import field_v2, scoring
    reasons=[]
    if not isinstance(expected,dict) or any(type(expected.get(k)) is not str or not expected[k] for k in IDENTITY):
        reasons.append('invalid_expected_identity')
        if not isinstance(expected,dict): expected={}
    if type(row.get('raw_ms')) is not int or row['raw_ms'] <= 0:
        reasons.append('native_time_missing_or_invalid')
    native,penalty = row.get('native_payload'),row.get('penalty_payload')
    for key in IDENTITY:
        if not expected.get(key) or row.get(key) != expected[key]: reasons.append('identity.'+key)
    if row.get('conflicts'): reasons.append('server_conflicts')
    for channel,payload in (('native',native),('penalty',penalty)):
        if row.get(channel+'_received') is not True or not isinstance(payload,dict):
            reasons.append(channel+'_absent'); continue
        try:
            field_v2.validate(payload,expected['station_id'],channel)
        except (field_v2.FieldError,KeyError,TypeError,ValueError):
            reasons.append(channel+'_schema'); continue
        for key in IDENTITY:
            if payload.get(key) != expected.get(key): reasons.append(channel+'.'+key)
        if type(payload.get('raw_ms')) is not int or payload.get('raw_ms') != row.get('raw_ms'): reasons.append(channel+'.raw_ms')
    if isinstance(native,dict) and native.get('native_valid') is not True:
        reasons.append('native_validity_unknown')
    if isinstance(native,dict) and isinstance(penalty,dict):
        if (type(native.get('assignment_revision')) is not int or type(penalty.get('assignment_revision')) is not int
            or native.get('assignment_revision') != penalty.get('assignment_revision')):
            reasons.append('assignment_revision')
        if type(native.get('rules_version')) is not int or native.get('rules_version') != penalty.get('rules_version'):
            reasons.append('rules_version')
        if not any(r == 'penalty_schema' for r in reasons):
            try: reasons.extend(field_v2.evidence_reasons(penalty))
            except (KeyError,TypeError): reasons.append('penalty_evidence_missing')
        settings=penalty.get('settings') or {}
        if any(settings.get(key) is not True for key,_,_ in field_v2.DISABLE_FLAGS):
            reasons.append('disabled_or_unknown_detectors')
    verification=row.get('verification') or {}
    if row.get('evidence_verified') is not True or verification.get('approved') is not True:
        reasons.append('server_evidence_unverified')
    if isinstance(penalty,dict):
        try:
            if verification.get('penalty_sha256') != hashlib.sha256(field_v2.compact(penalty).encode()).hexdigest():
                reasons.append('verified_penalty_hash_mismatch')
        except (ValueError,TypeError): reasons.append('invalid_penalty_json')
    complete=not reasons
    events=penalty.get('events') if complete else None
    counts=None
    if complete:
        counts={kind:len({e.get('cone_id') if kind=='cone_contact' else e.get('incident_id')
                         for e in events if e['type']==kind and e.get('penalizable') is True})
                for kind in ('cone_contact','off_course','slalom_violation')}
    return {'complete':complete,'identity':dict(expected),'reasons':sorted(set(reasons)),
            'native_ms':native.get('raw_ms') if isinstance(native,dict) else None,
            'counts':counts,'official':False,
            'schema_sha256':hashlib.sha256(Path(field_v2.__file__).read_bytes()).hexdigest()}


def fetch_export(base_url, token):
    from urllib.parse import urlsplit
    parts=urlsplit(base_url)
    if parts.scheme != 'http' or parts.hostname not in ('127.0.0.1','localhost') or parts.path not in ('','/'):
        raise ValueError('only local read-only server origin is allowed')
    request=Request(base_url.rstrip('/')+'/api/admin/v2/field-export.json',
                    headers={'Authorization':'Bearer '+token},method='GET')
    # Do not follow redirects with credentials.
    from urllib.request import HTTPRedirectHandler, build_opener
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs): return None
    with build_opener(NoRedirect).open(request,timeout=5) as response:
        raw=response.read(16*1024*1024+1)
    if len(raw)>16*1024*1024: raise ValueError('export too large')
    result=json.loads(raw)
    if result.get('protocol') != 'rice-lap-or-v2' or not isinstance(result.get('laps'),list):
        raise ValueError('unrecognized server export')
    return result


def diagnose(world, rows, vehicle=None):
    from dataclasses import asdict
    provisional=vehicle is None
    vehicle=vehicle or Vehicle()
    region=world.road.buffer(-vehicle.margin); obs=obstacles(world)
    contacts=set(); margin=set(); outside=0; crossings=[]; minimum=math.inf
    previous=None; previous_body=None; quality=[]
    for index,row in enumerate(rows):
        faults=[]
        if row.get('protocol') != 'cleanlap-1': faults.append('protocol')
        if row.get('live') is not True or row.get('replay',False) is not False or row.get('paused',False) is not False:
            faults.append('not_live')
        for key in ('car','epoch','track'):
            if not isinstance(row.get(key),str) or not row[key]: faults.append('invalid_'+key)
        if row.get('track') != world.document['track']: faults.append('track_identity')
        if type(row.get('seq')) is not int or row['seq'] < 0: faults.append('invalid_sequence')
        if type(row.get('source_t')) not in (int,float) or not math.isfinite(row['source_t']): faults.append('invalid_time')
        if previous is not None:
            if any(row.get(k) != previous.get(k) for k in ('car','epoch','track')): faults.append('identity_discontinuity')
            if type(row.get('seq')) is int and row['seq'] <= previous['seq']: faults.append('sequence_regression')
            if not faults and not 0 < row['source_t']-previous['source_t'] <= .15: faults.append('time_gap')
        if faults:
            quality.extend({'index':index,'reason':reason} for reason in faults)
            previous=None; previous_body=None
        x,z,h = (float(row[k]) for k in ('x','z','heading'))
        if not all(math.isfinite(v) for v in (x,z,h)): raise ValueError('nonfinite trace')
        body=footprint(x,z,h,vehicle)
        if previous is not None:
            turn=abs(wrap(h-float(previous['heading'])))
            body=unary_union([body,previous_body]).convex_hull.buffer(math.hypot(vehicle.half_width,vehicle.half_length)*(1-math.cos(turn/2)))
            t=float(row.get('source_t',row.get('time',0)))
            old_t=float(previous.get('source_t',previous.get('time',0)))
            if not 0 < t-old_t <= .15: quality.append({'index':index,'reason':'time_gap'})
            for i,gate in enumerate(world.gates):
                a,b=([gate[k]['x'],gate[k]['z']] for k in ('a','b'))
                hit=_segment_hit([float(previous['x']),float(previous['z'])],[x,z],a,b)
                if hit is not None: crossings.append({'gate':i,'direction':hit,'index':index,
                                                       'forward':hit==gate['direction']})
        for i,o in enumerate(obs):
            minimum=min(minimum,body.distance(o))
            if body.intersects(o): contacts.add(i)
            if body.intersects(o.buffer(vehicle.margin)): margin.add(i)
        outside+=not region.covers(body)
        previous=None if faults else row
        previous_body=None if faults else footprint(x,z,h,vehicle)
    # A full ordered start/checkpoint/finish sequence, not mere proximity.
    required=list(range(len(world.gates)))+[0]
    actual=[x['gate'] for x in crossings]
    complete=any(actual[i:i+len(required)]==required and all(e['forward'] for e in crossings[i:i+len(required)])
                 for i in range(max(0,len(actual)-len(required)+1))) and not quality and not provisional
    return {'evidence':'GEOMETRIC_DIAGNOSTIC_NOT_OFFICIAL',
            'configuration_status':'PROVISIONAL_DEFAULT_VEHICLE' if provisional else 'EXPLICIT_VEHICLE',
            'vehicle':asdict(vehicle),'provisional_vehicle':provisional,'candidate_contacts':sorted(contacts),
            'margin_conflicts':sorted(margin),'offtrack_samples':outside,'samples':len(rows),
            'minimum_cone_clearance_m':None if not math.isfinite(minimum) else minimum,
            'slalom':slalom_evidence([[float(r['x']),float(r['z'])] for r in rows],world.slaloms),
            'checkpoint_crossings':crossings,'checkpoints_complete':complete,'quality_gaps':quality,
            'native_lap_ms':None,'official_penalty_counts':None,'geometry_sha256':world.digest}
