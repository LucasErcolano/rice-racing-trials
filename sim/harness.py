"""Scenario harness over sim/cspmock.py: real server + mock CSP + kinematic driver.

Everything named MOCK is simulated; the client Lua and the server are production code.
"""
import json
import math
import os
import random
import tempfile
from pathlib import Path

try:
    from . import cspmock as M
except ImportError:  # run as a script from sim/
    import cspmock as M

ADMIN = 'MOCK-admin-token'
TOKENS = {'ST01': 'MOCK-station-ST01', 'ST02': 'MOCK-station-ST02'}
FSAEJ_GEOMETRY_ENV = 'RICE_FSAEJ_GEOMETRY'


def load_geometry(kind='synthetic'):
    if kind == 'fsaej':
        path = os.environ.get(FSAEJ_GEOMETRY_ENV)
        if not path or not Path(path).is_file():
            return None
        return json.loads(Path(path).read_text(encoding='utf-8'))
    return M.synthetic_geometry()


class Harness:
    def __init__(self, geometry=None, station='ST01', fps=60, seed=7, diagnostics=True,
                 car_fields=None, hidden_apis=(), latency_frames=2, overflow='queue',
                 checkpoints=4, server=None, workdir=None, assign=True):
        self._tmp = None
        if workdir is None:
            self._tmp = tempfile.TemporaryDirectory(prefix='rice-sim-')
            workdir = self._tmp.name
        self.dir = Path(workdir)
        self.geometry = geometry or M.synthetic_geometry()
        self.route_text, self.route = M.build_route(self.geometry, checkpoints=checkpoints)
        self.track = M.Track(self.geometry['ai_path'])
        self.server = server or M.InProcessServer(str(self.dir / 'trials.sqlite'), ADMIN, TOKENS)
        self.station, self.fps = station, fps
        self.rng = random.Random(seed)
        self.disk = M.Disk(self.dir / 'disk')
        self.net = M.Network(latency_frames=latency_frames, overflow=overflow)
        self.car_fields, self.hidden = car_fields, tuple(hidden_apis)
        self.revision = None
        if assign:
            self.admin('POST', '/api/admin/sessions', {'session_id': 'S1', 'name': 'MOCK session'})
            self.admin('POST', '/api/admin/pilots', {'pilot_id': 'P1', 'name': 'MOCK pilot 1'})
            self.admin('POST', '/api/admin/pilots', {'pilot_id': 'P2', 'name': 'MOCK pilot 2'})
            self.assign('P1')
        M.install_app(self.disk, station, self.server.url, TOKENS[station], self.route_text,
                      diagnostics=diagnostics)
        self.rt = None
        self.launch()
        # Spawn in the pit lane equivalent: some metres before the start line on the AI path.
        self.driver = M.Driver(self.track, self.route, s0=self._start_s() - 15.0)
        self.frames_text, self.errors = [], []

    # ------------------------------------------------------------------ setup helpers
    def _start_s(self):
        g = self.route['start']
        mid = ((g['a']['x'] + g['b']['x']) / 2, (g['a']['z'] + g['b']['z']) / 2)
        best = min(range(len(self.track.pts) - 1), key=lambda i: math.dist(self.track.pts[i], mid))
        return self.track.cum[best]

    def admin(self, method, path, payload=None):
        code, body = M.api(self.server.url, method, path, ADMIN, payload)
        if code != 200:
            raise AssertionError(f'admin {path} -> {code} {body}')
        return body

    def assign(self, pilot, session='S1'):
        body = self.admin('POST', '/api/admin/assignments',
                          {'station_id': self.station, 'session_id': session, 'pilot_id': pilot})
        self.revision = body.get('assignment_revision')
        return body

    def launch(self):
        """Start (or restart) the game: fresh Lua state, same disk."""
        self.rt = M.Runtime(self.disk, self.net, car_fields=self.car_fields, hidden_apis=self.hidden,
                            seed=self.rng.randrange(1, 10 ** 6))
        return self.rt

    def close(self):
        self.net.drain()
        if self.server:
            self.server.stop()
        if self._tmp:
            self._tmp.cleanup()

    # ------------------------------------------------------------------ driving
    def dt(self, jitter=0.0):
        base = 1.0 / self.fps
        if jitter:
            return max(1e-4, base * (1 + self.rng.uniform(-jitter, jitter)))
        return base

    def run(self, seconds, jitter=0.0, draw_every=15, update=True, sim=None, move=True):
        t = 0.0
        while t < seconds:
            dt = self.dt(jitter)
            if move:
                self.driver.step(dt)
            draw = draw_every and self.rt.frames % draw_every == 0
            text, errors = self.rt.frame(dt, self.driver.car_state(), update=update, draw=draw, sim=sim)
            if text:
                self.frames_text = text
            self.errors += errors
            t += dt
        return self

    def laps(self, n, **kw):
        return self.run(n * self.lap_time_s() + 1.0, **kw)

    def lap_time_s(self):
        return sum(math.dist(p, q) / max(1.0, self.track.speed_at(self.track.cum[i]) * self.driver.pace)
                   for i, (p, q) in enumerate(zip(self.track.pts, self.track.pts[1:])))

    def settle(self, seconds=6.0):
        """Car parked (no movement) while network replies arrive and retries fire."""
        for _ in range(3):
            self.net.drain()
            self.run(seconds / 3, move=False)
        return self

    def draw(self):
        text, errors = self.rt.frame(self.dt(), self.driver.car_state(), draw=True)
        self.errors += errors
        self.frames_text = text
        return text

    # ------------------------------------------------------------------ observations
    def server_laps(self):
        return self.admin('GET', '/api/admin/laps')['laps']

    def journal_files(self):
        return sorted(p.name for p in (self.disk.ac_root / 'apps/lua/RiceTrials/state').glob('*'))

    def status(self):
        # The HUD model is cached for 0.25 s (client/hud.lua): let it refresh, car parked.
        self.run(0.3, move=False, draw_every=0)
        text = self.draw()
        return next((line[len('Estado: '):] for line in text if line.startswith('Estado: ')),
                    text[1] if len(text) > 1 else None)

    def hud(self):
        self.run(0.3, move=False, draw_every=0)
        return self.draw()

    def go_offline_after_assignment(self):
        """Let the client authenticate its assignment, then cut the network."""
        self.run(0.5, move=False)
        self.net.drain()
        self.run(0.2, move=False)
        self.net.down = True
