"""Deterministic server-side analysis for Rice Racing trajectory protocol V2.

This module is deliberately fail-closed: only calibrated geometry can generate
penalty counts. It never trusts client-reported penalty totals.
"""
from __future__ import annotations

import math

try:
    from server import scoring
except ImportError:  # imported by server/app.py as a standalone module
    import scoring


PROTOCOL = 'rice-trajectory-v2'
CONFIG_SCHEMA = 'rice-penalties-v2'
DETECTOR_VERSION = 1


class TrajectoryError(ValueError):
    """Invalid or unsafe V2 trajectory/configuration input."""


def _finite(value, name):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise TrajectoryError(f'invalid {name}')
    return float(value)


def _point(value, name):
    if not isinstance(value, dict):
        raise TrajectoryError(f'invalid {name}')
    return (_finite(value.get('x'), name + '.x'), _finite(value.get('z'), name + '.z'))


def _validate(trace, config):
    if not isinstance(trace, dict) or trace.get('protocol') != PROTOCOL:
        raise TrajectoryError('unsupported trajectory protocol')
    lap = trace.get('lap')
    if not isinstance(lap, dict):
        raise TrajectoryError('lap object required')
    identity = {}
    for key in ('station_id', 'session_id', 'lap_id'):
        value = lap.get(key)
        if not isinstance(value, str) or not value or len(value) > 80:
            raise TrajectoryError('invalid lap.' + key)
        identity[key] = value
    raw_ms = lap.get('raw_ms')
    if type(raw_ms) is not int or not 0 < raw_ms <= 3600000:
        raise TrajectoryError('invalid lap.raw_ms')
    if not isinstance(config, dict) or config.get('schema') != CONFIG_SCHEMA:
        raise TrajectoryError('unsupported penalty configuration')
    track = config.get('track')
    if not isinstance(track, str) or not track or len(track) > 120:
        raise TrajectoryError('invalid config.track')
    samples = trace.get('samples')
    if not isinstance(samples, list) or not 2 <= len(samples) <= 20000:
        raise TrajectoryError('samples must contain 2..20000 frames')
    parsed = []
    previous = None
    for index, item in enumerate(samples):
        if not isinstance(item, dict):
            raise TrajectoryError(f'invalid samples[{index}]')
        t = item.get('t_ms')
        if type(t) is not int or t < 0 or (previous is not None and t <= previous):
            raise TrajectoryError('sample timestamps must be increasing integer milliseconds')
        previous = t
        pos = _point(item.get('position'), f'samples[{index}].position')
        forward = _point(item.get('forward'), f'samples[{index}].forward')
        norm = math.hypot(*forward)
        if not 0.8 <= norm <= 1.2:
            raise TrajectoryError(f'invalid samples[{index}].forward')
        forward = (forward[0] / norm, forward[1] / norm)
        velocity = _point(item.get('velocity'), f'samples[{index}].velocity')
        parsed.append({'t_ms': t, 'position': pos, 'forward': forward, 'velocity': velocity,
                       'wheel_contacts': item.get('wheel_contacts')})
    vehicle = config.get('vehicle')
    if not isinstance(vehicle, dict):
        raise TrajectoryError('vehicle geometry required')
    length = _finite(vehicle.get('length_m'), 'vehicle.length_m')
    width = _finite(vehicle.get('width_m'), 'vehicle.width_m')
    if not 0.2 <= length <= 10 or not 0.2 <= width <= 5:
        raise TrajectoryError('vehicle dimensions out of range')
    return identity, raw_ms, parsed, vehicle


def _vehicle_polygon(sample, vehicle):
    x, z = sample['position']
    fx, fz = sample['forward']
    rx, rz = fz, -fx  # vehicle-right in the AC XZ plane
    local = vehicle.get('collision_polygon')
    if local is None:
        half_l = float(vehicle['length_m']) / 2
        half_w = float(vehicle['width_m']) / 2
        local = [(-half_w, -half_l), (half_w, -half_l), (half_w, half_l), (-half_w, half_l)]
    if not isinstance(local, list) or len(local) < 3 or len(local) > 32:
        raise TrajectoryError('invalid vehicle.collision_polygon')
    points = []
    for item in local:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise TrajectoryError('invalid vehicle.collision_polygon point')
        right, forward = _finite(item[0], 'collision_polygon.right'), _finite(item[1], 'collision_polygon.forward')
        points.append((x + rx * right + fx * forward, z + rz * right + fz * forward))
    return points


