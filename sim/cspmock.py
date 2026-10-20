"""MOCK Assetto Corsa / CSP host for the unmodified production Lua client.

What is real here: client/*.lua (loaded from an app directory exactly as installed), LuaJIT 2.1
(lupa.luajit21, the VM family CSP embeds), CSP's JSON implementation (vendored verbatim from
acc-lua-sdk), the Python trials server (in-process or the PyInstaller exe over real HTTP), and
the files on disk (real directories, real paths, the host OS file semantics).

What is MOCKED (never evidence of CSP behaviour): the ac/io/web/ui API surface, the timing of
web callbacks, FFI struct layouts, how/when CSP calls script.update during pause, replay and
session restarts, and the car physics (a kinematic driver following the AI line).
See docs/csp-mock.md.
"""
import bisect
import concurrent.futures
import json
import math
import os
import random
import shutil
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import lupa.luajit21 as luajit

ROOT = Path(__file__).resolve().parents[1]
SIM = Path(__file__).resolve().parent

# Field sets for the FFI structs. CSP's real state_car has many more fields; only the ones the
# client/diagnostics read are modelled. 'modern' is what docs/csp-api-reference.md describes.
CAR_FIELDS_MODERN = [
    ('position', 'rr_vec3'), ('look', 'rr_vec3'), ('velocity', 'rr_vec3'), ('speedKmh', 'float'),
    ('splinePosition', 'float'), ('lapTimeMs', 'int'), ('lapCount', 'int'), ('isLapValid', 'bool'),
    ('previousLapTimeMs', 'int'), ('isLastLapValid', 'bool'), ('collidedWith', 'int'),
    ('collisionDepth', 'float'), ('wheelsOutside', 'int'), ('resetCounter', 'int'),
]
CAR_FIELDS_LEGACY = [f for f in CAR_FIELDS_MODERN if f[0] not in ('resetCounter', 'collisionDepth')]
SIM_FIELDS = [('isPaused', 'bool'), ('isReplayActive', 'bool'), ('isInMainMenu', 'bool'),
              ('dt', 'float'), ('time', 'double'), ('frame', 'int')]


