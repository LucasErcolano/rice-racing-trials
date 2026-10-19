"""Candidate Dummy.kn5 patches for the native first-lap problem (§8.B).

CANDIDATO NO VERIFICADO EN JUEGO. Linux can only prove file integrity:
- the original parses and re-serialises byte for byte (round trip);
- only the 64-byte matrix spans of the chosen nodes change;
- the candidate re-parses and the new world positions/headings are as requested;
- the new spawn lies on a physics surface flagged IS_VALID_TRACK.
Whether Assetto Corsa then stops producing the ~18 s first lap is only testable in game.
"""
import hashlib
import json
import math
import struct

try:
    from track import geometry, kn5
except ImportError:
    import geometry
    import kn5

LABEL = 'CANDIDATO NO VERIFICADO EN JUEGO'


def spawn_matrix(original, x, y, z, forward):
    """Keep the node's uniform scale, align local +Z with ``forward`` (XZ), local +Y up.

    AC dummies use row vectors: rows 0/1/2 are the local X/Y/Z axes in world space.
    The FSAEJ HOTLAP spawn proves local +Z is the driving direction (it matches fast_lane.ai).
    """
    scale = math.sqrt(sum(v * v for v in original[8:11])) or 1.0
    fx, fz = forward
    norm = math.hypot(fx, fz)
    if not norm:
        raise ValueError('zero forward vector')
    fx, fz = fx / norm, fz / norm
    return [fz * scale, 0.0, -fx * scale, 0.0,
            0.0, scale, 0.0, 0.0,
            fx * scale, 0.0, fz * scale, 0.0,
            x, y, z, 1.0]


def point_along(path, start_index, start_point, distance):
    """Walk ``distance`` metres along the closed AI polyline from ``start_point``.

    Negative distances walk backwards (before the line); the returned direction is
    always the driving direction of the AI line.
    """
    if distance < 0:
        reversed_path = path[::-1]
        point, direction = point_along(reversed_path, len(path) - 1 - (start_index + 1) % len(path),
                                       start_point, -distance)
        return point, (-direction[0], -direction[1])
    remaining = distance
    current = start_point
    i = start_index
    for _ in range(len(path) + 1):
        target = path[(i + 1) % len(path)]
        step = math.hypot(target[0] - current[0], target[1] - current[1])
        if step >= remaining and step > 0:
            ratio = remaining / step
            point = (current[0] + ratio * (target[0] - current[0]), current[1] + ratio * (target[1] - current[1]))
            return point, (target[0] - current[0], target[1] - current[1])
        remaining -= step
        current = target
        i = (i + 1) % len(path)
    raise ValueError('AI path shorter than requested distance')


def line_crossing_on_path(path, gate):
    """First AI segment crossing the native gate in the gate direction."""
    a = (gate['a']['x'], gate['a']['z'])
    b = (gate['b']['x'], gate['b']['z'])
    direction = gate['direction']
    for i in range(len(path)):
        p, q = path[i], path[(i + 1) % len(path)]
        before, after = geometry.side(p, a, b), geometry.side(q, a, b)
        if direction * before < 0 <= direction * after:
            ratio = before / (before - after)
            point = (p[0] + ratio * (q[0] - p[0]), p[1] + ratio * (q[1] - p[1]))
            dx, dz = b[0] - a[0], b[1] - a[1]
            projected = ((point[0] - a[0]) * dx + (point[1] - a[1]) * dz) / (dx * dx + dz * dz)
            if 0 <= projected <= 1:
                return i, point
    raise ValueError('AI path does not cross the native timing line')


def min_distance(path, point):
    return min(math.hypot(p[0] - point[0], p[1] - point[1]) for p in path)


