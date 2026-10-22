# Assetto Corsa / CSP MOCK environment (`sim/`)

**This is not CSP validation.** Passing this suite proves that the production code behaves correctly against the behavior we *assume* CSP has. What CSP really does is only confirmed inside the game (see `docs/windows-validation.md`).

## What is real and what is simulated

| Piece | Real or simulated |
|---|---|
| `client/*.lua` (`RiceTrials`, `core`, `journal`, `hud`, `diagnostics`, `scoring`) | **Real, unmodified.** Installed into an on-disk `apps/lua/RiceTrials/` exactly as `install.ps1` does (including a generated `station_config.lua`) and loaded with `require` from that folder. |
| Lua virtual machine | **Real:** LuaJIT 2.1 (`lupa.luajit21`), the same family CSP embeds. |
| JSON | **Real:** `JSON.parse`/`JSON.stringify` copied verbatim from the official SDK (`sim/vendor/csp_json.lua`, MIT license in `sim/vendor/acc-lua-sdk.LICENSE`), including empty-table semantics. |
| Server | **Real:** `server/app.py` over real HTTP (in process, or the PyInstaller exe). |
| Disk | **Real:** host operating system files and folders, with injected faults (write cut halfway, disk full, I/O error). |
| `ac`, `io`, `web`, `ui`, `script` surface (`sim/csp_prelude.lua`) | **Simulated.** Signatures taken from `acc-lua-sdk` and `acc-lua-internal`. |
| Car and session structures | **Simulated** as FFI cdata: reading a field that does not exist raises an error, as in CSP. There is a "modern" profile and a "legacy" one without `resetCounter` or `collisionDepth`. |
| Timing of `web.*` responses | **Simulated:** per-frame queue, configurable latency, server down, lost response, 500, garbage response, limit on concurrent requests. |
| When CSP calls `script.update` during pause, replay, menu and session restart | **Simulated**, and it is the main assumption: see the table below. |
| Car physics | **Simulated:** a kinematic driver follows the AI line (synthetic, or the real FSAEJ `fast_lane` if `RICE_FSAEJ_GEOMETRY` is set), integrated at 1 kHz to compute the true time of each lap. |

## CSP assumptions to confirm in game

| Assumption | What the client does if it is false |
|---|---|
| `ac.getSim()` exposes `isReplayActive`, `isPaused`, `isInMainMenu` | Reads the fields safely: if missing it does not block, but it does not protect against replays either. Confirm in game. |
| During a pause `script.update` may receive `dt > 0` | The client discards the lap in progress when it sees `isPaused` and requires a new out lap, so the result is correct with `dt = 0` or `dt > 0`. |
| `car.resetCounter` exists in the pinned CSP version | If missing, position-jump detection and `ac.onCarJumped` are used. |
| `web.post` delivers a numeric `response.status` and `err` on transport failures | If a failure leaves neither `err` nor a 0/5xx `status`, the assignment is cleared (more restrictive). |
| `io.save` + readback detects incomplete writes | The A/B journal with checksum covers it; real Windows behavior on power loss remains untested. |

## Scenarios (`sim/test_sim_scenarios.py`)

Clean laps against the true time (error under 3 ms at 30, 60 and 144 fps and with irregular frames); replay, menu and pause (with `dt` at zero or advancing); resets via `resetCounter`, via `ac.onCarJumped` and teleport without `resetCounter`; session restart; reversing and shortcuts; network down and recovery without losses or duplicates; lost response resent identically; 500 and captive portal not counted as confirmation; bounded concurrent requests; wrong token; game restart with an offline queue; cut write; full disk; corrupt journal with blocking and correct diagnosis; missing optional APIs; no `io`; valid diagnostics JSONL; laps on the real FSAEJ AI line.

## How to run it

```sh
python -m pip install lupa==2.8
python sim/run_evidence.py --out <folder>                    # 24 scenarios + 1 skipped
RICE_FSAEJ_GEOMETRY=<geometry.json> python sim/run_evidence.py --out <folder>   # 25
```

The FSAEJ geometry is generated with `track/tool.py` from the mod ZIP and **is not distributed** (derived from third-party assets). `run_evidence.py` writes `summary.json` (commit, SHA-256 hashes of client and server, LuaJIT version, result of each test) and `unittest.log`. The stored evidence is in `docs/evidence/csp-mock/`.

## Real bugs this environment found (fixed)

1. `RiceTrials.lua` read `car.resetCounter` directly: on a CSP without that field, the read raises an error every frame and timing shuts off entirely. It is now read with `pcall`.
2. The client did not check `isReplayActive`, `isPaused` or `isInMainMenu`: a replay produced fake laps and a pause with `dt > 0` inflated the time (43.5 s versus 23.5 s). It now discards the lap in progress and requires a new out lap.
3. The line crossing was taken at the end of the frame: up to 33 ms of error per crossing at 30 fps. It is now interpolated within the frame (`core.crossed` returns the fraction); the measured error drops below 3 ms.
4. A brief network outage cleared the authenticated assignment and the following laps were not recorded. Now only a transport failure (no response or 5xx) keeps the last assignment, for at most 600 s of game time. Those laps carry `assignment_revision` and the server leaves them `PENDING_REVIEW`. Any server response that is not a valid assignment clears it.
5. With a corrupt queue, the status showed `STATION_CONFIG_INVALID` and hid `QUEUE_CORRUPT`. The real cause is now visible.
