# VM Windows 11 de pruebas (`rice-win11`)

Estado: **operativa** desde 2026-10-20. Decisión y alternativas: `virtualization-decision.md`. Auditoría del host: `hardware-audit.md`.

## Qué es

| | |
|---|---|
| Host | Ubuntu Server (Gigabyte X99-UD4P, i7-5820K), QEMU/KVM + libvirt, OVMF con Secure Boot, TPM 2.0 por `swtpm` |
| VM | Windows 11 Pro (build 26300), **sin activar** (ISO oficial de Microsoft verificada por SHA-256) |
| CPU / RAM / disco | 8 vCPU fijados a los núcleos físicos 2 a 5 con sus hilos; 12 GiB; qcow2 de 60 GB |
| GPU | **RTX 3060 por VFIO**: Windows la ve con el driver NVIDIA 32.0.16.1088, estado OK. La RTX 3090 sigue en el host con el driver `nvidia`. Salida de video: la TV conectada por HDMI a la 3060. |
| Red | NAT de libvirt (`192.168.122.0/24`), sin puertos expuestos fuera del host |
| Acceso | OpenSSH de Windows sólo con clave (`riceadmin`); la contraseña de la cuenta local no se usa por red |
| Toolchain | Python 3.12, `lupa` 2.8 (LuaJIT 2.1), PyInstaller |

Los scripts del host están en `/home/lucas/vm/rice-win11/` y no entran al repo porque contienen la clave SSH y la contraseña local:

- `create-vm.sh`: crea la VM y aborta si la 3060 no está en `vfio-pci`, si la 3090 no está en `nvidia`, si el grupo IOMMU no es exactamente la 3060 con su audio, si el hash de la ISO no coincide o si no queda disco suficiente para el host.
- `vmssh`: corre PowerShell en la VM por SSH.
- `sync-repo`: copia el repo (archivos versionados y no ignorados) a `C:\rr\src`.
- `jobs/*.ps1`: tests, build, E2E, simulador y sesión interactiva (`run-interactive.ps1` usa una tarea programada para correr dentro del escritorio de `riceadmin`, porque el E2E de instalación necesita ese token).

## Qué se validó en la VM

Evidencia de **Windows real**. La carpeta de Assetto Corsa fue falsa y CSP simulado para las
suites, el build y el E2E de instalación; la validación dentro del juego (última línea) es AC y
CSP reales:

- Suites del repo: 88 OK (9 omitidas por herramientas de Linux o del navegador) y 104 del cliente.
- Build con PyInstaller y los tres ZIP (`SHA256SUMS.txt` en `docs/evidence/csp-mock/`).
- E2E de instalación (`tests/windows/e2e-windows.ps1`) en la sesión interactiva: 33 OK. Evidencia en `docs/evidence/windows-vm/e2e-interactive/`.
- Escenarios MOCK de CSP con el servidor en proceso y contra el `RiceTrialsServer.exe` compilado: ver `docs/csp-mock.md` y `docs/evidence/csp-mock/`.
- **Dentro del juego (AC + CSP 0.2.11 reales, pista FSAEJ y auto MFT02 instalados)**: el cliente carga por CSP y llega al servidor (autentica, recibe la asignación ST01/S1/P01 y late con `online:true`). Prueba: `docs/evidence/windows-vm/ingame-validation.md`.

## Qué falta

- Confirmar en pista `io.save`/`io.load`, el HUD, `ac.onCarJumped`/`resetCounter`, el
  cronometraje y la detección de vuelta (requiere manejar de verdad; el scoring sigue desarmado).
- La VM no reemplaza al rig: el volante, los pedales y la validación final se hacen en la
  estación real. La checklist sigue siendo `docs/windows-validation.md`.
