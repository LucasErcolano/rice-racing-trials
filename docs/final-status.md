# Deployment status — integration handoff

**BLOCKED for official trials.** No Windows runtime, AC/CSP rig, live calibration or service reboot validation has been performed. Do not publish release ZIPs until the parent integration pass and Windows operator sign-off.

## Prepared

- Windows PyInstaller build template stages the Lua app, read-only validation kit, offline landmark hashes, deployment notes, frozen server, DPAPI secret setup and foreground server launcher. Manually activated CI template runs tests, checks archives for common proprietary/secret/database formats and smoke-tests `/tv` on a Windows runner; it does **not** publish a release.
- Client installer hashes externally obtained archives and checks installed folder IDs, KN5/physics presence and a CSP-loader candidate. Preflight is read-only. On installation it prompts for a station token (masked), writes `apps/lua/RiceTrials/station_config.lua` with Lua-safe byte escaping and fields `server_url`, `station_id`, `station_token`, and applies restrictive NTFS ACLs; this local plaintext file is excluded from both package builders. Upgrade requires an explicit external backup directory; staging/rollback preserves the previous app on failure. No third-party assets are extracted, installed or included. Client bootstrap now calls `require('station_config')` in a guarded first update; CSP module resolution remains unverified.
- Inventory kit hashes installed Physics/Dummy KN5 and car physics file and compares track landmark hashes to offline evidence; its `ready` result is always false, pending in-game identity, layout, geometry, timing and network attestation.
- Server startup design uses Windows DPAPI CLIXML under the same dedicated account, a durable absolute DB outside release media, loopback binding, and Task Scheduler setup instructions. It is **not** an installed Windows Service.

## Verified in Linux

- Con el venv temporal que contiene `lupa`, la suite completa pasó: **30 tests de servidor/integración/packaging y 29 tests de cliente Lua**. El E2E unió Lua con HTTP real, SQLite, reenvío idempotente tras pérdida de respuesta, reasignación y leaderboard vacío.
- El servidor real arranca con scoring desarmado para los tres eventos y rechaza aprobación a `VALID`. El cálculo con scoring habilitado se ejercita únicamente en tests de contrato. El inventario conserva `ready: false`.
- El build Linux source-only y su verificación de integridad y exclusiones se efectúan en la pasada final de integración; no se ejecutó la compilación Windows.

## Remaining gates

1. Integrate sibling changes, rerun full tests and package audits, then build and exercise the Windows ZIPs and PowerShell scripts on an actual Windows runner. Linux cannot validate PowerShell syntax/runtime, DPAPI behavior, PyInstaller output, scheduled startup, or uninstall rollback in Windows.
2. On each rig perform the signed checklist in `docs/windows-validation.md`: approved asset archive/hash provenance, installed hashes, AC/CSP exact build, selected track/layout/car, Lua loader, surveyed X/Z directional gates and ordered route, outlap, independent video/timing, penalties, server authorization/ack/retry/read-back and backup/restore.
3. Do not enable automatic official scoring or claim commissioning until those gates and the sporting protocol are approved. Upload/publish artifacts only after integration and sign-off.
