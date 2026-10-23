import unittest
from cleanlap.tests.test_geometry import fixture
from cleanlap.geometry import ingest
from cleanlap.planning import plan


class DriveTests(unittest.TestCase):
    def test_default_gate_refuses_without_arm_release_and_measured_calibration(self):
        from cleanlap.drive import calibration_template
        from cleanlap.preflight import authorize
        world=ingest(fixture()); trajectory=plan(world)
        calibration=calibration_template(world,trajectory)
        reasons=authorize(calibration,world,trajectory,{'track':'synthetic','car':'test'})
        self.assertIn('explicit_arm_required',reasons)
        self.assertIn('explicit_vm_release_required',reasons)
        self.assertIn('full_wheel_envelope_verified',reasons)
        self.assertIsNone(calibration['stick_per_road_rad'])
        for key,value in list(calibration.items()):
            if value is False: calibration[key]=True
        calibration.update(trajectory['vehicle'])
        calibration.update(operator='fixture',car='test',stick_per_road_rad=1.,steer_sign=1,
                           evidence_paths=[{'path':'/nonexistent/calibration.json','sha256':'0'*64}])
        reasons=authorize(calibration,world,trajectory,{'track':'synthetic','car':'test'},arm=True,vm_released=True)
        self.assertIn('measurement_evidence_unreadable',reasons)
        import tempfile
        from pathlib import Path
        from cleanlap.preflight import digest
        with tempfile.TemporaryDirectory() as directory:
            evidence=Path(directory)/'synthetic-measurement';evidence.write_bytes(b'SYNTHETIC_NOT_AC')
            calibration['evidence_paths']=[{'path':str(evidence),'sha256':digest(evidence.read_bytes())}]
            self.assertEqual(authorize(calibration,world,trajectory,{'track':'synthetic','car':'test'},arm=True,vm_released=True),[])
            calibration['stick_per_road_rad']=10.
            self.assertIn('steering_range_not_representable',authorize(calibration,world,trajectory,
                {'track':'synthetic','car':'test'},arm=True,vm_released=True))

    def test_controls_prepare_cli_gates_and_restores_exact_bytes(self):
        import json,tempfile,contextlib,io
        from pathlib import Path
        from cleanlap.drive import main
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);controls=root/'controls.ini';original=b'[HEADER]\r\nINPUT_METHOD=KEYBOARD\r\n'
            controls.write_bytes(original)
            argv=['controls-apply','--controls',str(controls),'--backup',str(root/'backup'),'--output',str(root/'prepared.json')]
            with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit): main(argv)
            self.assertEqual(controls.read_bytes(),original)
            self.assertEqual(main(argv+['--arm','--vm-released']),0)
            self.assertIn(b'X360',controls.read_bytes())
            self.assertEqual(main(['restore','--controls',str(controls),'--backup',str(root/'backup'),
                                   '--vm-released','--output',str(root/'restored.json')]),0)
            self.assertEqual(controls.read_bytes(),original)

    def test_observe_cli_never_imports_gamepad_and_writes_automatic_diagnostics(self):
        import json,math,socket,threading,tempfile,sys
        from pathlib import Path
        from cleanlap.drive import main
        world=ingest(fixture());trajectory=plan(world);a,b=trajectory['points'][:2]
        done=threading.Event()
        with tempfile.TemporaryDirectory() as directory,socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as producer:
            root=Path(directory);(root/'geometry.json').write_text(json.dumps(fixture()),encoding='utf-8')
            (root/'plan.json').write_text(json.dumps(trajectory),encoding='utf-8')
            producer.bind(('127.0.0.1',0));producer.settimeout(.05)
            def serve():
                i=0
                while not done.is_set():
                    try:nonce,address=producer.recvfrom(100)
                    except socket.timeout:continue
                    i+=1
                    producer.sendto(json.dumps(dict(protocol='cleanlap-1',nonce=nonce.decode(),epoch='boot',seq=i,source_t=i*.02,
                        live=True,track='synthetic',car='test',gear=1,in_pit=False,lap_count=0,lap_ms=0,
                        x=a[0],z=a[1],heading=math.atan2(b[1]-a[1],b[0]-a[0]),speed=0.)).encode(),address)
            worker=threading.Thread(target=serve);worker.start()
            before=sys.modules.get('vgamepad')
            try:
                self.assertEqual(main(['observe','--geometry',str(root/'geometry.json'),'--plan',str(root/'plan.json'),
                    '--seconds','.08','--port',str(producer.getsockname()[1]),'--output',str(root/'trace.jsonl')]),2)
                report=json.loads((root/'trace.jsonl.report.json').read_text(encoding='utf-8'))
                self.assertGreater(report['accepted_frames'],0)
                self.assertEqual(report['diagnostics']['evidence'],'GEOMETRIC_DIAGNOSTIC_NOT_OFFICIAL')
                self.assertIs(sys.modules.get('vgamepad'),before)
            finally:done.set();worker.join(1)

    def test_production_loop_uses_socket_and_stops_on_disconnect(self):
        import socket,threading,json
        from cleanlap.drive import run_loop
        from cleanlap.runtime import Runtime, TelemetrySocket
        from cleanlap.offline_runtime import FakeActuator
        world=ingest(fixture()); trajectory=plan(world); pad=FakeActuator()
        runtime=Runtime(world,trajectory,pad,authorized=True)
        p,q=trajectory['points'][:2]
        import math
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as producer,TelemetrySocket(timeout=.03) as transport:
            producer.bind(('127.0.0.1',0)); transport.peer=producer.getsockname()
            def reply():
                nonce,address=producer.recvfrom(100)
                producer.sendto(json.dumps(dict(protocol='cleanlap-1',nonce=nonce.decode(),epoch='boot',seq=1,source_t=1.,
                    live=True,track='synthetic',car='test',gear=1,in_pit=False,lap_count=0,lap_ms=0,
                    x=p[0],z=p[1],heading=math.atan2(q[1]-p[1],q[0]-p[0]),speed=0.)).encode(),address)
            worker=threading.Thread(target=reply);worker.start()
            report=run_loop(runtime,transport,max_seconds=.2)
            worker.join()
        self.assertEqual(report['mode'],'STOP')
        self.assertEqual(pad.last['throttle'],0.)
        self.assertEqual(report['accepted_frames'],1)
        self.assertEqual(report['reason'],'transport:TimeoutError')


if __name__=='__main__':unittest.main()
