"""Feedback controller and explicitly synthetic kinematic-bicycle evaluation."""
from dataclasses import dataclass
import math
import numpy as np
from shapely.geometry import Point
from shapely.ops import unary_union
from cleanlap.planning import Vehicle, footprint, obstacles
from track.geometry import _segment_hit


def wrap(angle):
    return (angle+math.pi)%(2*math.pi)-math.pi


@dataclass
class State:
    x: float
    z: float
    heading: float
    speed: float
    timestamp: float


class Controller:
    def __init__(self, plan, simulation=False):
        self.plan=plan; self.points=np.asarray(plan['points']); self.speeds=np.asarray(plan['speeds'])
        self.vehicle=Vehicle(**plan['vehicle']); self.simulation=simulation
        self.segment=0; self.progress=0.; self.last_t=None; self.stop_reason=None;self.last_steer=0.
        self.ds=np.linalg.norm(np.roll(self.points,-1,axis=0)-self.points,axis=1)
        self.arc=np.r_[0,np.cumsum(self.ds)];self.length=float(self.arc[-1]);self.stationary_since=None

    def update(self, state, now):
        result={'steer':0.,'throttle':0.,'brake':1.,'mode':'HOLD','reason':'geometry_unresolved',
                'progress':self.progress,'reference_x':None,'reference_z':None,'lateral_error':0.,
                'heading_error':0.,'target_speed':0.}
        if not self.simulation: return result  # no live path exists; authorization is not an input flag
        if not self.plan['geometric_clear']:
            self.stop_reason='unsafe_plan'
        values=[state.x,state.z,state.heading,state.speed,state.timestamp,now]
        if not all(math.isfinite(v) for v in values) or state.speed<0:
            self.stop_reason='invalid_state'
        if now-state.timestamp>0.15 or state.timestamp>now+0.01:
            self.stop_reason='stale_state'
        if self.last_t is not None and not 0<state.timestamp-self.last_t<=0.15:
            self.stop_reason='timestamp_discontinuity'
        dt=.02 if self.last_t is None else max(.001,state.timestamp-self.last_t)
        self.last_t=state.timestamp
        if self.stop_reason:
            return {**result,'mode':'STOP','reason':self.stop_reason}
        pos=np.array([state.x,state.z]); n=len(self.points)
        # Local causal projection prevents jumps to a nearby parallel section.
        candidates=range(max(0,self.segment-2),self.segment+min(n//3,16))
        choices=[]
        for absolute in candidates:
            i=absolute%n;a=self.points[i];d=self.points[(i+1)%n]-a
            u=float(np.clip(np.dot(pos-a,d)/np.dot(d,d),0,1));ref=a+u*d
            choices.append((float(np.linalg.norm(pos-ref)),absolute,u,ref,d))
        distance,absolute,u,ref,d=min(choices,key=lambda x:x[0])
        if absolute<self.segment-1 or absolute>self.segment+10:
            self.stop_reason='progress_jump'
        self.segment=max(self.segment,absolute)
        s=(absolute//n)*self.length+self.arc[absolute%n]+u*self.ds[absolute%n]
        self.progress=max(self.progress,s)
        lateral=float(np.cross(np.r_[d,0],np.r_[pos-ref,0])[2]/np.linalg.norm(d))
        heading_error=wrap(math.atan2(d[1],d[0])-state.heading)
        if distance>1.5 or abs(heading_error)>1.2:
            self.stop_reason='tracking_envelope_exceeded'
        if state.speed<.15:
            if self.stationary_since is None:self.stationary_since=now
            elif now-self.stationary_since>8:self.stop_reason='stuck'
        else:self.stationary_since=None
        if self.stop_reason:return {**result,'mode':'STOP','reason':self.stop_reason}
        lookahead=max(1.8,min(4.0,1.0+.45*state.speed))
        target_s=(s+lookahead)%self.length
        target=np.array([np.interp(target_s,self.arc,np.r_[self.points[:,k],self.points[0,k]]) for k in (0,1)])
        delta=target-pos; alpha=wrap(math.atan2(delta[1],delta[0])-state.heading)
        requested=math.atan2(2*self.vehicle.wheelbase*math.sin(alpha),max(np.linalg.norm(delta),.1))
        requested=float(np.clip(requested,-self.vehicle.max_steer,self.vehicle.max_steer))
        steer=float(np.clip(requested,self.last_steer-1.0*dt,self.last_steer+1.0*dt));self.last_steer=steer
        target_speed=float(min(self.speeds[(absolute+q)%n] for q in range(5)))
        error=target_speed-state.speed
        throttle=min(1.,max(0.,.8*error));brake=min(1.,max(0.,-.6*error))
        return {**result,'steer':steer,'throttle':throttle,'brake':brake,'mode':'RUNNING','reason':None,
                'progress':self.progress,'reference_x':float(ref[0]),'reference_z':float(ref[1]),
                'lateral_error':lateral,'heading_error':heading_error,'target_speed':target_speed}


def simulate(world, plan, laps=1, lateral_offset=0., heading_offset=0., dt=.02, max_seconds=900.,
             steering_gain=1., acceleration_gain=1., diagnostic_unsafe=False):
    if plan.get('geometry_sha256')!=world.digest:raise ValueError('world/plan identity mismatch')
    if not 0<dt<=.05 or laps<1 or max_seconds<=0: raise ValueError('invalid simulation bounds')
    controller=Controller({**plan,'geometric_clear':True} if diagnostic_unsafe else plan,simulation=True); vehicle=controller.vehicle
    p=np.asarray(plan['points']);d=p[1]-p[0];h=math.atan2(d[1],d[0])
    state=State(float(p[0,0]-math.sin(h)*lateral_offset),float(p[0,1]+math.cos(h)*lateral_offset),h+heading_offset,0.,0.)
    obs=obstacles(world);region=world.road.buffer(-vehicle.margin)
    from cleanlap.validation import GateTracker
    from track.geometry import side
    g=world.gates[0];a,b=([g[key]['x'],g[key]['z']] for key in ('a','b'))
    seed=abs(side([state.x,state.z],a,b))<1e-8
    tracker=GateTracker([g['direction'] for g in world.gates],controller.length,seeded=seed)
    rows=[]; contacts=set();margin_conflicts=set();off=0;events=[];lap_count=0
    minclear=math.inf;last_body=footprint(state.x,state.z,state.heading,vehicle)
    for k in range(math.ceil(max_seconds/dt)):
        command=controller.update(state,now=state.timestamp)
        body=footprint(state.x,state.z,state.heading,vehicle)
        # Convex sweep plus rotation sagitta bound over the integration interval.
        rotation=abs(wrap(state.heading-(rows[-1]['heading'] if rows else state.heading)))
        sweep=unary_union([body,last_body]).convex_hull.buffer(math.hypot(vehicle.half_length,vehicle.half_width)*(1-math.cos(rotation/2)))
        last_body=body
        nearby=[];current_contacts=[]
        for j,o in enumerate(obs):
            # avoid costly full polygon distance for clearly distant candidates
            c=world.cones[j]
            if math.hypot(c['x']-state.x,c['z']-state.z)>10+c['footprint_m']:continue
            dist=sweep.distance(o);nearby.append(dist);minclear=min(minclear,dist)
            if sweep.intersects(o):contacts.add(j);current_contacts.append(j)
            if sweep.intersects(o.buffer(vehicle.margin)):margin_conflicts.add(j)
        outside=not region.covers(sweep);off+=int(outside)
        if rows:
            previous=[rows[-1]['x'],rows[-1]['z']];current=[state.x,state.z]
            for j,g in enumerate(world.gates):
                a,b=([g[key]['x'],g[key]['z']] for key in ('a','b'))
                hit=_segment_hit(previous,current,a,b)
                if hit is not None:
                    events.append(tracker.observe(j,hit,state.timestamp,controller.progress))
                    lap_count=tracker.laps
        rows.append({'time':state.timestamp,'x':state.x,'z':state.z,'speed':state.speed,'heading':state.heading,
                     **command,'cone_distance_m':min(nearby) if nearby else None,'offtrack':outside,
                     'contact_ids':current_contacts,'incident_count':len(contacts),'margin_conflict_count':len(margin_conflicts),'gates_passed':len(events),'gate_phase':'TIMED' if tracker.started else 'OUTLAP','laps_completed':lap_count})
        if command['mode']!='RUNNING':break
        if lap_count>=laps and controller.progress>=laps*controller.length-.5:break
        speed=max(0.,state.speed+dt*(vehicle.acceleration*command['throttle']*acceleration_gain-vehicle.braking*command['brake']))
        yaw_rate=state.speed/vehicle.wheelbase*math.tan(command['steer']*steering_gain)
        midpoint=state.heading+yaw_rate*dt/2
        state=State(state.x+state.speed*math.cos(midpoint)*dt,state.z+state.speed*math.sin(midpoint)*dt,
                    wrap(state.heading+yaw_rate*dt),speed,(k+1)*dt)
    completed=bool(lap_count>=laps and controller.progress>=laps*controller.length-.5)
    from cleanlap.validation import slalom_evidence
    # Audit each completed lap separately: repeated legal gate crossings are not duplicates.
    lap_traces=[]; lap_reports=[]; first=0
    for e in events:
        if e['phase']=='start':
            first=next((i for i in range(first,len(rows)) if rows[i]['time']>=e['time']),first)
        if e['phase']=='finish':
            end=next((i for i in range(first,len(rows)) if rows[i]['time']>=e['time']),len(rows)-1)
            trace=rows[first:end+1]
            lap_traces.append(slalom_evidence([[r['x'],r['z']] for r in trace],world.slaloms))
            lap_reports.append({'lap':len(lap_reports)+1,'time_s':e['lap_time_s'],
                'cone_contacts':sorted({j for r in trace for j in r['contact_ids']}),
                'offtrack_samples':sum(r['offtrack'] for r in trace),
                'min_cone_clearance_m':min((r['cone_distance_m'] for r in trace if r['cone_distance_m'] is not None),default=None),
                'max_lateral_error_m':max(abs(r['lateral_error']) for r in trace),
                'slalom':lap_traces[-1]})
            first=end
    slalom_ok=not world.slaloms or bool(lap_traces) and all(s['candidate_sequence_ok'] for s in lap_traces)
    return {'evidence':'SYNTHETIC_BICYCLE_NOT_ASSETTO_CORSA','completed':completed,'laps_completed':lap_count,
            'diagnostic_unsafe':diagnostic_unsafe,'slalom_laps':lap_traces,'lap_reports':lap_reports,
            'elapsed_s':state.timestamp,'lap_times_s':[e['lap_time_s'] for e in events if e['phase']=='finish'],'stop_reason':None if completed else controller.stop_reason or 'timeout',
            'cone_contacts':sorted(contacts),'cone_margin_conflicts':sorted(margin_conflicts),'offtrack_samples':off,'gate_events':events,
            'max_lateral_error_m':max(abs(r['lateral_error']) for r in rows),
            'min_cone_clearance_m':minclear if math.isfinite(minclear) else None,
            'clean_model_lap':completed and not diagnostic_unsafe and slalom_ok and not contacts and not margin_conflicts and off==0 and all(e['ordered'] for e in events),
            'geometry_unresolved':world.unresolved,'telemetry':rows}