def _distance_point_segment(point, a, b):
    dx, dz = b[0] - a[0], b[1] - a[1]
    den = dx * dx + dz * dz
    if den <= 1e-16:
        return math.hypot(point[0] - a[0], point[1] - a[1])
    u = max(0.0, min(1.0, ((point[0] - a[0]) * dx + (point[1] - a[1]) * dz) / den))
    return math.hypot(point[0] - (a[0] + dx * u), point[1] - (a[1] + dz * u))


def _inside_polygon(point, polygon):
    x, z = point
    inside = False
    for i, a in enumerate(polygon):
        b = polygon[(i + 1) % len(polygon)]
        if _distance_point_segment(point, a, b) <= 1e-9:
            return True
        if (a[1] > z) != (b[1] > z):
            x_cross = a[0] + (z - a[1]) * (b[0] - a[0]) / (b[1] - a[1])
            if x < x_cross:
                inside = not inside
    return inside


def _point_polygon_distance(point, polygon):
    if _inside_polygon(point, polygon):
        return 0.0
    return min(_distance_point_segment(point, polygon[i], polygon[(i + 1) % len(polygon)])
               for i in range(len(polygon)))


def _lerp(a, b, u):
    return (a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u)


def _lerp_heading(a, b, u):
    dot = max(-1.0, min(1.0, a[0] * b[0] + a[1] * b[1]))
    delta = math.atan2(a[0] * b[1] - a[1] * b[0], dot)
    angle = math.atan2(a[1], a[0]) + delta * u
    return (math.cos(angle), math.sin(angle))


def _interpolated(a, b, u):
    result = {'t_ms': round(a['t_ms'] + (b['t_ms'] - a['t_ms']) * u),
              'position': _lerp(a['position'], b['position'], u),
              'forward': _lerp_heading(a['forward'], b['forward'], u),
              'velocity': _lerp(a['velocity'], b['velocity'], u)}
    left, right = a.get('wheel_contacts'), b.get('wheel_contacts')
    if isinstance(left, list) and isinstance(right, list) and len(left) == len(right) == 4:
        left_by_id = {item.get('id'): item for item in left if isinstance(item, dict)}
        right_by_id = {item.get('id'): item for item in right if isinstance(item, dict)}
        if set(left_by_id) == {'FL', 'FR', 'RL', 'RR'} and set(right_by_id) == set(left_by_id):
            result['wheel_contacts'] = [
                {'id': key,
                 'x': _finite(left_by_id[key].get('x'), 'wheel.x') +
                      (_finite(right_by_id[key].get('x'), 'wheel.x') - _finite(left_by_id[key].get('x'), 'wheel.x')) * u,
                 'z': _finite(left_by_id[key].get('z'), 'wheel.z') +
                      (_finite(right_by_id[key].get('z'), 'wheel.z') - _finite(left_by_id[key].get('z'), 'wheel.z')) * u}
                for key in ('FL', 'FR', 'RL', 'RR')]
    return result


def _segments(a, b, settings):
    distance = math.dist(a['position'], b['position'])
    angle = abs(math.atan2(a['forward'][0] * b['forward'][1] - a['forward'][1] * b['forward'][0],
                           a['forward'][0] * b['forward'][0] + a['forward'][1] * b['forward'][1]))
    count = max(1, math.ceil(distance / settings['max_sweep_step_m']),
                math.ceil(angle / math.radians(settings['max_heading_step_deg'])))
    if count > 500:
        raise TrajectoryError('trajectory segment exceeds interpolation limit')
    return count


def _settings(overrides):
    values = {
        'enable_cones': True, 'enable_off_track': True, 'enable_slalom': True,
        'cone_penalty_ms': 2000, 'off_course_penalty_ms': 20000, 'slalom_penalty_ms': 20000,
        'cone_tolerance_m': 0.0, 'boundary_tolerance_m': 0.05,
        'off_track_hold_ms': 150, 'max_gap_ms': 1000, 'max_sweep_step_m': 0.1,
        'max_heading_step_deg': 2.0, 'max_speed_mps': 80.0, 'teleport_slack_m': 4.0,
        'progress_epsilon_m': 0.5, 'max_route_projection_m': 25.0,
    }
    if overrides is not None:
        if not isinstance(overrides, dict) or set(overrides) - set(values):
            raise TrajectoryError('unknown penalty setting')
        values.update(overrides)
    for key in ('enable_cones', 'enable_off_track', 'enable_slalom'):
        if type(values[key]) is not bool:
            raise TrajectoryError('invalid ' + key)
    for key in ('cone_penalty_ms', 'off_course_penalty_ms', 'slalom_penalty_ms', 'off_track_hold_ms', 'max_gap_ms'):
        if type(values[key]) is not int or not 0 <= values[key] <= 3600000:
            raise TrajectoryError('invalid ' + key)
    for key in ('cone_tolerance_m', 'boundary_tolerance_m', 'max_sweep_step_m', 'max_heading_step_deg',
                'max_speed_mps', 'teleport_slack_m', 'progress_epsilon_m', 'max_route_projection_m'):
        values[key] = _finite(values[key], key)
        if values[key] < 0:
            raise TrajectoryError('invalid ' + key)
    if values['max_sweep_step_m'] <= 0 or values['max_heading_step_deg'] <= 0 or values['max_speed_mps'] <= 0:
        raise TrajectoryError('sweep and speed limits must be positive')
    return values


