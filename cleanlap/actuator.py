"""vgamepad backend with an independent, latched stale-command watchdog.

The live CLI runs this object in a separate process: a stalled controller/GIL
cannot keep throttle asserted. No keyboard/menu/reverse automation is provided.
"""
import math
import threading
import time


class GuardedPad:
    def __init__(self, pad, steer_scale, steer_sign, timeout=.15):
        if not math.isfinite(steer_scale) or steer_scale <= 0 or steer_sign not in (-1, 1):
            raise ValueError('measured steering calibration required')
        if not .02 <= timeout <= .25:
            raise ValueError('unsafe watchdog timeout')
        self.pad, self.scale, self.sign = pad, steer_scale, steer_sign
        self.timeout = timeout
        self.lock = threading.Lock()
        self.done = threading.Event()
        self.tripped = False
        self.last = time.monotonic()
        self.release()
        self.thread = threading.Thread(target=self._watch, daemon=True)
        self.thread.start()

    def release(self):
        with self.lock:
            self.pad.reset()
            self.pad.update()

    def _watch(self):
        while not self.done.wait(.01):
            with self.lock:
                if not self.tripped and time.monotonic()-self.last > self.timeout:
                    self.tripped = True
                    self.pad.reset()
                    self.pad.update()

    def send(self, command):
        values = [command[k] for k in ('steer', 'throttle', 'brake')]
        if not all(type(v) in (float, int) and math.isfinite(v) for v in values):
            raise ValueError('invalid command')
        steer, gas, brake = values
        if abs(steer*self.scale) > 1.000001 or not 0 <= gas <= 1 or not 0 <= brake <= 1 or gas*brake > 0:
            raise ValueError('command outside calibrated range')
        with self.lock:
            if self.tripped:
                raise RuntimeError('watchdog latched; explicit restart required')
            self.pad.left_joystick_float(x_value_float=max(-1.,min(1.,steer*self.scale*self.sign)), y_value_float=0.)
            self.pad.right_trigger_float(value_float=gas)
            self.pad.left_trigger_float(value_float=brake)
            self.pad.update()
            self.last = time.monotonic()

    def close(self):
        self.done.set()
        self.thread.join(timeout=1.)
        self.release()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def pad_worker(connection, calibration):
    """Only this child imports/creates the Windows pad. EOF always releases."""
    import vgamepad
    try:
        with GuardedPad(vgamepad.VX360Gamepad(), calibration['stick_per_road_rad'],
                        calibration['steer_sign']) as guard:
            connection.send({'ready': True})
            while not guard.tripped:
                if connection.poll(.02):
                    message = connection.recv()
                    if message is None:
                        break
                    if time.monotonic()-message['sent'] > .1:
                        break
                    guard.send(message['command'])
    finally:
        connection.close()
