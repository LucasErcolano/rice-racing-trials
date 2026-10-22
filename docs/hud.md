# Client HUD (`client/hud.lua`)

Status: **tested on Linux with `ui` doubles (lupa, Lua 5.4 and LuaJIT 2.1)**. Not run inside Assetto Corsa/CSP. Integrated in `RiceTrials.lua`: `script.windowMain` draws the cached model (0.25 s) and, if `hud.lua` fails to load or `hud.draw` fails, falls back to the plain text window. Penalties show as "No Evidence" because no detector is commissioned. Per-station settings in `station_config.lua`: `hud_scale`, `hud_x`, `hud_y`, `hud_max_errors`, `hud_font`.

## Model / render split

- `hud.model(state, config) -> model`: pure function. It does not read `ac`, `ui` or `io`; it only uses `state`, `config` and `scoring.corrected_ms` (the `scoring` module is loaded once with `require`; it can be injected by assigning `hud.scoring`). Returns:
  ```lua
  {schema=1, title='Rice Trials - PROVISIONAL / CSP not validated',
   banner={text=..., level=...} | nil,   -- most severe warning, first of its level
   lines={{text=..., level='ok'|'info'|'warn'|'error'}, ...},
   layout={scale=, x=, y=, max_errors=}}
  ```
- `hud.draw(model, config) -> boolean`: draws the model. Never raises (everything runs under `pcall`); returns `false` if `ui` is missing or fails.
- `hud.cache(interval)`: object with `:update(dt, state, config) -> model` that recomputes the model at most every `interval` seconds (default 0.25 s). The per-frame window callback therefore only draws a prebuilt table (no heavy per-frame work).
- `hud.format_ms(ms)`: `m:ss.mmm`, or `--` if the value is not a valid integer.

HUD strings are deliberately ASCII (no accents): glyph coverage of the CSP fonts is not verified.

## `state` schema (every field tolerates `nil`)

```lua
state = {
  assignment = {pilot_id='p1', pilot_name='Ana', session_id='s1'} | nil,
  event = 'endurance' | 'autocross' | 'skidpad' | nil,
  phase = 'waiting' | 'outlap' | 'timed' | nil,   -- nil: route not calibrated
  lap = {
    elapsed_ms = 70123,          -- integer; only shown when phase == 'timed'
    penalties = {                 -- per key: nil = NO EVIDENCE (never an implicit 0)
      cone = 0..100,              -- endurance
      off_course = 0..100,        -- endurance
      slalom = {'S1', 'S4'},      -- endurance: ids of missed gates; {} = evidence of 0
      doo = 0..100, oc = 0..100,  -- autocross
    },
  },
  last_official = {lap_id=, status='VALID'|'PENDING_REVIEW'|'INVALID'|'OUTLAP'|'DETECTOR_ERROR',
                   raw_ms=, corrected_ms=} | nil,  -- exactly as returned by the server
  best = {status='VALID', corrected_ms=} | nil,     -- only shown if status == 'VALID'
  sync = {
    online = true | false | nil,                    -- nil: unknown
    pending = 0,                                    -- entries in the local queue
    last_ack = {lap_id='ST01-x-3', age_s=12} | nil,
    persistence = 'ok' | 'degraded' | 'corrupt' | 'unavailable' | nil,
  },
  detector_errors = {'text', ...} | nil,           -- critical detector errors
  status = 'free-form client text' | nil,          -- e.g. the current RiceTrials.lua status
}
```

Suggested mapping to the current `RiceTrials.lua`: `assignment` = the authenticated `assignment`; `phase` = `route.phase` (`waiting`/`outlap`/`timed`) or `nil` if there is no `calibration`; `lap.elapsed_ms` = `(elapsed-started)*1000` rounded; `sync.pending` = `#queue.entries`; `sync.persistence`: `'corrupt'` if `status` contains `QUEUE_CORRUPT`, `'unavailable'` if it contains `STORAGE_UNAVAILABLE`, `'degraded'` if it contains `STORAGE_UNVERIFIED`; `sync.online` from the last heartbeat/assignment reply; `last_ack` when a `POST /api/laps` is acknowledged. Today the server only replies `PENDING_REVIEW` to the client, so `best` stays at `--` until an endpoint returns `VALID` results.

## Display rules

