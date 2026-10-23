"""Standalone localhost telemetry admission and supervised command runtime.

No RiceTrials imports, VM transport, or input creation at import time.
"""
import json
import math
import secrets
import socket
import time


class TelemetrySocket:
    def __init__(self, port=0, peer=('127.0.0.1', 9877), timeout=.10):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.bind(('127.0.0.1', port))
        self.socket.settimeout(timeout)
        self.peer = peer
        self.pending = None

    def request(self):
        if self.pending is not None:
            raise ValueError('request still pending')
        nonce = secrets.token_hex(16)
        self.pending = (nonce, time.monotonic())
        self.socket.sendto(nonce.encode('ascii'), self.peer)
        return nonce

    def receive(self):
        raw, sender = self.socket.recvfrom(8193)
        if len(raw) > 8192 or sender != self.peer or self.pending is None:
            raise ValueError('unsolicited/oversize telemetry')
        nonce, sent = self.pending
        self.pending = None
        row = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite JSON')))
        if row.get('nonce') != nonce or time.monotonic()-sent > .15:
            raise ValueError('stale/replayed telemetry')
        return row

    def close(self):
        self.socket.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class Runtime:
    """Same tested feedback algorithm; admission/safety are outside the controller.

    authorized is dependency injection for offline tests, NOT the live CLI gate.
    The CLI must complete preflight before constructing a real actuator.
    """
    def __init__(self, world, plan, actuator, authorized=False, connector=None, commissioned_car=None, admitted_epoch=None):
        import numpy as np
        from cleanlap.control import Controller
        from cleanlap.planning import validate_path, Vehicle
        if plan.get('geometry_sha256') != world.digest:
            raise ValueError('world/plan identity mismatch')
        v = Vehicle(**plan['vehicle'])
        audit = validate_path(world,plan['points'],v)
        if (not plan.get('geometric_clear') or audit['cone_margin_conflicts'] or audit['offtrack_segments']
                or not audit['gates_ok'] or audit['max_curvature'] > math.tan(v.max_steer)/v.wheelbase):
            raise ValueError('unsafe plan')
        self.world, self.plan, self.actuator = world, plan, actuator
        self.controller = Controller(plan,simulation=True)  # algorithm reuse; NOT an authorization bypass
        self.authorized = authorized
        self.commissioned_car = commissioned_car
        self.admitted_epoch = admitted_epoch
        self.last = None
        self.reason = None
        self.connector = connector
        self.connector_index = 0
        self.rows = []
        self.phase = 'PIT_START' if connector is not None else 'START'
        if connector is not None:
            from cleanlap.startup import verify_connector
            p = np.asarray(connector,float)
            h0 = math.atan2(*(p[1]-p[0])[::-1]); end = np.asarray(plan['points'][0])
            h1 = math.atan2(*(np.asarray(plan['points'][1])-end)[::-1])
            if np.linalg.norm(p[-1]-end) > .05 or not verify_connector(world,p,v,h0,h1)['safe']:
                raise ValueError('unverified merge')

    def stop(self, reason):
        self.reason = self.reason or reason
        self.actuator.release()
        return {'mode':'STOP','reason':self.reason,'steer':0.,'throttle':0.,'brake':0.}

    def step(self, frame, now=None):
        from cleanlap.control import State, wrap
        from cleanlap.planning import footprint, obstacles
        from shapely.ops import unary_union
        now = time.monotonic() if now is None else now
        if self.reason:
            return self.stop(self.reason)
        if not self.authorized:
            self.actuator.release()
            return {'mode':'HOLD','reason':'not_armed','steer':0.,'throttle':0.,'brake':0.}
        try:
            if frame.get('protocol') != 'cleanlap-1' or frame.get('live') is not True:
                raise ValueError('not_live')
            for key in ('x','z','heading','speed','source_t'):
                if type(frame.get(key)) not in (int,float) or not math.isfinite(frame[key]):
                    raise ValueError('nonfinite_state')
            for key in ('seq','gear','lap_count','lap_ms'):
                if type(frame.get(key)) is not int:
                    raise ValueError('invalid_counter')
            if (frame['speed'] < 0 or frame['speed'] > self.controller.vehicle.max_speed+3
                    or not isinstance(frame.get('epoch'),str) or not frame['epoch']):
                raise ValueError('invalid_state')
            if frame.get('track') != self.world.document['track']:
                raise ValueError('track_identity')
            if ((self.commissioned_car is not None and frame.get('car') != self.commissioned_car)
                    or (self.admitted_epoch is not None and frame.get('epoch') != self.admitted_epoch)):
                raise ValueError('reset_or_car_changed')
            if self.last:
                old,old_now = self.last
                if frame['epoch'] != old['epoch'] or frame['car'] != old['car']:
                    raise ValueError('reset_or_car_changed')
                if frame['seq'] <= old['seq'] or not 0 < frame['source_t']-old['source_t'] <= .15:
                    raise ValueError('out_of_order_or_frozen')
                if not 0 < now-old_now <= .15:
                    raise ValueError('receive_gap')
                distance = math.hypot(frame['x']-old['x'],frame['z']-old['z'])
                if distance > .3+max(old['speed'],frame['speed'])*(frame['source_t']-old['source_t'])*1.5:
                    raise ValueError('teleport')
                if frame['lap_count'] < old['lap_count']:
                    raise ValueError('lap_reset')
            elif frame['speed'] > .2:
                raise ValueError('startup_requires_stationary')
            v = self.controller.vehicle
            body = footprint(frame['x'],frame['z'],frame['heading'],v)
            if self.last:
                old,_ = self.last
                before = footprint(old['x'],old['z'],old['heading'],v)
                turn = abs(wrap(frame['heading']-old['heading']))
                body = unary_union([body,before]).convex_hull.buffer(math.hypot(v.half_length,v.half_width)*(1-math.cos(turn/2)))
            if not self.world.road.buffer(-v.margin).covers(body):
                raise ValueError('outside_verified_envelope')
            if any(body.intersects(o) for o in obstacles(self.world,v.margin)):
                raise ValueError('cone_safety_envelope')
            self.last = (dict(frame),now)
            if frame['gear'] < 1:
                self.actuator.release()
                return {'mode':'HOLD','reason':'engage_forward_gear_manually','steer':0.,'throttle':0.,'brake':0.}
            if self.connector is not None:
                command = self._pit(frame)
            else:
                state = State(frame['x'],frame['z'],frame['heading'],frame['speed'],frame['source_t'])
                command = self.controller.update(state,frame['source_t'])
            if command['mode'] != 'RUNNING':
                return self.stop(command.get('reason') or 'controller_stop')
            self.actuator.send(command)
            self.rows.append({**frame,**command,'received_t':now,'startup_phase':self.phase})
            return command
        except (ValueError,KeyError,TypeError,RuntimeError) as error:
            return self.stop(str(error))

    def _pit(self, frame):
        import numpy as np
        from cleanlap.control import wrap
        p = np.asarray(self.connector)
        start = self.connector_index
        candidates = range(max(0,start-1),min(len(p),start+15))
        i = min(candidates,key=lambda i: np.linalg.norm(p[i]-[frame['x'],frame['z']]))
        if np.linalg.norm(p[i]-[frame['x'],frame['z']]) > .4:
            raise ValueError('pit_tracking_envelope')
        self.connector_index = max(i,start)
        if i >= len(p)-3 and math.hypot(frame['x']-p[-1,0],frame['z']-p[-1,1]) < .3:
            self.connector = None
            self.phase = 'MERGED'
            from cleanlap.control import State
            return self.controller.update(State(frame['x'],frame['z'],frame['heading'],frame['speed'],frame['source_t']),frame['source_t'])
        target = p[min(len(p)-1,i+6)]
        delta = target-[frame['x'],frame['z']]
        alpha = wrap(math.atan2(delta[1],delta[0])-frame['heading'])
        if abs(alpha) > .6:
            raise ValueError('pit_heading_envelope')
        steer = math.atan2(2*self.controller.vehicle.wheelbase*math.sin(alpha),max(.2,np.linalg.norm(delta)))
        if abs(steer) > self.controller.vehicle.max_steer:
            raise ValueError('pit_steering_limit')
        error = .7-frame['speed']
        self.phase = 'PIT_MERGE'
        return {'mode':'RUNNING','reason':None,'steer':steer,'throttle':min(.35,max(0.,.8*error)),
                'brake':min(1.,max(0.,-.6*error)),'target_speed':.7,'progress':0.}