def surface_height(physics, keys, x, z):
    """(surface KEY, valid_track, y) of the highest physics triangle under (x, z), or None."""
    best = None
    for node, world, _ in physics.walk():
        if node.kind != 2:
            continue
        key = geometry.surface_of(node.name, keys)
        if key is None:
            continue
        floats, index = geometry.mesh_arrays(node)
        if not floats:
            continue
        # cheap bounding-box reject
        xs = floats[0::11]
        zs = floats[2::11]
        corners = [geometry.world_xz(world, cx, 0, cz) for cx in (min(xs), max(xs)) for cz in (min(zs), max(zs))]
        if not (min(c[0] for c in corners) - 1 <= x <= max(c[0] for c in corners) + 1 and
                min(c[1] for c in corners) - 1 <= z <= max(c[1] for c in corners) + 1):
            continue
        points = [(floats[i] * world[0] + floats[i + 1] * world[4] + floats[i + 2] * world[8] + world[12],
                   floats[i] * world[1] + floats[i + 1] * world[5] + floats[i + 2] * world[9] + world[13],
                   floats[i] * world[2] + floats[i + 1] * world[6] + floats[i + 2] * world[10] + world[14])
                  for i in range(0, len(floats), 11)]
        for t in range(0, len(index) - len(index) % 3, 3):
            p0, p1, p2 = points[index[t]], points[index[t + 1]], points[index[t + 2]]
            det = (p1[2] - p2[2]) * (p0[0] - p2[0]) + (p2[0] - p1[0]) * (p0[2] - p2[2])
            if abs(det) < 1e-12:
                continue
            l0 = ((p1[2] - p2[2]) * (x - p2[0]) + (p2[0] - p1[0]) * (z - p2[2])) / det
            l1 = ((p2[2] - p0[2]) * (x - p2[0]) + (p0[0] - p2[0]) * (z - p2[2])) / det
            l2 = 1 - l0 - l1
            if min(l0, l1, l2) < -1e-6:
                continue
            y = l0 * p0[1] + l1 * p1[1] + l2 * p2[1]
            if best is None or y > best[2]:
                best = (key, keys[key]['valid_track'], y)
    return best


def changed_spans(original, candidate):
    if len(original) != len(candidate):
        raise ValueError('candidate size differs from original')
    spans, start = [], None
    for i, (a, b) in enumerate(zip(original, candidate)):
        if a != b and start is None:
            start = i
        elif a == b and start is not None:
            spans.append([start, i])
            start = None
    if start is not None:
        spans.append([start, len(original)])
    return spans


def matrix_offsets(data):
    """Byte offset of each transform node's 64-byte matrix, by node name (re-walks the file)."""
    model = kn5.parse(data)
    offsets = {}
    position = len(model.header_raw) + len(model.textures_raw) + len(model.materials_raw)

    def visit(node):
        nonlocal position
        position += 4 + 4 + len(node.name_raw) + 4 + 1
        if node.kind == 1:
            offsets.setdefault(node.name, []).append(position)
            position += 64
        else:
            position += len(node.mesh_raw)
        for child in node.children:
            visit(child)

    visit(model.root)
    return offsets


