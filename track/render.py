"""Static self-contained SVG/HTML rendering of a ``track-geometry-v1`` document.

No server, no external scripts, fonts or images. Layer toggles use plain CSS
(checkbox + sibling selectors), so the HTML works offline from a file:// URL.
"""
import html
import math

LAYERS = (
    ('limits', 'Limits (valid surface)', '#3fa7ff'),
    ('ai', 'AI line (not an official centreline)', '#40dfb0'),
    ('mesh', 'Selected mesh points', '#8c8c8c'),
    ('cones', 'Cone clusters (unconfirmed)', '#ff9d1c'),
    ('slalom', 'Slalom (calibration)', '#e04cff'),
    ('route', 'Exported route', '#ffe14d'),
    ('telemetry', 'Telemetry', '#ff4d6d'),
    ('timing', 'Timing gates / spawns', '#ffffff'),
)


class Frame:
    def __init__(self, points, width=1400, height=1000, margin=40):
        xs = [p[0] for p in points] or [0.0, 1.0]
        zs = [p[1] for p in points] or [0.0, 1.0]
        self.x0, self.z0 = min(xs), min(zs)
        self.scale = min((width - 2 * margin) / max(max(xs) - self.x0, 1.0),
                         (height - 2 * margin) / max(max(zs) - self.z0, 1.0))
        self.margin = margin
        self.width = int(2 * margin + (max(xs) - self.x0) * self.scale)
        self.height = int(2 * margin + (max(zs) - self.z0) * self.scale)

    def __call__(self, x, z):
        return (self.margin + (x - self.x0) * self.scale, self.margin + (z - self.z0) * self.scale)


def _poly(frame, points, cls, closed=False):
    if len(points) < 2:
        return ''
    coords = ' '.join('%.1f,%.1f' % frame(x, z) for x, z in points)
    tag = 'polygon' if closed else 'polyline'
    return f'<{tag} class="{cls}" points="{coords}"/>'


def _gate(frame, gate, cls, label):
    ax, az = frame(gate['a']['x'], gate['a']['z'])
    bx, bz = frame(gate['b']['x'], gate['b']['z'])
    out = [f'<line class="{cls}" x1="{ax:.1f}" y1="{az:.1f}" x2="{bx:.1f}" y2="{bz:.1f}"/>']
    direction = gate.get('direction')
    if direction in (1, -1):
        # Normal pointing to the side a crossing must END on (direction * side >= 0).
        dx, dz = gate['b']['x'] - gate['a']['x'], gate['b']['z'] - gate['a']['z']
        nx, nz = -dz * direction, dx * direction
        norm = math.hypot(nx, nz) or 1
        mx, mz = (gate['a']['x'] + gate['b']['x']) / 2, (gate['a']['z'] + gate['b']['z']) / 2
        px, pz = frame(mx, mz)
        qx, qz = frame(mx + nx / norm * 3, mz + nz / norm * 3)
        out.append(f'<line class="{cls} arrow" x1="{px:.1f}" y1="{pz:.1f}" x2="{qx:.1f}" y2="{qz:.1f}" marker-end="url(#arrow)"/>')
    out.append(f'<text class="label" x="{bx + 4:.1f}" y="{bz - 4:.1f}">{html.escape(label)}</text>')
    return ''.join(out)


def svg(document, telemetry=None, route=None, width=1400, height=1000, compact=False):
    """Return an SVG string. ``compact`` drops mesh points, telemetry and AI for a light preview."""
    points = []
    for line in document['limits'].get('polylines', []):
        points.extend(line)
    points.extend(document.get('ai_path', []))
    for mark in document.get('landmarks', {}).values():
        points.append([mark['x'], mark['z']])
    if telemetry:
        points.extend(telemetry)
    frame = Frame(points, width, height)
    groups = {key: [] for key, _, _ in LAYERS}
    for line in document['limits'].get('polylines', []):
        groups['limits'].append(_poly(frame, line, 'limits'))
    if not compact:
        groups['ai'].append(_poly(frame, document.get('ai_path', []), 'ai'))
        for name, pts in document.get('mesh_points', {}).items():
            groups['mesh'].append(''.join('<circle class="mesh" cx="%.1f" cy="%.1f" r="0.8"/>' % frame(x, z)
                                          for x, z in pts))
    for cone in document['cones'].get('clusters', []):
        cx, cz = frame(cone['x'], cone['z'])
        groups['cones'].append(f'<circle class="cone" cx="{cx:.1f}" cy="{cz:.1f}" r="2.5">'
                               f'<title>Unconfirmed cluster ({cone["x"]}, {cone["z"]}) '
                               f'{cone["footprint_m"]} m</title></circle>')
    for gate in document['slalom'].get('gates', []):
        groups['slalom'].append(_gate(frame, {**gate, 'direction': None}, 'slalom', gate.get('label', 'slalom')))
    if route:
        for gate in route.get('checkpoints', []):
            groups['route'].append(_gate(frame, gate, 'route', gate.get('label', 'cp')))
    if telemetry and not compact:
        groups['telemetry'].append(_poly(frame, telemetry, 'telemetry'))
    gate = document.get('timing_gate')
    if gate:
        groups['timing'].append(_gate(frame, gate, 'timing', 'AC_TIME_0 (native finish)'))
    for name, mark in document.get('landmarks', {}).items():
        if name.startswith('AC_TIME_0'):
            continue
        cx, cz = frame(mark['x'], mark['z'])
        fx, fz = mark.get('forward_xz', [0, 0])
        qx, qz = frame(mark['x'] + fx * 4, mark['z'] + fz * 4)
        groups['timing'].append(
            f'<circle class="spawn" cx="{cx:.1f}" cy="{cz:.1f}" r="4"/>'
            f'<line class="timing arrow" x1="{cx:.1f}" y1="{cz:.1f}" x2="{qx:.1f}" y2="{qz:.1f}" marker-end="url(#arrow)"/>'
            f'<text class="label" x="{cx + 6:.1f}" y="{cz + 12:.1f}">{html.escape(name)}</text>')
    style = ''.join(f'.{key}{{stroke:{color};}}' for key, _, color in LAYERS)
    body = ''.join(f'<g class="layer-{key}">{"".join(groups[key])}</g>' for key, _, _ in LAYERS)
    scale_m = 10 if frame.scale > 3 else 50
    bar = scale_m * frame.scale
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {frame.width} {frame.height + 30}" '
            f'width="{frame.width}" height="{frame.height + 30}" font-family="sans-serif">'
            '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" '
            'orient="auto-start-reverse"><path d="M0,0L10,5L0,10z" fill="#fff"/></marker></defs>'
            f'<style>polyline,polygon{{fill:none;stroke-width:1.2}}line{{stroke-width:2}}'
            f'.mesh{{fill:#8c8c8c;stroke:none}}.cone{{fill:#ff9d1c;stroke:none;opacity:.85}}'
            f'.spawn{{fill:#ffffff;stroke:none}}.label{{fill:#ddd;font-size:11px}}.arrow{{stroke-width:1.4}}'
            f'.telemetry{{stroke-width:1.6}}{style}</style>'
            f'<rect width="100%" height="100%" fill="#10181f"/>{body}'
            f'<line x1="{frame.margin}" y1="{frame.height + 15}" x2="{frame.margin + bar:.1f}" y2="{frame.height + 15}" '
            f'stroke="#fff"/><text class="label" x="{frame.margin + bar + 6:.1f}" y="{frame.height + 19}">{scale_m} m</text>'
            '</svg>')


