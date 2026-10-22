# V2: field testing, provisional results

This version is opt-in and does not replace or deploy over the operational V1. It is not ready for official competition. Use a separate SQLite database, port and directory for V2. Official scoring stays disarmed.

## Quick installation on Windows

1. Download the **complete-win64** ZIP, verify its SHA-256 with `Get-FileHash -Algorithm SHA256` against `SHA256SUMS.txt` and extract it to a new folder. Do not run from inside the ZIP.
2. Keep the V1 installation and back up the app before installing. Close AC before changing the app so a lap is not lost to a hot reload.
3. Prepare DPAPI credentials with `configure-secrets.ps1` as described in `deployment.md`; the credentials file and the database must live outside the release directory. Never include tokens in reports or screenshots.
4. Launch a test instance with a new database and a separate port:

```powershell
.\start-server.ps1 -SecretsPath 'C:\RiceV2Data\secrets.xml' -DatabasePath 'C:\RiceV2Data\field.sqlite' -Port 8082 -EnablePenaltiesV2
```

To register a test task, use a separate `-TaskName RiceTrialsV2Field` and pass `-EnablePenaltiesV2` to `server-admin.ps1 -Action RegisterTask`. Do not modify the V1 task.

5. Create a test session, driver and assignment in this instance's `/admin`. Install the client with an explicit backup:

```powershell
.\install.ps1 -AssettoRoot 'C:\...\assettocorsa' -StationId ST01 -ServerUrl 'http://<server-LAN-IP>:8082' -BackupDirectory 'C:\RiceV2Data\backups' -SkipArchiveHashes -RouteConfig '.\track-config\FSAEJ_Endurance.route_config.lua' -ConeConfig '.\track-config\cone_config.lua' -EnableDiagnostics -ProtocolV2
```

`-SkipArchiveHashes` skips the origin check of the installed mods; it does not validate their bytes. V2 is off by default without `-ProtocolV2` and `-EnablePenaltiesV2`. Never copy `station_config.lua` into a ZIP or to GitHub. The track and the car are obtained separately and are not included.

## OR protocol and review

The native and penalized channels have separate persistence and retries. The native channel does not depend on penalty analysis or submission working. A native lap without penalty evidence is kept as `PENALTIES_PENDING` and is never read as zero penalties. Laps are reconciled by stable identity, not by closeness of times. Contradictions, incomplete evidence or ambiguous identity require manual review. Calculated times are provisional previews, not an official leaderboard.

The independence is between queues and transport inside the client: both channels share AC, CSP, the app, the disk and the machine. If that runtime does not capture the lap, there is no guaranteed second, external timing source. The native journal is the available local fallback, not a confirmed Velocity integration.

Each row carries `penalty_evidence_state` ("Penalties" column on the TV) and `disabled_penalties`:

| Status | Meaning |
| --- | --- |
| `ABSENT` | Only the native lap arrived. Penalties pending, never zero. |
| `INCOMPLETE` | Evidence arrived but is missing or invalid (detector, calibration, unresolved events). |
| `ZERO_INCIDENTS` | Complete evidence with no penalizable incidents. |
| `PENALIZED` | Complete evidence with a penalty calculated by the server. |
| `PENALTIES_DISABLED` | Incidents were recorded, but their penalty was disabled. The evidence is kept. |
| `CONFLICT` | The messages contradict each other. Goes to manual review without a corrected time. |

## Dedicated display and field verification

Open `http://<server-LAN-IP>:8082/v2/field` on the TV. Enter the admin token of the V2 instance (it does not go in the URL). It is kept in memory only. The display refreshes every 3 seconds and shows a single row per station/session/lap, the native raw time, the corrected preview and the review reasons. `/admin` and `/tv` keep the V1 behavior and are NOT the provisional monitor. The packages include Endurance routes and cones to get started; they can be adjusted during testing. No Velocity API is needed.

The preview is recalculated on the server from events and settings, even without final calibration. It is not validated evidence: no `detector_ok` or `calibration_verified` sent by the client verifies the lap on its own.

After inspecting the exported evidence, the operator can give a **manual field attestation**, without enabling an official leaderboard:

- `POST /api/admin/v2/field-review` with the admin Bearer token and JSON `{"station_id":"ST01","session_id":"S1","lap_id":"<exact-id>","approved":true,"reviewer":"operator","reason":"Specific reason from the evidence review"}`.
- The lap moves to `VERIFIED_PROVISIONAL`, `evidence_verified=true`, and keeps `corrected_ms=null`, `final_ms=null` and `official=false`. The display shows the corrected time through `preview_ms`. This certifies only the declared human review, not an automatic physical calibration.
- `approved:false` revokes the attestation. Each decision is persisted with date, reason, reviewer and the SHA-256 of the exact penalized message. History: `GET /api/admin/v2/field-reviews?station_id=ST01&session_id=S1&lap_id=<exact-id>`.
- A native lap without penalties cannot be verified. Inconsistencies invalidate any attestation and stay in review, with no preview and no official promotion.

Full export: `GET /api/admin/v2/field-export.json` or `.csv`, with the admin Bearer token. Both original messages and the evidence are kept. Zero penalties are never inferred from missing evidence.

## Diagnostics and reporting

Keep the `apps\lua\RiceTrials\state` directory and the client diagnostic logs, plus the server logs in the external data directory. Before sharing, remove `station_config.lua`, DPAPI credentials, tokens and personal data. For a bug, record station, session, driver, track, lap_id, native raw time, detector raw time, local events, penalty settings, status of both queues and the time. Export the provisional laps with the stored evidence. Do not edit the A/B journals by hand.

## Rollback

Stop only the V2 task with `server-admin.ps1 -Action Stop -TaskName RiceTrialsV2Field -Port 8082` and that instance's data parameters. Do not kill only the exe: the supervisor brings it back. With AC closed, restore the app backup with `install.ps1 -Rollback '<backup>' -BackupDirectory '<existing folder>'`. Keep the test database as evidence; do not open it with V1 or overwrite the operational database. Track, car and CSP files are not modified.

## Validation gates

`Dummy.kn5`: validated in AC by explicit user confirmation. This does not validate the V2 detectors or the physical calibration. Automated Linux and Windows tests do not replace a real CSP session. Final calibration does not block collecting laps, but it does block their official use. See `test-report.md`, `windows-validation.md` and `velocity-integration.md` for the evidence and limits of this delivery.
