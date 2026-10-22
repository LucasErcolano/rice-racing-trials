# CSP MOCK environment evidence

**MOCK, not CSP validation.** What is simulated and what is real: `docs/csp-mock.md`.

Runs from 2026-10-20 on commit `aed4359` (client) plus the `RICE_SIM_SERVER_EXE` option of `sim/cspmock.py`, which adds `ExeServer`. All three used the real FSAEJ geometry (`RICE_FSAEJ_GEOMETRY`), so all 25 scenarios ran.

| Folder | Platform | Server | Result |
|---|---|---|---|
| `linux-inprocess/` | Ubuntu 24.04 (host), LuaJIT 2.1 | `server/app.py` in process | 25/25 |
| `windows-inprocess/` | Windows 11 Pro 26300 (VM `rice-win11`), LuaJIT 2.1 | `server/app.py` in process | 25/25 |
| `windows-frozen-exe/` | Windows 11 Pro 26300 (VM `rice-win11`), LuaJIT 2.1 | `RiceTrialsServer.exe` (PyInstaller, built in the VM) | 25/25 |

The VM has no git checkout (`sync-repo` copies the files), so `git_commit` is `null`. Traceability comes from `client_sha256` and `server_sha256`: they match the files of `aed4359`. `SHA256SUMS-windows-build.txt` lists the ZIPs built in the VM (version `0.2.0-vm`, not a release).

To reproduce: `python sim/run_evidence.py --out <folder>`. Optional: `RICE_FSAEJ_GEOMETRY=<geometry.json>` and `RICE_SIM_SERVER_EXE=<RiceTrialsServer.exe>`.
