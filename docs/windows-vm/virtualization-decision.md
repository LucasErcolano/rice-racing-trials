# Virtualization decision (Windows for Assetto Corsa)

Date: 2026-10-19. Basis: `hardware-audit.md`. Status: **decided, not executed**. Blocked until VT-x and VT-d are enabled in the BIOS.

## Purpose

Test on Windows what GitHub Actions cannot: Assetto Corsa with CSP, the `RiceTrials` Lua app, the `Dummy.kn5` patch, the FSAEJ track and the MFT02. The installer, DPAPI, frozen server, `server-admin.ps1` and scheduled task already pass on a real Windows runner (run `39334554771`, 32/32; see `docs/windows-validation.md`).

## Options

| | A. VM without GPU (QXL/virtio-gpu) | B. KVM VM with the RTX 3060 via VFIO | C. Physical Windows PC |
|---|---|---|---|
| Requires | VT-x | VT-x and VT-d | another machine with AC |
| Runs AC (DirectX 11) | not in practice: software rendering | yes, with the native NVIDIA driver | yes |
| Effect on the host | none | the 3060 is unavailable to Linux while assigned to vfio | none |
| Use | installer and server only (already covered by CI) | in-game client validation | final validation on the rig |

**Decision: B.** The RTX 3060 (`02:00.0` `10de:2503` and its audio `02:00.1` `10de:228e`) moves to the VM. The RTX 3090 (`03:00.0`) stays on the `nvidia` driver for training. C remains mandatory before the official trials: a VM does not replace the wheel, the pedals or the rig.

## Planned configuration

- **Hypervisor:** QEMU/KVM with libvirt, OVMF firmware (UEFI with Secure Boot) and `swtpm` (TPM 2.0 for Windows 11). None of this is installed today (`virsh` does not exist).
- **CPU:** 8 vCPU (4 cores with their threads), pinned to physical cores; 2 cores and 4 threads remain for the host.
- **RAM:** 12 GiB (~16 GiB left for the host). 16 GiB only if no training runs at the same time.
- **Disk:** dynamic qcow2 of **70 GB virtual** in `/var/lib/libvirt/images`. With 81 GB free, even when full it leaves ~11 GB for the host. Do not use 120 GB: a qcow2 that grows until it fills `/` breaks the host.
- **Network:** libvirt NAT. The visitor Wi-Fi network does not allow an L2 bridge. No VM port is exposed to the network.
- **Remote access:** via Tailscale and an SSH tunnel to `127.0.0.1`. With the GPU assigned, the image goes out through the 3060 and not through SPICE, so a video output is needed:
  - an **HDMI/DP dummy plug** on the 3060 (recommended, a few dollars), or
  - a virtual display driver in Windows,
  - plus Sunshine/Moonlight or Parsec over the tunnel to see and drive.
- **Licenses:** Windows, Steam/Assetto Corsa and CSP are provided by the user. The JSAE and MFT02 assets are copied from `assets/source/` (they never enter git or the ZIPs).

## Steps after the BIOS (in order, with verification)

1. **BIOS** (user, requires a reboot): enable "Intel Virtualization Technology" and "Intel VT-d".
2. **Verify:** `grep -c vmx /proc/cpuinfo` > 0, `/dev/kvm` exists, `dmesg | grep -i dmar` shows the DMAR table.
3. **Kernel:** add `intel_iommu=on iommu=pt` to `GRUB_CMDLINE_LINUX_DEFAULT`, `update-grub`, reboot. Check `/sys/kernel/iommu_groups`: `02:00.0` and `02:00.1` must be alone in their group. If they share a group with another device, stop and reassess (do not use the ACS override patch without an explicit decision).
4. **vfio:** `options vfio-pci ids=10de:2503,10de:228e` and `softdep nvidia pre: vfio-pci` in `/etc/modprobe.d/vfio.conf`, `update-initramfs -u`, reboot. Verify with `lspci -nnk -s 02:00` that the driver in use is `vfio-pci` and with `nvidia-smi` that the 3090 is still available. The 3060 IDs differ from the 3090 IDs, so binding by ID does not affect the 3090.
5. **Packages:** `qemu-kvm libvirt-daemon-system virtinst ovmf swtpm`.
6. **VM:** create with `virt-install` (UEFI, TPM, 70 GB virtio disk, 12 GiB, 8 vCPU, hostdev `02:00.0` and `02:00.1`). Install Windows 11, the NVIDIA driver, Steam, AC, CSP and then the client ZIP with `install.ps1 -Preflight` and afterward without `-Preflight`.
7. **In-game validation:** follow `docs/windows-validation.md` and `docs/csp-api-validation.md`.

## Rollback

Removing the file `/etc/modprobe.d/vfio.conf`, running `update-initramfs -u` and rebooting returns the 3060 to the `nvidia` driver. The `intel_iommu=on iommu=pt` parameters can stay. Disabling VT-x/VT-d in the BIOS returns to the audited state.

## Risks

- While the 3060 is on vfio, the autopilot checkpoint sweep that uses it cannot run; training on the 3090 is unaffected.
- The host has no monitor: if something goes wrong at boot, the console is still available over `ttyS0`.
- AC performance in the VM does not represent the rig; it serves to validate behavior, not to measure FPS or wheel latency.
