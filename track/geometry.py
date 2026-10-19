"""Derived track geometry from KN5 files (Python stdlib only).

Everything here is *derived* from proprietary geometry: outputs belong in
``dist/track-calibration/`` (git-ignored), never in the repository.

Coordinates are Assetto Corsa world metres. The 2D projection is XZ
(x to the right, z downwards, like the in-game map image).
"""
import configparser
import hashlib
import io
import json
import math
import re
import struct
import zipfile
from pathlib import Path

try:  # executed as ``python -m track.tool`` from the repository root
    from track import kn5
except ImportError:  # executed as a plain script from inside track/
    import kn5

SCHEMA = 'track-geometry-v1'
MAX_MEMBER = 400_000_000
CONE_PATTERN = re.compile(r'(?i)pylon|cone')
TIMING_NAMES = ('AC_TIME_0_L', 'AC_TIME_0_R', 'AC_START_0', 'AC_HOTLAP_START_0', 'AC_PIT_0')


# ---------------------------------------------------------------- sources
class TrackSource:
    """Read members of a track from an extracted folder or the original ZIP."""

    def __init__(self, path, track_id):
        self.path = Path(path)
        self.track_id = track_id
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', track_id):
            raise ValueError('invalid track id')
        self.archive = zipfile.ZipFile(self.path) if self.path.is_file() else None
        if self.archive is None and not self.path.is_dir():
            raise FileNotFoundError(self.path)

    def _member(self, name):
        return f'{self.track_id}/{name}'

    def exists(self, name):
        if self.archive:
            return self._member(name) in self.archive.namelist()
        target = self.path / name
        return target.is_file() and not target.is_symlink()

    def read(self, name):
        if self.archive:
            info = self.archive.getinfo(self._member(name))
            if info.file_size > MAX_MEMBER:
                raise ValueError(f'oversized member {name}')
            return self.archive.read(info)
        target = self.path / name
        if target.is_symlink() or not target.is_file():
            raise FileNotFoundError(target)
        if target.stat().st_size > MAX_MEMBER:
            raise ValueError(f'oversized file {name}')
        return target.read_bytes()


def sha256(data):
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------- helpers
def world_xz(matrix, x, y, z):
    return (x * matrix[0] + y * matrix[4] + z * matrix[8] + matrix[12],
            x * matrix[2] + y * matrix[6] + z * matrix[10] + matrix[14])


def mesh_arrays(node):
    """Return (flat float tuple of vertices, index tuple) for a static mesh."""
    count = node.vertex_count()
    raw = node.mesh_raw
    floats = struct.unpack_from('<%df' % (count * 11), raw, 7)
    offset = 7 + count * kn5.VERTEX_SIZE
    indices = struct.unpack_from('<i', raw, offset)[0]
    index = struct.unpack_from('<%dH' % indices, raw, offset + 4)
    if index and max(index) >= count:
        raise kn5.KN5Error(f'mesh {node.name!r} index out of range')
    return floats, index


def mesh_xz(node, world):
    floats, _ = mesh_arrays(node)
    return [world_xz(world, floats[i], floats[i + 1], floats[i + 2]) for i in range(0, len(floats), 11)]


def nodes_table(model):
    """Node inventory: name, kind, vertices, material and XZ bounding box."""
    rows = []
    for node, world, parent in model.walk():
        row = {'name': node.name, 'kind': 'mesh' if node.kind == 2 else 'transform', 'parent': parent}
        if node.kind == 2:
            points = mesh_xz(node, world)
            row['vertices'] = len(points)
            mid = node.material_id()
            row['material'] = model.material_names[mid] if 0 <= mid < len(model.material_names) else None
            if points:
                xs, zs = [p[0] for p in points], [p[1] for p in points]
                row['bbox_xz'] = [round(min(xs), 3), round(min(zs), 3), round(max(xs), 3), round(max(zs), 3)]
        else:
            row['position'] = [round(v, 4) for v in world[12:15]]
        rows.append(row)
    return rows


