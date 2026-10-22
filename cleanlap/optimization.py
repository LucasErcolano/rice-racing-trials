"""Offline periodic constrained refinement; independent full-sweep audit is authoritative."""
import itertools
import math
import numpy as np


def refine(world, points, vehicle):
    """Try both legal phases per inferred run; optimize the entire periodic loop.

    Soft constraints guide search, never authorize output. Every returned candidate
    must still pass planning.validate_path and the independent finite-gate audit.
    """
    from scipy.optimize import least_squares
    from scipy.sparse import lil_matrix
    from scipy.spatial import cKDTree
    import shapely
    from cleanlap.planning import curvature, validate_path
    from cleanlap.validation import slalom_evidence

    anchor=np.asarray(points); n=len(anchor)
    tangent=np.roll(anchor,-1,axis=0)-np.roll(anchor,1,axis=0)
    tangent/=np.linalg.norm(tangent,axis=1)[:,None]
    normal=np.column_stack([-tangent[:,1],tangent[:,0]])
    road=world.road.buffer(-vehicle.margin-.07)
    cones=np.array([[c['x'],c['z']] for c in world.cones],dtype=float).reshape(-1,2)
    radii=np.array([c['footprint_m']/math.sqrt(2)+vehicle.margin+.10 for c in world.cones])
    pairs=[(i,j) for i,ids in enumerate(cKDTree(cones).query_ball_point(anchor,6.)) for j in ids] if len(cones) else []
    pi=np.array([a for a,b in pairs],dtype=int); pj=np.array([b for a,b in pairs],dtype=int)
    gate_data=[]
    for run in world.slaloms:
        group=[]
        for gate in run['gates']:
            origin=np.array([gate['x'],gate['z']]); t=np.array([gate['tx'],gate['tz']])
            before=(anchor-origin)@t; after=np.roll(before,-1)
            choices=np.flatnonzero((before<0)&(after>=0))
            i=min(choices,key=lambda j:np.linalg.norm(anchor[j]-origin))
            group.append((int(i),origin,t))
        gate_data.append(group)
    limit=math.tan(vehicle.max_steer)/vehicle.wheelbase
    # Each residual depends on only local stations. Sparse differences preserve
    # periodic heading/curvature coupling without a dense N-by-N numerical solve.
    dependencies=[]
    for i in range(n): dependencies.extend([[i]]+[[i-1,i,i+1]]*2)
    for i in pi: dependencies.extend([[i-1,i,i+1]]*3)
    for i in range(n): dependencies.extend([[i-1,i,i+1]]*12)
    for group in gate_data:
        for i,_,_ in group: dependencies.append([i,i+1])
    sparse=lil_matrix((len(dependencies),n),dtype=int)
    for row,ids in enumerate(dependencies): sparse[row,np.array(ids)%n]=1
    sparse=sparse.tocsr()
    corners=np.array([[-vehicle.half_length,-vehicle.half_width],[-vehicle.half_length,vehicle.half_width],
                      [vehicle.half_length,vehicle.half_width],[vehicle.half_length,-vehicle.half_width]])

    def residual(offset,phases):
        p=anchor+normal*offset[:,None]
        delta=np.roll(p,-1,axis=0)-p; direction=delta/np.linalg.norm(delta,axis=1)[:,None]
        previous=np.roll(direction,1,axis=0)
        centers=np.stack([p,p,np.roll(p,-1,axis=0)],axis=1)
        directions=np.stack([direction,previous,direction],axis=1)
        normals=np.stack([-directions[:,:,1],directions[:,:,0]],axis=-1)
        smooth=np.sum((np.roll(p,1,axis=0)+np.roll(p,-1,axis=0)-2*p)*normal,axis=1)
        base=np.column_stack([.015*offset,.8*smooth,60*np.maximum(abs(curvature(p))-limit*.92,0)]).ravel()
        xy=centers[:,:,None,:]+directions[:,:,None,:]*corners[None,None,:,0,None]+normals[:,:,None,:]*corners[None,None,:,1,None]
        if len(pi):
            hulls=shapely.convex_hull(shapely.multipoints(xy.reshape(n,12,2)))
            queries=shapely.points(cones[pj])
            signed=shapely.distance(hulls[pi],queries)
            inside=shapely.covers(hulls[pi],queries)
            signed=np.where(inside,-shapely.distance(shapely.boundary(hulls[pi]),queries),signed)
            rotation=np.arccos(np.clip(np.sum(direction*previous,axis=1),-1,1))
            sagitta=math.hypot(vehicle.half_length,vehicle.half_width)*(1-np.cos(rotation/2))
            obstacle=np.repeat(100*np.maximum(radii[pj]+sagitta[pi]-signed,0),3)
        else: obstacle=np.array([])
        pts=shapely.points(xy.reshape(-1,2))
        outside=~shapely.covers(road,pts)
        road_error=60*np.where(outside,shapely.distance(pts,road),0)
        gates=[]
        for phase,group in zip(phases,gate_data):
            for k,(i,origin,t) in enumerate(group):
                a=p[i]; b=p[(i+1)%n]; den=(b-a)@t
                u=np.clip(-((a-origin)@t)/(den if abs(den)>1e-8 else 1e-8),0,1)
                hit=a+u*(b-a); lat=np.dot(hit-origin,[-t[1],t[0]])
                gates.append(60*max(1.45-phase*(-1)**k*lat,0))
        return np.r_[base,obstacle,road_error,gates]

    preferred=[]
    for group in gate_data:
        votes=[]
        for k,(i,o,t) in enumerate(group): votes.append(np.dot(anchor[i]-o,[-t[1],t[0]])*(-1)**k)
        preferred.append(1 if sum(votes)>=0 else -1)
    phases=list(itertools.product((-1,1),repeat=len(gate_data)))
    phases.sort(key=lambda x:sum(a!=b for a,b in zip(x,preferred)))
    best=anchor; best_score=None; trials=[]
    for phase in phases:
        seed=np.zeros(n)
        for ph,group in zip(phase,gate_data):
            for k,(i,o,t) in enumerate(group):
                lat=np.dot(anchor[i]-o,[-t[1],t[0]])
                target=ph*(-1)**k*1.7
                projection=np.dot(normal[i],[-t[1],t[0]])
                if abs(projection)>.4:
                    distance=np.minimum((np.arange(n)-i)%n,(i-np.arange(n))%n)
                    seed+=(target-lat)/projection*np.exp(-.5*(distance/3.)**2)
        result=least_squares(residual,np.clip(seed,-3.5,3.5),args=(phase,),jac_sparsity=sparse,
                             bounds=(-4.,4.),max_nfev=100,ftol=1e-5,xtol=1e-5,gtol=1e-5)
        candidate=anchor+normal*result.x[:,None]
        check=validate_path(world,candidate,vehicle)
        audit=slalom_evidence(candidate.tolist(),world.slaloms,closed=True)
        score=(len(check['cone_contacts'])+len(check['offtrack_segments']),
               sum(len(r['issues']) for r in audit['runs']),len(check['cone_margin_conflicts']),
               max(0,check['max_curvature']-limit),check['max_curvature'])
        trials.append({'phases':list(phase),'nfev':result.nfev,'cost':float(result.cost),'score':list(score)})
        if best_score is None or score<best_score: best=candidate;best_score=score
        if score[:4]==(0,0,0,0):break
    return best,trials
