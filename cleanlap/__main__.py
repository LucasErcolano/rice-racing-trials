"""Run with python -m cleanlap. Always offline; exit 2 means NOT AUTHORIZED."""
import argparse
import csv
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import platform
import time
import numpy as np
from cleanlap.geometry import ingest
from cleanlap.planning import Vehicle, plan
from cleanlap.control import simulate


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def plots(world, proposal, rows, output):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,ax=plt.subplots(figsize=(14,7))
    for line in world.document['limits']['polylines']:
        x=np.asarray(line);ax.plot(x[:,0],x[:,1],color='grey',lw=.7)
    ai=world.reference;ax.plot(ai[:,0],ai[:,1],'--',lw=.7,label='AI reference')
    points=np.asarray(proposal['points']);ax.plot(points[:,0],points[:,1],lw=1,label='Candidate plan')
    if world.cones:
        ax.scatter([c['x'] for c in world.cones],[c['z'] for c in world.cones],s=4,c='orange',label='Unconfirmed cone clusters')
    for run in world.slaloms:
        for g in run['gates']:
            ax.plot([g['x']+g['tz']*5.5,g['x']-g['tz']*5.5],
                    [g['z']-g['tx']*5.5,g['z']+g['tx']*5.5],c='purple',alpha=.5)
            ax.annotate(g['id'],(g['x'],g['z']),fontsize=5)
    for i,g in enumerate(world.gates):
        ax.plot([g['a']['x'],g['b']['x']],[g['a']['z'],g['b']['z']],c='green',lw=2)
        ax.annotate('Timing gate '+str(i),(g['a']['x'],g['a']['z']),fontsize=6)
    if rows:
        ax.plot([r['x'] for r in rows],[r['z'] for r in rows],c='red',lw=.7,label='Synthetic bicycle')
        bad=[r for r in rows if r['offtrack'] or r['incident_count']]
        if bad:ax.scatter([r['x'] for r in bad[::20]],[r['z'] for r in bad[::20]],c='red',s=6)
    ax.set_aspect('equal');ax.invert_yaxis();ax.set_xlabel('AC X (m)');ax.set_ylabel('AC Z (m)')
    ax.legend(loc='upper left',fontsize=7);ax.set_title('OFFLINE CANDIDATE — NOT AN ASSETTO CORSA LAP')
    fig.tight_layout();fig.savefig(output/'trajectory.png',dpi=140);plt.close(fig)
    fig,axes=plt.subplots(4,1,figsize=(12,10),sharex=True)
    if rows:
        t=[r['time'] for r in rows]
        for key in ['speed','target_speed']:axes[0].plot(t,[r[key] for r in rows],label=key)
        for key in ['lateral_error','heading_error']:axes[1].plot(t,[r[key] for r in rows],label=key)
        for key in ['steer','throttle','brake']:axes[2].plot(t,[r[key] for r in rows],label=key)
        axes[3].plot(t,[r['cone_distance_m'] for r in rows],label='cone_distance_m')
    for ax in axes:ax.grid();ax.legend()
    axes[0].set_ylabel('m/s');axes[1].set_ylabel('m / rad');axes[2].set_ylabel('rad / normalized');axes[3].set_ylabel('m')
    axes[-1].set_xlabel('Synthetic elapsed time (s)');fig.tight_layout();fig.savefig(output/'signals.png',dpi=120);plt.close(fig)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--geometry',required=True);parser.add_argument('--output',required=True)
    parser.add_argument('--ring-indices',type=int,nargs=2,metavar=('OUTER','INNER'))
    parser.add_argument('--checkpoints');parser.add_argument('--laps',type=int,default=1)
    parser.add_argument('--max-speed',type=float,default=6.);parser.add_argument('--margin',type=float,default=.2)
    parser.add_argument('--lateral-offset',type=float,default=0.);parser.add_argument('--heading-offset',type=float,default=0.)
    parser.add_argument('--steering-gain',type=float,default=1.);parser.add_argument('--acceleration-gain',type=float,default=1.)
    parser.add_argument('--dt',type=float,default=.02);parser.add_argument('--max-seconds',type=float,default=900.)
    parser.add_argument('--diagnostic-unsafe',action='store_true',help='simulate rejected candidate; never mark clean or authorize')
    args=parser.parse_args(argv)
    if not 0<args.max_speed<=12 or not .05<=args.margin<=1:parser.error('speed or margin outside offline bounds')
    out=Path(args.output)
    if out.exists() and any(out.iterdir()):parser.error('output must be empty; preserve previous evidence')
    out.mkdir(parents=True,exist_ok=True)
    try:
        geometry=json.loads(Path(args.geometry).read_text(encoding='utf-8'))
        checkpoints=json.loads(Path(args.checkpoints).read_text(encoding='utf-8')) if args.checkpoints else None
        if checkpoints and checkpoints.get('track')!=geometry['track']:raise ValueError('checkpoint track mismatch')
        world=ingest(geometry,args.ring_indices,checkpoints['checkpoints'] if checkpoints else ())
        start=time.monotonic();proposal=plan(world,replace(Vehicle(),max_speed=args.max_speed,margin=args.margin))
        planning_seconds=time.monotonic()-start
        result=simulate(world,proposal,laps=args.laps,lateral_offset=args.lateral_offset,heading_offset=args.heading_offset,
                        dt=args.dt,max_seconds=args.max_seconds,steering_gain=args.steering_gain,
                        acceleration_gain=args.acceleration_gain,diagnostic_unsafe=args.diagnostic_unsafe)
        rows=result.pop('telemetry')
        result.update(authorized=False,geometry_sha256=world.digest,planning_seconds=planning_seconds,
                      plan_violations=proposal['violations'],native_ac_lap_ms=None,v2_native_receipt=None,v2_penalty_receipt=None,
                      actual_ac_cone_contacts=None,actual_ac_offtracks=None)
        for name,data in [('plan.json',proposal),('report.json',result)]:
            (out/name).write_text(json.dumps(data,indent=2,allow_nan=False)+'\n',encoding='utf-8')
        with (out/'telemetry.csv').open('w',encoding='utf-8',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
        plots(world,proposal,rows,out)
        import shapely, matplotlib, scipy
        manifest={'schema':'cleanlap-offline-manifest-v1','arguments':vars(args),'geometry_sha256':digest(args.geometry),
                  'source_sha256':geometry.get('source_sha256'),
                  'checkpoint_sha256':digest(args.checkpoints) if args.checkpoints else None,
                  'code_sha256':{str(p.relative_to(Path(__file__).parent.parent)):digest(p)
                                 for p in sorted(Path(__file__).parent.glob('*.py'))},
                  'versions':{'python':platform.python_version(),'numpy':np.__version__,'shapely':shapely.__version__,'matplotlib':matplotlib.__version__,'scipy':scipy.__version__},
                  'artifact_sha256':{p.name:digest(p) for p in sorted(out.iterdir()) if p.is_file()}}
        (out/'manifest.json').write_text(json.dumps(manifest,indent=2,allow_nan=False)+'\n',encoding='utf-8')
        print(json.dumps({k:result[k] for k in ['completed','clean_model_lap','laps_completed','elapsed_s','stop_reason','plan_violations','authorized']}))
        return 2
    except (ValueError,KeyError,IndexError) as exc:
        (out/'rejection.json').write_text(json.dumps({'authorized':False,'reason':str(exc)},allow_nan=False)+'\n',encoding='utf-8')
        print('REJECTED:',exc)
        return 2


if __name__=='__main__':raise SystemExit(main())
