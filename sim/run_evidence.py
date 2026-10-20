"""Run the MOCK CSP scenario suite and write reproducible evidence.

Usage: python sim/run_evidence.py --out docs/evidence/csp-mock/<platform>
Writes summary.json (environment, git commit, per-test outcome) and unittest.log.
Exit code is non-zero if any scenario fails. This is MOCK evidence, never CSP validation.
"""
import argparse
import datetime
import hashlib
import io
import json
import os
import platform
import subprocess
import sys
import time
import unittest
from pathlib import Path

SIM = Path(__file__).resolve().parent
ROOT = SIM.parent
sys.path.insert(0, str(SIM))


class Recorder(unittest.TextTestResult):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.outcomes = []

    def _add(self, test, outcome, detail=None):
        self.outcomes.append({'test': test.id(), 'outcome': outcome, 'detail': detail})

    def addSuccess(self, test):
        super().addSuccess(test); self._add(test, 'pass')

    def addFailure(self, test, err):
        super().addFailure(test, err); self._add(test, 'fail', self._exc_info_to_string(err, test)[-2000:])

    def addError(self, test, err):
        super().addError(test, err); self._add(test, 'error', self._exc_info_to_string(err, test)[-2000:])

    def addSkip(self, test, reason):
        super().addSkip(test, reason); self._add(test, 'skip', reason)

    def addSubTest(self, test, subtest, err):
        super().addSubTest(test, subtest, err)
        if err is not None:
            kind = 'fail' if issubclass(err[0], test.failureException) else 'error'
            self._add(subtest, kind, self._exc_info_to_string(err, test)[-2000:])


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(*args):
    try:
        return subprocess.run(['git', *args], cwd=ROOT, capture_output=True, text=True, timeout=20).stdout.strip()
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', required=True)
    ap.add_argument('--pattern', default='test_*.py')
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    import cspmock  # noqa: E402  (after sys.path)
    import lupa
    rt_jit = None
    try:
        import lupa.luajit21 as lj
        rt_jit = lj.LuaRuntime().eval('jit and jit.version')
    except Exception as exc:  # recorded, the suite will fail loudly anyway
        rt_jit = 'unavailable: %r' % (exc,)

    suite = unittest.defaultTestLoader.discover(str(SIM), pattern=args.pattern, top_level_dir=str(SIM))
    log = io.StringIO()
    runner = unittest.TextTestRunner(stream=log, verbosity=2, resultclass=Recorder)
    t0 = time.time()
    result = runner.run(suite)
    elapsed = time.time() - t0
    (out / 'unittest.log').write_text(log.getvalue(), encoding='utf-8')

    counts = {}
    for o in result.outcomes:
        counts[o['outcome']] = counts.get(o['outcome'], 0) + 1
    summary = {
        'kind': 'MOCK CSP/AC scenario suite (NOT CSP validation)',
        'utc': datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds'),
        'git_commit': git('rev-parse', 'HEAD'),
        'git_dirty': bool(git('status', '--porcelain', '--', 'client', 'server', 'sim')),
        'platform': platform.platform(),
        'python': sys.version.split()[0],
        'lupa': getattr(lupa, '__version__', None),
        'luajit': rt_jit,
        'fsaej_geometry': bool(os.environ.get('RICE_FSAEJ_GEOMETRY')),
        'server': ('frozen exe ' + Path(os.environ['RICE_SIM_SERVER_EXE']).name + ' sha256=' +
                   sha256(Path(os.environ['RICE_SIM_SERVER_EXE']))) if os.environ.get('RICE_SIM_SERVER_EXE')
                  else 'server/app.py in process',
        'client_sha256': {p.name: sha256(p) for p in sorted((ROOT / 'client').glob('*.lua'))},
        'server_sha256': sha256(ROOT / 'server' / 'app.py'),
        'tests_run': result.testsRun,
        'counts': counts,
        'successful': result.wasSuccessful(),
        'elapsed_s': round(elapsed, 1),
        'outcomes': result.outcomes,
        'mocked': cspmock.__doc__.strip(),
    }
    (out / 'summary.json').write_text(json.dumps(summary, indent=1, ensure_ascii=False), encoding='utf-8')
    print(json.dumps({k: summary[k] for k in ('platform', 'luajit', 'tests_run', 'counts', 'successful', 'elapsed_s')}))
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    sys.exit(main())
