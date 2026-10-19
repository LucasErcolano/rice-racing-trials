"""Telemetry import (CSV / JSONL) and gate crossing analysis (stdlib only).

Accepted inputs (one sample per row/line, extra fields ignored):
- CSV with a header containing ``t``, ``x``, ``z`` (``y`` optional).
- JSONL with ``{"t": .., "x": .., "z": ..}``.
- JSONL from the CSP diagnostic client: ``{"t": .., "pos": {"x": .., "y": .., "z": ..}, ...}``.
Lines without a position (events, headers, comments) are counted and skipped.
"""
import csv
import io
import json
import math
from pathlib import Path

MAX_BYTES = 200_000_000
MAX_SAMPLES = 2_000_000


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _sample(record):
    if not isinstance(record, dict):
        return None
    t = _number(record.get('t'))
    pos = record.get('pos') if isinstance(record.get('pos'), dict) else record
    x, z = _number(pos.get('x')), _number(pos.get('z'))
    if t is None or x is None or z is None:
        return None
    return {'t': t, 'x': x, 'z': z, 'y': _number(pos.get('y'))}


def load(path):
    """Return (samples sorted by t, stats). Raises ValueError on unusable input."""
    path = Path(path)
    if path.stat().st_size > MAX_BYTES:
        raise ValueError('telemetry file too large')
    text = path.read_text(encoding='utf-8-sig', errors='replace')
    samples, skipped, kind = [], 0, None
    stripped = text.lstrip()
    if path.suffix.lower() in ('.jsonl', '.ndjson', '.json') or stripped.startswith('{'):
        kind = 'jsonl'
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            try:
                sample = _sample(json.loads(line))
            except ValueError:
                sample = None
            if sample is None:
                skipped += 1
            else:
                samples.append(sample)
            if len(samples) > MAX_SAMPLES:
                raise ValueError('too many telemetry samples')
    else:
        kind = 'csv'
        reader = csv.DictReader(io.StringIO(text))
        fields = {f.strip().lower(): f for f in (reader.fieldnames or [])}
        if not {'t', 'x', 'z'} <= set(fields):
            raise ValueError('CSV needs t, x and z columns')
        for row in reader:
            sample = _sample({k: row.get(v) for k, v in fields.items()})
            if sample is None:
                skipped += 1
            else:
                samples.append(sample)
            if len(samples) > MAX_SAMPLES:
                raise ValueError('too many telemetry samples')
    if len(samples) < 2:
        raise ValueError('telemetry needs at least two positioned samples')
    samples.sort(key=lambda s: s['t'])
    return samples, {'format': kind, 'samples': len(samples), 'skipped_lines': skipped,
                     't_start': samples[0]['t'], 't_end': samples[-1]['t']}


def _side(p, a, b):
    return (b['x'] - a['x']) * (p['z'] - a['z']) - (b['z'] - a['z']) * (p['x'] - a['x'])


def crossing(previous, current, gate):
    """Port of client/core.lua ``core.crossed``; returns interpolated t or None."""
    a, b, direction = gate['a'], gate['b'], gate.get('direction')
    if direction not in (1, -1):
        return None
    before, after = _side(previous, a, b), _side(current, a, b)
    if direction * before >= 0 or direction * after < 0:
        return None
    ratio = before / (before - after)
    x = previous['x'] + ratio * (current['x'] - previous['x'])
    z = previous['z'] + ratio * (current['z'] - previous['z'])
    dx, dz = b['x'] - a['x'], b['z'] - a['z']
    projected = ((x - a['x']) * dx + (z - a['z']) * dz) / (dx * dx + dz * dz)
    if not 0 <= projected <= 1:
        return None
    return previous['t'] + ratio * (current['t'] - previous['t'])


def crossings(samples, gate, max_gap_s=1.0):
    """All directional crossings; segments with time gaps (teleports/pauses) are ignored."""
    result = []
    for previous, current in zip(samples, samples[1:]):
        if current['t'] - previous['t'] > max_gap_s:
            continue
        t = crossing(previous, current, gate)
        if t is not None:
            result.append(round(t, 4))
    return result


def analyse(samples, gate, checkpoints=()):
    """Summarise native-line crossings and checkpoint order for a telemetry trace."""
    report = {'gate_crossings_t': crossings(samples, gate) if gate else [], 'laps': [], 'checkpoints': []}
    times = report['gate_crossings_t']
    if times:
        report['first_crossing_after_start_s'] = round(times[0] - samples[0]['t'], 3)
        report['laps'] = [round(b - a, 3) for a, b in zip(times, times[1:])]
    for index, gate_item in enumerate(checkpoints):
        report['checkpoints'].append({'index': index + 1, 'label': gate_item.get('label'),
                                      'crossings_t': crossings(samples, gate_item)})
    return report


def decimate(samples, limit=5000):
    step = max(1, math.ceil(len(samples) / limit))
    return [[round(s['x'], 2), round(s['z'], 2)] for s in samples[::step]]
