"""Deterministic scoring, rules v1. Mirrors client/scoring.lua; parity tested against rules/scoring-v1.json.

All times are integer milliseconds. Any malformed input returns None (never a guessed time).
"""
import re

RULES_VERSION = 1
PENALTIES = {
    'endurance': {'cone': 2000, 'off_course': 20000, 'slalom': 20000},
    'autocross': {'doo': 2000, 'oc': 20000},
    'skidpad': {'doo': 125},
}
STATUSES = ('VALID', 'INVALID', 'OUTLAP', 'PENDING_REVIEW', 'DETECTOR_ERROR')
_GATE = re.compile(r'[A-Za-z0-9_.:-]{1,64}')


def _count(value):
    return type(value) is int and 0 <= value <= 100


def _ms(value):
    return type(value) is int and 0 < value <= 3600000


def corrected_ms(event, raw_ms, events):
    if event not in PENALTIES or not _ms(raw_ms) or not isinstance(events, dict):
        return None
    p = PENALTIES[event]
    if event == 'endurance':
        slalom = events.get('slalom', [])
        cone, off = events.get('cone', 0), events.get('off_course', 0)
        if set(events) - {'cone', 'off_course', 'slalom'} or not _count(cone) or not _count(off):
            return None
        if not isinstance(slalom, list) or len(slalom) > 100 or len(set(slalom)) != len(slalom) \
                or any(not isinstance(g, str) or not _GATE.fullmatch(g) for g in slalom):
            return None
        return raw_ms + cone * p['cone'] + off * p['off_course'] + len(slalom) * p['slalom']
    if event == 'autocross':
        doo, oc = events.get('doo', 0), events.get('oc', 0)
        if set(events) - {'doo', 'oc'} or not _count(doo) or not _count(oc):
            return None
        return raw_ms + doo * p['doo'] + oc * p['oc']
    # skidpad: mean of right/left timed laps (floored to integer ms) + 0.125 s per cone.
    right, left, doo = events.get('right_ms'), events.get('left_ms'), events.get('doo', 0)
    if set(events) - {'right_ms', 'left_ms', 'doo', 'oc'} or events.get('oc', 0) != 0:
        return None
    if not _ms(right) or not _ms(left) or not _count(doo):
        return None
    return (right + left) // 2 + doo * p['doo']


def corrected_ms_v2(raw_ms, events, settings):
    """Score V2 evidence deterministically, deduplicating by cone and incident identity."""
    if type(raw_ms) is not int or not 0 < raw_ms <= 3600000:
        return None
    required = ('enable_cones', 'enable_off_track', 'enable_slalom', 'cone_penalty_ms',
                'off_course_penalty_ms', 'slalom_penalty_ms')
    if not isinstance(settings, dict) or any(key not in settings for key in required):
        return None
    if any(type(settings[key]) is not bool for key in required[:3]):
        return None
    rates = [settings[key] for key in required[3:]]
    if any(type(rate) is not int or not 0 <= rate <= 3600000 for rate in rates):
        return None
    if not isinstance(events, list) or len(events) > 10000:
        return None
    cones, incidents = set(), {}
    for event in events:
        if not isinstance(event, dict):
            return None
        kind = event.get('type')
        if kind not in ('cone_contact', 'off_course', 'slalom_violation'):
            continue
        if type(event.get('penalizable')) is not bool:
            return None
        if not event['penalizable']:
            continue
        if kind == 'cone_contact':
            identifier = event.get('cone_id')
            if not isinstance(identifier, str) or not identifier:
                return None
            cones.add(identifier)
        else:
            identifier = event.get('incident_id')
            if not isinstance(identifier, str) or not identifier:
                return None
            # A true off-course event represents the incident if both detectors report it.
            previous = incidents.get(identifier)
            if kind == 'off_course' or previous is None:
                incidents[identifier] = kind
    total = raw_ms
    if settings['enable_cones']:
        total += len(cones) * settings['cone_penalty_ms']
    for kind in incidents.values():
        if kind == 'off_course' and settings['enable_off_track']:
            total += settings['off_course_penalty_ms']
        elif kind == 'slalom_violation' and settings['enable_slalom']:
            total += settings['slalom_penalty_ms']
    return total if total <= 7200000 else None


def resolve_status(proposed, detector_ok):
    """Server-side status policy: a VALID claim without a healthy mandatory detector is DETECTOR_ERROR."""
    if proposed not in STATUSES:
        return 'DETECTOR_ERROR'
    if proposed == 'VALID' and detector_ok is not True:
        return 'DETECTOR_ERROR'
    return proposed