def _detect_cones(samples, vehicle, config, settings, lap_id, events, review):
    cones = config.get('cones')
    if not isinstance(cones, dict):
        review.append('cone_geometry_missing')
        return
    items = cones.get('items', [])
    if not isinstance(items, list) or len(items) > 5000:
        raise TrajectoryError('invalid cone geometry')
    if not cones.get('calibrated'):
        if items:
            review.append('cone_geometry_uncalibrated')
        return
    parsed_cones = []
    seen_ids = set()
    for cone in items:
        if not isinstance(cone, dict):
            raise TrajectoryError('invalid cone entry')
        cone_id = cone.get('id')
        if not isinstance(cone_id, str) or not cone_id or len(cone_id) > 80 or cone_id in seen_ids:
            raise TrajectoryError('invalid or duplicate cone id')
        seen_ids.add(cone_id)
        if cone.get('calibrated', True) is not True:
            review.append('cone_' + cone_id + '_uncalibrated')
            continue
        x = _finite(cone.get('x'), 'cone.x')
        z = _finite(cone.get('z'), 'cone.z')
        radius = _finite(cone.get('radius_m'), 'cone.radius_m')
        if not 0 < radius <= 1:
            raise TrajectoryError('cone radius out of range')
        parsed_cones.append((cone_id, (x, z), radius))
    touched = set()
    for left, right in zip(samples, samples[1:]):
        dt = right['t_ms'] - left['t_ms']
        if dt > settings['max_gap_ms']:
            review.append('telemetry_gap')
            continue
        distance = math.dist(left['position'], right['position'])
        if distance > settings['max_speed_mps'] * dt / 1000 + settings['teleport_slack_m']:
            review.append('teleport_detected')
            continue
        count = _segments(left, right, settings)
        for step in range(count + 1):
            current = _interpolated(left, right, step / count)
            polygon = _vehicle_polygon(current, vehicle)
            for cone_id, point, radius in parsed_cones:
                if cone_id in touched:
                    continue
                if _point_polygon_distance(point, polygon) <= radius + settings['cone_tolerance_m']:
                    touched.add(cone_id)
                    events.append({
                        'type': 'cone_contact', 'cone_id': cone_id, 'lap_id': lap_id,
                        'timestamp_ms': current['t_ms'], 'position': {'x': point[0], 'z': point[1]},
                        'penalizable': settings['enable_cones'],
                        'penalty_ms': settings['cone_penalty_ms'] if settings['enable_cones'] else 0,
                        'evidence': {'method': 'swept_vehicle_polygon_vs_calibrated_circle',
                                     'sample_interval_ms': [left['t_ms'], right['t_ms']],
                                     'cone_radius_m': radius, 'vehicle_polygon_world': polygon},
                    })



