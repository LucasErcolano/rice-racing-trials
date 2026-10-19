"""Real-browser tests (Playwright + Chromium) against the real HTTP server with data seeded through the API.

Skipped cleanly when playwright or its Chromium build is unavailable.
Screenshots go to docs/evidence/screenshots only when RICE_BROWSER_EVIDENCE=1 (otherwise a temp dir),
so the base suite never rewrites committed evidence.
Run: RICE_BROWSER_EVIDENCE=1 python -m unittest tests.browser.test_browser -v
"""
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path

from server.app import Store, make_handler

try:
    from playwright.sync_api import sync_playwright, expect
except ImportError:  # pragma: no cover
    sync_playwright = None

ROOT = Path(__file__).resolve().parents[2]
ADMIN = 'browser-admin-secret'
STATIONS = {f'ST0{i}': f'browser-station-{i}' for i in range(1, 5)}
EVIDENCE = os.environ.get('RICE_BROWSER_EVIDENCE') == '1'


def log(message):
    print(time.strftime('%H:%M:%S ') + message, file=sys.stderr, flush=True)


class Server:
    """ThreadingHTTPServer on an ephemeral port, with an API client for seeding."""

    def __init__(self, folder, armed):
        self.store = Store(Path(folder) / ('armed' if armed else 'unarmed') / 'trials.sqlite', scoring_armed=armed)
        self.start()

    def start(self, port=0):
        self.http = ThreadingHTTPServer(('127.0.0.1', port), make_handler(self.store, ADMIN, STATIONS))
        self.port = self.http.server_port
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()

    def stop(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join()

    @property
    def url(self):
        return f'http://127.0.0.1:{self.port}'

    def call(self, path, data, token=ADMIN):
        conn = HTTPConnection('127.0.0.1', self.port, timeout=10)
        conn.request('POST', path, json.dumps(data).encode(), {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token})
        response = conn.getresponse()
        body = json.loads(response.read())
        conn.close()
        if response.status != 200:
            raise AssertionError(f'{path} -> {response.status} {body}')
        return body

    def lap(self, station, pilot, lap_id, raw_ms, session='S1', event='autocross', events=None, **extra):
        self.call('/api/admin/assignments', {'station_id': station, 'session_id': session, 'pilot_id': pilot})
        data = dict(protocol_version=1, station_id=station, session_id=session, pilot_id=pilot, lap_id=lap_id, raw_ms=raw_ms,
                    event=event, status='valid', events=events if events is not None else {'doo': 0, 'oc': 0}, lap_number=1)
        data.update(extra)
        return self.call('/api/laps', data, STATIONS[station])

    def heartbeat(self, station):
        self.call('/api/station/heartbeat', {'protocol_version': 1, 'station_id': station}, STATIONS[station])


NAMES = ['Ana Gómez', 'Bruno Díaz', 'Carla Ruiz', 'Diego Paz', 'Elena Sosa', 'Facundo Ríos', 'Gala Torres', 'Hugo Vera',
         'Inés Molina', 'Julián Castro', 'Karen Ortiz', 'Lucas Benítez', 'María Luna', 'Nicolás Rey', 'Olga Ferrer', 'Pablo Ibáñez']


def seed_ranking(server, session='S1', pilots=16, name='Clasificación mañana', active=True):
    server.call('/api/admin/sessions', {'session_id': session, 'name': name, 'active': active})
    for i in range(1, pilots + 1):
        pilot = f'{session}-P{i:02d}'
        server.call('/api/admin/pilots', {'pilot_id': pilot, 'name': NAMES[(i - 1 + (3 if session != 'S1' else 0)) % len(NAMES)]})
        station = f'ST0{(i - 1) % 4 + 1}'
        # Deliberately scrambled submission order and penalties: ranking must sort by corrected time.
        raw = 60000 + ((i * 7919) % 16) * 450
        server.lap(station, pilot, f'{session}-L{i:02d}a', raw, session=session, events={'doo': i % 3, 'oc': 0})
        server.lap(station, pilot, f'{session}-L{i:02d}b', raw + 3000, session=session, events={'doo': 0, 'oc': 0})
        if i % 5 == 0:  # a provisional lap that must never rank
            server.lap(station, pilot, f'{session}-L{i:02d}x', 30000, session=session, status='dnf')


@unittest.skipIf(sync_playwright is None, 'playwright not installed')
class BrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.shots = ROOT / 'docs' / 'evidence' / 'screenshots' if EVIDENCE else Path(cls.tmp.name) / 'shots'
        cls.shots.mkdir(parents=True, exist_ok=True)
        try:
            cls.pw = sync_playwright().start()
            cls.browser = cls.pw.chromium.launch()
        except Exception as error:  # pragma: no cover: browser binary missing
            raise unittest.SkipTest(f'chromium unavailable: {error}')
        log(f'chromium {cls.browser.version}; evidence={cls.shots}')

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.tmp.cleanup()

    def setUp(self):
        self.servers = []
        self.context = self.browser.new_context(viewport={'width': 1920, 'height': 1080}, accept_downloads=True)
        self.page = self.context.new_page()
        self.console_errors = []
        self.page.on('console', lambda m: m.type == 'error' and self.console_errors.append(m.text))
        self.page.on('pageerror', lambda e: self.console_errors.append(str(e)))

    def tearDown(self):
        self.context.close()
        for server in self.servers:
            try:
                server.stop()
            except Exception:
                pass

    def server(self, armed=True):
        server = Server(self.tmp.name + f'/{self.id()}', armed)
        self.servers.append(server)
        return server

    def shot(self, name):
        path = self.shots / f'{name}.png'
        self.page.screenshot(path=str(path))
        log(f'screenshot {path}')
        return path

    def assert_no_overflow(self):
        size = self.page.evaluate('() => [document.documentElement.scrollWidth, document.documentElement.scrollHeight, innerWidth, innerHeight]')
        self.assertLessEqual(size[0], size[2], f'horizontal overflow {size}')
        self.assertLessEqual(size[1], size[3], f'vertical overflow {size}')

    def ranking(self):
        return self.page.eval_on_selector_all('#results tr', 'rows => rows.map(r => [...r.cells].map(c => c.textContent))')

    def test_tv_ranking_order_and_1080p(self):
        server = self.server()
        seed_ranking(server)
        for station in ('ST01', 'ST02', 'ST03'):
            server.heartbeat(station)
        self.page.goto(server.url + '/tv')  # no session typed: uses the active session
        expect(self.page.locator('#results tr')).to_have_count(16)
        expect(self.page.locator('#connection')).to_have_attribute('data-state', 'online')
        expect(self.page.locator('#banner')).to_be_hidden()
        expect(self.page.locator('#session-name')).to_have_text('Clasificación mañana')
        rows = self.ranking()
        corrected = [float(r[2]) for r in rows]
        self.assertEqual(corrected, sorted(corrected))
        api = server.store.leaderboard('S1')['ranking']['autocross']
        self.assertEqual([r[0] for r in rows], [str(r['rank']) for r in api])
        self.assertEqual([r[1] for r in rows], [r['pilot_name'] + r['pilot_id'] for r in api])
        self.assertEqual(rows[0][5], 'Líder')
        self.assertTrue(all(r[5].startswith('+') for r in rows[1:]))
        self.assertNotIn('30.000', ' '.join(r[2] + r[3] for r in rows), 'dnf raw time must never rank')
        expect(self.page.locator('#stations li.offline')).to_have_count(1)  # ST04 never sent a heartbeat
        self.assertNotEqual(self.page.text_content('#updated'), '--:--:--')
        self.assert_no_overflow()
        self.shot('tv-ranking-1080p')
        self.assertEqual(self.console_errors, [])
        log(f'tv ranking ok: {len(rows)} rows, leader {rows[0][1]} {rows[0][2]}')

    def test_tv_auto_refresh_and_session_change(self):
        server = self.server()
        seed_ranking(server, 'S1', pilots=4)
        seed_ranking(server, 'S2', pilots=3, name='Tarde', active=False)
        self.page.goto(server.url + '/tv?refresh=1')
        expect(self.page.locator('#results tr')).to_have_count(4)
        before = self.ranking()[0]
        server.call('/api/admin/pilots', {'pilot_id': 'NEW', 'name': 'Piloto Nuevo'})
        server.lap('ST01', 'NEW', 'fast', 50000, session='S1')
        expect(self.page.locator('#results tr')).to_have_count(5, timeout=8000)
        expect(self.page.locator('#results tr').first).to_contain_text('Piloto Nuevo')
        self.assertEqual(self.ranking()[1][1], before[1])
        self.shot('tv-auto-refresh')
        log('tv auto refresh ok: new leader appeared without reload')
        self.page.select_option('#session', 'S2')
        expect(self.page.locator('#session-name')).to_have_text('Tarde')
        expect(self.page.locator('#results tr')).to_have_count(3)
        expect(self.page.locator('#session-active')).to_be_hidden()
        self.assertIn('session_id=S2', self.page.url)
        # Admin switches the active session: an unpinned TV follows it.
        server.call('/api/admin/sessions/active', {'session_id': 'S2'})
        self.page.goto(server.url + '/tv?refresh=1')
        expect(self.page.locator('#session-active')).to_be_visible()
        expect(self.page.locator('#session-name')).to_have_text('Tarde')
        self.shot('tv-session-change')
        log('tv session change ok')

    def test_tv_connection_lost_indicator(self):
        server = self.server()
        seed_ranking(server, pilots=6)
        self.page.goto(server.url + '/tv?refresh=1')
        expect(self.page.locator('#results tr')).to_have_count(6)
        server.stop()
        self.servers.remove(server)
        expect(self.page.locator('#connection')).to_have_attribute('data-state', 'offline', timeout=8000)
        expect(self.page.locator('#connection')).to_contain_text('SIN CONEXIÓN')
        expect(self.page.locator('#results tr')).to_have_count(6)  # last known data stays visible
        self.shot('tv-server-down')
        server.start(server.port)
        self.servers.append(server)
        expect(self.page.locator('#connection')).to_have_attribute('data-state', 'online', timeout=8000)
        log('tv connection indicator ok: offline then recovered')

    def test_tv_unarmed_banner_never_shows_official_times(self):
        server = self.server(armed=False)
        seed_ranking(server, pilots=8)
        self.page.goto(server.url + '/tv')
        expect(self.page.locator('#banner')).to_be_visible()
        expect(self.page.locator('#banner')).to_have_text('SCORING NO COMISIONADO: resultados provisionales, no oficiales')
        expect(self.page.locator('#results tr')).to_have_count(0)
        expect(self.page.locator('#message')).to_contain_text('Ranking oficial deshabilitado')
        recent = self.page.eval_on_selector_all('#recent-laps tr', 'rows => rows.map(r => r.textContent)')
        self.assertTrue(recent and all('(prov.)' in r for r in recent))
        self.assert_no_overflow()
        self.shot('tv-unarmed-banner')
        log('tv unarmed banner ok')

    def test_admin_full_flow(self):
        server = self.server()
        page = self.page
        page.set_viewport_size({'width': 1600, 'height': 1000})
        page.goto(server.url + '/admin')
        page.fill('#token', 'wrong-token')
        page.click('#login')
        expect(page.locator('#auth-state')).to_have_text('Token inválido')
        expect(page.locator('#message')).to_have_text('authentication required')
        self.assertTrue(all('401' in e for e in self.console_errors))  # the deliberate bad login only
        self.console_errors.clear()
        page.fill('#token', ADMIN)
        page.click('#login')
        expect(page.locator('#auth-state')).to_have_text('Autenticado')
        self.assertEqual(page.input_value('#token'), '')
        self.assertEqual(page.evaluate("sessionStorage.getItem('rice_admin_token')"), ADMIN)
        self.assertIsNone(page.evaluate("localStorage.getItem('rice_admin_token')"))
        # Session, pilots, assignment through the UI.
        page.fill('#session', 'FINAL'); page.fill('#session-name', 'Final sábado'); page.click('#save-session')
        expect(page.locator('#message')).to_contain_text('Sesión guardada: FINAL (activa)')
        for pilot, name in (('P7', 'Ana Gómez'), ('P8', 'Bruno Díaz')):
            page.fill('#pilot', pilot); page.fill('#pilot-name', name); page.click('#save-pilot')
            expect(page.locator('#pilots')).to_contain_text(name)
        page.select_option('#assign-station', 'ST02'); page.select_option('#assign-pilot', 'P7'); page.click('#assign')
        expect(page.locator('#message')).to_contain_text('Asignado P7 a ST02')
        page.select_option('#assign-pilot', 'P8'); page.click('#assign')
        expect(page.locator('#assignment-history tr.reassignment')).to_have_count(1)
        page.select_option('#assign-pilot', 'P7'); page.click('#assign')
        # Station activity seeded through the station API (simulator stand-in).
        server.heartbeat('ST02')
        detector = {'gates_seen': 14, 'gates_expected': 14, 'confidence': 0.97, 'version': 'det-1'}
        endurance = dict(event='endurance', status='VALID', scoring_version=1, events={'cone': 0, 'off_course': 0, 'slalom': []},
                         track_config='mft02', calibration_verified=True, detector_ok=True, detector=detector, calibration_version='cal-3')
        conn_lap = server.lap('ST02', 'P7', 'E1', 95000, session='FINAL', **endurance)
        self.assertEqual(conn_lap['status'], 'PENDING_REVIEW')  # no admin calibration yet
        try:
            server.call('/api/laps', {'protocol_version': 2, 'station_id': 'ST02'}, STATIONS['ST02'])
        except AssertionError:
            pass
        page.click('#refresh')
        expect(page.locator('#stations tr[data-station="ST02"]')).to_contain_text('EN LÍNEA')
        expect(page.locator('#stations tr[data-station="ST02"]')).to_contain_text('gates_seen')
        expect(page.locator('#stations tr[data-station="ST01"]')).to_contain_text('DESCONECTADA')
        expect(page.locator('#sync-errors')).to_contain_text('unsupported protocol_version')
        # Calibration through the UI, then penalty review and approval.
        page.select_option('#cal-station', 'ST02'); page.fill('#track-config', 'mft02'); page.fill('#reference', 'Acta 12'); page.fill('#operator', 'Inspector R'); page.click('#calibrate')
        expect(page.locator('#calibrations')).to_contain_text('Acta 12')
        row = page.locator('#pending tr[data-lap="ST02/E1"]')
        expect(row).to_contain_text('PENDING_REVIEW')
        row.get_by_role('button', name='Seleccionar').click()
        page.fill('#pen-cone', '2'); page.fill('#pen-slalom', 'S3'); page.fill('#pen-reviewer', 'Juez A'); page.fill('#pen-reason', 'Video cámara 2')
        page.click('#save-penalties')
        expect(page.locator('#message')).to_contain_text('corregido calculado 119.000')
        expect(page.locator('#audit')).to_contain_text('Video cámara 2')
        row = page.locator('#pending tr[data-lap="ST02/E1"]')
        row.get_by_role('button', name='Seleccionar').click()
        page.select_option('#decision', 'approve'); page.fill('#reviewer', 'Juez A'); page.fill('#reason', 'Calibración verificada')
        page.click('#review')
        expect(page.locator('#message')).to_contain_text('E1 → VALID')
        expect(page.locator('#audit li')).to_have_count(2)
        expect(page.locator('#audit')).to_contain_text('PENDING_REVIEW → VALID')
        board = server.store.leaderboard('FINAL')
        self.assertEqual(board['ranking']['endurance'][0]['best_corrected_ms'], 95000 + 2 * 2000 + 20000)
        # Export and backup.
        with page.expect_download() as download:
            page.click('#export-json')
        exported = json.loads(Path(download.value.path()).read_text())
        self.assertEqual(len(exported['reviews']), 2)
        self.assertEqual(exported['laps'][0]['detector'], detector)
        page.click('#backup')
        expect(page.locator('#message')).to_contain_text('Backup creado')
        expect(page.locator('#backups li')).to_have_count(1)
        page.uncheck('#only-pending')
        expect(page.locator('#pending tr[data-lap="ST02/E1"]')).to_contain_text('VALID')
        page.screenshot(path=str(self.shots / 'admin-full-flow.png'), full_page=True)
        log(f'screenshot {self.shots / "admin-full-flow.png"}')
        # TV reflects the approved lap.
        tv = self.context.new_page()
        tv.goto(server.url + '/tv')
        expect(tv.locator('#results tr')).to_have_count(1)
        expect(tv.locator('#results tr').first).to_contain_text('119.000')
        tv.screenshot(path=str(self.shots / 'tv-after-admin-approval.png'))
        log(f'screenshot {self.shots / "tv-after-admin-approval.png"}')
        self.assertEqual(self.console_errors, [])
        log('admin full flow ok: login, session, pilots, reassignment, calibration, penalties, approval, export, backup')


if __name__ == '__main__':
    unittest.main()