def page(document, telemetry=None, telemetry_report=None, route=None):
    title = f"{document['track']}: track analysis (derived, do not distribute)"
    toggles = ''.join(
        f'<input type="checkbox" id="t-{key}" checked><label for="t-{key}" style="color:{color}">{html.escape(text)}</label> '
        for key, text, color in LAYERS)
    hide = ''.join(f'#t-{key}:not(:checked) ~ .map .layer-{key}{{display:none}}' for key, _, _ in LAYERS)
    marks = ''.join(f'<tr><td>{html.escape(n)}</td><td>{m["x"]}</td><td>{m["y"]}</td><td>{m["z"]}</td>'
                    f'<td>{m.get("forward_xz")}</td></tr>' for n, m in document.get('landmarks', {}).items())
    gate = document.get('timing_gate') or {}
    surfaces = ''.join(f'<tr><td>{html.escape(k)}</td><td>{"yes" if v["valid_track"] else "no"}</td>'
                       f'<td>{v.get("meshes", 0)}</td><td>{v.get("vertices", 0)}</td></tr>'
                       for k, v in document.get('surfaces', {}).items())
    warnings = list(document.get('warnings', []))
    if document['slalom'].get('warning'):
        warnings.append(document['slalom']['warning'])
    warnings.append(document['cones']['note'])
    warnings.append(document['limits'].get('note', ''))
    telemetry_html = ''
    if telemetry_report:
        telemetry_html = ('<h2>Telemetry</h2><pre>' + html.escape(str(telemetry_report)) + '</pre>')
    route_html = ''
    if route:
        route_html = (f'<h2>Exported route</h2><p>verified = <b>{str(route["verified"]).lower()}</b>'
                      f'{" (approved by " + html.escape(route["approved_by"]) + ")" if route.get("approved_by") else ""}'
                      f'; checkpoints: {len(route["checkpoints"])}</p>')
    return f'''<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>{html.escape(title)}</title>
<style>body{{background:#0b1116;color:#ddd;font:14px sans-serif;margin:16px}}table{{border-collapse:collapse}}
td,th{{border:1px solid #345;padding:2px 6px}}.map{{overflow:auto;border:1px solid #345;margin:8px 0}}
label{{margin-right:12px}}.warn{{color:#ffcc66}}{hide}</style></head><body>
<h1>{html.escape(title)}</h1>
<p class="warn">Derived from proprietary mod geometry: internal team use only, DO NOT DISTRIBUTE.
Assetto Corsa coordinates (m), XZ projection with z pointing down.</p>
{toggles}
<div class="map">{svg(document, telemetry, route)}</div>
<h2>Timing landmarks (Dummy.kn5)</h2>
<table><tr><th>Node</th><th>x</th><th>y</th><th>z</th><th>forward xz</th></tr>{marks}</table>
<p>Native line: direction {gate.get("direction")} ({html.escape(str(gate.get("direction_source")))});
distance from AC_START_0 spawn to line midpoint: {gate.get("spawn_to_line_midpoint_m")} m.</p>
<h2>Surfaces (surfaces.ini)</h2>
<table><tr><th>KEY</th><th>IS_VALID_TRACK</th><th>meshes</th><th>vertices</th></tr>{surfaces}</table>
<p>Limits: {html.escape(document["limits"]["status"])}; polylines: {len(document["limits"].get("polylines", []))}.</p>
<p>Cones: {html.escape(document["cones"]["status"])}; clusters: {len(document["cones"].get("clusters", []))}.</p>
<p>Slalom: {html.escape(document["slalom"]["status"])}; gates: {len(document["slalom"].get("gates", []))}.</p>
{route_html}{telemetry_html}
<h2>Warnings</h2><ul>{"".join(f"<li>{html.escape(w)}</li>" for w in warnings if w)}</ul>
<h2>Source SHA-256</h2><pre>{html.escape(chr(10).join(f"{k}  {v}" for k, v in document["source_sha256"].items()))}</pre>
</body></html>
'''