def landmarks(model):
    """AC_* dummies with world position and forward heading (local +Z row)."""
    result = {}
    for node, world, _ in model.walk():
        if node.kind != 1 or not node.name.startswith('AC_'):
            continue
        fx, fz = world[8], world[10]
        norm = math.hypot(fx, fz) or 1.0
        result[node.name] = {'x': round(world[12], 4), 'y': round(world[13], 4), 'z': round(world[14], 4),
                             'forward_xz': [round(fx / norm, 4), round(fz / norm, 4)]}
    return result


def side(p, a, b):
    """Same orientation test as client/core.lua (sign decides gate direction)."""
    return (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])


def _segment_hit(p, q, a, b):
    before, after = side(p, a, b), side(q, a, b)
    # Half-open convention of client/core.lua: a side value of 0 counts as the positive side,
    # so a path vertex lying exactly on the line is counted once (not zero or two times).
    if (before >= 0) == (after >= 0):
        return None
    ratio = before / (before - after)
    x, z = p[0] + ratio * (q[0] - p[0]), p[1] + ratio * (q[1] - p[1])
    dx, dz = b[0] - a[0], b[1] - a[1]
    projected = ((x - a[0]) * dx + (z - a[1]) * dz) / (dx * dx + dz * dz)
    return (1 if after >= 0 else -1) if 0 <= projected <= 1 else None


def timing_gate(marks, path=None):
    """Native timing line from AC_TIME_0_L/R.

    The client (client/core.lua) counts a crossing when direction*side goes from <0 to >=0.
    Direction comes from the AI driving line when it crosses the segment; otherwise from the
    AC_START_0 side (assumed to be before the line). Both are unverified until tested in game.
    """
    if 'AC_TIME_0_L' not in marks or 'AC_TIME_0_R' not in marks:
        return None
    a = (marks['AC_TIME_0_L']['x'], marks['AC_TIME_0_L']['z'])
    b = (marks['AC_TIME_0_R']['x'], marks['AC_TIME_0_R']['z'])
    gate = {'a': {'x': a[0], 'z': a[1]}, 'b': {'x': b[0], 'z': b[1]}, 'source': 'AC_TIME_0_L/R (Dummy.kn5)',
            'direction': None, 'direction_source': None}
    spawn = marks.get('AC_START_0')
    spawn_direction = None
    if spawn:
        value = side((spawn['x'], spawn['z']), a, b)
        spawn_direction = (-1 if value > 0 else 1) if value else None
        mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        gate['spawn_to_line_midpoint_m'] = round(math.hypot(spawn['x'] - mid[0], spawn['z'] - mid[1]), 3)
    hits = []
    if path and len(path) > 2:
        for i in range(len(path)):
            hit = _segment_hit(path[i], path[(i + 1) % len(path)], a, b)
            if hit is not None:
                hits.append(hit)
    if hits and all(h == hits[0] for h in hits):
        gate['direction'] = hits[0]
        gate['direction_source'] = 'sentido de fast_lane.ai (no verificado en juego)'
        if spawn_direction is not None:
            gate['spawn_before_line'] = spawn_direction == hits[0]
    elif spawn_direction is not None:
        gate['direction'] = spawn_direction
        gate['direction_source'] = 'lado de AC_START_0, supuesto antes de la línea (no verificado)'
    return gate


def surface_keys(text):
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    parser.read_string(text)
    result = {}
    for section in parser.sections():
        if section.upper().startswith('SURFACE_') and 'KEY' in parser[section]:
            key = parser[section]['KEY'].strip()
            valid = parser[section].get('IS_VALID_TRACK', '0').strip() == '1'
            pit = parser[section].get('IS_PITLANE', '0').strip() == '1'
            result[key] = {'valid_track': valid, 'pitlane': pit}
    return result


def surface_of(name, keys):
    """AC physics mesh naming: leading digits + surface KEY (e.g. ``1ASPHALT_000``)."""
    stripped = name.lstrip('0123456789')
    if stripped == name:
        return None
    best = None
    for key in keys:
        if stripped.upper().startswith(key.upper()) and (best is None or len(key) > len(best)):
            best = key
    return best


