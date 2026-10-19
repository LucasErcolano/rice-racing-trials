"""Native timing evidence tests; no KN5 modification or game certification."""
import io
import struct
import unittest
from pathlib import Path
from zipfile import ZipFile

from timing.audit import analyze_archive, classify_native_fix

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / 'assets/source'
HAVE_SOURCES = (SOURCES / 'FSAEJ_Endurance.zip').is_file() and (SOURCES / 'FSAEJ_Skidpad.zip').is_file()


@unittest.skipUnless(HAVE_SOURCES, 'JSAE track archives are proprietary and gitignored (assets/source); not present in CI')
class NativeTimingTests(unittest.TestCase):
    def test_endurance_spawn_before_timing_line_not_correction(self):
        result = analyze_archive(ROOT / 'assets/source/FSAEJ_Endurance.zip', 'FSAEJ_Endurance')
        markers = result['markers_ac_xyz']
        self.assertIn('AC_START_0', markers)
        self.assertIn('AC_HOTLAP_START_0', markers)
        self.assertIn('AC_PIT_0', markers)
        self.assertIn('AC_TIME_0_L', markers)
        self.assertIn('AC_TIME_0_R', markers)
        self.assertNotEqual(markers['AC_START_0'], markers['AC_TIME_0_L'])
        self.assertEqual(classify_native_fix(result), 'REQUIRES_GAME_VALIDATION')

    def test_skidpad_without_optional_hotlap_marker(self):
        result = analyze_archive(ROOT / 'assets/source/FSAEJ_Skidpad.zip', 'FSAEJ_Skidpad')
        self.assertIn('AC_TIME_0_L', result['markers_ac_xyz'])
        self.assertEqual(result['native_fix_status'], 'REQUIRES_GAME_VALIDATION')

    def test_invalid_archive_rejected(self):
        with self.assertRaises((ValueError, KeyError)):
            analyze_archive(ROOT / 'assets/source/FSAEJ_Endurance.zip', 'not_the_track')


if __name__ == '__main__':
    unittest.main()