def _detect_direct_cone_contacts(trace, config, settings, lap_id, samples, events, review):
    contacts = trace.get('contact_events', [])
    if not isinstance(contacts, list) or len(contacts) > 5000:
        raise TrajectoryError('invalid contact_events')
    if not contacts:
        return
    cones = config.get('cones')
    if not isinstance(cones, dict) or not cones.get('calibrated'):
        review.append('direct_cone_contact_without_calibrated_geometry')
        return
    calibrated_ids = {item.get('id') for item in cones.get('items', [])
                      if isinstance(item, dict) and item.get('calibrated', True) is True}
    first_t, last_t = samples[0]['t_ms'], samples[-1]['t_ms']
    for contact in contacts:
        if not isinstance(contact, dict) or contact.get('type') != 'cone_contact' or contact.get('source') != 'csp_collision':
            review.append('unsupported_direct_contact_event')
            continue
        cone_id, timestamp = contact.get('cone_id'), contact.get('t_ms')
        if not isinstance(cone_id, str) or cone_id not in calibrated_ids:
            review.append('direct_contact_cone_id_unmapped')
            continue
        if type(timestamp) is not int or not first_t <= timestamp <= last_t:
            review.append('direct_contact_timestamp_out_of_lap')
            continue
        position = _point(contact.get('position'), 'contact.position')
        existing = next((event for event in events if event['type'] == 'cone_contact' and event['cone_id'] == cone_id), None)
        direct = {'source': 'csp_collision', 'contact_id': contact.get('contact_id'), 'position': {'x': position[0], 'z': position[1]}}
        if existing:
            existing['evidence']['direct_collision_event'] = direct
            continue
        events.append({'type': 'cone_contact', 'cone_id': cone_id, 'lap_id': lap_id,
                       'timestamp_ms': timestamp, 'position': {'x': position[0], 'z': position[1]},
                       'penalizable': settings['enable_cones'],
                       'penalty_ms': settings['cone_penalty_ms'] if settings['enable_cones'] else 0,
                       'evidence': {'method': 'direct_csp_collision_event', 'source': 'csp_collision',
                                    'contact_id': contact.get('contact_id'),
                                    'calibrated_cone_id': cone_id}})


def _route_data(config):
    route = config.get('route')
    if not isinstance(route, dict):
        return None
    points = route.get('points')
    if not isinstance(points, list) or len(points) < 2 or len(points) > 20000:
        return None
    parsed = []
    for point in points:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            return None
        parsed.append((_finite(point[0], 'route.x'), _finite(point[1], 'route.z')))
    closed, direction = route.get('closed'), route.get('direction')
    if type(closed) is not bool or direction not in (-1, 1):
        return None
    segments = list(zip(parsed, parsed[1:]))
    if closed and parsed[-1] != parsed[0]:
        segments.append((parsed[-1], parsed[0]))
    lengths = [math.dist(a, b) for a, b in segments]
    if not lengths or any(length <= 1e-6 for length in lengths):
        return None
    cumulative, total = [], 0.0
    for length in lengths:
        cumulative.append(total)
        total += length
    return {'points': parsed, 'segments': segments, 'lengths': lengths, 'cumulative': cumulative,
            'length': total, 'closed': closed, 'direction': direction}


def _project(point, route):
    best = (float('inf'), None)
    for index, (a, b) in enumerate(route['segments']):
        length = route['lengths'][index]
        dx, dz = b[0] - a[0], b[1] - a[1]
        u = max(0.0, min(1.0, ((point[0] - a[0]) * dx + (point[1] - a[1]) * dz) / (length * length)))
        projected = (a[0] + dx * u, a[1] + dz * u)
        distance = math.dist(point, projected)
        if distance < best[0]:
            best = distance, route['cumulative'][index] + length * u
    return best


def _wheel_positions(sample, vehicle):
    contacts = sample.get('wheel_contacts')
    if contacts is not None:
        if not isinstance(contacts, list) or len(contacts) != 4:
            return None
        by_id = {}
        for item in contacts:
            if not isinstance(item, dict) or item.get('id') not in ('FL', 'FR', 'RL', 'RR') or item['id'] in by_id:
                return None
            by_id[item['id']] = _point(item, 'wheel_contact')
        if set(by_id) != {'FL', 'FR', 'RL', 'RR'}:
            return None
        return [by_id[key] for key in ('FL', 'FR', 'RL', 'RR')]
    offsets = vehicle.get('wheel_offsets')
    if not isinstance(offsets, list) or len(offsets) != 4:
        return None
    by_id = {}
    for item in offsets:
        if not isinstance(item, dict) or item.get('id') not in ('FL', 'FR', 'RL', 'RR') or item['id'] in by_id:
            return None
        by_id[item['id']] = (_finite(item.get('forward_m'), 'wheel.forward_m'),
                             _finite(item.get('right_m'), 'wheel.right_m'))
    if set(by_id) != {'FL', 'FR', 'RL', 'RR'}:
        return None
    x, z = sample['position']
    fx, fz = sample['forward']
    rx, rz = fz, -fx
    return [(x + fx * by_id[key][0] + rx * by_id[key][1],
             z + fz * by_id[key][0] + rz * by_id[key][1]) for key in ('FL', 'FR', 'RL', 'RR')]


