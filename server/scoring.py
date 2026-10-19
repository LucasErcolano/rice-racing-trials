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


def resolve_status(proposed, detector_ok):
    """Server-side status policy: a VALID claim without a healthy mandatory detector is DETECTOR_ERROR."""
    if proposed not in STATUSES:
        return 'DETECTOR_ERROR'
    if proposed == 'VALID' and detector_ok is not True:
        return 'DETECTOR_ERROR'
    return proposed
