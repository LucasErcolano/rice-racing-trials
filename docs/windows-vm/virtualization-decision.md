# Decisión de virtualización (Windows para Assetto Corsa)

Fecha: 2026-10-19. Base: `hardware-audit.md`. Estado: **decidido, no ejecutado**. Bloqueado hasta activar VT-x y VT-d en el BIOS.

## Para qué

Probar en Windows lo que GitHub Actions no puede: Assetto Corsa con CSP, la app Lua `RiceTrials`, el parche de `Dummy.kn5`, la pista FSAEJ y el MFT02. Instalador, DPAPI, servidor congelado, `server-admin.ps1` y tarea programada ya pasan en un runner Windows real (run `39334554771`, 32/32; ver `docs/windows-validation.md`).

## Opciones

| | A. VM sin GPU (QXL/virtio-gpu) | B. VM KVM con la RTX 3060 por VFIO | C. PC Windows física |
|---|---|---|---|
| Requiere | VT-x | VT-x y VT-d | otra máquina con AC |
| Corre AC (DirectX 11) | no en la práctica: render por software | sí, con driver NVIDIA nativo | sí |
| Efecto en el host | ninguno | la 3060 deja de estar disponible para Linux mientras esté asignada a vfio | ninguno |
| Uso | sólo instalador y servidor (ya cubiertos por CI) | validación de cliente en juego | validación final en el rig |

**Decisión: B.** La RTX 3060 (`02:00.0` `10de:2503` y su audio `02:00.1` `10de:228e`) pasa a la VM. La RTX 3090 (`03:00.0`) queda con el driver `nvidia` para entrenar. C sigue siendo obligatoria antes de los trials oficiales: una VM no reemplaza el volante, los pedales ni el rig.

## Configuración prevista

- **Hipervisor:** QEMU/KVM con libvirt, firmware OVMF (UEFI con Secure Boot) y `swtpm` (TPM 2.0 para Windows 11). Hoy no está instalado nada de esto (`virsh` no existe).
- **CPU:** 8 vCPU (4 núcleos con sus hilos), fijados a núcleos físicos; quedan 2 núcleos y 4 hilos al host.
- **RAM:** 12 GiB (quedan ~16 GiB al host). 16 GiB sólo si no se entrena a la vez.
- **Disco:** qcow2 dinámico de **70 GB virtuales** en `/var/lib/libvirt/images`. Con 81 GB libres, aun lleno deja ~11 GB al host. No usar 120 GB: un qcow2 que crece hasta llenar `/` rompe el host.
- **Red:** NAT de libvirt. La red Wi-Fi de visitantes no permite bridge L2. Ningún puerto de la VM se expone a la red.
- **Acceso remoto:** por Tailscale y túnel SSH a `127.0.0.1`. Con la GPU asignada, la imagen sale por la 3060 y no por SPICE, así que hace falta una salida de video:
  - un **enchufe dummy HDMI/DP** en la 3060 (recomendado, unos pocos dólares), o
  - un driver de pantalla virtual en Windows,
  - más Sunshine/Moonlight o Parsec sobre el túnel para ver y manejar.
- **Licencias:** Windows, Steam/Assetto Corsa y CSP las aporta el usuario. Los assets JSAE y MFT02 se copian desde `assets/source/` (nunca entran a git ni a los ZIP).

## Pasos después del BIOS (en orden, con verificación)

1. **BIOS** (usuario, requiere reinicio): activar «Intel Virtualization Technology» y «Intel VT-d».
2. **Verificar:** `grep -c vmx /proc/cpuinfo` > 0, `/dev/kvm` existe, `dmesg | grep -i dmar` muestra la tabla DMAR.
3. **Kernel:** agregar `intel_iommu=on iommu=pt` a `GRUB_CMDLINE_LINUX_DEFAULT`, `update-grub`, reiniciar. Revisar `/sys/kernel/iommu_groups`: `02:00.0` y `02:00.1` deben estar solos en su grupo. Si comparten grupo con otro dispositivo, frenar y reevaluar (no usar el parche ACS override sin decidirlo explícitamente).
4. **vfio:** `options vfio-pci ids=10de:2503,10de:228e` y `softdep nvidia pre: vfio-pci` en `/etc/modprobe.d/vfio.conf`, `update-initramfs -u`, reiniciar. Verificar con `lspci -nnk -s 02:00` que el driver en uso es `vfio-pci` y con `nvidia-smi` que la 3090 sigue disponible. Los IDs de la 3060 son distintos de los de la 3090, así que el binding por ID no afecta a la 3090.
5. **Paquetes:** `qemu-kvm libvirt-daemon-system virtinst ovmf swtpm`.
6. **VM:** crear con `virt-install` (UEFI, TPM, disco virtio de 70 GB, 12 GiB, 8 vCPU, hostdev `02:00.0` y `02:00.1`). Instalar Windows 11, driver NVIDIA, Steam, AC, CSP y luego el ZIP de cliente con `install.ps1 -Preflight` y después sin `-Preflight`.
7. **Validación en juego:** seguir `docs/windows-validation.md` y `docs/csp-api-validation.md`.

## Reversión

Sacar el archivo de `/etc/modprobe.d/vfio.conf`, `update-initramfs -u` y reiniciar devuelve la 3060 al driver `nvidia`. Los parámetros `intel_iommu=on iommu=pt` pueden quedar. Desactivar VT-x/VT-d en el BIOS vuelve al estado auditado.

## Riesgos

- Mientras la 3060 esté en vfio, el barrido de checkpoints del autopilot que la usa no puede correr; entrenar en la 3090 sigue igual.
- El host no tiene monitor: si algo sale mal en el arranque, la consola sigue disponible por `ttyS0`.
- El rendimiento de AC en la VM no representa el del rig; sirve para validar comportamiento, no para medir FPS ni latencia del volante.
