"""Conservative cyclic lateral-lattice planner; no actuator or timing imports."""
from dataclasses import dataclass, asdict
import math
import numpy as np
from shapely.geometry import Point, LineString, Polygon
from shapely.ops import unary_union
from track.geometry import _segment_hit


@dataclass(frozen=True)
class Vehicle:
    half_width: float = 0.706
    half_length: float = 1.52  # conservatively encloses host-source collider; wheels unverified
    wheelbase: float = 1.6  # provisional, must be measured before AC operation
    margin: float = 0.20
    max_steer: float = 0.60
    lateral_accel: float = 2.0
    braking: float = 2.0
    acceleration: float = 1.5
    max_speed: float = 6.0


def obstacles(world, extra=0):
    # footprint_m is max X/Z cluster extent, hence use circumscribed base radius.
    return [Point(c['x'],c['z']).buffer(c['footprint_m']/math.sqrt(2)+extra, quad_segs=16)
            for c in world.cones]


def resample(points, step=1.0):
    p=np.asarray(points); q=np.vstack([p,p[0]])
    lengths=np.linalg.norm(np.diff(q,axis=0),axis=1)
    if np.any(lengths<1e-8): raise ValueError('duplicate path station')
    s=np.r_[0,np.cumsum(lengths)]; u=np.linspace(0,s[-1],max(8,math.ceil(s[-1]/step)),endpoint=False)
    return np.column_stack([np.interp(u,s,q[:,k]) for k in (0,1)])


def curvature(points):
    p=np.asarray(points); a=p-np.roll(p,1,axis=0); b=np.roll(p,-1,axis=0)-p
    denom=np.linalg.norm(a,axis=1)*np.linalg.norm(b,axis=1)*np.linalg.norm(a+b,axis=1)
    return 2*(a[:,0]*b[:,1]-a[:,1]*b[:,0])/np.maximum(denom,1e-10)


def speed_profile(points, vehicle):
    p=np.asarray(points); ds=np.linalg.norm(np.roll(p,-1,axis=0)-p,axis=1)
    speed=np.minimum(vehicle.max_speed,np.sqrt(vehicle.lateral_accel/np.maximum(abs(curvature(p)),1e-6)))
    for _ in range(5):
        for i in range(len(p)-1,-1,-1): speed[i]=min(speed[i],math.sqrt(speed[(i+1)%len(p)]**2+2*vehicle.braking*ds[i]))
        for i in range(len(p)): speed[(i+1)%len(p)]=min(speed[(i+1)%len(p)],math.sqrt(speed[i]**2+2*vehicle.acceleration*ds[i]))
    return speed.tolist()


def footprint(x,z,heading,vehicle):
    c,s=math.cos(heading),math.sin(heading)
    return Polygon([(x+c*f-s*l,z+s*f+c*l) for f,l in
                    [(-vehicle.half_length,-vehicle.half_width),(vehicle.half_length,-vehicle.half_width),
                     (vehicle.half_length,vehicle.half_width),(-vehicle.half_length,vehicle.half_width)]])


def validate_path(world, points, vehicle=Vehicle()):
    p=np.asarray(points); contacts=set(); margin_conflicts=set(); off=[]; mind=math.inf
    obs=obstacles(world); region=world.road.buffer(-vehicle.margin)
    from shapely.strtree import STRtree
    tree=STRtree(obs)
    for i,(a,b) in enumerate(zip(p,np.roll(p,-1,axis=0))):
        h=math.atan2(*(b-a)[::-1])
        previous=a-p[(i-1)%len(p)]; before=math.atan2(previous[1],previous[0])
        rotation=abs((h-before+math.pi)%(2*math.pi)-math.pi)
        sagitta=math.hypot(vehicle.half_length,vehicle.half_width)*(1-math.cos(rotation/2))
        body=unary_union([footprint(a[0],a[1],before,vehicle),footprint(a[0],a[1],h,vehicle),
                          footprint(b[0],b[1],h,vehicle)]).convex_hull.buffer(sagitta)
        if not region.covers(body): off.append(i)
        if obs:
            mind=min(mind,body.distance(obs[int(tree.nearest(body))]))
            contacts.update(int(j) for j in tree.query(body,predicate='intersects'))
            margin_conflicts.update(int(j) for j in tree.query(body.buffer(vehicle.margin),predicate='intersects'))
    gate_hits=[]
    for g in world.gates:
        a,b=([g[k]['x'],g[k]['z']] for k in ('a','b'))
        hits=[(i,_segment_hit(x,y,a,b)) for i,(x,y) in enumerate(zip(p,np.roll(p,-1,axis=0)))]
        gate_hits.append([{'segment':i,'direction':v} for i,v in hits if v is not None])
    gate_ok=all(len(h)==1 and h[0]['direction']==g['direction'] for h,g in zip(gate_hits,world.gates))
    return {'cone_contacts':sorted(contacts),'cone_margin_conflicts':sorted(margin_conflicts),'offtrack_segments':off,'min_cone_clearance_m':None if not obs else mind,
            'gate_hits':gate_hits,'gates_ok':gate_ok,
            'max_curvature':float(max(abs(curvature(p))))}


def smooth_path(world, points, vehicle=Vehicle(), iterations=60):
    """Elastic smoothing with checked adjacent swept rectangles; no unchecked output."""
    from shapely.prepared import prep
    road=prep(world.road.buffer(-vehicle.margin-.30)); blocked=prep(unary_union(obstacles(world,vehicle.margin+.30)))
    p=np.asarray(points).copy(); anchor=p.copy(); n=len(p)
    for _ in range(iterations):
        for i in range(n):
            candidate=p[i]+.35*(p[(i-1)%n]+p[(i+1)%n]-2*p[i])+.015*(anchor[i]-p[i])
            safe=True
            for a,b in [(p[(i-1)%n],candidate),(candidate,p[(i+1)%n])]:
                h=math.atan2(b[1]-a[1],b[0]-a[0])
                shape=unary_union([footprint(a[0],a[1],h,vehicle),footprint(b[0],b[1],h,vehicle)]).convex_hull
                if not road.covers(shape) or blocked.intersects(shape):safe=False;break
            if safe:p[i]=candidate
    return p


