# Evidencia del entorno MOCK de CSP

**MOCK, no validación de CSP.** Qué es simulado y qué es real: `docs/csp-mock.md`.

Corridas del 2026-10-20 sobre el commit `aed4359` (cliente) más la opción `RICE_SIM_SERVER_EXE` de `sim/cspmock.py`, que agrega `ExeServer`. Las tres usaron la geometría real de FSAEJ (`RICE_FSAEJ_GEOMETRY`), así que se corrieron los 25 escenarios.

| Carpeta | Plataforma | Servidor | Resultado |
|---|---|---|---|
| `linux-inprocess/` | Ubuntu 24.04 (host), LuaJIT 2.1 | `server/app.py` en proceso | 25/25 |
| `windows-inprocess/` | Windows 11 Pro 26300 (VM `rice-win11`), LuaJIT 2.1 | `server/app.py` en proceso | 25/25 |
| `windows-frozen-exe/` | Windows 11 Pro 26300 (VM `rice-win11`), LuaJIT 2.1 | `RiceTrialsServer.exe` (PyInstaller, compilado en la VM) | 25/25 |

En la VM no hay checkout de git (`sync-repo` copia los archivos), por eso `git_commit` sale `null`. La trazabilidad está en `client_sha256` y `server_sha256`: coinciden con los archivos de `aed4359`. `SHA256SUMS-windows-build.txt` lista los ZIP compilados en la VM (versión `0.2.0-vm`, no es un release).

Reproducir: `python sim/run_evidence.py --out <carpeta>`. Opcional: `RICE_FSAEJ_GEOMETRY=<geometry.json>` y `RICE_SIM_SERVER_EXE=<RiceTrialsServer.exe>`.
