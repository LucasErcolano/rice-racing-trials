# Windows 11 test VM (`rice-win11`)

Status: **operational** since 2026-10-20. Decision and alternatives: `virtualization-decision.md`. Host audit: `hardware-audit.md`.

## What it is

| | |
|---|---|
| Host | Ubuntu Server (Gigabyte X99-UD4P, i7-5820K), QEMU/KVM + libvirt, OVMF with Secure Boot, TPM 2.0 via `swtpm` |
| VM | Windows 11 Pro (build 26300), **not activated** (official Microsoft ISO verified by SHA-256) |
| CPU / RAM / disk | 8 vCPU pinned to physical cores 2 to 5 with their threads; 12 GiB; 60 GB qcow2 |
| GPU | **RTX 3060 via VFIO**: Windows sees it with NVIDIA driver 32.0.16.1088, status OK. The RTX 3090 stays on the host with the `nvidia` driver. Video output: the TV connected over HDMI to the 3060. |
| Network | libvirt NAT (`192.168.122.0/24`), no ports exposed outside the host |
| Access | Windows OpenSSH with key only (`riceadmin`); the local account password is not used over the network |
| Toolchain | Python 3.12, `lupa` 2.8 (LuaJIT 2.1), PyInstaller |

The host scripts live in `/home/lucas/vm/rice-win11/` and are kept out of the repo because they contain the SSH key and the local password:

- `create-vm.sh`: creates the VM and aborts if the 3060 is not on `vfio-pci`, if the 3090 is not on `nvidia`, if the IOMMU group is not exactly the 3060 with its audio, if the ISO hash does not match, or if not enough disk would remain for the host.
- `vmssh`: runs PowerShell in the VM over SSH.
- `sync-repo`: copies the repo (versioned and non-ignored files) to `C:\rr\src`.
- `jobs/*.ps1`: tests, build, E2E, simulator and interactive session (`run-interactive.ps1` uses a scheduled task to run inside the `riceadmin` desktop, because the installation E2E needs that token).

## What was validated in the VM

Evidence from **real Windows**. The Assetto Corsa folder was fake and CSP was simulated for the
suites, the build and the installation E2E; the in-game validation (last item) uses real AC and
CSP:

- Repo suites: 88 OK (9 skipped for Linux or browser tooling) and 104 client tests.
- PyInstaller build and the three ZIPs (`SHA256SUMS.txt` in `docs/evidence/csp-mock/`).
- Installation E2E (`tests/windows/e2e-windows.ps1`) in the interactive session: 33 OK. Evidence in `docs/evidence/windows-vm/e2e-interactive/`.
- CSP MOCK scenarios with the in-process server and against the compiled `RiceTrialsServer.exe`: see `docs/csp-mock.md` and `docs/evidence/csp-mock/`.
- **In game (real AC + CSP 0.2.11, FSAEJ track and MFT02 car installed)**: the client loads through CSP and reaches the server (authenticates, receives the ST01/S1/P01 assignment and sends heartbeats with `online:true`). Proof: `docs/evidence/windows-vm/ingame-validation.md`.

## What is missing

- Confirm on track `io.save`/`io.load`, the HUD, `ac.onCarJumped`/`resetCounter`,
  timing and lap detection (requires real driving; scoring is still disarmed).
- The VM does not replace the rig: the wheel, the pedals and the final validation happen at the
  real station. The checklist is still `docs/windows-validation.md`.
