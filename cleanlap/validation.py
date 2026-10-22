"""Independent candidate slalom audit; never an official penalty calculation."""
import math


class GateTracker:
    """Offline route state only; no imports or writes to Rice timing/scoring."""
    def __init__(self, directions, length, seeded=False):
        self.directions=directions;self.length=length;self.started=seeded
        self.next_gate=1%len(directions) if seeded else 0
        self.laps=0;self.start_progress=0.;self.start_time=0. if seeded else None

    def observe(self, gate, direction, time, progress):
        ordered=gate==self.next_gate and direction==self.directions[gate]
        phase='invalid';lap_time=None
        if ordered and not self.started:
            self.started=True;self.start_progress=progress;self.start_time=time;phase='start'
            self.next_gate=1%len(self.directions)
        elif ordered and gate==0:
            if progress-self.start_progress<.8*self.length:ordered=False
            else:
                phase='finish';self.laps+=1;lap_time=time-self.start_time
                self.start_progress=progress;self.start_time=time;self.next_gate=1%len(self.directions)
        elif ordered:
            phase='checkpoint';self.next_gate=(self.next_gate+1)%len(self.directions)
        return {'time':time,'gate':gate,'direction':direction,'ordered':ordered,'phase':phase,'lap_time_s':lap_time}


def slalom_evidence(points, runs, half_gate=5.5, clearance=1.1, closed=False):
    segments=list(zip(points,points[1:]))
    if closed:segments.append((points[-1],points[0]))
    reports=[]
    for run in runs:
        observed=[]; issues=[]
        for gate in run['gates']:
            t=(gate['tx'],gate['tz']); origin=(gate['x'],gate['z'])
            hits=[]
            for i,(a,b) in enumerate(segments):
                before=(a[0]-origin[0])*t[0]+(a[1]-origin[1])*t[1]
                after=(b[0]-origin[0])*t[0]+(b[1]-origin[1])*t[1]
                if before<0<=after:
                    u=-before/(after-before)
                    x=a[0]+u*(b[0]-a[0]);z=a[1]+u*(b[1]-a[1])
                    lateral=-(x-origin[0])*t[1]+(z-origin[1])*t[0]
                    if abs(lateral)<=half_gate:hits.append({'segment':i,'lateral_m':lateral,'sign':1 if lateral>0 else -1})
            if len(hits)!=1:issues.append(gate['id']+':missing_or_duplicate')
            elif abs(hits[0]['lateral_m'])<clearance:issues.append(gate['id']+':insufficient_clearance')
            observed.append({'id':gate['id'],'hits':hits})
        one=[g['hits'][0] for g in observed if len(g['hits'])==1]
        # Circular path order can wrap once at the start station.
        if len(one)>1:
            drops=sum(a['segment']>b['segment'] for a,b in zip(one,one[1:]))
            if drops>(1 if closed else 0):issues.append('wrong_order')
            if any(a['sign']==b['sign'] for a,b in zip(one,one[1:])):issues.append('broken_alternation')
        reports.append({'id':run['id'],'gates':observed,'issues':issues,'inference_confidence_m':run.get('confidence')})
    return {'candidate_sequence_ok':bool(runs) and all(not r['issues'] for r in reports),
            'surveyed':False,'runs':reports,'unresolved':not bool(runs)}
