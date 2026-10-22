#!/usr/bin/env python3
"""Rasterize the native-style overlays from the REAL native_render.lua draw calls.

The renderer runs unchanged against a recording `ui` double; every drawRectFilled /
setCursor / acText call is then replayed with Pillow. This is a layout and colour
preview, NOT a capture of Assetto Corsa: the AC font ("aria") is not available here, so
a bold sans substitute is used. Use it to compare proportions/colours/placement with the
original AC Lap Timing and Performance Delta apps, not glyph shapes.

Usage:  python tools/overlay_preview.py OUT_DIR
Needs lupa (tests venv) and Pillow (falls back to the system dist-packages).
"""
import json
import sys
from pathlib import Path

sys.path.append('/usr/lib/python3/dist-packages')
from lupa import LuaRuntime  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

CLIENT = Path(__file__).resolve().parents[1] / 'client'
FONT = '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'
BG = (38, 44, 52)  # neutral "track" backdrop so translucent panels read like in-game


def runtime():
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.globals().package.path = str(CLIENT / '?.lua') + ';' + lua.globals().package.path
    lua.execute('''
      vec2 = function(x, y) return {x=x, y=y} end
      rgbm = function(r, g, b, m) return {r=r, g=g, b=b, m=m} end
    ''')
    return lua


def record(lua, models, width):
    """Run native_render.draw for each model; return the list of ui calls."""
    calls = []
    wrap = lua.eval('function(f) return function(...) return f(...) end end')

    def pt(v):
        return (float(v['x']), float(v['y']))

    def col(c):
        return [float(c['r']), float(c['g']), float(c['b']), float(c['m'])]

    ui = {
        'pushACFont': wrap(lambda n: None),
        'popACFont': wrap(lambda: None),
        'windowSize': wrap(lambda: lua.eval('{x=%d, y=400}' % width)),
        'calculateACTextSize': wrap(lambda t, s: lua.eval('{x=%f}' % (len(t) * float(s['x']) * 0.6))),
        'setCursor': wrap(lambda p: calls.append(('cursor', pt(p)))),
        'acText': wrap(lambda t, s, sp, c: calls.append(('text', t, float(s['x']), col(c)))),
        'drawRectFilled': wrap(lambda a, b, c, r=0: calls.append(('rect', pt(a), pt(b), col(c), r))),
    }
    lua.globals().ui = lua.table_from(ui)
    render = lua.eval("(require('native_render'))")
    for m in models:
        render.draw(m, None)
    return calls


def raster(calls, size, scale=2):
    img = Image.new('RGBA', (size[0] * scale, size[1] * scale), BG + (255,))
    layer = Image.new('RGBA', img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    cursor = (0, 0)
    for c in calls:
        if c[0] == 'cursor':
            cursor = c[1]
        elif c[0] == 'rect':
            (x0, y0), (x1, y1), (r, g, b, m), rad = c[1], c[2], c[3], c[4]
            box = [x0 * scale, y0 * scale, x1 * scale, y1 * scale]
            fill = (int(r * 255), int(g * 255), int(b * 255), int(m * 255))
            if rad and rad > 0:
                d.rounded_rectangle(box, radius=rad * scale, fill=fill)
            else:
                d.rectangle(box, fill=fill)
        elif c[0] == 'text':
            text, px, (r, g, b, m) = c[1], c[2], c[3]
            f = ImageFont.truetype(FONT, int(px * scale * 0.82))
            d.text((cursor[0] * scale, cursor[1] * scale), text, font=f,
                   fill=(int(r * 255), int(g * 255), int(b * 255), int(m * 255)))
    return Image.alpha_composite(img, layer).convert('RGB')


def build(lua):
    ov = lua.eval("(require('native_overlay'))")
    tab = lambda v: lua.table_from(v, recursive=True) if v is not None else None
    timing = lambda s, cfg=None: ov.timing(tab(s), tab(cfg))
    delta = lambda ms, o: ov.delta(ms, tab(o), None)
    return timing, delta


SCENES = {
    'timing_clean': ('timing', {'phase': 'timed', 'lap_number': 3, 'current_ms': 84520,
                                'last_ms': 90000, 'best_ms': 88000}),
    'timing_penalty': ('timing', {'phase': 'timed', 'lap_number': 3, 'current_ms': 88520,
                                  'penalty_ms': 4000, 'last_ms': 90000, 'best_ms': 88000}),
    'timing_no_evidence': ('timing', {'phase': 'timed', 'lap_number': 2, 'current_ms': 84520,
                                      'penalty_pending': True, 'last_ms': 90000}),
    'timing_outlap': ('timing', {'phase': 'outlap', 'lap_number': 1, 'last_ms': None}),
    'delta_faster': ('delta', (-830, {'has_reference': True})),
    'delta_slower': ('delta', (1200, {'has_reference': True})),
    'delta_no_reference': ('delta', (None, {'has_reference': False})),
    'delta_pending': ('delta', (None, {'has_reference': True, 'pending': True})),
}


def main(out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for name, (kind, arg) in SCENES.items():
        lua = runtime()
        timing, delta = build(lua)
        model = timing(arg) if kind == 'timing' else delta(*arg)
        calls = record(lua, [model], 300)
        path = out / (name + '.png')
        raster(calls, (300, 150 if kind == 'timing' else 70)).save(path)
        manifest[name] = {'calls': len(calls)}
    # windowNative exactly as the app composes it: timing then delta, same config.
    lua = runtime()
    timing, delta = build(lua)
    t = timing(SCENES['timing_penalty'][1])
    dm = delta(-830, {'has_reference': True})
    # Same stacking as script.windowNative: delta panel directly under the timing panel.
    render = lua.eval("(require('native_render'))")
    lay = lua.table_from({'scale': t.layout.scale, 'x': t.layout.x,
                          'y': t.layout.y + render.height(t) + 4, 'alpha': t.layout.alpha})
    dm.layout = lay
    calls = record(lua, [t, dm], 300)
    raster(calls, (300, 220)).save(out / 'window_native_composed.png')
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2))
    print('wrote', len(manifest) + 1, 'previews to', out)


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'docs/evidence/overlay-previews')
