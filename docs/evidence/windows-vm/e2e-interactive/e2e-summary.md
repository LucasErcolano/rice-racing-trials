# Windows E2E: 33 OK, 0 failures

Environment: Microsoft Windows 11 Pro 10.0.26300 build 26300; PowerShell 5.1.26100.9444 Desktop; ZIP RiceTrials-0.2.0-vm-complete-win64.zip (sha256 3963a85780f387204524ba2df3a26b967902494b3d2180507c6a0792149481cf)

| Step | Result | s | Error |
|---|---|---|---|
| Extract complete ZIP | pass | 2.8 |  |
| AC root fixture (not the game) | pass | 0.4 |  |
| install.ps1 refuses to install without source hashes (fail-closed) | pass | 0.6 |  |
| install.ps1 rejects HTTP to a public IP | pass | 0.2 |  |
| install.ps1 verifies SHA-256 and rejects a tampered file | pass | 0.2 |  |
| install.ps1 -Preflight writes nothing | pass | 0.6 |  |
| install.ps1 installs with token via Read-Host and restricted ACL | pass | 4.9 |  |
| install.ps1 refuses to update without -BackupDirectory | pass | 0.2 |  |
| install.ps1 updates with backup, then rollback | pass | 2.7 |  |
| uninstall.ps1 preflight, cancellation and uninstall with backup | pass | 0.4 |  |
| Clean reinstall | pass | 1.1 |  |
| AC fixture files left intact | pass | 1.4 |  |
| configure-secrets.ps1 stores tokens with DPAPI | pass | 9.9 |  |
| configure-secrets.ps1 refuses to overwrite and to store inside the release | pass | 0.1 |  |
| start-server.ps1 rejects 0.0.0.0 and a DB inside the release | pass | 0.7 |  |
| start-server.ps1 starts the PyInstaller exe with DPAPI secrets | pass | 15 |  |
| API: session, driver, assignment, heartbeat, lap, duplicate, leaderboard, CSV | pass | 0.5 |  |
| server-admin.ps1 Backup (token via Read-Host) and Check | pass | 1.6 |  |
| Lap after the backup (to test the restore) | pass | 0.1 |  |
| server-admin.ps1 Restore refuses while the server is running | pass | 11.4 |  |
| server-admin.ps1 Restore (server stopped) and verification | pass | 10.3 |  |
| Server restart keeps the leaderboard | pass | 5.4 |  |
| LAN bind to own private IPv4 (-AllowLanBind) | pass | 7.3 |  |
| server-admin.ps1 FirewallRule (create, idempotent, remove) and NetworkCheck | pass | 28.8 |  |
| Create dedicated local account and folder permissions | pass | 1.4 |  |
| configure-secrets.ps1 run by the service account (that account's DPAPI) | pass | 14.3 |  |
| Another account (the runner admin) can NOT decrypt the service secrets | pass | 0.1 |  |
| server-admin.ps1 RegisterTask + Start-ScheduledTask (dedicated account) | pass | 10.1 |  |
| Task server: previous data kept and service tokens valid | pass | 0.1 |  |
| exe crash: start-server.ps1 relaunches it (supervisor, 10 s) | pass | 13.6 |  |
| server-admin.ps1 Stop halts task, supervisor and exe (no relaunch) | pass | 36 |  |
| UnregisterTask and account cleanup | pass | 3.7 |  |
| Evidence without tokens or passwords | pass | 0 |  |

AC root = text fixture. This does NOT validate Assetto Corsa, CSP, the track or the car. A real Windows reboot was not tested.
