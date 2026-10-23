"""Standalone supervised runtime. Observe is default; never connects to a VM.

Run on the same Windows host as AC only after explicit release and commissioning.
"""
import argparse
import json
import math
import multiprocessing
import os
from pathlib import Path
import signal
import time
from cleanlap.preflight import authorize, digest, ControlsTransaction
from cleanlap.runtime import Runtime, TelemetrySocket


class ProcessActuator:
    """Independent process owns the device and watchdog; parent never imports vgamepad."""
    def __init__(self, calibration):
        from cleanlap.actuator import pad_worker
        context=multiprocessing.get_context('spawn')
        self.connection,child=context.Pipe()
        self.process=context.Process(target=pad_worker,args=(child,calibration),daemon=True)
        self.process.start();child.close()
        if not self.connection.poll(10):
            self.close();raise RuntimeError('pad process did not become ready')
        try:
            if self.connection.recv()!={'ready':True}: raise RuntimeError('pad not ready')
        except (EOFError,OSError):
            self.close();raise RuntimeError('pad process initialization failed') from None

    def send(self, command):
        if not self.process.is_alive(): raise RuntimeError('actuator watchdog stopped')
        self.connection.send({'sent':time.monotonic(),'command':command})

    def release(self):
        # None makes the child release and exit; STOP cannot re-arm it.
        try: self.connection.send(None)
        except (OSError,EOFError,BrokenPipeError): pass

    def close(self):
        self.release(); self.process.join(timeout=1.)
        # Never terminate a worker before its own watchdog has had time to reset.
        self.connection.close()
        if self.process.is_alive(): raise RuntimeError('actuator did not exit: manual stop required')


def calibration_template(world, plan):
    result=dict(schema='cleanlap-calibration-1',geometry_sha256=world.digest,
                plan_sha256=digest(json.dumps(plan,sort_keys=True,allow_nan=False).encode()),
                track=world.document['track'],car='madformulateam_mft02',operator=None,evidence_paths=[],
                stick_per_road_rad=None,steer_sign=None)
    for key in ('installed_assets_verified','boundaries_surveyed','cones_surveyed','slaloms_surveyed',
                'full_wheel_envelope_verified','reference_transform_verified','steering_verified',
                'manual_override_verified','stop_verified','csp_telemetry_verified'):
        result[key]=False
    for key in ('wheelbase','max_steer','half_width','half_length'): result[key]=None
    result['provisional_plan_vehicle']=plan['vehicle']
    return result


def run_loop(runtime, transport, max_seconds=30., stop_file=None, initial=None, record=None):
    started=time.monotonic(); accepted=0; result={'mode':'HOLD','reason':'duration_limit'}
    try:
        while time.monotonic()-started < max_seconds:
            if stop_file and Path(stop_file).exists():
                result=runtime.stop('operator_stop_file');break
            if initial is None:
                transport.request(); frame=transport.receive()
            else:
                frame=initial;initial=None
            result=runtime.step(frame)
            accepted+=1
            if record:
                record.write(json.dumps({'telemetry':frame,'command':result},allow_nan=False)+'\n');record.flush()
            if result['mode']=='STOP': break
            time.sleep(.01)  # CSP updates respond at the next frame; <=100 Hz requests
        else: result=runtime.stop('duration_limit')
    except (OSError,ValueError,TypeError,KeyError) as error:
        result=runtime.stop('transport:'+type(error).__name__)
    except KeyboardInterrupt:
        result=runtime.stop('operator_interrupt')
    finally:
        runtime.actuator.release()
    return {**result,'accepted_frames':accepted,'elapsed_s':time.monotonic()-started,
            'evidence':'SUPERVISED_RUNTIME_NOT_OFFICIAL_SCORING'}


class NeutralSink:
    def send(self, command): raise RuntimeError('observe cannot send controls')
    def release(self): pass


