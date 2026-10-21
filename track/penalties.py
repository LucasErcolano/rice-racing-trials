"""Penalty geometry for cone_config.lua: asphalt ring (off-course) and slalom pylon runs.

Everything is DERIVED from the mod geometry and stays uncertified until validated by real driving.
The expected side of every slalom gate comes from the reference racing line (fast_lane.ai), which
weaves around the pylons; the client uses the same lateral formula (see detectors.slalom_runs).
"""
import math


def _area(poly):
    return abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(poly, poly[1:] + poly[:1]))) / 2


def asphalt_ring(geometry):
    """(outer, inner) polygons: the two largest closed IS_VALID_TRACK polylines whose union is a ring.
    The very large third polyline is the surrounding ground box and is ignored."""
    polys = [p for p in geometry['limits']['polylines'] if len(p) >= 8]
    polys.sort(key=_area)
    if len(polys) < 2:
        raise ValueError('geometry has no asphalt ring')
    # the ground box is far bigger than the ring: drop anything > 4x the area of the second one
    rings = [p for p in polys if _area(p) <= 4 * _area(polys[0]) or len(polys) == 2]
    rings.sort(key=_area)
    inner, outer = rings[-2], rings[-1]
    return outer, inner


def _pip(poly, x, z):
    inside, j = False, len(poly) - 1
    for i, a in enumerate(poly):
        b = poly[j]
        if (a[1] > z) != (b[1] > z) and x < (b[0] - a[0]) * (z - a[1]) / (b[1] - a[1]) + a[0]:
            inside = not inside
        j = i
    return inside


def _edge_distance(poly, x, z):
    best = math.inf
    for i in range(len(poly)):
        a, b = poly[i], poly[(i + 1) % len(poly)]
        dx, dz = b[0] - a[0], b[1] - a[1]
        q = dx * dx + dz * dz
        t = 0 if q == 0 else max(0, min(1, ((x - a[0]) * dx + (z - a[1]) * dz) / q))
        best = min(best, math.hypot(x - a[0] - t * dx, z - a[1] - t * dz))
    return best


def _merge_pylons(clusters, radius=1.5):
    """Pylon base + top land in two clusters about 1 m apart: merge them into one centre."""
    cs = [c for c in clusters if 0.1 <= c.get('footprint_m', 0) <= 0.4]
    used, out = [False] * len(cs), []
    for i, c in enumerate(cs):
        if used[i]:
            continue
        group, used[i] = [c], True
        for j in range(i + 1, len(cs)):
            if not used[j] and math.hypot(c['x'] - cs[j]['x'], c['z'] - cs[j]['z']) <= radius:
                group.append(cs[j])
                used[j] = True
        out.append((sum(g['x'] for g in group) / len(group), sum(g['z'] for g in group) / len(group)))
    return out


def lateral(point, origin, tangent):
    """Lateral offset of `point` relative to `origin` (same formula as the Lua client)."""
    return (point[0] - origin[0]) * -tangent[1] + (point[1] - origin[1]) * tangent[0]


def slalom_runs(geometry, outer, inner, max_ai_distance=2.5, min_run=3, spacing=(5.0, 13.0), min_edge=3.0):
    """Runs of >= min_run mid-road pylons spaced like a slalom along the reference line."""
    ai = geometry['ai_path']
    n = len(ai)
    seg = []  # (start_xz, tangent, arc_start, length)
    arc = 0.0
    for i in range(n):
        a, b = ai[i], ai[(i + 1) % n]
        length = math.hypot(b[0] - a[0], b[1] - a[1])
        if length > 1e-6:
            seg.append((a, ((b[0] - a[0]) / length, (b[1] - a[1]) / length), arc, length))
        arc += length

    def project(p):
        best = None
        for a, t, s0, length in seg:
            u = max(0.0, min(length, (p[0] - a[0]) * t[0] + (p[1] - a[1]) * t[1]))
            d = math.hypot(p[0] - a[0] - t[0] * u, p[1] - a[1] - t[1] * u)
            if best is None or d < best[0]:
                best = (d, s0 + u, t, (a[0] + t[0] * u, a[1] + t[1] * u))
        return best

    mids = []
    for p in _merge_pylons(geometry['cones']['clusters']):
        if _pip(outer, *p) and not _pip(inner, *p) and min(_edge_distance(outer, *p), _edge_distance(inner, *p)) >= min_edge:
            d, s, t, foot = project(p)
            if d <= max_ai_distance:
                mids.append({'p': p, 's': s, 't': t, 'foot': foot})
    mids.sort(key=lambda m: m['s'])
    groups, cur = [], mids[:1]
    for m in mids[1:]:
        if spacing[0] <= m['s'] - cur[-1]['s'] <= spacing[1]:
            cur.append(m)
        else:
            groups.append(cur)
            cur = [m]
    groups.append(cur)
    runs = []
    for group in groups:
        if len(group) < min_run:
            continue
        # alternation anchor: weighted vote of the AI line side at each pylon
        votes = [lateral(m['foot'], m['p'], m['t']) * (-1) ** k for k, m in enumerate(group)]
        anchor = 1 if sum(votes) >= 0 else -1
        gates = []
        for k, m in enumerate(group):
            gates.append({'id': f"S{len(runs) + 1}G{k + 1}", 'x': m['p'][0], 'z': m['p'][1],
                          'tx': m['t'][0], 'tz': m['t'][1], 'expected': anchor * (-1) ** k,
                          'ai_lateral': lateral(m['foot'], m['p'], m['t'])})
        runs.append({'id': f"S{len(runs) + 1}", 'gates': gates,
                     'confidence': min(abs(g['ai_lateral']) for g in gates)})
    return runs


def reference_line_cones(geometry, cones, half_width, half_length, shrink, radius, depth, laterals=(-0.25, 0.0, 0.25)):
    """Ids of cones that the shrunk car box touches while following the reference line itself.

    A cone the racing line drives through is not a lane obstacle (pit/start pylons, decoration), so it
    must never become an AUTOMATIC penalty; it is still listed as a candidate for the reviewer."""
    ai = geometry['ai_path']
    bw, bl = half_width - shrink, half_length - shrink
    hit = set()
    cell = 4.0
    grid = {}
    for c in cones:
        grid.setdefault((math.floor(c['x'] / cell), math.floor(c['z'] / cell)), []).append(c)
    n = len(ai)
    for i in range(n):
        a, b = ai[i], ai[(i + 1) % n]
        dx, dz = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dz)
        if length < 1e-6:
            continue
        tx, tz = dx / length, dz / length
        steps = max(1, math.ceil(length / 0.1))
        for k in range(steps):
            u = length * k / steps
            for off in laterals:
                x, z = a[0] + tx * u - tz * off, a[1] + tz * u + tx * off
                cx, cz = math.floor(x / cell), math.floor(z / cell)
                for gx in (cx - 1, cx, cx + 1):
                    for gz in (cz - 1, cz, cz + 1):
                        for c in grid.get((gx, gz), ()):
                            ddx, ddz = c['x'] - x, c['z'] - z
                            f, l = ddx * tx + ddz * tz, -ddx * tz + ddz * tx
                            ox, oz = max(abs(f) - bl, 0), max(abs(l) - bw, 0)
                            if radius - math.hypot(ox, oz) >= depth:
                                hit.add(c['id'])
    return sorted(hit)
