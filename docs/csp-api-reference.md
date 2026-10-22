# CSP APIs used: reference (verified in documentation, not at runtime)

Sources consulted on 2026-10-19:
- Generated SDK definitions (`dabinn/ac-lua-sdk-api`, `ac_apps/README.md`, derived from `acc-lua-sdk`).
- `ac.StateCar` documentation (`lint069.github.io/csp-lua-api-docs/ac/objects/state-car`).
- Real usage in `ac-custom-shaders-patch/acc-lua-internal` (`lua-module/src/hotlap.lua`: the `io.save` to `.tmp` + `io.move` pattern).

**Status:** signatures confirmed in documentation. None was run inside Assetto Corsa. The minimum CSP version that exposes each field is not confirmed; the client must check existence (`type(x) == 'function'` / field not nil) and degrade to fail-closed.

## Files (`io`)
| Function | Signature | Returns | Use in Rice Trials |
|---|---|---|---|
| `io.save(filename, data)` | string, string | `boolean` | write temporary journal |
| `io.move(existing, new)` | string, string | `boolean` | rename the temporary file to the final one |
| `io.load(filename, fallback)` | string, string? | `string\|nil` | read journal |
| `io.fileExists(filename)` | string | `boolean` | recovery |
| `io.createDir(path)` | string | `boolean` (creates parents) | state folder |
| `io.deleteFile(filename)` | string | `boolean` | clean up temporary files |
| `io.scanDir(dir, mask, cb)` | | | not used |
| `ac.getFolder(ac.FolderID.X)` | `ac.FolderID` | `string` | data root; `ac.FolderID.Root` confirmed; other IDs not confirmed in this source |

No `fsync` is documented. `io.move` onto an existing target: undocumented behavior (on Windows `MoveFileEx` without `REPLACE_EXISTING` fails). That is why the journal uses **two alternating slots** (A/B) with a sequence number and checksum, and never depends on replacing an existing file.

## Network (`web`)
`web.get(url, headers?, callback)` and `web.post(url, headers?, data, callback)`; callback `fun(err: string, response: WebResponse)`; `response.status`, `response.body`. At most **two simultaneous requests** per script (documented). Asynchronous: does not block the frame.

## Car state (`ac.getCar(index)` -> `ac.StateCar`)
| Field | Type | Documented description |
|---|---|---|
| `position`, `look`, `velocity` | vec3 | position, normalized forward vector, velocity in m/s |
| `speedKmh` | number | |
| `lapTimeMs` | integer | current lap time |
| `previousLapTimeMs` / `bestLapTimeMs` | integer | last / best of the session |
| `lapCount` | integer | completed laps, including spoiled ones |
| `sessionLapCount` | integer | laps in the current session |
| `isLapValid` / `isLastLapValid` | boolean | native validity |
| `lastLapCutsCount` | integer | cuts on the last lap |
| `currentSplits` | integer[] | splits of the current lap |
| `splinePosition` | number | 0 start line, 1 finish |
| `wheelsOutside` | integer | wheels outside the allowed area |
| `collidedWith` | integer | 0 track, nonzero cars |
| `collisionPosition` | vec3 | collision point in car space |
| `collisionDepth` | number | current collision depth (m) |
| `resetCounter` | integer | +1 per reset |
| `isInPit` / `isInPitlane` | boolean | |

`wheelsOutside` depends on the mod's `IS_VALID_TRACK` surfaces; it **is not** proof of off-course under FSAE rules until calibrated. `collidedWith` + `collisionDepth` is a contact signal; it does **not** identify a cone (the KN5 cone meshes are merged).

## Events
- `ac.onCarJumped(carIndex, fun(carIndex))`: car teleport/reset.
- `ac.onSessionStart(fun(sessionIndex, restarted))`: new session or restart.
- `ac.getSim()` -> `ac.StateSim` (cheap).
- `ac.getPatchVersion()` -> string; `ac.getPatchVersionCode()` -> integer.
- `ac.getTrackFullID(sep?)`, `ac.getTrackID()`, `ac.getCarID(index)`.

## Native Best Lap
- `ac.markLapAsSpoiled()`: "Marks current lap done by player as spoiled. **Works only during the race**".
- `physics.markLapAsSpoiled(carIndex)`: same restriction, physics context.
The documentation says it only works in a race, so it is **not assumed** to invalidate the out lap in Practice/Hotlap. It remains an experiment in the Windows Validation Kit (a measured step, not a declared fix).

## UI
`ui.transparentWindow(id, pos, size, noPadding, content)`, `ui.toolWindow(...)`, `ui.pushDWriteFont(name)`, `ui.dwriteText(text, size, color)`, `ui.dwriteDrawText(text, size, pos, color)`, `ui.drawRectFilled(p1, p2, color, rounding)`, `ui.textColored(text, color)`, `ui.windowSize()`, `ui.setCursor(v)`, `ac.setWindowOpen(windowID, opened)`. Scoring runs in `script.update`, regardless of whether the HUD window is open.