# ----------------------------------------------------------------------------- disk
class Disk:
    """Real directory tree standing in for the AC install + CSP's ac.storage file.

    Survives runtime restarts (a game restart keeps the disk). Fault hooks are consulted per io
    call: faults[name] = list of (path_substring, action, remaining_count); actions are
    'false' (return false), 'raise' (Lua error), 'torn' (write half then raise: crash mid-write),
    'enospc' (return false, nothing written).
    """

    def __init__(self, root):
        self.root = Path(root)
        self.ac_root = self.root / 'ac'
        (self.ac_root / 'apps' / 'lua').mkdir(parents=True, exist_ok=True)
        self.storage = {}
        self.faults = {}
        self.calls = []

    def inject(self, op, action, path_contains='', count=1):
        self.faults.setdefault(op, []).append([path_contains, action, count])

    def clear_faults(self):
        self.faults = {}

    def _fault(self, op, path):
        for rule in self.faults.get(op, []):
            if rule[2] != 0 and rule[0] in str(path):
                if rule[2] > 0:
                    rule[2] -= 1
                return rule[1]
        return None

    def _p(self, path):
        return Path(str(path))

    def io(self, name, *args):
        path = args[0] if args else None
        self.calls.append((name, str(path)))
        fault = self._fault(name, path)
        if fault == 'raise':
            return False, f'MOCK io.{name} raised (injected)', None
        if fault in ('false', 'enospc'):
            return True, False, None
        try:
            if name == 'save':
                target, data = self._p(path), args[1]
                if not target.parent.is_dir():
                    return True, False, None
                if data is None:
                    data = ''
                if fault == 'torn':
                    with open(target, 'w', encoding='utf-8', newline='') as fh:
                        fh.write(str(data)[: max(1, len(str(data)) // 2)])
                    return False, 'MOCK crash during io.save (torn write injected)', None
                with open(target, 'w', encoding='utf-8', newline='') as fh:
                    fh.write(str(data))
                return True, True, None
            if name == 'load':
                fallback = args[1] if len(args) > 1 else None
                target = self._p(path)
                if not target.is_file():
                    return True, fallback, None
                return True, target.read_text(encoding='utf-8', errors='replace'), None
            if name == 'fileExists':
                return True, self._p(path).is_file(), None
            if name == 'createDir':
                self._p(path).mkdir(parents=True, exist_ok=True)
                return True, True, None
            if name == 'deleteFile':
                target = self._p(path)
                if target.is_file():
                    target.unlink()
                    return True, True, None
                return True, False, None
            if name == 'move':
                src, dst = self._p(args[0]), self._p(args[1])
                # Windows MoveFileEx without MOVEFILE_REPLACE_EXISTING: refuses an existing target.
                if not src.is_file() or dst.exists():
                    return True, False, None
                os.rename(src, dst)
                return True, True, None
        except OSError as error:
            return True, False, None if error else None
        return False, f'MOCK io.{name} not modelled', None


# ----------------------------------------------------------------------------- network
class Network:
    """web.get/web.post transport: real HTTP to the trials server, with injectable faults.

    Callbacks are delivered on the 'main thread' at the start of a frame no earlier than
    latency_frames after the request (CSP delivers web replies asynchronously between frames;
    the exact timing is not documented). Fault modes per request (first matching rule):
      'refused'      no HTTP, err string (server unreachable)
      'drop_reply'   request reaches the server, the reply is lost (err string)
      'http500'      reply status 500
      'garbage'      status 200, body not JSON
    """

    def __init__(self, latency_frames=2, max_parallel=2, overflow='queue'):
        self.latency_frames = latency_frames
        self.max_parallel = max_parallel
        self.overflow = overflow  # 'queue' or 'reject' when more than max_parallel in flight
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=8)
        self.pending = []
        self.next_id = 1
        self.frame = 0
        self.down = False
        self.rules = []
        self.log = []
        self.max_in_flight = 0
        self.overflows = 0

    def inject(self, mode, path_contains='', count=1):
        self.rules.append([path_contains, mode, count])

    def _rule(self, url):
        if self.down:
            return 'refused'
        for rule in self.rules:
            if rule[2] != 0 and rule[0] in url:
                if rule[2] > 0:
                    rule[2] -= 1
                return rule[1]
        return None

    @staticmethod
    def _http(method, url, headers, data):
        req = urllib.request.Request(url, method=method, data=data.encode('utf-8') if data is not None else None)
        for line in (headers or '').split('\n'):
            if '=' in line:
                k, v = line.split('=', 1)
                req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                return None, response.status, response.read().decode('utf-8', 'replace')
        except urllib.error.HTTPError as error:
            return None, error.code, error.read().decode('utf-8', 'replace')
        except (urllib.error.URLError, OSError) as error:
            return f'MOCK transport error: {error}', 0, ''

    def request(self, method, url, headers, data):
        rid = self.next_id
        self.next_id += 1
        in_flight = sum(1 for p in self.pending) + 1
        self.max_in_flight = max(self.max_in_flight, in_flight)
        mode = self._rule(url)
        if in_flight > self.max_parallel:
            self.overflows += 1
            if self.overflow == 'reject':
                mode = 'reject'
        entry = {'id': rid, 'method': method, 'url': url, 'mode': mode, 'due': self.frame + self.latency_frames,
                 'body': data}
        if mode in ('refused', 'reject'):
            entry['future'] = None
        else:
            entry['future'] = self.pool.submit(self._http, method, url, headers, data)
        self.pending.append(entry)
        self.log.append({'id': rid, 'frame': self.frame, 'method': method,
                         'path': url.split('//', 1)[-1].split('/', 1)[-1], 'mode': mode})
        return rid

    def next_reply(self):
        for i, entry in enumerate(self.pending):
            if entry['due'] <= self.frame:
                self.pending.pop(i)
                mode = entry['mode']
                if mode == 'refused':
                    return entry['id'], 'MOCK: connection refused', 0, ''
                if mode == 'reject':
                    return entry['id'], 'MOCK: too many concurrent requests', 0, ''
                err, status, body = entry['future'].result(timeout=30)
                if mode == 'drop_reply':
                    return entry['id'], 'MOCK: reply lost after server processed request', 0, ''
                if mode == 'http500':
                    return entry['id'], None, 500, '{"error":"MOCK injected 500"}'
                if mode == 'garbage':
                    return entry['id'], None, 200, '<html>MOCK captive portal</html>'
                return entry['id'], err, status, body
        return None

    def drain(self):
        for entry in self.pending:
            if entry.get('future'):
                entry['future'].result(timeout=30)


# ----------------------------------------------------------------------------- track/driver
def _side(p, a, b):
    return (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])


def crossed(prev, cur, gate):
    """Independent Python re-implementation of the crossing predicate, for ground truth."""
    a, b, d = (gate['a']['x'], gate['a']['z']), (gate['b']['x'], gate['b']['z']), gate['direction']
    before, after = _side(prev, a, b), _side(cur, a, b)
    if d * before >= 0 or d * after < 0:
        return None
    t = before / (before - after)
    x, z = prev[0] + t * (cur[0] - prev[0]), prev[1] + t * (cur[1] - prev[1])
    dx, dz = b[0] - a[0], b[1] - a[1]
    proj = ((x - a[0]) * dx + (z - a[1]) * dz) / (dx * dx + dz * dz)
    return t if 0 <= proj <= 1 else None


class Track:
    def __init__(self, path, closed=True):
        pts = [tuple(map(float, p[:2])) for p in path]
        if closed and math.dist(pts[0], pts[-1]) > 1e-6:
            pts.append(pts[0])
        self.pts = pts
        self.cum = [0.0]
        for p, q in zip(pts, pts[1:]):
            self.cum.append(self.cum[-1] + math.dist(p, q))
        self.length = self.cum[-1]
        self.speed = self._speed_profile()

    def at(self, s):
        s %= self.length
        i = max(0, min(len(self.pts) - 2, bisect.bisect_right(self.cum, s) - 1))
        seg = self.cum[i + 1] - self.cum[i] or 1e-9
        u = (s - self.cum[i]) / seg
        p, q = self.pts[i], self.pts[i + 1]
        return (p[0] + u * (q[0] - p[0]), p[1] + u * (q[1] - p[1])), ((q[0] - p[0]) / seg, (q[1] - p[1]) / seg)

    def _speed_profile(self, vmax=28.0, alat=14.0, acc=6.0, brake=11.0):
        n = len(self.pts) - 1
        v = []
        for i in range(n):
            a, b, c = self.pts[i - 1], self.pts[i], self.pts[(i + 1) % n]
            ab, bc, ca = math.dist(a, b), math.dist(b, c), math.dist(c, a)
            cross = abs((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]))
            radius = (ab * bc * ca) / (2 * cross) if cross > 1e-9 else 1e9
            v.append(min(vmax, math.sqrt(alat * radius)))
        for _ in range(2):  # loop twice so the closure wraps
            for i in range(1, n + 1):
                d = self.cum[i % n] - self.cum[i - 1] if i < n else self.cum[n] - self.cum[n - 1]
                v[i % n] = min(v[i % n], math.sqrt(v[i - 1] ** 2 + 2 * acc * d))
            for i in range(n - 1, -1, -1):
                d = self.cum[i + 1] - self.cum[i]
                v[i] = min(v[i], math.sqrt(v[(i + 1) % n] ** 2 + 2 * brake * d))
        return v

    def speed_at(self, s):
        s %= self.length
        i = max(0, min(len(self.speed) - 1, bisect.bisect_right(self.cum, s) - 1))
        return self.speed[i]