def load_world(args):
    from cleanlap.geometry import ingest
    checkpoints=json.loads(Path(args.checkpoints).read_text(encoding='utf-8'))['checkpoints'] if args.checkpoints else ()
    return ingest(json.loads(Path(args.geometry).read_text(encoding='utf-8')),
                  ring_indices=args.ring_indices,checkpoints=checkpoints)


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('operation',choices=('observe','run','template','controls-apply','restore','diagnose','receipts'))
    p.add_argument('--geometry');p.add_argument('--plan');p.add_argument('--calibration')
    p.add_argument('--ring-indices',type=int,nargs=2);p.add_argument('--checkpoints')
    p.add_argument('--arm',action='store_true');p.add_argument('--vm-released',action='store_true')
    p.add_argument('--controls');p.add_argument('--backup');p.add_argument('--stop-file')
    p.add_argument('--pit-connector',action='store_true',help='Generate checked Hermite connector from stationary telemetry')
    p.add_argument('--seconds',type=float,default=30.);p.add_argument('--port',type=int,default=9877)
    p.add_argument('--output',required=True);p.add_argument('--trace');p.add_argument('--export');p.add_argument('--identity')
    args=p.parse_args(argv)
    output=Path(args.output)
    if output.exists(): p.error('output must be new (preserve evidence)')
    if args.operation=='controls-apply':
        if not args.arm or not args.vm_released or not args.controls or not args.backup:
            p.error('pre-launch controls write requires --arm --vm-released --controls --backup')
        transaction=ControlsTransaction(args.controls,args.backup)
        transaction.apply();record=transaction.verify_applied()
        Path(args.output).write_text(json.dumps(record,indent=2),encoding='utf-8');return 0
    if args.operation=='restore':
        if not args.vm_released: p.error('explicit --vm-released required for local controls restore')
        ControlsTransaction(args.controls,args.backup).restore()
        output.write_text(json.dumps({'restored':True}),encoding='utf-8');return 0
    if args.operation=='receipts':
        from cleanlap.diagnostics import receipt
        export=json.loads(Path(args.export).read_text(encoding='utf-8'))
        identity=json.loads(Path(args.identity).read_text(encoding='utf-8'))
        rows=[r for r in export['laps'] if all(r.get(k)==v for k,v in identity.items())]
        report=receipt(rows[0],identity) if len(rows)==1 else {'complete':False,'reasons':['missing_or_ambiguous_lap'],'counts':None}
        output.write_text(json.dumps(report,indent=2,allow_nan=False),encoding='utf-8');return 0 if report['complete'] else 2
    world=load_world(args)
    if args.operation=='diagnose':
        from cleanlap.diagnostics import diagnose
        rows=[json.loads(line) for line in Path(args.trace).read_text(encoding='utf-8').splitlines() if line.strip()]
        rows=[r.get('telemetry',r) for r in rows]
        from cleanlap.planning import Vehicle
        if not args.plan: p.error('diagnose requires --plan with the exact vehicle configuration')
        plan=json.loads(Path(args.plan).read_text(encoding='utf-8'))
        if plan.get('geometry_sha256') != world.digest: p.error('world/plan identity mismatch')
        report=diagnose(world,rows,Vehicle(**plan['vehicle']))
        report['plan_sha256']=digest(json.dumps(plan,sort_keys=True,allow_nan=False).encode())
        output.write_text(json.dumps(report,indent=2,allow_nan=False),encoding='utf-8');return 0
    plan=json.loads(Path(args.plan).read_text(encoding='utf-8'))
    if args.operation=='template':
        output.write_text(json.dumps(calibration_template(world,plan),indent=2),encoding='utf-8');return 2
    if not math.isfinite(args.seconds) or not 0 < args.seconds <= 900: p.error('seconds must be within (0,900]')
    live=args.operation=='run'
    if live and (not args.arm or not args.vm_released): p.error('run requires --arm and --vm-released; default is no controls')
    if live and os.name!='nt': p.error('live backend only supported on commissioned local Windows host')
    calibration=json.loads(Path(args.calibration).read_text(encoding='utf-8')) if live else {}
    # Validate plan before constructing a device or changing controls.
    Runtime(world,plan,NeutralSink())
    with TelemetrySocket(peer=('127.0.0.1',args.port)) as transport:
        transport.request();frame=transport.receive()
        reasons=authorize(calibration,world,plan,frame,arm=args.arm,vm_released=args.vm_released) if live else ['observe_only']
        if live and reasons:
            output.write_text(json.dumps({'mode':'HOLD','reasons':reasons},indent=2),encoding='utf-8');return 2
        connector=None
        if args.pit_connector:
            from cleanlap.startup import hermite_connector
            from cleanlap.planning import Vehicle
            a,b=plan['points'][:2]
            if frame['speed']>.2: raise ValueError('stationary pit startup required')
            connector,_=hermite_connector(world,[frame['x'],frame['z']],frame['heading'],a,
                                         math.atan2(b[1]-a[1],b[0]-a[0]),Vehicle(**plan['vehicle']))
        # Gates and path admission are checked with a non-actuating probe first.
        from cleanlap.offline_runtime import FakeActuator
        probe=Runtime(world,plan,FakeActuator(),authorized=True,connector=connector)
        admission=probe.step(frame)
        if live and admission['mode']!='RUNNING':
            output.write_text(json.dumps(admission,indent=2),encoding='utf-8');return 2
        runtime=Runtime(world,plan,NeutralSink(),authorized=live,connector=connector,
                        commissioned_car=calibration['car'] if live else None,
                        admitted_epoch=frame['epoch'])
        transaction=None;pad=NeutralSink()
        try:
            if live:
                if not args.controls or not args.backup or not args.stop_file: p.error('controls, backup and stop-file required')
                if Path(args.stop_file).exists(): raise ValueError('stop file already exists')
                transaction=ControlsTransaction(args.controls,args.backup)
                transaction.verify_applied()  # pre-launch transaction; no live INI toggle
                pad=ProcessActuator(calibration)
            runtime.actuator=pad
            # Fresh frame AFTER device startup; old preflight frame never drives.
            with output.open('x',encoding='utf-8') as record:
                report=run_loop(runtime,transport,args.seconds,args.stop_file,record=record)
            from cleanlap.diagnostics import diagnose
            trace=[json.loads(line)['telemetry'] for line in output.read_text(encoding='utf-8').splitlines()]
            from cleanlap.planning import Vehicle
            try: report['diagnostics']=diagnose(world,trace,Vehicle(**plan['vehicle']))
            except (ValueError,KeyError,TypeError):
                report['diagnostics']={'evidence':'INCOMPLETE_TRACE','native_lap_ms':None,'official_penalty_counts':None}
            report['geometry_sha256']=world.digest
            report['plan_sha256']=digest(json.dumps(plan,sort_keys=True,allow_nan=False).encode())
            report['trace_sha256']=digest(output.read_bytes())
            report['controls_restored']=False if live else None  # null = no controls write requested
            Path(str(output)+'.report.json').write_text(json.dumps(report,indent=2,allow_nan=False),encoding='utf-8')
        finally:
            try:
                if isinstance(pad,ProcessActuator): pad.close()
            finally:
                if transaction: transaction.restore()
        if transaction:
            report['controls_restored']=True
            Path(str(output)+'.report.json').write_text(json.dumps(report,indent=2,allow_nan=False),encoding='utf-8')
    print(json.dumps(report,indent=2));return 2 if not live or report['reason'] else 0


if __name__=='__main__': raise SystemExit(main())
