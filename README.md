# Rice Racing Driver Trials Core + Package B

**Status: development and commissioning, not ready for official trials.** The initial ZIP did not contain the FSAEJ Endurance KN5 or the MFT02 car; both authorized files were later obtained from the shared Drive folder. They are kept in `assets/source/` for local audit and **are not redistributed**. The CSP app and the server have not been run together inside Assetto Corsa on Windows. There is no physical validation of cones. No provisional result may be used to select drivers.

## V2 field build (opt-in pre-release)

Installation and testing guide: [`docs/v2-field-testing.md`](docs/v2-field-testing.md). V2 adds native and penalized capture with separate journals/retries, reconciliation by lap identity and its own monitor at `/v2/field`. It does not depend on the Velocity leaderboard. V1 remains the default.

Native laps on their own are shown as `PENALTIES_PENDING`, not as clean laps or official results. The corrected time requires penalty evidence; inconsistencies go to review. Sufficient geometric cone contact keeps the V1 criterion and does not require a physical knock-down. `Dummy.kn5` is validated in game by user confirmation.

Velocity confirmed CSP 2.11, installation of our Lua app, LAN and use of our server/TV. Pending calibrations are completed during testing and do not block installing the pre-release. The complete V2 still needs in-game validation.

## Components

- `client/`: CSP Lua app, HUD and isolated tests. Spatial detection only operates with verifiable calibration; it does not certify knock-downs from proximity.
- `server/`: HTTP intake, SQLite storage, driver assignment, leaderboards and export.
- `leaderboard/`: public display and admin console.
- `packaging/`: packaging and installation scripts; Windows binaries must be built and tested on Windows.
- `docs/`: audit of the original archive, limitations, contract, deployment and on-site checklist.

## Unmodified source

`Rice_Driver_Trials_v1.0_Hardware_Human_Handoff.zip`: SHA-256 `46b33cf4085d7e4e8f003010fc3e75903596601531e7d047f20ec026c27f7574`. Our packages do not redistribute tracks, the car or Assetto Corsa.

## Linux tests

They require a venv with `lupa` (and `playwright` for the browser tests):

```sh
V=<venv>/bin/python
$V -m unittest discover -s tests -p '*test*.py'
(cd client/tests && $V -m unittest discover -p 'test_*.py')
```

Full status and open items: `docs/final-status.md`. Results: `docs/test-report.md`.

Read `docs/zip-audit.md`, `docs/limitations.md` and `docs/windows-validation.md` before any deployment. The criterion for official enablement is a real test of track, car, cones, out lap, geometry and network at each station, not just passing Linux tests.