def synthetic_geometry():
    """Stadium-shaped loop with a slalom-ish chicane: NOT FSAEJ. Used when the proprietary
    geometry is not available (CI). Labelled track_id FSAEJ_Endurance because the client
    accepts only that id; asset hash is all zeros and marked synthetic."""
    pts = []
    r, straight = 25.0, 120.0
    for i in range(60):
        pts.append((i * straight / 60, -r))
    for i in range(40):
        a = -math.pi / 2 + math.pi * i / 40
        pts.append((straight + r * math.cos(a), r * math.sin(a)))
    for i in range(60):
        x = straight - i * straight / 60
        pts.append((x, r + 4.0 * math.sin(2 * math.pi * i / 20)))
    for i in range(40):
        a = math.pi / 2 + math.pi * i / 40
        pts.append((r * math.cos(a), r * math.sin(a)))
    pts = [[round(x, 3), round(z, 3)] for x, z in pts]
    s0 = 20.0
    track = Track(pts)
    (x, z), (tx, tz) = track.at(s0)
    nx, nz = -tz, tx
    a = {'x': round(x + nx * 6, 4), 'z': round(z + nz * 6, 4)}
    b = {'x': round(x - nx * 6, 4), 'z': round(z - nz * 6, 4)}
    (px, pz), _ = track.at(s0 - 2)
    before = _side((px, pz), (a['x'], a['z']), (b['x'], b['z']))
    return {'schema': 'synthetic', 'track': 'FSAEJ_Endurance', 'synthetic': True,
            'source_sha256': {'Physics.kn5': '0' * 64},
            'timing_gate': {'a': a, 'b': b, 'direction': -1 if before > 0 else 1,
                            'source': 'SYNTHETIC', 'direction_source': 'synthetic loop order'},
            'ai_path': pts}