def _polygon(value, name):
    if not isinstance(value, list) or (value and len(value) < 3) or len(value) > 50000:
        raise TrajectoryError('invalid ' + name)
    points = []
    for item in value:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise TrajectoryError('invalid ' + name + ' point')
        points.append((_finite(item[0], name + '.x'), _finite(item[1], name + '.z')))
    return points


def _outside_track(sample, vehicle, outer, inner, tolerance):
    wheels = _wheel_positions(sample, vehicle)
    if wheels is None:
        return None
    for wheel in wheels:
        in_outer = _inside_polygon(wheel, outer) or _point_polygon_distance(wheel, outer) <= tolerance
        in_inner = bool(inner) and _inside_polygon(wheel, inner) and _point_polygon_distance(wheel, inner) > tolerance
        if in_outer and not in_inner:
            return False
    return True


def _detect_off_track(samples, vehicle, config, settings, lap_id, events, review):
    boundaries = config.get('boundaries')
    if not isinstance(boundaries, dict) or not boundaries.get('calibrated'):
        review.append('track_boundaries_uncalibrated')
        return
    outer = _polygon(boundaries.get('outer'), 'boundaries.outer')
    inner = _polygon(boundaries.get('inner', []), 'boundaries.inner')
    if len(outer) < 3:
        review.append('track_outer_boundary_missing')
        return
    route = _route_data(config)
    if route is None:
        review.append('route_geometry_missing')
        return
    hold_ms, epsilon = settings['off_track_hold_ms'], settings['progress_epsilon_m']
    candidate = active = None
    previous_s = previous_u = progress = None
    sequence = 0

    def route_progress(frame):
        nonlocal previous_s, previous_u, progress
        distance, s = _project(frame['position'], route)
        if distance > settings['max_route_projection_m']:
            review.append('route_projection_uncertain')
            previous_s = previous_u = progress = None
            return None
        u = s if route['direction'] == 1 else route['length'] - s
        if route['closed']:
            u %= route['length']
        if previous_u is None:
            progress = u
        else:
            delta = u - previous_u
            if route['closed']:
                delta = (delta + route['length'] / 2) % route['length'] - route['length'] / 2
            progress += delta
        previous_s, previous_u = s, u
        return progress

    def emit_reentry(exit_frame, exit_progress, frame, reentry_progress):
        nonlocal sequence
        sequence += 1
        ahead = (exit_progress is not None and reentry_progress is not None and
                 reentry_progress - exit_progress > epsilon)
        events.append({'type': 'off_course', 'lap_id': lap_id,
                       'timestamp_ms': exit_frame['t_ms'],
                       'position': {'x': exit_frame['position'][0], 'z': exit_frame['position'][1]},
                       'reentry_timestamp_ms': frame['t_ms'],
                       'reentry_position': {'x': frame['position'][0], 'z': frame['position'][1]},
                       's_exit_m': exit_progress, 's_reentry_m': reentry_progress,
                       'penalizable': bool(ahead and settings['enable_off_track']),
                       'penalty_ms': settings['off_course_penalty_ms'] if ahead and settings['enable_off_track'] else 0,
                       'incident_id': f'off-course:{lap_id}:{sequence}',
                       'evidence': {'method': 'all_four_wheels_outside_calibrated_boundaries',
                                    'hold_ms': hold_ms, 'boundary_tolerance_m': settings['boundary_tolerance_m'],
                                    'rule': 'reentry_progress_gt_exit_progress'}})
        if exit_progress is None or reentry_progress is None:
            review.append('off_course_route_progress_unknown')

    def consume(frame):
        nonlocal candidate, active
        p = route_progress(frame)
        outside = _outside_track(frame, vehicle, outer, inner, settings['boundary_tolerance_m'])
        if outside is None:
            review.append('wheel_geometry_missing')
            candidate = active = None
            return
        if outside:
            if active is None and candidate is None:
                candidate = (frame, p)
            if active is None and candidate is not None and frame['t_ms'] - candidate[0]['t_ms'] >= hold_ms:
                active = candidate
                candidate = None
        else:
            if active is not None:
                emit_reentry(active[0], active[1], frame, p)
            active = candidate = None

    consume(samples[0])
    for left, right in zip(samples, samples[1:]):
        dt = right['t_ms'] - left['t_ms']
        distance = math.dist(left['position'], right['position'])
        if dt > settings['max_gap_ms'] or distance > settings['max_speed_mps'] * dt / 1000 + settings['teleport_slack_m']:
            review.append('telemetry_gap' if dt > settings['max_gap_ms'] else 'teleport_detected')
            if active is not None:
                review.append('off_course_reentry_missing_after_gap')
            active = candidate = None
            previous_s = previous_u = progress = None
            continue
        count = _segments(left, right, settings)
        for step in range(1, count + 1):
            consume(_interpolated(left, right, step / count))
    if active is not None:
        sequence += 1
        events.append({'type': 'off_course_unresolved', 'lap_id': lap_id,
                       'timestamp_ms': active[0]['t_ms'],
                       'position': {'x': active[0]['position'][0], 'z': active[0]['position'][1]},
                       's_exit_m': active[1], 's_reentry_m': None, 'penalizable': False, 'penalty_ms': 0,
                       'incident_id': f'off-course:{lap_id}:{sequence}',
                       'evidence': {'method': 'all_four_wheels_outside_calibrated_boundaries',
                                    'reason': 'lap_ended_without_reentry'}})
        review.append('off_course_reentry_missing')



