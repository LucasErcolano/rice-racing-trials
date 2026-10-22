# Test report (Linux)

Date of the latest V2 field run: 2026-10-22.

## V2 field build

Latest Linux run of the integrated candidate `integration/v0.3.0-rc.2-final` (2026-10-22): 185 server, integration, browser, packaging and RR99 audit tests (OK; 4 skipped for optional prerequisites), 248 Lua client tests (OK; 2 skipped) and 25 LuaJIT 2.1 MOCK scenarios (24 OK; 1 skipped for lack of local geometry). The independent HTTP/process/SQLite OR smoke test passed its 11 checks, including both arrival orders, duplicates, conflicts, restart, separate channels and exclusion from the official leaderboard. It includes the regression that guarantees the authenticated assignment is not reused after 600 s of game clock when processing the next native lap.

The OR smoke test uses real processes and HTTP: authentication, both arrival orders, duplicates, conflict, restart, persistence and exclusion from the official leaderboard. Chromium tests the V2 monitor, its auto-refresh, export and administrative review. Windows evidence is produced in the package workflow, including this same smoke test against the compiled exe.

`Dummy.kn5` is validated in game by user confirmation. These suites do not imply in-game validation of the full V2 protocol.

The rest of this report keeps the historical V1 detail and results.

## How to run them

Requires a venv with `lupa` (real Lua inside Python) and, for the browser tests, `playwright` with Chromium:

```sh
V=<venv>/bin/python
$V -m unittest discover -s tests -p '*test*.py'              # server, integration, packaging, track, browser
(cd client/tests && $V -m unittest discover -p 'test_*.py')  # Lua client
$V sim/run_evidence.py --out <folder>                        # CSP MOCK scenarios
```

With the system `python3` (no `lupa` or Playwright) the tests that need them are skipped or fail on import; that run does not count as valid.

## Results

| Suite | Tests | Result |
|---|---|---|
| `tests/`: server, Lua + HTTP + SQLite E2E, packaging, validation kit, track tool, scoring parity, browser | 88 | OK |
| `client/tests/`: real Lua via lupa (LuaJIT 2.1 and Lua 5.4) | 104 | OK |
| `sim/`: scenarios in the CSP **MOCK** environment (unmodified client, LuaJIT 2.1, real server over HTTP); see `docs/csp-mock.md` | 25 (24 without the FSAEJ geometry) | OK |
| PowerShell scripts (`packaging/*.ps1`, `track/patches/Apply-DummyPatch.ps1`), parsed with pwsh 7 on Linux | 7 files | 0 syntax errors |

The 5 browser tests use Playwright with Chromium against the real HTTP server and leave screenshots in `docs/evidence/screenshots/`: 1080p leaderboard with 16 drivers and no scrolling or clipped cells, auto-refresh, session change, server down, not-commissioned banner and the full `/admin` flow.

## What they cover

- **Scoring:** `rules/scoring-v1.json` evaluated with `server/scoring.py` and `client/scoring.lua`; the same cases give the same result in Python and in Lua.
- **Client:** out-lap state machine (the start 19.3 m before the line and the first full lap are not timed); reset, teleport and session restart (`ac.onCarJumped`, `resetCounter`, `ac.onSessionStart`, position jump and absence of those APIs); persistent queue in A/B files with checksum and readback (cut before writing, truncated write, mismatched readback, both slots corrupt with quarantine and send blocking, migration from `ac.storage`); HUD (missing evidence is never shown as 0, and it falls back to plain text if the HUD fails); bounded JSONL diagnostics, off by default.
- **Server:** authentication (401/403), assignment revisions for laps sent offline, disarmed server that does not rank, schema migrations, active session, penalty review with before/after, CSV export without formula injection, backup and restore with integrity check.
- **E2E:** real Lua VM against real HTTP and SQLite: assignment, heartbeat, pending lap, response lost after commit, VM restart, byte-identical resend, idempotency and intermediate reassignment.
- **Packaging and track:** the ZIPs contain no credentials, databases or third-party files (KN5, ACD, car). The candidate `Dummy.kn5` patch is generated from the original ZIP without modifying it, changes only the `AC_START_0` matrix, and the result is read back without errors.

## CSP MOCK environment (`sim/`)

The production client runs unchanged on a simulation of the CSP surface (`ac`, `io`, `web`, `ui`, car and session cdata), with real disk and network plus injected faults, and a synthetic driver that provides the true time of each lap. It found five real client bugs, all fixed (list in `docs/csp-mock.md`). Evidence in `docs/evidence/csp-mock/`: Linux, Windows 11 with the in-process server, and Windows 11 against the compiled `RiceTrialsServer.exe`. **This is evidence on a mock, not CSP validation.**

## Windows (GitHub Actions)

`.github/workflows/windows-package.yml` runs on a real Windows runner: the two suites above, parsing of every `.ps1`, the PyInstaller build, ZIP inspection, an exe smoke test and `tests/windows/e2e-windows.ps1` against the compiled package. Run `39334554771` (commit `ddeb289`): all green, 32/32 E2E steps. Details in `docs/windows-validation.md`. Run `39400713102` (commit `4efcb7f`): all green, now with the 24 CSP MOCK scenarios (without the FSAEJ geometry, which is not distributed) against `server/app.py` and against the compiled exe.

## Not run

Full V2 inside Assetto Corsa/CSP on Windows, physical detection of cones and track edges, real timing, `io.save` behavior on power loss, the scheduled task after a real reboot, several stations on a network, and a full event. The `Dummy.kn5` patch was validated in game by the user. The Linux tests verify contracts and emulated behavior; they certify nothing for competition. This does not block the field-test pre-release; provisional results stay out of the official leaderboard.