def build_route(geometry, checkpoints=4, operator='MOCK-SIM'):
    """route_config.lua via the production exporter (track/route_export.py)."""
    import sys
    sys.path.insert(0, str(ROOT))
    from track import route_export
    gates = route_export.checkpoints_from_ai(geometry['ai_path'], checkpoints, width=12.0)
    text, summary = route_export.build(geometry, gates, approve=True, operator=operator,
                                       checkpoint_source='MOCK candidates from AI line')
    return text, summary


class Driver:
    """Kinematic car following the AI line; produces AC-like car state and ground truth."""

    def __init__(self, track, route_summary, s0=0.0, pace=1.0):
        self.track, self.route = track, route_summary
        self.s, self.t, self.pace = s0, 0.0, pace
        self.reset_counter, self.lap_count, self.lap_start_t = 0, 0, None
        self.prev_lap_ms, self.start_crossings = 0, []
        self.reverse = False
        pos, tan = track.at(s0)
        self.pos, self.tan, self.speed = pos, tan, 0.0

    PHYSICS_HZ = 1000  # ground truth integrates at 1 kHz, independent of the frame rate

    def step(self, dt):
        """Advance one rendered frame. Ground-truth start-line crossings are found on the
        1 kHz sub-steps (not on the frame chord the client sees), so client error is real."""
        n = max(1, int(round(dt * self.PHYSICS_HZ)))
        h = dt / n
        for _ in range(n):
            v = self.track.speed_at(self.s) * self.pace
            prev = self.pos
            self.s += (-v if self.reverse else v) * h
            self.pos, self.tan = self.track.at(self.s)
            self.speed = v
            t_frac = crossed(prev, self.pos, self.route['start'])
            if t_frac is not None:
                ct = self.t + t_frac * h
                if self.lap_start_t is not None:
                    self.prev_lap_ms = int(round((ct - self.lap_start_t) * 1000))
                self.lap_start_t = ct
                self.lap_count += 1
                self.start_crossings.append(ct)
            self.t += h

    def truth_laps_ms(self):
        c = self.start_crossings
        return [(b - a) * 1000 for a, b in zip(c, c[1:])]

    def teleport(self, s, count_reset=True):
        self.s = s
        self.pos, self.tan = self.track.at(s)
        self.speed = 0.0
        self.lap_start_t = None
        if count_reset:
            self.reset_counter += 1

    def car_state(self):
        x, z = self.pos
        sign = -1 if self.reverse else 1
        return {'position': [x, 0.0, z], 'look': [sign * self.tan[0], 0.0, sign * self.tan[1]],
                'velocity': [sign * self.tan[0] * self.speed, 0.0, sign * self.tan[1] * self.speed],
                'speedKmh': self.speed * 3.6, 'splinePosition': (self.s % self.track.length) / self.track.length,
                'lapTimeMs': int((self.t - self.lap_start_t) * 1000) if self.lap_start_t is not None else 0,
                'lapCount': self.lap_count, 'isLapValid': True, 'previousLapTimeMs': self.prev_lap_ms,
                'isLastLapValid': True, 'collidedWith': -1, 'collisionDepth': 0.0, 'wheelsOutside': 0,
                'resetCounter': self.reset_counter}


