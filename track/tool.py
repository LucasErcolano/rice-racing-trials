#!/usr/bin/env python3
"""Rice Racing track analysis tool (Linux and Windows, Python stdlib only).

Run from the repository root:  python -m track.tool <command> ...

  extract       KN5 -> derived geometry JSON (landmarks, limits, cone clusters, AI line, slalom)
  render        geometry JSON (+ telemetry, + route) -> self-contained HTML and SVG
  telemetry     analyse a CSV/JSONL trace against the native line and route checkpoints
  export-route  geometry JSON + checkpoints -> route_config.lua (verified=false unless approved)
  dummy-patch   build a Dummy.kn5 CANDIDATE (never in place), with manifest and hashes

Inputs from the mod are untrusted: run with ``python -I`` when they live outside this repo.
Outputs derived from proprietary geometry must stay in dist/track-calibration/ (git-ignored).
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

if not __package__:  # ``python -I track/tool.py``: -I drops the script dir, add the repo root explicitly
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from track import dummy_patch, geometry, render, route_export, telemetry  # noqa: E402


def _write(path, data, binary=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if binary:
        path.write_bytes(data)
    else:
        path.write_text(data, encoding='utf-8', newline='\n')
    print(path)


def cmd_extract(args):
    document = geometry.extract(args.source, args.track, slalom=args.slalom, mesh_points=args.mesh_points,
                                limit_tolerance=args.tolerance)
    _write(args.output, geometry.dumps(document))
    gate = document['timing_gate'] or {}
    print(f"{args.track}: landmarks={len(document['landmarks'])} limits={len(document['limits']['polylines'])} "
          f"cone_clusters={len(document['cones']['clusters'])} slalom={len(document['slalom']['gates'])} "
          f"direction={gate.get('direction')} spawn_to_line={gate.get('spawn_to_line_midpoint_m')} m")


def _load_route(path):
    if not path:
        return None
    return json.loads(Path(path).read_text(encoding='utf-8'))


def cmd_render(args):
    document = json.loads(Path(args.geometry).read_text(encoding='utf-8'))
    trace, report = None, None
    if args.telemetry:
        samples, stats = telemetry.load(args.telemetry)
        trace = telemetry.decimate(samples)
        report = {**stats, **telemetry.analyse(samples, document.get('timing_gate'))}
    route = _load_route(args.route)
    stem = Path(args.output)
    _write(stem.with_suffix('.html'), render.page(document, trace, report, route))
    if args.svg:
        _write(Path(args.svg), render.svg(document, compact=True, width=900, height=640))


def cmd_telemetry(args):
    document = json.loads(Path(args.geometry).read_text(encoding='utf-8'))
    samples, stats = telemetry.load(args.input)
    route = _load_route(args.route)
    report = {'track': document['track'], 'input_sha256': hashlib.sha256(Path(args.input).read_bytes()).hexdigest(),
              **stats, **telemetry.analyse(samples, document.get('timing_gate'),
                                           route['checkpoints'] if route else ())}
    text = json.dumps(report, indent=1) + '\n'
    if args.output:
        _write(args.output, text)
    else:
        print(text, end='')


def cmd_export_route(args):
    document = json.loads(Path(args.geometry).read_text(encoding='utf-8'))
    if args.checkpoints:
        checkpoints = route_export.load_checkpoints(args.checkpoints, document['track'])
        source = f'archivo de calibración {Path(args.checkpoints).name}'
    elif args.ai_candidates:
        checkpoints = route_export.checkpoints_from_ai(document['ai_path'], args.ai_candidates, args.width)
        source = 'CANDIDATOS generados desde fast_lane.ai (no son puertas oficiales)'
    else:
        raise SystemExit('need --checkpoints FILE or --ai-candidates N')
    lua, summary = route_export.build(document, checkpoints, approve=args.approve, operator=args.operator,
                                      checkpoint_source=source)
    _write(args.output, lua)
    _write(Path(args.output).with_suffix('.json'), json.dumps(summary, indent=1) + '\n')
    print('verified =', str(summary['verified']).lower())


def cmd_dummy_patch(args):
    output = Path(args.output).resolve()
    source = Path(args.source).resolve()
    if source.is_dir() and (output == source or source in output.parents):
        raise ValueError('output must not be inside the track folder (the original is never touched)')
    candidate, manifest = dummy_patch.build(args.source, args.track, nodes=tuple(args.nodes),
                                            after_line_m=args.after_line_m, pit_extra_m=args.pit_extra_m)
    # The original Dummy.kn5 is proprietary: only its SHA-256 is recorded. Apply-DummyPatch.ps1 backs up
    # the installed copy on the station before replacing it.
    _write(output / 'Dummy.kn5.candidate', candidate, binary=True)
    _write(output / 'manifest.json', json.dumps(manifest, indent=1) + '\n')
    _write(output / 'SHA256SUMS.txt', f"{manifest['original_sha256']}  Dummy.kn5 (original instalado, no incluido)\n"
                                      f"{manifest['candidate_sha256']}  Dummy.kn5.candidate\n")
    script = Path(__file__).resolve().parent / 'patches' / 'Apply-DummyPatch.ps1'
    _write(output / 'Apply-DummyPatch.ps1', script.read_bytes(), binary=True)
    print(dummy_patch.LABEL, json.dumps(manifest['verification_linux']))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)

    p = sub.add_parser('extract')
    p.add_argument('--source', required=True, help='extracted track folder or original ZIP')
    p.add_argument('--track', required=True)
    p.add_argument('--output', required=True)
    p.add_argument('--slalom', help='slalom-gates-v1 JSON (optional)')
    p.add_argument('--mesh-points', help='regex of visual mesh names whose vertices are sampled into the view')
    p.add_argument('--tolerance', type=float, default=0.05, help='limit simplification tolerance (m)')
    p.set_defaults(func=cmd_extract)

    p = sub.add_parser('render')
    p.add_argument('--geometry', required=True)
    p.add_argument('--output', required=True, help='HTML path')
    p.add_argument('--svg', help='also write a light SVG preview (limits, gates, clusters only)')
    p.add_argument('--telemetry')
    p.add_argument('--route', help='route summary JSON written by export-route')
    p.set_defaults(func=cmd_render)

    p = sub.add_parser('telemetry')
    p.add_argument('--geometry', required=True)
    p.add_argument('--input', required=True)
    p.add_argument('--route')
    p.add_argument('--output')
    p.set_defaults(func=cmd_telemetry)

    p = sub.add_parser('export-route')
    p.add_argument('--geometry', required=True)
    p.add_argument('--output', required=True, help='route_config.lua path')
    p.add_argument('--checkpoints', help='route-checkpoints-v1 JSON surveyed by the operator')
    p.add_argument('--ai-candidates', type=int, help='generate N candidate gates from the AI line')
    p.add_argument('--width', type=float, default=8.0)
    p.add_argument('--approve', action='store_true', help='operator approval: writes verified=true')
    p.add_argument('--operator', help='name of the approving operator (required with --approve)')
    p.set_defaults(func=cmd_export_route)

    p = sub.add_parser('dummy-patch')
    p.add_argument('--source', required=True)
    p.add_argument('--track', required=True)
    p.add_argument('--output', required=True, help='new directory for the candidate (never the track folder)')
    p.add_argument('--nodes', nargs='+', default=['AC_START_0'], choices=['AC_START_0', 'AC_PIT_0'])
    p.add_argument('--after-line-m', type=float, default=8.0,
                   help='metres along the AI line from the native line; negative = before the line')
    p.add_argument('--pit-extra-m', type=float, default=8.0, help='extra offset of AC_PIT_0 from AC_START_0')
    p.set_defaults(func=cmd_dummy_patch)

    args = parser.parse_args(argv)
    try:
        args.func(args)
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(2, f'error: {exc}\n')


if __name__ == '__main__':
    sys.exit(main())