def plan(world, vehicle=Vehicle(), step=1., lateral_extent=3., lateral_step=.3):
    p=resample(world.reference,step); n=len(p)
    tangent=np.roll(p,-1,axis=0)-np.roll(p,1,axis=0)
    tangent/=np.linalg.norm(tangent,axis=1)[:,None]
    normal=np.column_stack([-tangent[:,1],tangent[:,0]])
    offsets=np.arange(-lateral_extent,lateral_extent+lateral_step/2,lateral_step)
    nodes=p[:,None,:]+normal[:,None,:]*offsets[None,:,None]
    from shapely.prepared import prep
    road=prep(world.road.buffer(-vehicle.margin-.30))
    blocked=prep(unary_union(obstacles(world,vehicle.margin+.30)))
    def safe_shape(shape):
        return road.covers(shape) and not blocked.intersects(shape)
    def safe_edge(a,b):
        h=math.atan2(b[1]-a[1],b[0]-a[0])
        shape=unary_union([footprint(a[0],a[1],h,vehicle),footprint(b[0],b[1],h,vehicle)]).convex_hull
        return safe_shape(shape)
    valid=np.array([[safe_shape(footprint(x[0],x[1],math.atan2(t[1],t[0]),vehicle)) for x in row]
                    for row,t in zip(nodes,tangent)])
    violations=[]
    if np.any(~valid.any(axis=1)):
        violations.append('no_safe_lateral_station')
        out=p
    else:
        start=min(np.flatnonzero(valid[0]),key=lambda j:abs(offsets[j]))
        cost=np.full(len(offsets),math.inf);cost[start]=0
        parents=[]
        for i in range(1,n):
            new=np.full(len(offsets),math.inf); par=np.full(len(offsets),-1,dtype=int)
            for j in np.flatnonzero(valid[i]):
                choices=sorted(np.flatnonzero(np.isfinite(cost)),key=lambda k:cost[k]+8*(offsets[j]-offsets[k])**2)
                for k in choices:
                    if abs(offsets[j]-offsets[k])>0.65: continue
                    if safe_edge(nodes[i-1,k],nodes[i,j]):
                        new[j]=cost[k]+8*(offsets[j]-offsets[k])**2+0.02*offsets[j]**2; par[j]=k; break
            cost=new;parents.append(par)
        candidates=[j for j in np.flatnonzero(np.isfinite(cost))
                    if abs(offsets[j]-offsets[start])<.65 and safe_edge(nodes[-1,j],nodes[0,start])]
        if not candidates:
            violations.append('no_cyclic_lattice_solution');out=p
        else:
            j=min(candidates,key=lambda j:cost[j]+8*(offsets[j]-offsets[start])**2); indices=[j]
            for par in parents[::-1]: j=int(par[j]);indices.append(j)
            indices.reverse();out=nodes[np.arange(n),indices]
    checks=validate_path(world,out,vehicle)
    if not violations:
        smoothed=smooth_path(world,out,vehicle)
        candidate_checks=validate_path(world,smoothed,vehicle)
        severity=lambda c:(len(c['offtrack_segments']),not c['gates_ok'],len(c['cone_contacts']),len(c['cone_margin_conflicts']),c['max_curvature'])
        if severity(candidate_checks)<severity(checks):
            out=smoothed;checks=candidate_checks
    refinement=[]
    from cleanlap.validation import slalom_evidence
    audit=slalom_evidence(out.tolist(),world.slaloms,closed=True)
    if not violations and world.slaloms and (checks['cone_margin_conflicts'] or
            checks['max_curvature']>math.tan(vehicle.max_steer)/vehicle.wheelbase or not audit['candidate_sequence_ok']):
        from cleanlap.optimization import refine
        candidate,refinement=refine(world,out,vehicle)
        candidate_checks=validate_path(world,candidate,vehicle)
        candidate_audit=slalom_evidence(candidate.tolist(),world.slaloms,closed=True)
        quality=lambda c,a:(len(c['offtrack_segments'])+len(c['cone_contacts']),
                            sum(len(r['issues']) for r in a['runs']),len(c['cone_margin_conflicts']),c['max_curvature'])
        if quality(candidate_checks,candidate_audit)<quality(checks,audit):
            out=candidate;checks=candidate_checks
    if checks['cone_margin_conflicts']: violations.append('footprint_cone_margin_conflict')
    if checks['offtrack_segments']: violations.append('footprint_outside_road')
    if not checks['gates_ok']: violations.append('timing_gate_crossing_invalid')
    if checks['max_curvature']>math.tan(vehicle.max_steer)/vehicle.wheelbase: violations.append('steering_curvature_limit')
    from cleanlap.validation import slalom_evidence
    slalom=slalom_evidence(out.tolist(),world.slaloms,closed=True)
    if world.slaloms and not slalom['candidate_sequence_ok']: violations.append('slalom_candidate_sequence_invalid')
    return {'schema':'cleanlap-plan-v1','geometry_sha256':world.digest,'points':out.tolist(),
            'speeds':speed_profile(out,vehicle),'vehicle':asdict(vehicle),'checks':checks,
            'violations':violations,'geometric_clear':not violations,'authorized':False,
            'unresolved':world.unresolved,'refinement_trials':refinement,'slalom_candidates':world.slaloms,'slalom_audit':slalom}