def build(source, track_id, nodes=('AC_START_0',), after_line_m=8.0, pit_extra_m=8.0, physics=None):
    """Return (candidate bytes, manifest). ``source`` is an extracted track folder or the ZIP."""
    src = geometry.TrackSource(source, track_id)
    original = src.read('Dummy.kn5')
    model = kn5.parse(original)
    roundtrip = kn5.write(model) == original
    if not roundtrip:
        raise ValueError('KN5 writer is not byte exact for this Dummy.kn5: refusing to patch')
    marks = geometry.landmarks(model)
    path = geometry.ai_path(src.read('ai/fast_lane.ai'))
    gate = geometry.timing_gate(marks, path)
    if not gate or gate.get('direction') is None:
        raise ValueError('Dummy.kn5 lacks a usable AC_TIME_0 line and AC_START_0')
    index, crossing = line_crossing_on_path(path, gate)
    keys = geometry.surface_keys(src.read('data/surfaces.ini').decode('utf-8-sig', errors='replace'))
    physics_model = physics or kn5.parse(src.read('Physics.kn5'))
    changes, checks = [], []
    pit_offset = after_line_m + math.copysign(pit_extra_m, after_line_m)
    for offset_m, name in zip([after_line_m, pit_offset], nodes):
        found = model.find(name)
        if len(found) != 1:
            raise ValueError(f'node {name} not found exactly once')
        node = found[0]
        before = list(node.matrix)
        old_surface = surface_height(physics_model, keys, before[12], before[14])
        (x, z), forward = point_along(path, index, crossing, offset_m)
        surface = surface_height(physics_model, keys, x, z)
        if surface is None or not surface[1]:
            raise ValueError(f'{name}: candidate point is not on an IS_VALID_TRACK surface')
        clearance = before[13] - old_surface[2] if old_surface else 1.0
        node.set_matrix(spawn_matrix(before, round(x, 3), round(surface[2] + clearance, 3), round(z, 3), forward))
        changes.append({'node': name, 'before': {'position': [round(v, 4) for v in before[12:15]],
                                                 'forward_xz': marks[name]['forward_xz']},
                        'after_line_along_ai_m': offset_m,
                        'after': {'position': [round(x, 3), round(surface[2] + clearance, 3), round(z, 3)],
                                  'forward_xz': [round(forward[0] / math.hypot(*forward), 4),
                                                 round(forward[1] / math.hypot(*forward), 4)]},
                        'surface_under_new_position': {'key': surface[0], 'valid_track': surface[1],
                                                       'y': round(surface[2], 3)},
                        'clearance_kept_m': round(clearance, 3)})
    candidate = kn5.write(model)
    reparsed = kn5.parse(candidate)
    new_marks = geometry.landmarks(reparsed)
    offsets = matrix_offsets(original)
    allowed = [[o, o + 64] for change in changes for o in offsets[change['node']]]
    spans = changed_spans(original, candidate)
    inside = all(any(a <= s and e <= b for a, b in allowed) for s, e in spans)
    for change in changes:
        got = new_marks[change['node']]
        want = change['after']['position']
        checks.append(abs(got['x'] - want[0]) < 1e-3 and abs(got['z'] - want[2]) < 1e-3)
        new_gate_side = geometry.side((got['x'], got['z']), (gate['a']['x'], gate['a']['z']),
                                      (gate['b']['x'], gate['b']['z']))
        change['new_side_of_line'] = 'después de la línea (ya cruzada)' if gate['direction'] * new_gate_side >= 0 \
            else 'antes de la línea'
    untouched = all(new_marks[n] == marks[n] for n in marks if n not in [c['node'] for c in changes])
    spawn = marks['AC_START_0']
    manifest = {
        'label': LABEL, 'track': track_id, 'file': 'Dummy.kn5',
        'original_sha256': hashlib.sha256(original).hexdigest(), 'original_bytes': len(original),
        'candidate_sha256': hashlib.sha256(candidate).hexdigest(), 'candidate_bytes': len(candidate),
        'changes': changes,
        'verification_linux': {
            'roundtrip_original_byte_exact': roundtrip,
            'candidate_reparses': True,
            'changed_byte_spans': spans,
            'changes_only_inside_target_matrices': inside,
            'new_positions_match': all(checks),
            'other_landmarks_unchanged': untouched,
        },
        'native_line': gate,
        'evidence': {
            'spawn_to_line_midpoint_m': gate.get('spawn_to_line_midpoint_m'),
            'original_spawn_distance_to_ai_loop_m': round(min_distance(path, (spawn['x'], spawn['z'])), 2),
            'ai_crossing_point': [round(crossing[0], 3), round(crossing[1], 3)],
        },
        'not_verified': ['Que AC use la nueva posición en Practice', 'Que desaparezca la vuelta de ~18 s',
                         'Que la primera vuelta completa se registre correctamente',
                         'Checksums de servidor online (no aplica en offline)'],
    }
    if not all(manifest['verification_linux'].values()):
        raise ValueError('candidate failed Linux integrity checks: ' + json.dumps(manifest['verification_linux']))
    return candidate, manifest


def matrix_hex(data, offset):
    return data[offset:offset + 64].hex()


def floats(raw):
    return struct.unpack('<16f', raw)
