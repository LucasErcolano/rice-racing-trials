"""Real Chromium coverage of the V2 provisional TV monitor, separate from V1."""
import json
from pathlib import Path
import tempfile
import unittest
from tests import test_field_v2_api as api
try:
    from playwright.sync_api import sync_playwright, expect
except ImportError:
    sync_playwright = expect = None


@unittest.skipIf(sync_playwright is None, 'playwright not installed')
class FieldBrowserTests(api.FieldV2HttpTests):
    def test_native_pending_reconciles_live_and_never_becomes_official(self):
        self.send_channel()
        assert sync_playwright is not None and expect is not None
        with sync_playwright() as pw:
            try:
                browser = pw.chromium.launch()
            except Exception as exc:
                self.skipTest(f'Chromium unavailable: {exc}')
            try:
                page = browser.new_page(viewport={'width': 1920, 'height': 1080})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(f'http://127.0.0.1:{self.http.server_port}/v2/field')
                expect(page.locator('#field-laps tr')).to_have_count(0)
                page.locator('#field-token').fill('admin')
                page.locator('#auth button[type=submit]').click()
                expect(page.locator('#field-laps')).to_contain_text('PENALTIES_PENDING')
                expect(page.locator('#field-laps')).to_contain_text('sin evidencia')
                expect(page.locator('#field-token')).to_have_value('')
                self.send_channel('penalty')
                expect(page.locator('#field-laps')).to_contain_text('62000', timeout=10000)
                expect(page.locator('#field-laps tr')).to_have_count(1)
                self.call('POST', '/api/admin/v2/field-review',
                    dict(station_id='ST01', session_id='S1', lap_id='L1', reviewer='browser-test',
                         approved=True, reason='Synthetic evidence reviewed for browser integration test'), 'admin')
                expect(page.locator('#field-laps')).to_contain_text('VERIFIED_PROVISIONAL', timeout=10000)
                with page.expect_download() as download_info:
                    page.locator('#export').click()
                exported = json.loads(Path(download_info.value.path()).read_text(encoding='utf-8'))
                self.assertTrue(exported['provisional'])
                self.assertFalse(exported['laps'][0]['official'])
                self.assertIsNone(exported['laps'][0]['corrected_ms'])
                page.screenshot(path=str(Path(tempfile.gettempdir()) / 'rice-v2-field-browser.png'), full_page=True)
                page.locator('#clear').click()
                expect(page.locator('#field-laps tr')).to_have_count(0)
                self.assertEqual(page.evaluate('localStorage.length + sessionStorage.length'), 0)
                self.assertEqual(errors, [])
            finally:
                browser.close()


def load_tests(loader, tests, pattern):
    return unittest.TestSuite([FieldBrowserTests('test_native_pending_reconciles_live_and_never_becomes_official')])
