# Client diagnostics (`client/diagnostics.lua`)

Status: **tested on Linux with a filesystem double (dict) in lupa, Lua 5.4 and LuaJIT 2.1**. Not run inside CSP; real `io.*` behavior on Windows is left to the Windows Validation Kit. Integrated in `RiceTrials.lua`: enabled only with `diagnostics = true` in `station_config.lua` (same `diagnostics_*` keys as below); when off it never touches `io`. It records car samples, route resets with their reason, and connection/queue state.

## Enabling

Only with `config.diagnostics == true`. In any other case `diagnostics.new` returns an inert object (`update`/`event` do nothing, `flush` returns `false`, `stats()` returns `{enabled=false}`) that never touches `io`.

## API

```lua
local diagnostics = require('diagnostics')
local d = diagnostics.new(config, deps)
-- config: diagnostics=true, diagnostics_dir, diagnostics_file, diagnostics_max_bytes,
--         diagnostics_buffer_max, diagnostics_flush_s, diagnostics_sample_hz
-- deps (all optional):
--   io    = table with save/load/fileExists/createDir/deleteFile/move (default: global io)
--   env   = table with ac/io/web/JSON/ui for the API and version report (default: _G)
--   clock = fun(): simulation seconds (default: sum of dt received in update)
--   dir   = folder if config.diagnostics_dir is missing
d:update(dt, car, sim, extra)   -- from script.update; car = ac.getCar(0), sim = ac.getSim()
d:event(kind, fields)           -- one-off record (e.g. a network error)
d:flush() -> boolean            -- forces a write; also called every flush_s
d:stats() -> table              -- copy of the counters
```

`extra` (optional): `{detector = <table or string>, connection = <table>, errors = {'text', ...}}`. `detector` and `connection` are recorded only when they change (compared at the sampling rate, not per frame); every item in `errors` is always recorded.

`stats()`: `enabled, buffered, dropped, lines_written, file_bytes, flushes, rotations, io_errors, encode_errors, samples, last_error, path, create_dir_ok`.

If `diagnostics_dir` is missing, the module tries `ac.getFolder(ac.FolderID.Root)..'/apps/lua/RiceTrials/diagnostics'` (`FolderID.Root` is the only ID confirmed in `docs/csp-api-reference.md`). If there is no folder either, each flush counts an io error and the lines stay in the bounded buffer.

## Settings and limits

| Key | Default | Range | Effect |
|---|---|---|---|
| `diagnostics_max_bytes` | 2 MB | 4 KB to 64 MB | maximum size of each file |
| `diagnostics_buffer_max` | 1000 | 10 to 100000 | pending lines in memory; when full, the oldest is dropped and counted in `dropped` |
| `diagnostics_flush_s` | 10 | 1 to 300 | simulation seconds between writes |
| `diagnostics_sample_hz` | 2 | 0.1 to 20 | position samples per second |
| `diagnostics_file` | `rice_diagnostics` | `[%w_-]+` | base name |

Files: `<dir>/<file>.jsonl` (current) and `<dir>/<file>.1.jsonl` (previous). Maximum disk used: 2 x `max_bytes`.

## Why the current file lives in memory

`io.save(filename, data)` rewrites the whole file and the CSP reference does not document append. To avoid losing earlier lines, the module keeps the current file's text in memory (bounded by `max_bytes`) and on each flush writes `segment + new lines`. Rejected alternatives:

- **Reread with `io.load` and concatenate**: same peak memory, plus a disk read on every flush.
- **One file per flush (small segments)**: hundreds of files per session, and rotation would need `io.scanDir`, which is not verified.

Cost: peak memory of about 2 x `max_bytes` during the flush (4 MB with defaults) and a write of up to 2 MB every 10 s. If that turns out to be expensive on the rig, lowering `diagnostics_max_bytes` (for example 512 KB) reduces both.

## Rotation

1. Before the first write of the session: `io.createDir(dir)` (its result is not counted as an error because it may return `false` if the folder already exists; `io.save` decides). If a current file from a previous session already exists, it is rotated so it is not overwritten.
2. If `segment + new` would exceed `max_bytes`: if `.1.jsonl` exists it is deleted with `io.deleteFile` **before** `io.move(current, .1.jsonl)`, because `io.move` onto an existing target is undocumented (on Windows `MoveFileEx` without `REPLACE_EXISTING` fails). If `move` still fails, the current file is deleted to keep the cap. Then an empty segment starts.
3. If a single flush exceeds `max_bytes`, the newest lines are kept and the cut ones are added to `dropped`.

## Never blocks

