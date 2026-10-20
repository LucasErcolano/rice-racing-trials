# Auditoría de hardware del host (solo lectura)

Fecha: 2026-10-19. Host: servidor Ubuntu de Lucas, kernel 6.8.0-139-generic. Ningún comando de esta auditoría modificó el sistema.

## CPU

| Dato | Valor | Fuente |
|---|---|---|
| Modelo | Intel Core i7-5930K @ 3.50 GHz (Haswell-E) | `lscpu` |
| Núcleos / hilos | 6 / 12, 1 socket | `lscpu` |
| VT-x | **Desactivado en BIOS** | `dmesg`: `x86/cpu: VMX (outside TXT) disabled by BIOS`; 0 CPUs con flag `vmx` |
| `/dev/kvm` | **No existe** | `ls /dev/kvm` |
| VT-d | **Inactivo**: sin tabla DMAR activa, 0 grupos IOMMU | `/sys/kernel/iommu_groups` vacío |
| Virtualización anidada | No aplica (no hay KVM) | |

El procesador soporta VT-x y VT-d (X99, Haswell-E). Los dos están apagados en el firmware.

## Placa base

Gigabyte X99-UD4P-CF, BIOS F23c (2018-06-15). Opciones relevantes del BIOS: «Intel Virtualization Technology» y «Intel VT-d» (menú *BIOS Features* / *Peripherals* según versión).

## RAM

31 GiB totales, ~28 GiB disponibles en reposo. Swap 8 GiB (466 MiB usados). Una VM de 12 GiB dejaría unos 16 GiB a Ubuntu; 16 GiB sería el máximo razonable si no entrena nada en paralelo.

## Almacenamiento

| Dispositivo | Tamaño | Uso |
|---|---|---|
| `sda` Crucial BX500 SATA SSD | 223.6 GB | único disco físico |
| `sda1` vfat | 1 GB | `/boot/efi` |
| `sda3` ext4 | 222.6 GB | `/`: **128 GB usados, 81 GB libres (62 %)** al 2026-10-19 |

No hay otro disco ni montaje externo. La auditoría original encontró 3.8 GB libres (99 %). Ese mismo día, con autorización del usuario, se liberaron unos 81 GB:

| Ruta | Liberado | Condición |
|---|---|---|
| `radar-data/sessions` | ~62 GB | borrado después de comprobar por SHA-256 que los 78 archivos son idénticos a los de la Jetson (`/mnt/DataStorage`). Hashes en `~/radar-data/SESSIONS_SHA256_VERIFIED_ON_JETSON.txt`. El SSD de la Jetson tiene ahora la única copia de las grabaciones crudas. |
| `autopilot-work/cache/v2_230515`, `v2_train`, `all.npz` | ~13 GB | caches viejas, elegidas por el usuario |
| Imagen y contenedor Docker `gw-eval` | ~3 GB | evaluación cerrada |
| `~/.cache/huggingface` (`hub`, `xet`, `sam3.1`) | ~5 GB | modelos descargables; SAM 3.1 borrado a pedido del usuario |

Quedan: `autopilot-work/prep` 39 GB (derivados, `cache/v3` depende de ellos), `autopilot-work/cache` 6.9 GB (`v3`, `v4`), `runs` 2.8 GB, Docker `rallia/noetic` 7.4 GB más 2.9 GB de build cache.

Windows 11 con Assetto Corsa, CSP, la pista y el auto ocupa unos 50 GB; el disco virtual se dimensiona en `virtualization-decision.md`.

## GPU

| | GPU 0 | GPU 1 |
|---|---|---|
| Modelo | RTX 3060 (GA106) | RTX 3090 (GA102) |
| PCI | `02:00.0` `[10de:2503]` + audio `02:00.1` `[10de:228e]` | `03:00.0` `[10de:2204]` + audio `03:00.1` `[10de:1aef]` |
| VRAM | 12 GB | 24 GB |
| Driver | nvidia 580.173.02 | nvidia 580.173.02 |
| Uso actual | ninguno (1 MiB, sin procesos) | ninguno (1 MiB, sin procesos) |
| Display | ninguno (servidor sin monitor; consola por `ttyS0`) | ninguno |
| Grupo IOMMU | no disponible (VT-d apagado) | no disponible |

El historial del proyecto autopilot usa las dos GPU para entrenar (la 3090 entrena, la 3060 barre checkpoints). Con VT-d activo, cada GPU expone su función de audio en el mismo slot; en X99 los slots PCIe del CPU suelen quedar en grupos separados, pero no se puede confirmar hasta ver los grupos reales.

## Conectividad

| Dato | Valor |
|---|---|
| SSH | `ssh.service` activo, puerto 22 |
| Red principal | Wi-Fi USB `wlx28ee520a55b6`, SSID «Rice Visitor», 168.4.186.58/18 |
| Tailscale | `tailscale0` 100.72.129.18 (acceso remoto del usuario) |
| Ethernet `eno1` | sin cable (no carrier) |
| Firewall | UFW inactivo |
| Internet | OK |

Una red de visitantes por Wi-Fi no permite bridge L2: una VM usaría NAT de libvirt, con acceso remoto por Tailscale + túnel SSH hacia SPICE/VNC en `127.0.0.1`. Nunca expuesto a la red.

## Conclusión

Sin VT-x no hay KVM. Sin KVM, Windows 11 solo correría emulado por software (QEMU TCG): decenas de veces más lento, sin GPU y sin posibilidad de correr Assetto Corsa. Ninguna opción de VM (A o B) es viable hasta habilitar VT-x (y VT-d para passthrough) en el BIOS, lo que requiere al usuario y un reinicio del host. Ver `virtualization-decision.md`.
