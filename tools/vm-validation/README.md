# Validation tools for the `rice-win11` VM (testing only, not installed on the simulators)

Findings from the real runtime (2026-10-20):

- Launching `acs.exe` directly fails with `Steam init failed`: it is launched with `steamclient_loader.exe` and `ColdClientLoader.ini` with `Exe=acs.exe` (original in `.orig`).
- VC++ 2012/2013 x64 and DirectX 9 were missing (`0xc000007b`).
- Firewall rules must be created for `acs.exe`, `acs_x86.exe`, `AssettoCorsa.exe` and the loader, or the dialog blocks the game.
- Session 1 tasks use `-RunLevel Limited` and `-WindowStyle Hidden` (with `Highest` Steam fails; without Hidden a console opens on top of the game).
- AC ignores `mouse_event`/`keybd_event`: menu input works with `SendInput` by scancode / absolute mouse (`sendinput.ps1`).
- Driving control: virtual X360 gamepad (ViGEmBus + `vgamepad`) with `controls.ini` set to `INPUT_METHOD=X360` (original in `.orig`). Verified: 40 % throttle gives 24.7 km/h and the brake stops the car.
- Telemetry: Shared Memory `Local\acpmf_physics` and `Local\acpmf_graphics` (`acshm.py`).