# ---------------------------------------------------------------- limits
def _simplify(points, tolerance):
    """Iterative Douglas-Peucker."""
    if len(points) < 3:
        return points
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        first, last = stack.pop()
        ax, az = points[first]
        bx, bz = points[last]
        dx, dz = bx - ax, bz - az
        length = math.hypot(dx, dz)
        worst, index = -1.0, None
        for i in range(first + 1, last):
            px, pz = points[i]
            if length == 0:
                distance = math.hypot(px - ax, pz - az)
            else:
                distance = abs(dx * (az - pz) - dz * (ax - px)) / length
            if distance > worst:
                worst, index = distance, i
        if index is not None and worst > tolerance:
            keep[index] = True
            stack.append((first, index))
            stack.append((index, last))
    return [p for p, k in zip(points, keep) if k]


def boundary_polylines(meshes, quantum=0.01, tolerance=0.05, min_length=1.0):
    """Edges used by exactly one triangle of the union of the given meshes.

    ``meshes`` is a list of (node, world). Vertices are welded at ``quantum`` metres.
    Returns simplified polylines (lists of [x, z]).
    """
    scale = 1.0 / quantum
    counts = {}
    coords = {}
    for node, world in meshes:
        floats, index = mesh_arrays(node)
        keys = []
        for i in range(0, len(floats), 11):
            x, z = world_xz(world, floats[i], floats[i + 1], floats[i + 2])
            key = (round(x * scale), round(z * scale))
            coords[key] = (x, z)
            keys.append(key)
        for t in range(0, len(index) - len(index) % 3, 3):
            a, b, c = keys[index[t]], keys[index[t + 1]], keys[index[t + 2]]
            for p, q in ((a, b), (b, c), (c, a)):
                if p != q:
                    edge = (p, q) if p < q else (q, p)
                    counts[edge] = counts.get(edge, 0) + 1
    adjacency = {}
    for (p, q), n in counts.items():
        if n == 1:
            adjacency.setdefault(p, []).append(q)
            adjacency.setdefault(q, []).append(p)
    del counts
    used = set()
    polylines = []
    # Start at open ends first, then close remaining loops.
    starts = [k for k, v in adjacency.items() if len(v) != 2] + list(adjacency)
    for start in starts:
        for nxt in adjacency[start]:
            edge = (start, nxt) if start < nxt else (nxt, start)
            if edge in used:
                continue
            line = [start]
            previous, current = start, nxt
            used.add(edge)
            while True:
                line.append(current)
                candidates = [k for k in adjacency[current]
                              if ((current, k) if current < k else (k, current)) not in used]
                if len(adjacency[current]) != 2 or not candidates:
                    break
                following = candidates[0]
                used.add((current, following) if current < following else (following, current))
                previous, current = current, following
            points = [coords[k] for k in line]
            length = sum(math.hypot(points[i + 1][0] - points[i][0], points[i + 1][1] - points[i][1])
                         for i in range(len(points) - 1))
            if length >= min_length:
                polylines.append([[round(x, 3), round(z, 3)] for x, z in _simplify(points, tolerance)])
    polylines.sort(key=len, reverse=True)
    return polylines


