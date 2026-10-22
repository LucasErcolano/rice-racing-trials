# In-game client validation (`rice-win11`, real AC + CSP)

2026-10-20, by Lucas (Hermes agent), VM `rice-win11` (RTX 3060 in passthrough to the TV).

## What was confirmed

The `RiceTrials` Lua client loads inside Assetto Corsa through CSP 0.2.11 and
reaches the server end to end on the real runtime:

- `acs.exe` launched directly (`-e madformulateam_mft02 -t FSAEJ_Endurance -s 0`), engine
  running (PID 7992 in the capture, CPU time accumulating).
- CSP loaded (`dwrite.dll` present; `Documents\Assetto Corsa\logs\py_log.txt` was created).
- The client autostart ran: it read `station_config.lua`, authenticated with the station
  token, got the assignment (ST01 -> S1 -> P01) and is sending heartbeats.

## Proof (captured ~22:25 UTC on 2026-10-20)

`GET /api/station/assignment` (station token):

```json
{"station_id":"ST01","session_id":"S1","pilot_id":"P01",
 "assignment_revision":1,"online":true,
 "last_seen_utc":"2026-10-20T22:25:23.434161+00:00"}
```

`online:true` with a `last_seen_utc` that refreshes every ~30 s is the read-side proof
that the client is alive and talking to the server, not a static seed.

## What this does NOT prove (still pending)

- `io.save` / `io.load` writing state to `apps/lua/RiceTrials/state/` (no files were observed
  yet; the journal writes on session events).
- The HUD rendering on the TV.
- `ac.onCarJumped` / `resetCounter` firing as the mock assumes.
- On-track timing, lap detection or scoring (requires real driving; scoring
  is still disarmed, `scoring_armed=False`).

## VM configuration used (dev/test, not the packaged deployment)

- Server: `python -m server.app --db C:\rr\test.sqlite --host 127.0.0.1 --port 8080`
  run by the `RiceTrialsServer` scheduled task as `SYSTEM` (AtStartup), not the packaged
  path `start-server.ps1` + DPAPI + dedicated account from `docs/deployment.md`.
- Plain-text `secrets.env` for the VM only (read access for `SYSTEM` granted with a targeted
  `icacls`).
- AC runtime prerequisites installed: VC++ 2010/2012/2013/2015-2022 (x64) and
  DirectX June 2010 (`d3dx9_43.dll`); without them `acs.exe` dies with `0xc000007b`.
- `riceadmin` autologin enabled (registry) so the VM boots into the interactive session
  that owns the GPU.
