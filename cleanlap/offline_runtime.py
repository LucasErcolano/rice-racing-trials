"""Real UDP and production controller, explicitly synthetic bicycle/fake pad."""
import json
import math
import socket
from cleanlap.runtime import Runtime, TelemetrySocket
from cleanlap.geometry import ingest
from cleanlap.planning import plan
from cleanlap.tests.test_geometry import fixture
from cleanlap.control import wrap


class FakeActuator:
    def __init__(self): self.last={'steer':0.,'throttle':0.,'brake':0.}
    def send(self, command): self.last=dict(command)
    def release(self): self.last={'steer':0.,'throttle':0.,'brake':0.}


def exercise(fault=None):
    world=ingest(fixture()); trajectory=plan(world); pad=FakeActuator()
    runtime=Runtime(world,trajectory,pad,authorized=True)
    a,b=trajectory['points'][:2]; x,z=a; h=math.atan2(b[1]-z,b[0]-x); speed=0.
    dt=.02; distance=0.; command={'mode':'HOLD'}
    with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as producer, TelemetrySocket(timeout=.01) as transport:
        producer.bind(('127.0.0.1',0)); transport.peer=producer.getsockname()
        for i in range(15000):
            transport.request(); nonce,address=producer.recvfrom(100)
            row=dict(protocol='cleanlap-1',nonce=nonce.decode(),epoch='boot',seq=i+1,source_t=1+i*dt,
                     live=True,track='synthetic',car='test',gear=1,in_pit=False,lap_count=0,lap_ms=0,
                     x=x,z=z,heading=h,speed=speed)
            if i==30:
                if fault=='reset': row['epoch']='reset'
                if fault=='out_of_order': row['seq']=1
                if fault=='teleport': row['x']+=5
                if fault=='pause': row['live']=False
                if fault=='frozen': row['source_t']-=dt
                if fault=='disconnect':
                    try: transport.receive()
                    except socket.timeout: command=runtime.stop('disconnect')
                    break
            producer.sendto(json.dumps(row).encode(),address)
            command=runtime.step(transport.receive(),now=1+i*dt)
            if command['mode']!='RUNNING': break
            if runtime.controller.progress>=runtime.controller.length: break
            v=runtime.controller.vehicle
            yaw=speed/v.wheelbase*math.tan(pad.last['steer'])
            x+=speed*math.cos(h+yaw*dt/2)*dt; z+=speed*math.sin(h+yaw*dt/2)*dt
            h=wrap(h+yaw*dt); distance+=speed*dt
            speed=max(0.,speed+dt*(v.acceleration*pad.last['throttle']-v.braking*pad.last['brake']))
    return dict(evidence='SYNTHETIC_SOCKET_FAKE_PAD_NOT_AC',completed=bool(runtime.controller.progress>=runtime.controller.length),
                distance_m=distance,mode=command['mode'],reason=command.get('reason'),last_throttle=pad.last['throttle'])


if __name__=='__main__': print(json.dumps(exercise(),indent=2))