# ----------------------------------------------------------------------------- server
class InProcessServer:
    """The production server (server/app.py Store + handler) on a real localhost socket."""

    def __init__(self, db_path, admin_token, station_tokens, port=0):
        import sys
        sys.path.insert(0, str(ROOT))
        from server.app import Store, make_handler
        self.store = Store(db_path)
        self.handler = make_handler(self.store, admin_token, station_tokens)
        self.port = port
        self.httpd = None
        self.start()

    def start(self):
        self.httpd = ThreadingHTTPServer(('127.0.0.1', self.port), self.handler)
        self.port = self.httpd.server_port
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def stop(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None

    @property
    def url(self):
        return f'http://127.0.0.1:{self.port}'


class ExeServer:
    """The frozen production server (RiceTrialsServer.exe built by PyInstaller) as a subprocess."""

    def __init__(self, exe, db_path, admin_token, station_tokens):
        import socket
        import subprocess
        import time
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            self.port = sock.getsockname()[1]
        env = dict(os.environ, RICE_ADMIN_TOKEN=admin_token, RICE_STATION_TOKENS=json.dumps(station_tokens))
        self.log = open(str(db_path) + '.server.log', 'wb')
        self.proc = subprocess.Popen([exe, '--db', str(db_path), '--host', '127.0.0.1', '--port', str(self.port)],
                                     env=env, stdin=subprocess.DEVNULL, stdout=self.log,
                                     stderr=subprocess.STDOUT)
        deadline = time.time() + 30
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError('server exe exited with %s' % self.proc.returncode)
            try:
                urllib.request.urlopen(self.url + '/tv', timeout=2).read()
                return
            except Exception:
                time.sleep(0.2)
        self.stop()
        raise RuntimeError('server exe did not answer GET /tv')

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except Exception:
                self.proc.kill()
        self.proc = None
        self.log.close()

    @property
    def url(self):
        return f'http://127.0.0.1:{self.port}'


def make_server(db_path, admin_token, station_tokens):
    """RICE_SIM_SERVER_EXE selects the frozen exe; default is server/app.py in process."""
    exe = os.environ.get('RICE_SIM_SERVER_EXE')
    if exe:
        return ExeServer(exe, db_path, admin_token, station_tokens)
    return InProcessServer(db_path, admin_token, station_tokens)


def api(base, method, path, token=None, payload=None):
    req = urllib.request.Request(base + path, method=method,
                                 data=json.dumps(payload).encode() if payload is not None else None)
    if token:
        req.add_header('Authorization', 'Bearer ' + token)
    if payload is not None:
        req.add_header('Content-Type', 'application/json')
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status, json.loads(response.read() or b'{}')
    except urllib.error.HTTPError as error:
        body = error.read()
        try:
            return error.code, json.loads(body)
        except ValueError:
            return error.code, {'raw': body.decode('utf-8', 'replace')}


# ----------------------------------------------------------------------------- app install
def lua_bytes(text):
    """Same encoding idea as install.ps1 ConvertTo-LuaByteString: decimal escapes only."""
    return '"' + ''.join('\\%d' % b for b in text.encode('utf-8')) + '"'


def install_app(disk, station_id, server_url, token, route_text, diagnostics=False, extra=None,
                source=None):
    """Copy the production client into <ac>/apps/lua/RiceTrials like install.ps1 does
    (only *.lua + manifest.ini, never tests), then write station_config.lua / route_config.lua."""
    source = Path(source or ROOT / 'client')
    target = disk.ac_root / 'apps' / 'lua' / 'RiceTrials'
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    for f in source.iterdir():
        if f.is_file() and (f.suffix == '.lua' or f.name == 'manifest.ini') and f.name not in (
                'station_config.lua', 'route_config.lua'):
            shutil.copy2(f, target / f.name)
    write_station_config(disk, station_id, server_url, token, diagnostics, extra)
    if route_text is not None:
        (target / 'route_config.lua').write_text(route_text, encoding='utf-8')
    return target


def write_station_config(disk, station_id, server_url, token, diagnostics=False, extra=None):
    target = disk.ac_root / 'apps' / 'lua' / 'RiceTrials'
    fields = ['server_url=' + lua_bytes(server_url), 'station_id=' + lua_bytes(station_id),
              'station_token=' + lua_bytes(token)]
    if diagnostics:
        fields.append('diagnostics=true')
    for k, v in (extra or {}).items():
        fields.append(f'{k}={json.dumps(v) if isinstance(v, str) else v}')
    (target / 'station_config.lua').write_text('return {' + ', '.join(fields) + '}\n', encoding='utf-8')


# ----------------------------------------------------------------------------- runtime
class Runtime:
    """One 'game launch': a fresh LuaJIT state that loads the installed app from disk."""

    def __init__(self, disk, net, car_fields=None, hidden_apis=(), app_dir=None, seed=1):
        self.disk, self.net = disk, net
        self.lua = luajit.LuaRuntime(unpack_returned_tuples=True)
        jit = self.lua.eval('jit and jit.version')
        if not jit or not str(jit).startswith('LuaJIT 2.1'):
            raise RuntimeError(f'LuaJIT 2.1 required, got {jit}')
        self.jit_version = str(jit)
        self.log_lines = []
        self.hidden = list(hidden_apis)
        self.lua.execute(f'math.randomseed({int(seed)})')
        bridge = _Bridge(self)
        car_fields = car_fields or CAR_FIELDS_MODERN
        to_fields = lambda fs: self.lua.table_from([self.lua.table_from(list(f)) for f in fs])
        prelude = (SIM / 'csp_prelude.lua').read_text(encoding='utf-8')
        loader = self.lua.eval('function(src, ...) return assert(loadstring(src, "=csp_prelude.lua"))(...) end')
        self.api = loader(prelude, bridge, to_fields(car_fields), to_fields(SIM_FIELDS))
        self.app_dir = Path(app_dir or disk.ac_root / 'apps' / 'lua' / 'RiceTrials')
        self.api.load_app(self.app_dir.as_posix())
        self.frames = 0
        self.sim_state = {'isPaused': False, 'isReplayActive': False, 'isInMainMenu': False}

    def frame(self, dt, car, update=True, draw=False, sim=None):
        self.net.frame += 1
        self.frames += 1
        s = dict(self.sim_state)
        s.update(sim or {})
        s['dt'], s['frame'] = dt, self.frames
        table = self.lua.table_from({'car': self.lua.table_from({k: (self.lua.table_from(v) if isinstance(v, list) else v)
                                                                  for k, v in car.items()}),
                                     'sim': self.lua.table_from(s), 'update': update})
        text, errors = self.api.frame(dt, table, draw)
        text = [text[i] for i in range(1, len(text) + 1)] if text else []
        errors = [errors[i] for i in range(1, len(errors) + 1)] if errors else []
        return text, errors

    def car_jumped(self):
        return self.api.fire_car_jumped()

    def session_start(self, index=0, restarted=True):
        return self.api.fire_session_start(index, restarted)


class _Bridge:
    """Python functions the Lua prelude calls (attribute access only)."""

    def __init__(self, rt):
        self._rt = rt

    def vendor_json_path(self):
        return str(SIM / 'vendor' / 'csp_json.lua')

    def ac_root(self):
        return self._rt.disk.ac_root.as_posix()

    def car_id(self):
        return 'madformulateam_mft02'

    def track_full_id(self):
        return 'FSAEJ_Endurance'

    def log(self, text):
        self._rt.log_lines.append(str(text))

    def has_api(self, name):
        return name not in self._rt.hidden

    def hidden_apis(self):
        return self._rt.lua.table_from([n for n in self._rt.hidden])

    def storage_get(self, key):
        return self._rt.disk.storage.get(key)

    def storage_set(self, key, value):
        if value is None:
            self._rt.disk.storage.pop(key, None)
        else:
            self._rt.disk.storage[key] = value

    def io(self, name, *args):
        return self._rt.disk.io(name, *args)

    def web_request(self, method, url, headers, data):
        return self._rt.net.request(method, url, headers, data)

    def next_reply(self):
        return self._rt.net.next_reply()