def _gate_crossing(left, right, gate):
    a, b = gate['a'], gate['b']
    dx, dz = b[0] - a[0], b[1] - a[1]
    side0 = dx * (left['position'][1] - a[1]) - dz * (left['position'][0] - a[0])
    side1 = dx * (right['position'][1] - a[1]) - dz * (right['position'][0] - a[0])
    if side0 * side1 >= 0 or abs(side1 - side0) <= 1e-12:
        return None
    u = -side0 / (side1 - side0)
    if not 0.0 <= u <= 1.0:
        return None
    point = _lerp(left['position'], right['position'], u)
    length2 = dx * dx + dz * dz
    along = ((point[0] - a[0]) * dx + (point[1] - a[1]) * dz) / length2
    if not 0.0 <= along <= 1.0:
        return None
    return u, 1 if side1 > side0 else -1, point


def _trajectory_progress(samples, route, settings, review):
    result, previous_u, progress = [], None, None
    previous = None
    for frame in samples:
        if previous is not None:
            dt = frame['t_ms'] - previous['t_ms']
            distance = math.dist(frame['position'], previous['position'])
            if dt > settings['max_gap_ms'] or distance > settings['max_speed_mps'] * dt / 1000 + settings['teleport_slack_m']:
                review.append('telemetry_gap' if dt > settings['max_gap_ms'] else 'teleport_detected')
                previous_u = progress = None
                previous = frame
                result.append((frame, None))
                continue
        distance, s = _project(frame['position'], route)
        if distance > settings['max_route_projection_m']:
            review.append('route_projection_uncertain')
            previous_u = progress = None
            result.append((frame, None))
            previous = frame
            continue
        u = s if route['direction'] == 1 else route['length'] - s
        if route['closed']:
            u %= route['length']
        if previous_u is None:
            progress = u
        else:
            delta = u - previous_u
            if route['closed']:
                delta = (delta + route['length'] / 2) % route['length'] - route['length'] / 2
            progress += delta
        previous_u = u
        result.append((frame, progress))
        previous = frame
    return result