- Every `io` call runs inside `pcall`; an exception, a returned `false` or a missing function increments `io_errors` and stores `last_error`. Nothing propagates to `script.update`.
- If `io.save` fails, the lines stay in the (bounded) buffer and the next flush retries.
- Car fields are read with `pcall`: in CSP `ac.StateCar` is FFI cdata and reading a missing field raises an error.
- Per frame only 4 scalars are compared (collision and `lapCount`); sampling, detector and connection run at 2 Hz; encoding and writing happen only on flush or when recording.
- Own JSON encoder (deterministic, not relying on CSP's `JSON.stringify`, which may encode `{}` as `[]`). NaN and infinities are written as `null`; text with quotes, slashes and control characters is escaped.

## JSONL format

One object per line, always with `t` (simulation seconds) and `kind` first; the remaining keys in alphabetical order. Line types:

| `kind` | Fields | When |
|---|---|---|
| `session_start` | `format=1`, `csp_version` (`ac.getPatchVersion()`), `csp_code` (`ac.getPatchVersionCode()`), `car_id` (`ac.getCarID(0)`), `track_id` (`ac.getTrackFullID('/')`), `api` (name -> bool), `config` | first call to `update` |
| `car_fields` | `available` (`car.<field>` -> bool) | first call with a car |
| `sample` | `pos={x,y,z}`, `look={x,y,z}`, `speedKmh`, `lapTimeMs`, `lapCount`, `isLapValid`, `splinePosition` | every 1/`sample_hz` s with a car |
| `collision` | `collidedWith`, `wheelsOutside`, `collisionDepth` | only when `collidedWith`, `wheelsOutside` or the contact state (`collisionDepth > 0`) changes |
| `lap` | `lapCount`, `previousLapTimeMs`, `isLastLapValid` | when `lapCount` changes |
| `detector` / `connection` | `state` | when they change |
| `sim` | `isPaused` | when it changes (if `sim` exposes it) |
| `error` | `message` | for each `extra.errors` item |
| `car_missing` | | when `car` becomes nil |
| any other | whatever is passed to `:event(kind, fields)` | on demand |

Sample line (the track analysis tool imports these lines):

```json
{"t":12.5,"kind":"sample","isLapValid":true,"lapCount":0,"lapTimeMs":1000,"look":{"x":0,"y":0,"z":1},"pos":{"x":1.5,"y":0.25,"z":-3},"speedKmh":50,"splinePosition":0.1}
```

Missing fields (API not exposed by the CSP version) are simply absent from the line; `pos`/`look` are omitted if any component is not a finite number.

APIs reported in `api`: `ac.getCar`, `ac.getSim`, `ac.getPatchVersion`, `ac.getPatchVersionCode`, `ac.getCarID`, `ac.getTrackFullID`, `ac.onCarJumped`, `ac.onSessionStart`, `ac.getFolder`, `ac.storage`, `io.save`, `io.load`, `io.move`, `io.createDir`, `io.deleteFile`, `io.fileExists`, `web.get`, `web.post`, `JSON.parse`, `JSON.stringify`, `ui.pushDWriteFont`, `ui.popDWriteFont`, `ui.dwriteText`, `ui.drawRectFilled`, `ui.textColored`, `ui.windowSize`, `ui.setCursor`.

## Integration (reference)

```lua
local diagnostics = require('diagnostics')
local diag = diagnostics.new(settings, {})   -- settings.diagnostics == true to enable
function script.update(dt)
  -- ... existing scoring ...
  diag:update(dt, ac.getCar(0), ac.getSim and ac.getSim() or nil,
    {connection = {online = online, pending = #queue.entries}, detector = detectorState})
end
```

Calling `diag:event('server', {...})` from the `web` callbacks is recommended to record unrecognized responses. There is no verified shutdown hook in CSP, so unloading the app loses at most `flush_s` seconds of lines.

## Not verified

- Real path of `ac.getFolder(ac.FolderID.Root)` and write permissions in `apps/lua/RiceTrials/diagnostics`.
- Result of `io.createDir` on an existing folder, of `io.move` onto an existing target, and atomicity of `io.save`.
- Real cost of a 2 MB `io.save` on the rig (possible micro-stutter every 10 s).

## Tests

`client/tests/test_diagnostics.py`: inactive by default, header and valid JSON per line (including escapes), 2 Hz sampling, collisions only on change, periodic flush (not per frame) that keeps earlier lines, buffer cap dropping the oldest, size-based rotation with 2 bounded files in time order, rotation of the previous session with delete before move, missing `io`, `io` raising errors and later recovery, `io.save` returning `false`, missing car and invalid `dt`, detector/connection only on change, injected clock and folder via `ac.getFolder`, cdata-like car that raises on missing fields, and a LuaJIT 2.1 smoke test.
