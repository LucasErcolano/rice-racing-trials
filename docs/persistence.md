# Local queue persistence (client/journal.lua)

Status: tested on Linux with lupa (LuaJIT 2.1, same as CSP) over a simulated
in-memory filesystem (`client/tests/test_journal.py`). Not yet validated in CSP on
Windows. Integrated in `RiceTrials.lua`: the whole client state (entries, nonce, next_id, lap numbers) is a single journal record in `apps/lua/RiceTrials/state/`; the old `ac.storage` key is migrated once and deleted after the file is confirmed.

## Actual guarantees

- A queue saved with `j:save(items)` that returned `true` survives a restart of the
  Lua VM or of Assetto Corsa: `true` means the file was written, read back with
  `io.load`, and the content read is identical and has a valid checksum.
- If a write fails, is truncated or the readback does not match, the previous slot
  is left untouched and the last confirmed queue is still recoverable.
- `false` means "not confirmed", not "not written". If the bytes landed correctly on
  disk despite a failed readback, the next load may return that new version.
  The caller must treat `false` as "uncertain state" and retry the save.
- NO resistance to power loss or an operating system crash is claimed: the CSP API
  does not expose fsync, so a correct readback may come from the OS cache.
- Degraded mode (`ac.storage`) is weaker: CSP flushes ac.storage
  asynchronously, so the readback only confirms process memory.

## On-disk format

Two slots in `<dir>/<name>.a.json` and `<dir>/<name>.b.json` (default
`name = 'queue'`). Each holds a single JSON object:

```json
{"checksum":"bf9cf968","items":"[{\"id\":\"L1\"}]","seq":7,"v":1}
```

- `v`: format version (1).
- `seq`: increasing integer; the valid slot with the highest `seq` is the current one.
- `items`: the item array encoded as a JSON string. It is embedded as a string
  so the checksum covers exactly those bytes.
- `checksum`: 32-bit FNV-1a in hexadecimal (8 lowercase digits) of the `items`
  string. Uses LuaJIT `bit` if available and modular arithmetic on Lua 5.1/5.4 otherwise.
  Tested vectors: `''` = 811c9dc5, `'a'` = e40c292c, `'foobar'` = bf9cf968. It is a
  detector of accidental corruption, not a signature: it does not protect against deliberate edits.

`save` always writes the slot that does not hold the highest valid `seq`, with
`seq = max + 1`. It never uses `io.move` (its behavior on an existing target is not
documented) or append (which does not exist).

## States (`j:load()` returns `items, state`)

| State | Meaning | `can_send()` |
|---|---|---|
| `ok` | The valid slot with the highest `seq` was loaded (the other may be broken; it is reported in `last_error`). | yes |
| `empty` | No slots and no legacy key. Empty queue. | yes |
| `migrated` | There were no slots; `rice_trials_pending_review_v1` was imported from ac.storage, saved with a confirmed readback, and only then was the old key deleted. | yes |
| `degraded` | `io` missing or `io.save` fails: `ac.storage['rice_trials_journal_v2']` is used with the same envelope. Also if the migration could not be confirmed on file (the legacy key is kept). | yes |
| `corrupt` | Some file (or the degraded copy) exists but none is valid. Items empty, `save` refused. | no |

The legacy key is accepted as a JSON array or with the current `RiceTrials.lua` envelope
(`{version, nonce, next_id, entries}`); in that case `j.legacy_meta` stores
`nonce`, `next_id` and `version` so the orchestrator can keep them.

`j:state()` returns `{state, seq, slot ('a'|'b'|'storage'|nil), last_error,
quarantined (path of the first quarantined file or nil), quarantined_all, can_send}`.

## Manual recovery (`corrupt` state)

The client stays fail-closed: it must neither transmit nor discard laps, because
it does not know what was in the queue.

1. Close AC. The invalid raw contents were copied to
   `<dir>/<name>.quarantine.<n>.json` (n increases; an existing quarantine file is never
   overwritten). The original `.a.json` and `.b.json` files are not modified.
2. Inspect the slots and the quarantined copies; manually rescue the recoverable
   items and record them out of band (the track official's sheet).
3. Once the evidence is preserved, move `<name>.a.json` and `<name>.b.json` out of the
   folder (and delete `rice_trials_journal_v2` from ac.storage if it was corrupt).
4. On restart the state will be `empty` and the queue works again.

## Exact API

```lua
local journal = require('journal')
local j = journal.new({dir = <string>, name = 'queue'}, {
  io = <io table>,                 -- defaults to the global io
  json = {encode = fn, decode = fn}, -- defaults to JSON.stringify / JSON.parse
  storage = <ac.storage or similar>, -- optional: migration and degraded mode
  legacy_key = 'rice_trials_pending_review_v1',
  -- optional: degraded_key (default 'rice_trials_journal_v2'),
  --           io_disabled = true (do not use the global io)
})
local items, state = j:load()
local ok, err = j:save(items)   -- persists the whole array
local s = j:state()
local allowed = j:can_send()
journal.fnv1a(str)              -- also fnv1a_pure, FNV_IMPL ('bit' | 'pure')
```

`save` calls `load` if nothing has been loaded yet. Items must be a dense Lua array.

## Still to verify on Windows / CSP

- Real directory path (e.g. `ac.getFolder(ac.FolderID.Root)..'/apps/lua/RiceTrials/state'`
  or the documents folder) and that `io.createDir` creates it.
- Write permissions on that path with AC installed under `Program Files` or Steam.
- What `io.save` does on a full disk, a locked file or antivirus interference: whether it returns
  `false`, raises an error or leaves a truncated file. The module handles all three cases,
  but they were only tested with doubles.
- Whether `io.load` reads from cache or from disk, and whether an `io.save` interrupted by
  AC closing can leave partial content (which is why there are two slots).
- Behavior of CSP's `JSON.stringify` with empty tables (`{}` versus `[]`; the
  module normalizes `{}` to `[]`), large numbers (up to 2^53) and UTF-8.
- Whether assigning `nil` in `ac.storage` really deletes the legacy key.