def _detect_slaloms(samples, config, settings, lap_id, events, review):
    slaloms = config.get('slaloms')
    if not isinstance(slaloms, dict):
        review.append('slalom_geometry_missing')
        return
    sections = slaloms.get('sections', [])
    if not isinstance(sections, list) or len(sections) > 100:
        raise TrajectoryError('invalid slalom sections')
    if not slaloms.get('calibrated'):
        if sections:
            review.append('slalom_geometry_uncalibrated')
        return
    route = _route_data(config)
    if route is None:
        review.append('route_geometry_missing')
        return
    progress_frames = _trajectory_progress(samples, route, settings, review)
    # Candidate crossings are computed once per adjacent telemetry pair and
    # checked against each calibrated section's ordered gate list.
    crossing_cache = {}
    for section in sections:
        if not isinstance(section, dict):
            raise TrajectoryError('invalid slalom section')
        slalom_id = section.get('id')
        if not isinstance(slalom_id, str) or not slalom_id or len(slalom_id) > 80:
            raise TrajectoryError('invalid slalom id')
        start_s = _finite(section.get('start_s_m'), 'slalom.start_s_m')
        end_s = _finite(section.get('end_s_m'), 'slalom.end_s_m')
        if not 0 <= start_s <= route['length'] or not 0 <= end_s <= route['length']:
            raise TrajectoryError('slalom bounds outside route')
        gates = section.get('gates')
        if not isinstance(gates, list) or not 1 <= len(gates) <= 64:
            raise TrajectoryError('invalid slalom gates')
        gate_ids, parsed = set(), []
        for gate in gates:
            if not isinstance(gate, dict):
                raise TrajectoryError('invalid slalom gate')
            gate_id = gate.get('id')
            if not isinstance(gate_id, str) or not gate_id or gate_id in gate_ids:
                raise TrajectoryError('invalid or duplicate gate id')
            gate_ids.add(gate_id)
            a, b = gate.get('a'), gate.get('b')
            if not isinstance(a, (list, tuple)) or len(a) != 2 or not isinstance(b, (list, tuple)) or len(b) != 2:
                raise TrajectoryError('invalid gate endpoints')
            a, b = (_finite(a[0], 'gate.x'), _finite(a[1], 'gate.z')), (_finite(b[0], 'gate.x'), _finite(b[1], 'gate.z'))
            direction = gate.get('direction')
            if direction not in (-1, 1) or math.dist(a, b) <= 1e-6:
                raise TrajectoryError('invalid gate direction or length')
            parsed.append({'id': gate_id, 'a': a, 'b': b, 'direction': direction})
        start_u = start_s if route['direction'] == 1 else route['length'] - start_s
        end_u = end_s if route['direction'] == 1 else route['length'] - end_s
        if route['closed']:
            start_u %= route['length']
            end_u %= route['length']
            if end_u <= start_u:
                end_u += route['length']
        elif end_u <= start_u:
            raise TrajectoryError('slalom end must follow start')
        valid_progress = [p for _, p in progress_frames if p is not None]
        if not valid_progress:
            review.append('slalom_progress_unavailable:' + slalom_id)
            continue
        lo, hi = min(valid_progress), max(valid_progress)
        candidates = []
        if route['closed']:
            first_lap = math.floor((lo - end_u) / route['length'])
            last_lap = math.ceil((hi - start_u) / route['length'])
            for lap in range(first_lap, last_lap + 1):
                a, b = start_u + lap * route['length'], end_u + lap * route['length']
                if lo <= a and hi >= b:
                    candidates.append((a, b))
        elif lo <= start_u and hi >= end_u:
            candidates.append((start_u, end_u))
        if not candidates:
            review.append('slalom_section_incomplete:' + slalom_id)
            continue
        section_start, section_end = candidates[0]
        crossings = []
        for gate in parsed:
            cache_key = (gate['id'], gate['a'], gate['b'])
            if cache_key not in crossing_cache:
                found = []
                for index, (left, right) in enumerate(zip(samples, samples[1:])):
                    intersection = _gate_crossing(left, right, gate)
                    if intersection is None:
                        continue
                    u, direction, point = intersection
                    p0, p1 = progress_frames[index][1], progress_frames[index + 1][1]
                    if p0 is None or p1 is None:
                        continue
                    p = p0 + (p1 - p0) * u
                    found.append({'gate_id': gate['id'], 'direction': direction, 'progress': p,
                                  'timestamp_ms': round(left['t_ms'] + (right['t_ms'] - left['t_ms']) * u),
                                  'position': point, 'expected_direction': gate['direction']})
                crossing_cache[cache_key] = found
            crossings.extend(c for c in crossing_cache[cache_key]
                             if section_start - 1e-6 <= c['progress'] <= section_end + 1e-6)
        crossings.sort(key=lambda c: c['timestamp_ms'])
        actual_ids = [c['gate_id'] for c in crossings]
        expected_ids = [gate['id'] for gate in parsed]
        wrong_direction = [c['gate_id'] for c in crossings if c['direction'] != c['expected_direction']]
        for crossing in crossings:
            events.append({'type': 'slalom_gate_crossing', 'lap_id': lap_id, 'slalom_id': slalom_id,
                           'gate_id': crossing['gate_id'], 'timestamp_ms': crossing['timestamp_ms'],
                           'position': {'x': crossing['position'][0], 'z': crossing['position'][1]},
                           'direction': crossing['direction'], 'expected_direction': crossing['expected_direction'],
                           'penalty_ms': 0, 'penalizable': False,
                           'evidence': {'method': 'oriented_segment_intersection'}})
        if actual_ids == expected_ids and not wrong_direction:
            continue
        reasons = {'missing_gates': [gate_id for gate_id in expected_ids if gate_id not in actual_ids]}
        if wrong_direction:
            reasons['wrong_direction'] = wrong_direction
        if actual_ids != expected_ids:
            reasons['wrong_order'] = {'expected': expected_ids, 'observed': actual_ids}
        first = crossings[0] if crossings else None
        timestamp = first['timestamp_ms'] if first else next(
            (frame['t_ms'] for frame, p in progress_frames if p is not None and p >= section_start), samples[0]['t_ms'])
        position = first['position'] if first else samples[0]['position']
        events.append({'type': 'slalom_violation', 'lap_id': lap_id, 'slalom_id': slalom_id,
                       'timestamp_ms': timestamp, 'position': {'x': position[0], 'z': position[1]},
                       'reasons': reasons, 'penalizable': settings['enable_slalom'],
                       'penalty_ms': settings['slalom_penalty_ms'] if settings['enable_slalom'] else 0,
                       'incident_id': f'slalom:{lap_id}:{slalom_id}',
                       'evidence': {'method': 'ordered_calibrated_gate_crossings',
                                    'expected_gates': expected_ids, 'observed_gates': actual_ids,
                                    'section_progress_m': [section_start, section_end]}})



