"""Standalone runtime; fake pad tests are NOT Windows input verification."""
import json
import math
import socket
import time
import unittest
from dataclasses import asdict
from cleanlap.control import State
from cleanlap.tests.test_geometry import fixture


class RuntimeTests(unittest.TestCase):
    def test_socket_roundtrip_and_replay_fail_closed(self):
        from cleanlap.runtime import TelemetrySocket
        with TelemetrySocket(port=0) as receiver, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as peer:
            peer.bind(('127.0.0.1', 0))
            receiver.peer = peer.getsockname()
            nonce = receiver.request()
            request, address = peer.recvfrom(4096)
            self.assertEqual(request.decode(), nonce)
            frame = dict(protocol='cleanlap-1', nonce=nonce, epoch='boot-0', seq=1, source_t=1.,
                         x=1., z=2., heading=0., speed=0., live=True, gear=1,
                         track='track', car='car', in_pit=False, lap_count=0, lap_ms=0)
            peer.sendto(json.dumps(frame).encode(), address)
            self.assertEqual(receiver.receive()['x'], 1.)
            peer.sendto(json.dumps(frame).encode(), address)
            with self.assertRaises(ValueError):
                receiver.receive()

    def test_independent_process_watchdog_exits_and_releases(self):
        import multiprocessing,sys,types
        if 'fork' not in multiprocessing.get_all_start_methods(): self.skipTest('Linux process harness; not Windows verification')
        from cleanlap.actuator import pad_worker
        context=multiprocessing.get_context('fork'); events=context.Queue()
        class FakePad:
            def reset(self): events.put('reset')
            def update(self): pass
            def left_joystick_float(self,**kw): pass
            def right_trigger_float(self,**kw): events.put(('gas',kw['value_float']))
            def left_trigger_float(self,**kw): pass
        old=sys.modules.get('vgamepad');sys.modules['vgamepad']=types.SimpleNamespace(VX360Gamepad=FakePad)
        parent,child=context.Pipe()
        process=context.Process(target=pad_worker,args=(child,{'stick_per_road_rad':1.,'steer_sign':1}))
        try:
            process.start();child.close();self.assertTrue(parent.poll(2));self.assertTrue(parent.recv()['ready'])
            parent.send({'sent':time.monotonic(),'command':dict(steer=0.,throttle=.5,brake=0.)})
            process.join(2)
            self.assertFalse(process.is_alive());self.assertEqual(process.exitcode,0)
            calls=[]
            while not events.empty(): calls.append(events.get(timeout=1))
            self.assertIn(('gas',.5),calls);self.assertEqual(calls[-1],'reset')
        finally:
            parent.close()
            if process.is_alive(): process.terminate();process.join()
            if old is None:sys.modules.pop('vgamepad',None)
            else:sys.modules['vgamepad']=old
            events.close()

    def test_watchdog_releases_without_runtime_progress(self):
        from cleanlap.actuator import GuardedPad
        class Pad:
            def __init__(self): self.calls=[]
            def reset(self): self.calls.append(('reset',))
            def update(self): self.calls.append(('update',))
            def left_joystick_float(self, **kw): self.calls.append(('steer',kw))
            def right_trigger_float(self, **kw): self.calls.append(('gas',kw))
            def left_trigger_float(self, **kw): self.calls.append(('brake',kw))
        pad=Pad()
        with GuardedPad(pad, steer_scale=1., steer_sign=-1, timeout=.06) as guard:
            guard.send(dict(steer=.2,throttle=.4,brake=0.))
            self.assertIn(('gas',{'value_float':.4}),pad.calls)
            time.sleep(.12)
            self.assertTrue(guard.tripped)
            self.assertEqual(pad.calls[-2:],[('reset',),('update',)])
            with self.assertRaises(RuntimeError): guard.send(dict(steer=0.,throttle=1.,brake=0.))

    def test_runtime_transports_real_controller_commands_and_latches_faults(self):
        from cleanlap.runtime import Runtime
        from cleanlap.geometry import ingest
        from cleanlap.planning import plan as plan_path
        world=ingest(fixture()); plan=plan_path(world)
        class Sink:
            def __init__(self): self.commands=[]
            def send(self,c): self.commands.append(c)
            def release(self): self.commands.append({'throttle':0.,'brake':0.,'steer':0.})
        sink=Sink(); runtime=Runtime(world,plan,sink,authorized=True)
        frame=dict(protocol='cleanlap-1',epoch='boot',seq=1,source_t=1.,live=True,
                   track='synthetic',car='test',gear=1,in_pit=False,lap_count=0,lap_ms=0,
                   x=plan['points'][0][0],z=plan['points'][0][1],speed=0.)
        a,b=plan['points'][:2]; frame['heading']=math.atan2(b[1]-a[1],b[0]-a[0])
        self.assertEqual(runtime.step(frame,1.)['mode'],'RUNNING')
        self.assertGreater(sink.commands[-1]['throttle'],0.)
        self.assertEqual(runtime.step(frame,1.02)['mode'],'STOP')
        self.assertEqual(sink.commands[-1]['throttle'],0.)
        frame.update(seq=2,source_t=1.02)
        self.assertEqual(runtime.step(frame,1.04)['mode'],'STOP')
        base=dict(frame,seq=1,source_t=1.)
        for fault in ({'source_t':.5},{'epoch':'session-restart'},{'lap_count':-1},
                      {'car':'other-car'},{'x':float(base['x'])+10.},{'live':False}):
            sink=Sink();runtime=Runtime(world,plan,sink,authorized=True)
            self.assertEqual(runtime.step(base,1.)['mode'],'RUNNING')
            changed=dict(base,seq=2,source_t=1.02);changed.update(fault)
            self.assertEqual(runtime.step(changed,1.02)['mode'],'STOP')
            self.assertEqual(sink.commands[-1]['throttle'],0.)
            self.assertEqual(runtime.step(dict(base,seq=3,source_t=1.04),1.04)['mode'],'STOP')

    def test_connector_checks_swept_body_heading_and_curvature(self):
        from cleanlap.startup import verify_connector
        from cleanlap.geometry import ingest
        from cleanlap.planning import Vehicle
        world=ingest(fixture()); v=Vehicle()
        self.assertTrue(verify_connector(world,[[20,0],[20,.5],[20,1]],v,math.pi/2,math.pi/2)['safe'])
        for points,h in [([[20,0],[20,.5],[20,1]],0), ([[14,0],[14,.5],[14,1]],math.pi/2),
                         ([[20,0],[20,.2],[20.2,.2]],math.pi/2)]:
            self.assertFalse(verify_connector(world,points,v,h,math.pi/2)['safe'])

    def test_controls_backup_restore_refuses_external_edit(self):
        import tempfile
        from pathlib import Path
        from cleanlap.preflight import ControlsTransaction
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'controls.ini'; p.write_bytes(b'[HEADER]\nINPUT_METHOD=KEYBOARD\n')
            transaction=ControlsTransaction(p,Path(directory)/'backup')
            transaction.apply(); self.assertIn(b'X360',p.read_bytes())
            transaction.verify_applied()
            p.write_bytes(b'user edited')
            with self.assertRaises(ValueError): transaction.verify_applied()
            with self.assertRaises(ValueError): transaction.restore()
            self.assertEqual(p.read_bytes(),b'user edited')
            p.write_bytes(transaction.applied)
            transaction.restore()
            self.assertEqual(p.read_bytes(),b'[HEADER]\nINPUT_METHOD=KEYBOARD\n')

    def test_csp_uses_documented_shared_socket_module(self):
        from pathlib import Path
        source=Path('cleanlap/csp/CleanlapTelemetry/CleanlapTelemetry.lua').read_text(encoding='utf-8')
        self.assertIn("require('shared/socket')",source)

    def test_actual_lua_producer_roundtrip_and_reset(self):
        from pathlib import Path
        from lupa.luajit21 import LuaRuntime
        lua=LuaRuntime(unpack_returned_tuples=True)
        lua.execute('''
        script={}; sent={}; pending=nil
        udp={settimeout=function() end, setsockname=function() return 1 end,
          receivefrom=function() local p=pending; pending=nil; return p,'127.0.0.1',12345 end,
          sendto=function(self,s,ip,port) sent[#sent+1]=s; return #s end}
        package.preload['shared/socket']=function() return {udp=function() return udp end,gettime=function() return 10 end} end
        JSON={stringify=function(t) captured=t; return 'encoded' end}
        car={position={x=20,z=0},look={x=0,z=1},timestamp=1000,speedKmh=3.6,
          gear=1,isInPit=false,lapCount=0,lapTimeMs=0,collisionDepth=0,wheelsOutside=0,
          isLastLapValid=true,previousLapTimeMs=0}
        sim={isPaused=false,isInMainMenu=false,isReplayActive=false,isReplayOnlyMode=false}
        ac={getCar=function() return car end,getSim=function() return sim end,
          getTrackID=function() return 'synthetic' end,getCarID=function() return 'test' end,
          onCarJumped=function(index,f) assert(index==0 and type(f)=='function','player callback signature');jump=f end,onSessionStart=function(f) restart=f end}
        ''')
        lua.execute(Path('cleanlap/csp/CleanlapTelemetry/CleanlapTelemetry.lua').read_text(encoding='utf-8'))
        lua.execute("pending=string.rep('a',32); script.update(.02)")
        c=lua.globals().captured
        self.assertAlmostEqual(c.heading,math.pi/2)
        self.assertEqual(c.source_t,1.)
        self.assertEqual(c.speed,1.)
        self.assertEqual(c.wheel_capture_status,'UNKNOWN')
        lua.execute('''
          car.position.y=0;car.look.y=0;car.up={x=0,y=1,z=0};car.side={x=1,y=0,z=0};car.steer=0
          car.wheels={}
          for i=0,3 do car.wheels[i]={position={x=20+(i%2==0 and -.5 or .5),y=.23,z=i<2 and .8 or -.8},
            look={x=0,y=0,z=1},up={x=0,y=1,z=0},outside={x=1,y=0,z=0},tyreRadius=.23,tyreWidth=.2,suspensionTravel=.05} end
          pending=string.rep('d',32);script.update(.02)
        ''')
        self.assertEqual(lua.globals().captured.wheel_capture_status,'OBSERVED_NOT_COMMISSIONED')
        self.assertEqual(len(lua.globals().captured.wheel_capture.wheels),4)
        epoch=c.epoch
        lua.execute("jump(); car.timestamp=1020; pending=string.rep('b',32); script.update(.02)")
        self.assertNotEqual(lua.globals().captured.epoch,epoch)
        lua.execute("sim.isPaused=true; pending=string.rep('c',32); script.update(.02)")
        self.assertFalse(lua.globals().captured.live)
        lua.execute("sim.isPaused=false;sim.isReplayActive=true;pending=string.rep('d',32);script.update(.02)")
        self.assertFalse(lua.globals().captured.live)
        before=lua.globals().captured.epoch
        lua.execute("sim.isReplayActive=false;restart();pending=string.rep('e',32);script.update(.02)")
        self.assertNotEqual(lua.globals().captured.epoch,before)
        self.assertTrue(lua.globals().captured.live)

    def test_socket_to_runtime_to_fake_pad_bicycle_lap_and_faults(self):
        from cleanlap.offline_runtime import exercise
        report=exercise()
        json.dumps(report,allow_nan=False)
        self.assertTrue(report['completed'])
        self.assertGreater(report['distance_m'],120.)
        self.assertEqual(report['evidence'],'SYNTHETIC_SOCKET_FAKE_PAD_NOT_AC')
        for fault in ('reset','out_of_order','teleport','pause','frozen','disconnect'):
            result=exercise(fault=fault)
            self.assertEqual(result['mode'],'STOP',fault)
            self.assertEqual(result['last_throttle'],0.,fault)


if __name__ == '__main__':
    unittest.main()