| Situation | Line | Level |
|---|---|---|
| Assigned driver | `Driver: <name>` | ok |
| No assignment | `Driver: none assigned (not scored)` + banner `NO DRIVER ASSIGNED` | warn |
| Out lap | `OUTLAP: out lap, NOT timed`; time `--`; no projection | warn |
| Before the start line | `Waiting for start line` | info |
| Timed lap | `Lap in progress`, `Time: m:ss.mmm` | info |
| Route not calibrated | `Route not calibrated: no timing` | warn |
| Lap with no evidence | `Cones: No Evidence`, etc.; `Projected: No Evidence (...)` | warn |
| Partial evidence | only the known ones are shown; **no** projection | warn |
| Full evidence | `Projected PROVISIONAL: m:ss.mmm` via `scoring.corrected_ms` | info |
| Malformed penalty or `corrected_ms` nil | `Projected: cannot compute` | error |
| Skidpad | `Projected: not available` (needs per-side splits) | warn |
| Last official `VALID` | `Last official: <corrected> VALID` | ok |
| Last `PENDING_REVIEW` or other | `Last: <raw> raw <STATUS> (unofficial)` | warn/info/error |
| Unknown server status | `Last official: unknown status (...)` | error |
| Station-computed final times | `Last Final: ...`, `Best Final: ...` | ok |
| Best Corrected | `Best Corrected: ...` only with `best.status == 'VALID'`; otherwise `--` | ok/info |
| Online | `Server: Online` | ok |
| Offline with queue | `Server: Offline`, `Pending queue: N`, banner | warn |
| Corrupt / unavailable persistence | line + banner | error |
| Degraded persistence | line + banner | warn |
| Detector errors | up to `hud_max_errors` (default 3) + `(+N more errors)` + banner | error |

Text coming from the server or the operator is sanitized (control characters replaced) and truncated (40 characters).

## Settings

| Key | Default | Range |
|---|---|---|
| `hud_scale` | 1 | 0.5 to 3 (font size 15 px per scale unit) |
| `hud_x`, `hud_y` | 8, 8 | 0 to 10000 (px from the window corner) |
| `hud_max_errors` | 3 | 1 to 10 |
| `hud_font` | `Segoe UI;Weight=Bold` | DWrite name |

Non-numeric values fall back to the default; out-of-range values are clamped.

## Render and APIs

Only `ui.*` from `docs/csp-api-reference.md`, each checked with `type(ui.x) == 'function'` before use:

- 80 % black background with `ui.drawRectFilled(vec2, vec2, rgbm, 4)`, width taken from `ui.windowSize()` if available.
- Text with `ui.pushDWriteFont` + `ui.dwriteText(text, size, color)`; position with `ui.setCursor(vec2)`. High-contrast colors: green (ok), white (info), yellow (warn), red (error).
- `ui.popDWriteFont` is **not listed** in `docs/csp-api-reference.md`; it is used only if it exists at runtime. If it does not, DWrite is not used (so the font stack is never left unbalanced) and the HUD falls back to `ui.textColored`, then to `ui.text`.
- If DWrite fails mid-draw, `popDWriteFont` is still called.
- Not verified in game: whether `drawRectFilled` coordinates are window-relative, the real look of the fonts, and the per-frame cost.

## Closing the window does not stop scoring

Timing, the queue and submission live in `script.update(dt)`, which CSP calls whether or not the window is visible. `script.windowMain` (or whatever callback the orchestrator uses) should only call `hud.draw(cache:update(dt, state, config), config)`. If the window is closed, CSP does not call the callback and nothing is drawn. This is documented in the SDK but **not tested in game** (see `docs/limitations.md`, item 7).

## manifest.ini

Not modified. It currently declares `[WINDOW_MAIN]` with `FUNCTION_MAIN = windowMain` and `FLAGS = MAIN`. It is **not verified in game** that the HUD appears automatically when the session loads; the driver or operator may need to open it from the CSP app bar. Check this with the Windows Validation Kit.

## Integration (reference)

```lua
local hud = require('hud')
local hudCache = hud.cache(0.25)
function script.windowMain(dt)
  hud.draw(hudCache:update(dt, buildHudState(), settings), settings)
end
```

`buildHudState()` must be cheap (only copy references already computed in `script.update`).

## Tests

`client/tests/test_hud.py`: model with nil/empty state, no assignment, out lap, lap with no evidence (shows neither 0 nor a projection), partial evidence, combined penalties (fixture `endurance combined` = 2:34.500), explicit evidence of 0, invalid penalty, skidpad, offline with queue, corrupt/degraded persistence, `PENDING_REVIEW` vs `VALID` (last and best), unknown status, bounded detector errors, text sanitizing, clamped settings, cache every 0.25 s, balanced and scaled DWrite render, fallback to `textColored`, missing or failing `ui`, model purity and a LuaJIT 2.1 smoke test.