def _deduplicate_incidents(events):
    off_course = [event for event in events if event['type'] == 'off_course' and event['penalty_ms'] > 0]
    for slalom in (event for event in events if event['type'] == 'slalom_violation' and event['penalty_ms'] > 0):
        interval = slalom.get('evidence', {}).get('section_progress_m')
        if not isinstance(interval, list) or len(interval) != 2:
            continue
        a, b = min(interval), max(interval)
        matches = []
        for event in off_course:
            start, end = event.get('s_exit_m'), event.get('s_reentry_m')
            if type(start) not in (int, float) or type(end) not in (int, float):
                continue
            overlap = min(b, max(start, end)) - max(a, min(start, end))
            if overlap > 0:
                matches.append((overlap, event))
        if matches:
            _, related = max(matches, key=lambda pair: (pair[0], pair[1]['timestamp_ms']))
            slalom['penalizable'] = False
            slalom['penalty_ms'] = 0
            slalom['incident_id'] = related['incident_id']
            slalom['evidence']['deduplicated_with'] = related['incident_id']
            slalom['evidence']['deduplication_rule'] = 'overlapping_off_course_progress_interval'


def validate_config(config, settings=None):
    """Structurally validate track geometry without claiming physical commissioning."""
    trace = {'protocol': PROTOCOL,
             'lap': {'station_id': 'CAL', 'session_id': 'CAL', 'lap_id': 'CAL', 'raw_ms': 1000},
             'samples': [
                 {'t_ms': 0, 'position': {'x': 0.0, 'z': 0.0}, 'forward': {'x': 1.0, 'z': 0.0}, 'velocity': {'x': 1.0, 'z': 0.0}},
                 {'t_ms': 100, 'position': {'x': 0.1, 'z': 0.0}, 'forward': {'x': 1.0, 'z': 0.0}, 'velocity': {'x': 1.0, 'z': 0.0}},
             ]}
    return analyze_trajectory(trace, config, settings)


def analyze_trajectory(trace, config, settings=None):
    """Analyze one lap and return deterministic events and integer-millisecond totals."""
    identity, raw_ms, samples, vehicle = _validate(trace, config)
    cfg = _settings(settings)
    events, review = [], []
    _detect_cones(samples, vehicle, config, cfg, identity['lap_id'], events, review)
    _detect_direct_cone_contacts(trace, config, cfg, identity['lap_id'], samples, events, review)
    _detect_off_track(samples, vehicle, config, cfg, identity['lap_id'], events, review)
    _detect_slaloms(samples, config, cfg, identity['lap_id'], events, review)
    _deduplicate_incidents(events)
    counts = {'touched_cones': sum(event['type'] == 'cone_contact' for event in events),
              'off_course_events': len({event['incident_id'] for event in events
                                        if event['type'] in ('off_course', 'slalom_violation')}),
              'missed_slaloms': sum(event['type'] == 'slalom_violation' for event in events)}
    penalty_ms = scoring.corrected_ms_v2(raw_ms, events, cfg)
    if penalty_ms is None:
        raise TrajectoryError('V2 scoring rejected detector evidence')
    final_ms = penalty_ms
    penalty_ms -= raw_ms
    return {
        'protocol': PROTOCOL, 'detector_version': DETECTOR_VERSION, 'track': config['track'],
        'lap': identity, 'raw_ms': raw_ms, 'events': events,
        'penalty_counts': counts, 'penalty_ms': penalty_ms, 'final_ms': final_ms,
        'review_required': bool(review), 'review_reasons': sorted(set(review)),
    }
