"""Create and validate explicitly fail-closed V2 track calibration JSON."""
import argparse
import json
import sys
from pathlib import Path

from server import penalties_v2


def make_template(track, survey_id, vehicle_length_m, vehicle_width_m):
    return {
        'schema': penalties_v2.CONFIG_SCHEMA,
        'track': track,
        'coordinate_frame': 'Assetto Corsa world XZ, metres',
        'calibration': {'status': 'incomplete', 'survey_id': survey_id,
                        'provenance': 'Replace every candidate with independently surveyed geometry.'},
        'boundaries': {'calibrated': False, 'outer': [], 'inner': []},
        'route': {'calibrated': False, 'closed': True, 'direction': 1, 'points': []},
        'vehicle': {
            'length_m': vehicle_length_m,
            'width_m': vehicle_width_m,
            'wheel_offsets': [
                {'id': 'FL', 'forward_m': vehicle_length_m * 0.4, 'right_m': -vehicle_width_m * 0.4},
                {'id': 'FR', 'forward_m': vehicle_length_m * 0.4, 'right_m': vehicle_width_m * 0.4},
                {'id': 'RL', 'forward_m': -vehicle_length_m * 0.4, 'right_m': -vehicle_width_m * 0.4},
                {'id': 'RR', 'forward_m': -vehicle_length_m * 0.4, 'right_m': vehicle_width_m * 0.4},
            ],
        },
        'cones': {'calibrated': False, 'items': []},
        'slaloms': {'calibrated': False, 'sections': []},
    }


def calibration_report(config):
    missing = []
    if not isinstance(config, dict):
        raise ValueError('configuration must be a JSON object')
    boundaries = config.get('boundaries')
    if not isinstance(boundaries, dict) or boundaries.get('calibrated') is not True:
        missing.append('boundaries')
    cones = config.get('cones')
    if not isinstance(cones, dict) or cones.get('calibrated') is not True:
        missing.append('cones')
    slaloms = config.get('slaloms')
    if not isinstance(slaloms, dict) or slaloms.get('calibrated') is not True:
        missing.append('slaloms')
    route = config.get('route')
    if not isinstance(route, dict) or route.get('calibrated') is not True or not isinstance(route.get('points'), list) or len(route['points']) < 2:
        missing.append('route')
    trace = {
        'protocol': penalties_v2.PROTOCOL,
        'lap': {'station_id': 'CAL', 'session_id': 'CAL', 'lap_id': 'CAL', 'raw_ms': 1000},
        'samples': [
            {'t_ms': 0, 'position': {'x': 0.0, 'z': 0.0}, 'forward': {'x': 1.0, 'z': 0.0}, 'velocity': {'x': 1.0, 'z': 0.0}},
            {'t_ms': 100, 'position': {'x': 0.1, 'z': 0.0}, 'forward': {'x': 1.0, 'z': 0.0}, 'velocity': {'x': 1.0, 'z': 0.0}},
        ],
    }
    try:
        penalties_v2.analyze_trajectory(trace, config)
    except (penalties_v2.TrajectoryError, TypeError, KeyError) as error:
        return {'structurally_valid': False, 'ready_for_automatic_scoring': False,
                'uncalibrated_sections': sorted(set(missing)), 'error': str(error)}
    return {'structurally_valid': True, 'ready_for_automatic_scoring': not missing,
            'uncalibrated_sections': sorted(set(missing)),
            'note': 'Readiness is structural only; it is not physical validation.'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    init = sub.add_parser('init', help='write an explicitly uncalibrated template')
    init.add_argument('--track', required=True)
    init.add_argument('--survey-id', required=True)
    init.add_argument('--vehicle-length-m', type=float, required=True)
    init.add_argument('--vehicle-width-m', type=float, required=True)
    init.add_argument('--output', type=Path, required=True)
    init.add_argument('--force', action='store_true')
    validate = sub.add_parser('validate', help='validate a calibration and report missing sections')
    validate.add_argument('--config', type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == 'init':
        if args.output.exists() and not args.force:
            parser.error(f'{args.output} exists; pass --force to replace it')
        if not args.track.strip() or not args.survey_id.strip() or not 0.2 <= args.vehicle_length_m <= 10 or not 0.2 <= args.vehicle_width_m <= 5:
            parser.error('track, survey ID, or vehicle dimensions are invalid')
        config = make_template(args.track.strip(), args.survey_id.strip(), args.vehicle_length_m, args.vehicle_width_m)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(config, indent=2, sort_keys=True) + '\n', encoding='utf-8')
        report = calibration_report(config)
        print(json.dumps({'output': str(args.output), **report}, sort_keys=True))
        return 0
    try:
        config = json.loads(args.config.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as error:
        print(json.dumps({'structurally_valid': False, 'error': str(error)}), file=sys.stderr)
        return 2
    report = calibration_report(config)
    print(json.dumps(report, sort_keys=True))
    return 0 if report['ready_for_automatic_scoring'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