# ---------------------------------------------------------------- cones
def cone_clusters(model, cell=0.3, max_extent=1.2, min_vertices=3):
    """Spatial clusters of meshes whose node or material name says pylon/cone.

    Cone meshes in these tracks are merged; clusters are *unconfirmed candidates*,
    never individual surveyed cones.
    """
    clusters = []
    for node, world, _ in model.walk():
        if node.kind != 2:
            continue
        mid = node.material_id()
        material = model.material_names[mid] if 0 <= mid < len(model.material_names) else ''
        if not (CONE_PATTERN.search(node.name) or CONE_PATTERN.search(material)):
            continue
        cells = {}
        for x, z in mesh_xz(node, world):
            cells.setdefault((math.floor(x / cell), math.floor(z / cell)), []).append((x, z))
        seen = set()
        for start in cells:
            if start in seen:
                continue
            seen.add(start)
            stack, members = [start], []
            while stack:
                current = stack.pop()
                members.extend(cells[current])
                for dx in (-1, 0, 1):
                    for dz in (-1, 0, 1):
                        neighbour = (current[0] + dx, current[1] + dz)
                        if neighbour in cells and neighbour not in seen:
                            seen.add(neighbour)
                            stack.append(neighbour)
            xs, zs = [p[0] for p in members], [p[1] for p in members]
            extent = max(max(xs) - min(xs), max(zs) - min(zs))
            if len(members) < min_vertices or extent > max_extent:
                continue
            clusters.append({'x': round(sum(xs) / len(xs), 3), 'z': round(sum(zs) / len(zs), 3),
                             'footprint_m': round(extent, 3), 'vertices': len(members),
                             'mesh': node.name, 'material': material})
    merged = []
    for item in sorted(clusters, key=lambda c: (c['x'], c['z'])):
        for other in merged:
            if math.hypot(other['x'] - item['x'], other['z'] - item['z']) < 0.35:
                other['sources'].append(f"{item['mesh']}:{item['material']}")
                break
        else:
            merged.append({**item, 'sources': [f"{item['mesh']}:{item['material']}"]})
    for item in merged:
        item.pop('mesh')
        item.pop('material')
        item['status'] = 'candidato no confirmado'
    return merged


# ---------------------------------------------------------------- AI line
def ai_path(data):
    if len(data) < 16:
        raise ValueError('truncated AI line')
    version, count, _, _ = struct.unpack_from('<4i', data)
    if version != 7 or not 2 <= count <= 100000 or len(data) < 16 + count * 20:
        raise ValueError('unknown AI line layout')
    points = [struct.unpack_from('<4fi', data, 16 + i * 20) for i in range(count)]
    return [[round(p[0], 3), round(p[2], 3)] for p in points if math.isfinite(p[0]) and math.isfinite(p[2])]


# ---------------------------------------------------------------- slalom
def load_slalom(path, track_id):
    """Operator calibration file (``slalom-gates-v1``); absent means empty + warning."""
    if path is None or not Path(path).is_file():
        return {'status': 'sin calibración', 'gates': [],
                'warning': 'No existe archivo de calibración de slalom: no se infieren slaloms de la geometría.'}
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if data.get('version') != 'slalom-gates-v1' or data.get('track') != track_id:
        raise ValueError('slalom file is not slalom-gates-v1 for this track')
    gates = []
    for gate in data.get('gates', []):
        a, b = gate['a'], gate['b']
        values = [float(a['x']), float(a['z']), float(b['x']), float(b['z'])]
        if not all(math.isfinite(v) for v in values):
            raise ValueError('non-finite slalom coordinate')
        gates.append({'a': {'x': values[0], 'z': values[1]}, 'b': {'x': values[2], 'z': values[3]},
                      'label': str(gate.get('label', ''))[:40]})
    return {'status': data.get('status', 'candidate'), 'gates': gates, 'survey_id': data.get('survey_id'),
            'warning': None if data.get('survey_id') else 'Slalom sin survey_id: no verificado por operador.'}


