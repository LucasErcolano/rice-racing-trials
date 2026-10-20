# Herramientas de validación en la VM `rice-win11` (solo testing, no se instalan en los simuladores)

Hallazgos del runtime real (2026-10-20):

- `acs.exe` directo falla con `Steam init failed`: se lanza con `steamclient_loader.exe` y `ColdClientLoader.ini` con `Exe=acs.exe` (original en `.orig`).
- Faltaban VC++ 2012/2013 x64 y DirectX 9 (`0xc000007b`).
- Hay que crear reglas de firewall para `acs.exe`, `acs_x86.exe`, `AssettoCorsa.exe` y el loader, o el diálogo bloquea el juego.
- Las tareas de sesión 1 usan `-RunLevel Limited` y `-WindowStyle Hidden` (con `Highest` falla Steam; sin Hidden se abre una consola encima del juego).
- AC ignora `mouse_event`/`keybd_event`: la entrada de menú funciona con `SendInput` por scancode / mouse absoluto (`sendinput.ps1`).
- Control de conducción: gamepad virtual X360 (ViGEmBus + `vgamepad`) con `controls.ini` en `INPUT_METHOD=X360` (original en `.orig`). Verificado: 40 % de gas da 24,7 km/h y el freno detiene el auto.
- Telemetría: Shared Memory `Local\acpmf_physics` y `Local\acpmf_graphics` (`acshm.py`).
