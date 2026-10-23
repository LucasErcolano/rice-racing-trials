# Native HUD timing: route clock, same-lap identity, LAST/BEST and delta

Fix branch `fix/v030-native-hud-timing` (base `v0.3.0`).

## Problem and evidence

A lap on the Windows VM (session `RC2F-VM-01`, Field V2 lap `n-14`, `field.sqlite` from the VM) showed:

| Source | Time |
| --- | --- |
| AC `previousLapTimeMs` | 6:25.014 (385014 ms) |
| Rice route timer (`evidence.route_raw_ms`) | 6:19.292 (379292 ms) |
| Difference | 5.722 s |
| Penalties (29 cones, 2 slalom gates) | +90 s |

The HUD showed `6:19.292 + 1:30 = 7:49.292`. The server already showed `385014 + 90000 = 475014` (7:55.014): `server/field_v2.py` uses the native `raw_ms` of the lap and keeps the route time only as `evidence.route_raw_ms`.

Root cause (reproduced): v0.3.0 timed the route with `elapsed += dt`. On the VM the Lua update loop stalls while AC physics keeps running, and CSP passes in small, clamped `dt` values after a stall. In the VM diagnostics (`diag0/diag1.jsonl`), AC's `lapTimeMs` advanced 3 to 28 s between consecutive samples while the sum of `dt` advanced about 0.5 s. Laps with no stalls agree within 7 ms (n-2: 7 ms, n-9: 0 ms, n-16: 3 ms), so both systems time from the same line. Only laps with stalls diverge (n-10: 3.488 s, n-14: 5.722 s). The cause is not a different line, a lap desync or a session restart.

## Behaviour after the fix

### Route clock (`client/lap_clock.lua`)

On each frame, the route clock advances by the physics time AC measured since the previous frame: the step in `car.lapTimeMs`, or `previousLapTimeMs - last + lapTimeMs` across the native line. Frame `dt` is used only as a fallback, in these cases:

- the field is missing;
- the counter went backwards, or a reset happened;
- a jump of more than 60 s;
- a counter that has never advanced while frames run (a CSP build that does not fill it).

Once the counter has advanced, a zero step means a frozen physics frame and adds 0. The clock is reset in `resetRoute`. It is snapshotted at every timed-lap start. Each Field V2 penalty payload carries `evidence.route_clock`:

- frames;
- `dt_ms` and `clock_ms`;
- `fallback_frames`;
- `stall_frames`;
- `dt_minus_clock_ms`;
- `max_frame_deficit_ms`.

The route time is still an estimate (interpolated geometric gates). It is never shown as AC's time.

### Same-lap identity (`client/lap_sync.lua`)

At the route start crossing, the app reads `lapCount` and `lapTimeMs`. The offset between AC's line and the route line is `lapTimeMs` minus the part of the frame after the interpolated crossing. If that offset is within 1 s, the lap being timed is AC lap `lapCount + 1`. If AC's counter moves a frame later, the lap is re-phased once, under the same 1 s rule. Otherwise the lap has no native identity.

- **Field V2:** the native slot is reserved for exactly that counter (`field_v2:begin_lap(prev, target)`). A lap without identity is reserved as standalone, which keeps it under review on the server. The windows read the slot through `field_v2:native_result(token)`.
- **V1 (`native_laps.lua`):** the old pairing rule was "closest time within ±2 s". It is replaced by "same session **and** same AC counter". As a result, n-14 (5.722 s) now pairs, and a route lap without a counter never pairs. If the same counter shows a divergence above 20 s, that is a conflict (`native_conflict`) and the lap is not paired.

The time difference never decides a pairing. A native time of another counter is a `mismatch`, even if it is 1 ms away.

Lap states:

| State | Raw time shown | Label |
| --- | --- | --- |
| `confirmed` | AC lap time | `AC` |
| `pending` | route estimate | `EST`, AC pending |
| `unmatched` | route estimate | `EST` |
| `mismatch` | route estimate | `EST`, AC MISMATCH (review) |

A `pending` lap with no native time after 5 s becomes `unmatched (native_capture_missing)`. It is still upgraded if AC's time for the same counter arrives within 120 s.

### LAST, BEST, corrected time

A lap has two components:

- raw time: AC's time when confirmed, otherwise the route estimate;
- penalties: kept separate as `penalty_ms`.

`corrected_ms = raw + penalties`. This is the value shown in the Times window, the classic HUD (`Last Final` / `Best Final`), the native Lap Timing overlay (LAST, plus a `SOURCE` row when the value is an estimate) and the `/v2/field` preview. BEST is the lowest corrected time of laps whose identity is not contradicted. A `mismatch` lap is never BEST and never the delta reference.

### Delta (`client/delta.lua`)

The trace records **driving time only**. Penalties are shown on their own PENALTY row. Before, they were added to the trace, so a cone looked like a sudden loss of pace at one point and inflated the reference. The reference is the BEST lap that has a complete trace. When that lap is confirmed, its trace is rescaled to AC's time of the same lap (`adopt(trace, native_ms, corrected_ms, force)`), so the live lap and the reference share AC's time base. If the reference is still an estimate, the Times window shows `DELTA ... EST` and the overlay meter stays amber. When the reference lap is gone or contradicted, the delta shows `--`.

## LuaJIT

`script.update` was at 60/60 upvalues. The detector block (`observeDetectors`), the lap close (`closeLap`), the clock step and the lap start were moved to local functions with unchanged logic. The clock and the sync state live in one table (`lapTiming`). `script.update` now uses 37 upvalues (`scripts/luajit_upvalues.py client/RiceTrials.lua 0`).

## Tests

`client/tests/test_native_hud_timing.py` covers:

- the clock under a 28 s stall;
- the native line crossing, the fallbacks and the reset;
- identity versus proximity, unsynced lines, late resync and late confirmation;
- delta rescaling.

Three full app runs reproduce n-14:

- the new clock: the payload `route_raw_ms` matches AC's time within 60 ms, and `route_clock` shows the stall;
- the v0.3.0 `dt` clock in V1: the draft pairs beyond the old 2 s rule, and LAST/BEST show AC time + penalties;
- the v0.3.0 `dt` clock in V2, through the real server: `preview_ms` equals what the Times window shows.

`test_native_laps.py` was updated for pairing by identity.
