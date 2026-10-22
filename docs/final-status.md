# Status of the V2 field delivery

## Authorized scope

Velocity confirmed CSP 2.11, installation of our Lua app, LAN between stations and authorization to run our server and leaderboard on the TV. **Integrating the Velocity leaderboard is not required.** The V2 pre-release is for real-world testing and is not blocked by that integration or by pending calibrations.

**`Dummy.kn5`: VALIDATED IN GAME by the user.** The third-party file is not distributed. The tool that generates and applies the patch from the operator's local material is kept.

## Delivered

- Two A/B journals, payloads, queues, retries and separate endpoints: native AC capture and penalized result with local evidence. A detector error must not block native capture or submission.
- Stable identity `(station_id, session_id, lap_id)` backed by the journal. Byte-for-byte retries, reconciliation in either order, a single row per lap and conflicts persisted for review.
- Offline recovery of native laps: the last authenticated assignment context persists across app restarts and is reused only for up to 600 s of game clock. An expired assignment is removed before the next native lap is observed. Without a sufficient assignment, the lap stays `UNASSIGNED`; it is never automatically attributed to a different driver. `/v2/field` lets you review it and assign it manually with an audit trail, without entering it into the official ranking.
- Native lap alone: provisional raw time with `PENALTIES_PENDING`. Penalized: result calculated by the server from local events and settings, kept apart from the raw time and marked Pending Review while unverified.
- Authenticated administrative field verification, with reason, evidence hash and persistent audit. It allows showing a verified correction **for testing**, without turning it into an official result. Client flags never verify a lap on their own.
- Dedicated `/v2/field` monitor, auto-refresh, JSON export and per-channel status. `/admin`, `/tv` and the V1 ranking keep their previous behavior.
- Cones: sufficient geometric contact still penalizes, as in V1. The OR condition of contact or nearby trajectory cannot erase that contact. Uncalibrated route gates are skipped; the independent cone, edge and slalom channels remain available.
- Windows installer with `-ProtocolV2`, portable server with `-EnablePenaltiesV2`, and that flag persisted when registering the task. V1 remains the default.
- Process and real-HTTP smoke tests for the source and the compiled exe, plus Lua client, server, geometry, browser and packaging tests. Results for each Windows build are published as GitHub Actions evidence.

## Explicit limits

The two queues are independent, **not** two processes, machines or runtimes: both producers share AC/CSP and the disk. A failure of AC, CSP, the station or storage can affect both. Journal recovery does not prove tolerance to a physical power cut.

The V2 field build does not add provisional laps to the official ranking: `official=false` and `corrected_ms=null` even after a manual field verification. The corrected figure is shown in `corrected_preview_ms` (alias `preview_ms`) with its status. Sporting validation and on-track calibration remain pending, but they do not block installing this pre-release.

To be checked during testing: real behavior of the journals and CSP 2.11 APIs, HUD, cone/edge/slalom detection, network recovery, station concurrency and physical gate calibration. **Complete V2 is not claimed to be validated in game.** The `Dummy.kn5` confirmation is recorded as user validation.

## Install and test

See the [V2 field guide](v2-field-testing.md). Recommended: a dedicated V2 port and SQLite database so the operational V1 is untouched. The installable ZIPs are built on Windows; the complete package includes the compiled server and the client, but not Assetto Corsa, CSP, car, track, KN5, credentials or databases.
