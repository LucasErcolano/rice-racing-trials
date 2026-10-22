# Host hardware audit (read-only)

Date: 2026-10-19. Host: Lucas's Ubuntu server, kernel 6.8.0-139-generic. No command in this audit modified the system.

## CPU

| Item | Value | Source |
|---|---|---|
| Model | Intel Core i7-5930K @ 3.50 GHz (Haswell-E) | `lscpu` |
| Cores / threads | 6 / 12, 1 socket | `lscpu` |
| VT-x | **Disabled in BIOS** | `dmesg`: `x86/cpu: VMX (outside TXT) disabled by BIOS`; 0 CPUs with the `vmx` flag |
| `/dev/kvm` | **Does not exist** | `ls /dev/kvm` |
| VT-d | **Inactive**: no active DMAR table, 0 IOMMU groups | `/sys/kernel/iommu_groups` empty |
| Nested virtualization | Not applicable (no KVM) | |

The processor supports VT-x and VT-d (X99, Haswell-E). Both are turned off in the firmware.

## Motherboard

Gigabyte X99-UD4P-CF, BIOS F23c (2018-06-15). Relevant BIOS options: "Intel Virtualization Technology" and "Intel VT-d" (menu *BIOS Features* / *Peripherals* depending on version).

## RAM

31 GiB total, ~28 GiB available at idle. Swap 8 GiB (466 MiB used). A 12 GiB VM would leave about 16 GiB to Ubuntu; 16 GiB would be the reasonable maximum if nothing trains in parallel.

## Storage

| Device | Size | Use |
|---|---|---|
| `sda` Crucial BX500 SATA SSD | 223.6 GB | only physical disk |
| `sda1` vfat | 1 GB | `/boot/efi` |
| `sda3` ext4 | 222.6 GB | `/`: **128 GB used, 81 GB free (62 %)** as of 2026-10-19 |

There is no other disk or external mount. The original audit found 3.8 GB free (99 %). The same day, with the user's authorization, about 81 GB were freed:

| Path | Freed | Condition |
|---|---|---|
| `radar-data/sessions` | ~62 GB | deleted after checking by SHA-256 that the 78 files are identical to those on the Jetson (`/mnt/DataStorage`). Hashes in `~/radar-data/SESSIONS_SHA256_VERIFIED_ON_JETSON.txt`. The Jetson SSD now holds the only copy of the raw recordings. |
| `autopilot-work/cache/v2_230515`, `v2_train`, `all.npz` | ~13 GB | old caches, chosen by the user |
| Docker image and container `gw-eval` | ~3 GB | closed evaluation |
| `~/.cache/huggingface` (`hub`, `xet`, `sam3.1`) | ~5 GB | downloadable models; SAM 3.1 deleted at the user's request |

Remaining: `autopilot-work/prep` 39 GB (derived data, `cache/v3` depends on it), `autopilot-work/cache` 6.9 GB (`v3`, `v4`), `runs` 2.8 GB, Docker `rallia/noetic` 7.4 GB plus 2.9 GB of build cache.

Windows 11 with Assetto Corsa, CSP, the track and the car takes about 50 GB; the virtual disk is sized in `virtualization-decision.md`.

## GPU

| | GPU 0 | GPU 1 |
|---|---|---|
| Model | RTX 3060 (GA106) | RTX 3090 (GA102) |
| PCI | `02:00.0` `[10de:2503]` + audio `02:00.1` `[10de:228e]` | `03:00.0` `[10de:2204]` + audio `03:00.1` `[10de:1aef]` |
| VRAM | 12 GB | 24 GB |
| Driver | nvidia 580.173.02 | nvidia 580.173.02 |
| Current use | none (1 MiB, no processes) | none (1 MiB, no processes) |
| Display | none (headless server; console over `ttyS0`) | none |
| IOMMU group | not available (VT-d off) | not available |

The autopilot project history uses both GPUs for training (the 3090 trains, the 3060 sweeps checkpoints). With VT-d enabled, each GPU exposes its audio function on the same slot; on X99 the CPU PCIe slots usually land in separate groups, but this cannot be confirmed until the real groups are visible.

## Connectivity

| Item | Value |
|---|---|
| SSH | `ssh.service` active, port 22 |
| Main network | USB Wi-Fi `wlx28ee520a55b6`, SSID "Rice Visitor", 168.4.186.58/18 |
| Tailscale | `tailscale0` 100.72.129.18 (user's remote access) |
| Ethernet `eno1` | no cable (no carrier) |
| Firewall | UFW inactive |
| Internet | OK |

A visitor Wi-Fi network does not allow an L2 bridge: a VM would use libvirt NAT, with remote access via Tailscale + an SSH tunnel to SPICE/VNC on `127.0.0.1`. Never exposed to the network.

## Conclusion

Without VT-x there is no KVM. Without KVM, Windows 11 would only run under software emulation (QEMU TCG): dozens of times slower, with no GPU and no way to run Assetto Corsa. No VM option (A or B) is viable until VT-x (and VT-d for passthrough) is enabled in the BIOS, which requires the user and a host reboot. See `virtualization-decision.md`.
