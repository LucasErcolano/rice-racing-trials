# Windows/CSP validation — NOT PERFORMED

This Linux build has **not** been launched in Assetto Corsa, CSP, or Windows. Static tests are not a Windows smoke test. Do not represent the app as commissioned or an attempt as scored.

## Required operator procedure

1. Install the packaged `RiceTrials/` Lua files under `assettocorsa/apps/lua/RiceTrials/` with `install.ps1` under the game user's account; the installer creates local `station_config.lua` after a masked token prompt. The client still needs a guarded `require('station_config')`/`script.connect(...)` bootstrap integrated; verify CSP resolves that module and the app window loads without Lua errors. Never put that credential in a ZIP. Until bootstrap and CSP file-loading capability have been tested on Windows, network submission is not validated.
2. Run `py -3 tools\validation.py --assetto-root 'C:\path\to\assettocorsa' --output 'D:\protected\station-inventory.json'` from an extracted client/complete ZIP. Preserve the JSON off release media. It inventories CSP-loader candidate (`dwrite.dll`), exact installed car folder with physics-data SHA-256, and Physics/Dummy KN5 hashes against `track/calibration/timing-landmarks.json`; `ready=false` even on matching hashes. Confirm the selected event's **actual installed asset** identity and layout in game, and independently approved archive hashes on Windows; no test here proves the installed bytes came from the download. Do not substitute the Rice autocross track or another layout.
3. Survey/verify world X/Z timing endpoints and crossing direction against the exact installed asset. Confirm coordinate signs by logging real car positions, then replay stationary, reverse, off-segment, and forward crossings. Never copy geometry from an unrelated track. Keep `script.configure` unset until both survey and live asset binding are verified; its `verified=true` value is operator attestation only, **not cryptographic enforcement**.
4. Survey an ordered nonempty checkpoint route as well as the start/finish gate; verify the complete outlap is excluded from timing. With a test-only loopback server, verify station assignment/token expiration and heartbeat, protocol version 1 fields, HTTP acknowledgements, timeout, failure, crash-safe durable retries and deduplication before enabling any network submission. The client has `script.connect` but no automatic bootstrap yet; even after integration, crash-atomicity and authenticated assignment must be verified end to end. Do **not** connect to production until schema and authorization are signed off.
5. Have a reviewer record DOO, OC, autocross exit/re-entry, skidpad R,R,L,L, timed laps 2/4, DNF, and run validity. Compare independent stopwatch/video to HUD splits. Test reset, session restart, app closure/reopen, paused sim, invalid telemetry and multiple attempts. Validate scoring formulas separately against approved two-event protocol, which remains DRAFT with TBD sporting choices.
6. Record Windows version, AC/CSP exact build, asset SHA-256, selected track/layout, app log, gate calibration provenance, operator, sample event evidence and server read-back. Mark each result pass/fail; block operational release on any missing item.

## Calibration and validation record (one signed copy per station and installed layout)

| Item | Evidence to enter | Result |
| --- | --- | --- |
| Rig / operator / UTC / reviewer | ______ | pending |
| Windows / AC / CSP exact versions and Lua console | ______ | pending |
| Original archive SHA-256 + independently approved source | ______ | pending |
| Inventory JSON path + installed Physics/Dummy/car hashes | ______ | pending |
| In-game track ID/layout and selected MFT02 car ID | ______ | pending |
| Start/finish left/right world X,Z, forward normal, survey method | ______ | pending |
| Ordered checkpoints and full outlap video/time reference | ______ | pending |
| Stationary / reverse / off-segment / forward crossing traces | ______ | pending |
| R,R,L,L / autocross route / cone and OC reviewer evidence | ______ | pending |
| Server token/assignment/ack/retry/duplicate read-back | ______ | pending |
| Restart, DB backup/restore, task-start/reboot test | ______ | pending |
| Reviewer signature and final decision (default: BLOCKED) | ______ | BLOCKED |

The timing-landmarks JSON is offline evidence, **not** a gate configuration. Do not generate active `script.configure` gates from it without a live survey and signed validation. Archive matching, file-hash matching and a CSP-loader DLL alone cannot verify the active game session or certify scoring.

**Current result:** API source inspection and Linux static tests only. Spatial calibration, CSP execution, Windows integration, network contract, automatic penalties and real scoring all **UNVERIFIED**.