# ---------------------------------------------------------------- top level
def extract(source, track_id, slalom=None, mesh_points=None, max_points=20000,
            limit_tolerance=0.05):
    """Build the derived geometry document for one track."""
    src = TrackSource(source, track_id)
    hashes, warnings = {}, []
    surfaces_text = src.read('data/surfaces.ini')
    hashes['data/surfaces.ini'] = sha256(surfaces_text)
    keys = surface_keys(surfaces_text.decode('utf-8-sig', errors='replace'))

    dummy_raw = src.read('Dummy.kn5') if src.exists('Dummy.kn5') else None
    marks = {}
    if dummy_raw is not None:
        hashes['Dummy.kn5'] = sha256(dummy_raw)
        marks = landmarks(kn5.parse(dummy_raw))
    else:
        warnings.append('Dummy.kn5 ausente: landmarks buscados en el modelo visual.')

    physics_raw = src.read('Physics.kn5')
    hashes['Physics.kn5'] = sha256(physics_raw)
    physics = kn5.parse(physics_raw)
    del physics_raw
    surface_stats, valid_meshes = {}, []
    for node, world, _ in physics.walk():
        if node.kind != 2:
            continue
        key = surface_of(node.name, keys)
        if key is None:
            continue
        stats = surface_stats.setdefault(key, {'valid_track': keys[key]['valid_track'], 'meshes': 0, 'vertices': 0})
        stats['meshes'] += 1
        stats['vertices'] += node.vertex_count()
        if keys[key]['valid_track']:
            valid_meshes.append((node, world))
    if valid_meshes:
        polylines = boundary_polylines(valid_meshes, tolerance=limit_tolerance)
        limits = {'status': 'derivado de superficies IS_VALID_TRACK=1 (surfaces.ini)',
                  'note': 'Borde de la superficie válida, NO el trazado delimitado por conos.',
                  'tolerance_m': limit_tolerance, 'polylines': polylines}
    else:
        limits = {'status': 'no disponible', 'polylines': [],
                  'note': 'Ninguna malla física coincide con una superficie válida de surfaces.ini.'}
    for name in TIMING_NAMES:
        for node, world, _ in physics.walk():
            if node.kind == 1 and node.name == name and name not in marks:
                marks[name] = landmarks_from_node(world)
    cones = cone_clusters(physics)
    del valid_meshes, physics

    visual_name = f'{track_id}.kn5'
    visual_points = {}
    if src.exists(visual_name):
        visual_raw = src.read(visual_name)
        hashes[visual_name] = sha256(visual_raw)
        visual = kn5.parse(visual_raw)
        del visual_raw
        known = {(c['x'], c['z']) for c in cones}
        for item in cone_clusters(visual):
            if all(math.hypot(item['x'] - x, item['z'] - z) >= 0.35 for x, z in known):
                cones.append(item)
        if mesh_points:
            pattern = re.compile(mesh_points)
            selected = [(n, w) for n, w, _ in visual.walk() if n.kind == 2 and pattern.search(n.name)]
            total = sum(n.vertex_count() for n, _ in selected) or 1
            step = max(1, math.ceil(total / max_points))
            for node, world in selected:
                pts = mesh_xz(node, world)[::step]
                visual_points[node.name] = [[round(x, 2), round(z, 2)] for x, z in pts]
        del visual
    else:
        warnings.append(f'{visual_name} ausente: sin clusters visuales.')

    path = []
    if src.exists('ai/fast_lane.ai'):
        ai_raw = src.read('ai/fast_lane.ai')
        hashes['ai/fast_lane.ai'] = sha256(ai_raw)
        path = ai_path(ai_raw)
    else:
        warnings.append('ai/fast_lane.ai ausente.')

    return {
        'schema': SCHEMA, 'track': track_id, 'derived_from_proprietary_assets': True,
        'distribution': 'NO DISTRIBUIR fuera del equipo: derivado de geometría del mod.',
        'coordinates': 'Assetto Corsa world metres; 2D projection XZ (z hacia abajo como map.png)',
        'source_sha256': hashes,
        'landmarks': marks,
        'timing_gate': timing_gate(marks, path),
        'surfaces': {k: {**v, **surface_stats.get(k, {'meshes': 0, 'vertices': 0})} for k, v in keys.items()},
        'limits': limits,
        'cones': {'status': 'candidato no confirmado' if cones else 'no identificados',
                  'note': 'Mallas de conos fusionadas en el KN5: cada punto es un cluster espacial, '
                          'no un cono individual verificado.', 'clusters': cones},
        'slalom': load_slalom(slalom, track_id),
        'ai_path': path,
        'mesh_points': visual_points,
        'warnings': warnings,
    }


def landmarks_from_node(world):
    fx, fz = world[8], world[10]
    norm = math.hypot(fx, fz) or 1.0
    return {'x': round(world[12], 4), 'y': round(world[13], 4), 'z': round(world[14], 4),
            'forward_xz': [round(fx / norm, 4), round(fz / norm, 4)]}


def dumps(document):
    buffer = io.StringIO()
    json.dump(document, buffer, indent=1, sort_keys=False)
    return buffer.getvalue() + '\n'
