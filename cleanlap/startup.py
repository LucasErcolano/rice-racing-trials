"""Open pit connector verification: never treats unsurveyed space as road."""
import math
import numpy as np
from shapely.ops import unary_union
from cleanlap.control import wrap
from cleanlap.planning import footprint, obstacles


def verify_connector(world, points, vehicle, initial_heading, merge_heading):
    p = np.asarray(points, dtype=float)
    reasons = []
    if p.ndim != 2 or p.shape[1] != 2 or len(p) < 3 or not np.isfinite(p).all():
        return {'safe': False, 'reasons': ['invalid_points']}
    d = np.diff(p, axis=0)
    ds = np.linalg.norm(d, axis=1)
    if np.any(ds < .01) or np.any(ds > .5):
        return {'safe': False, 'reasons': ['connector_spacing_0.01_to_0.5m_required']}
    headings = np.arctan2(d[:,1], d[:,0])
    if abs(wrap(headings[0]-initial_heading)) > .1 or abs(wrap(headings[-1]-merge_heading)) > .1:
        reasons.append('heading_mismatch')
    turns = np.array([wrap(b-a) for a,b in zip(headings, headings[1:])])
    k = float(max(abs(turns / ((ds[:-1]+ds[1:])/2)), default=0.))
    if k > math.tan(vehicle.max_steer)/vehicle.wheelbase:
        reasons.append('curvature')
    region = world.road.buffer(-vehicle.margin)
    blocked = unary_union(obstacles(world, vehicle.margin))
    before = initial_heading
    for i,h in enumerate(headings):
        radius = math.hypot(vehicle.half_length,vehicle.half_width)
        sweep = unary_union([footprint(*p[i],before,vehicle), footprint(*p[i],h,vehicle),
                             footprint(*p[i+1],h,vehicle)]).convex_hull.buffer(radius*(1-math.cos(wrap(h-before)/2)))
        if not region.covers(sweep):
            reasons.append('outside_verified_road')
        if blocked.intersects(sweep):
            reasons.append('cone_envelope')
        before = h
    return {'safe': not reasons, 'reasons': sorted(set(reasons)), 'max_curvature': k,
            'geometry_sha256': world.digest}


def hermite_connector(world, start, heading, target, target_heading, vehicle):
    """Bounded candidate, not a completeness proof; rejection requires manual reposition."""
    a,b = np.asarray(start,float),np.asarray(target,float)
    length = float(np.linalg.norm(b-a))
    if not .5 <= length <= 50:
        raise ValueError('connector distance outside verified search bounds')
    tangent_a = length*np.array([math.cos(heading),math.sin(heading)])
    tangent_b = length*np.array([math.cos(target_heading),math.sin(target_heading)])
    t = np.linspace(0,1,max(8,math.ceil(length/.15)))[:,None]
    points = (2*t**3-3*t**2+1)*a+(t**3-2*t**2+t)*tangent_a+(-2*t**3+3*t**2)*b+(t**3-t**2)*tangent_b
    report = verify_connector(world,points,vehicle,heading,target_heading)
    if not report['safe']:
        raise ValueError('unsafe pit connector: '+','.join(report['reasons']))
    return points.tolist(), report
