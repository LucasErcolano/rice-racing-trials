"""Read-only analysis of CSP wheel samples. Never issues an arm certificate.

Wheel tyres are cylinders: axial half-width plus radial disk support, projected
onto the car's current axes. These are sampled bounds, not full-steering bounds.
"""
import argparse
import hashlib
import json
import math
import statistics
from pathlib import Path


def vector(value):
    result=tuple(float(value[k]) for k in ('x','y','z'))
    if not all(math.isfinite(v) for v in result):raise ValueError('nonfinite vector')
    return result


def dot(a,b):return sum(x*y for x,y in zip(a,b))
def unit(a):
    length=math.sqrt(dot(a,a))
    if not .95 < length < 1.05:raise ValueError('invalid orientation basis')
    return tuple(x/length for x in a)
def cross(a,b):return (a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0])


def analyze(rows) -> dict:
    widths=[];fronts=[];rears=[];wheelbases=[];angles=[];discarded=0;identities=set()
    for row in rows:
        try:
            if row.get('live') is not True or not 0 <= float(row['speed']) <= .2:raise ValueError('not stationary/live')
            if row.get('wheel_capture_status')!='OBSERVED_NOT_COMMISSIONED':raise ValueError('wheel data absent')
            capture=row['wheel_capture'];body=capture['body'];wheels=capture['wheels']
            if len(wheels)!=4 or {w['index'] for w in wheels}!={0,1,2,3}:raise ValueError('four unique wheels required')
            origin=vector(body['position']);forward=unit(vector(body['look']));side=unit(vector(body['side']))
            if abs(dot(forward,side)) > .01:raise ValueError('nonorthogonal basis')
            sample=[]
            for wheel in wheels:
                radius=float(wheel['radius']);width=float(wheel['tyre_width'])
                if not all(math.isfinite(v) and v>0 for v in (radius,width)):raise ValueError('dimensions unavailable')
                delta=tuple(v-o for v,o in zip(vector(wheel['position']),origin))
                look=unit(vector(wheel['look']));up=unit(vector(wheel['up']))
                if abs(dot(look,up))>.01:raise ValueError('nonorthogonal wheel basis')
                axis=unit(cross(up,look))
                def support(direction):
                    component=min(1.,abs(dot(axis,direction)))
                    return width*.5*component+radius*math.sqrt(max(0.,1.-component*component))
                sample.append((wheel['index'],dot(delta,side),dot(delta,forward),support(side),support(forward),
                               math.atan2(dot(look,side),dot(look,forward))))
            sample.sort()
            wheelbase=(sample[0][2]+sample[1][2]-sample[2][2]-sample[3][2])*.5
            if wheelbase<=0:raise ValueError('wheel ordering/forward axis disagreement')
            widths.append(max(abs(w[1])+w[3] for w in sample))
            fronts.append(max(w[2]+w[4] for w in sample));rears.append(max(-w[2]+w[4] for w in sample))
            wheelbases.append(wheelbase);angles.extend(w[5] for w in sample[:2])
            identities.add((row.get('track'),row.get('car')))
        except (KeyError,TypeError,ValueError,OverflowError):discarded+=1
    if len(identities)>1:
        return {'evidence':'MIXED_IDENTITY_REJECTED','full_wheel_envelope_verified':False,'sampled_envelope':None}
    return dict(evidence='OBSERVED_NOT_COMMISSIONED',samples=len(widths),discarded_samples=discarded,
                identity=list(next(iter(identities))) if identities else None,
                wheelbase_median_m=statistics.median(wheelbases) if wheelbases else None,
                front_wheel_angle_range_rad=[min(angles),max(angles)] if angles else None,
                sampled_envelope=dict(half_width=max(widths),front_overhang=max(fronts),rear_overhang=max(rears)) if widths else None,
                full_wheel_envelope_verified=False,steering_transfer_verified=False,
                remaining=['operator must verify wheel index/origin/basis against installed car',
                           'sample both steering limits and intermediate angles',
                           'verify stick-to-road-wheel transfer, deadzone, filters and dynamic lag',
                           'add measurement uncertainty margin; replan and revalidate before commissioning'])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('trace');parser.add_argument('--output',required=True);args=parser.parse_args()
    source=Path(args.trace).read_bytes()
    rows=[json.loads(line) for line in source.decode('utf-8').splitlines() if line.strip()]
    report=analyze([r.get('telemetry',r) for r in rows]);report['source_sha256']=hashlib.sha256(source).hexdigest()
    with Path(args.output).open('x',encoding='utf-8') as output:json.dump(report,output,indent=2,allow_nan=False)
    return 0 if report.get('samples') else 2


if __name__=='__main__':raise SystemExit(main())
