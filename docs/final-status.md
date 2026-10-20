# Estado final de la entrega

**BLOQUEADO para trials oficiales.** Nada se ejecutó dentro de Assetto Corsa con CSP en Windows. El servidor de producción arranca desarmado (`scoring_armed=False`): ninguna vuelta llega a `VALID` ni entra al ranking hasta completar la validación en Windows descrita abajo. No publicar los ZIP de release antes de esa validación.

## Hecho y probado en Linux

**Cliente CSP (`client/`)**
- `RiceTrials.lua`: autostart desde `station_config.lua`, asignación autenticada con revisión, máquina de outlap, detección de reset, teleport y reinicio de sesión, y payload con `lap_number`, `rules_version`, `calibration_version` y `client_timestamp`. Las vueltas salen siempre como `PENDING_REVIEW`.
- `journal.lua`: cola persistente en dos archivos alternados con número de secuencia, checksum FNV-1a y relectura. Si ambos están corruptos bloquea el envío y deja una copia en cuarentena. Migra la cola vieja de `ac.storage`. Sin `io`, vuelve a `ac.storage` y lo muestra como `degraded`.
- `hud.lua`: piloto, fase, tiempo, penalidades («sin evidencia» cuando falta), sincronización y estado de la cola. Si falla, la ventana cae a texto plano.
- `diagnostics.lua`: registro JSONL opcional (`diagnostics = true`), con un máximo de 2 MB por archivo y dos archivos.
- `scoring.lua`, `server/scoring.py` y `rules/scoring-v1.json`: una sola definición de penalidades, con paridad probada.

**Servidor (`server/`, `leaderboard/`)**
- HTTP con la biblioteca estándar y SQLite (WAL, migraciones versionadas), historial de asignaciones, sesión activa, errores de sincronización, revisión de penalidades con auditoría, export JSON y CSV, backup, `--check` y `--restore`.
- `/tv` (leaderboard 1080p con auto-refresco) y `/admin` (flujo completo sin comandos), probados con Playwright.

**Pista (`track/`, `timing/`)**
- Lectura de KN5, extracción de geometría y dummies, vistas 2D (`docs/evidence/track/`), exportación de rutas y análisis de telemetría.
- Parche candidato de `Dummy.kn5` para FSAEJ Endurance: mueve `AC_START_0` 8 m después de la línea de tiempo para que la salida de pits no genere una vuelta falsa de unos 18 s. Se genera desde el ZIP original sin modificarlo; `Apply-DummyPatch.ps1` aplica, revierte y verifica hashes. **Candidato no verificado en juego.**

**Windows (`packaging/`, `.github/workflows/`)**
- Instalador de estación con preflight de sólo lectura, verificación SHA-256 de los archivos de terceros, token pedido con `Read-Host -AsSecureString`, ACL restrictivas y rollback. Desinstalador, servidor portable con secretos en DPAPI, script de administración, kit de validación (`tools/validation.py`) y workflow de GitHub Actions de disparo manual que no publica releases.
- **Probado en Windows real** (runner de GitHub Actions, Windows PowerShell 5.1, run `39334554771`, 32/32 pasos del E2E): instalador, ACL, rollback, desinstalador, DPAPI por cuenta, servidor PyInstaller lanzado por `start-server.ps1` (API completa, backup, restore, reinicio, red local, firewall) y tarea programada con cuenta dedicada, incluida la recuperación ante una caída del exe. La carpeta de AC del runner es falsa: esto no prueba nada dentro del juego.
- Esa validación encontró y corrigió cuatro bugs que habrían fallado en el evento: `start-server.ps1` no arrancaba con `-File` (`$PSScriptRoot` vacío en `param()`), `-Action Backup` mandaba un POST sin cuerpo (400), la tarea no podía iniciar sesión con la cuenta dedicada (faltaba `SeBatchLogonRight`) y nadie relanzaba el exe si se caía (ahora lo supervisa `start-server.ps1`).
- `server-admin.ps1 -Action Stop` detiene tarea, supervisor y exe. Matar sólo el exe ya no detiene el servidor.

**Paquetes (`dist/`, generados con `packaging/build-linux.py`)**: `RiceRacing_Trials_Client.zip`, `RiceRacing_Trials_Server.zip`, `RiceRacing_Trials_Complete.zip` y `RiceRacing_Track_Calibration.zip`. Contienen sólo código propio y documentación: ningún KN5, ACD, auto, pista, credencial ni base de datos.

Pruebas: ver `docs/test-report.md` (88 + 101, todas OK).

## Qué falta, en orden

1. **Reinicio real del servidor del evento** con la tarea programada (lo único del lado Windows que el CI no puede probar).
2. **En una estación con AC y CSP**: confirmar que la app carga, que `io.save` e `io.load` escriben en `apps/lua/RiceTrials/state/`, que `web.post` llega al servidor, que el HUD se ve y que `ac.onCarJumped` y `resetCounter` disparan como se espera. El kit de validación y `docs/windows-validation.md` guían cada paso.
3. **Probar el parche de `Dummy.kn5` en juego**: salir de pits y verificar que no aparece la vuelta de 18 s y que la primera vuelta completa se cronometra bien. Si falla, revertir con `Apply-DummyPatch.ps1 -Revert`.
4. **Calibrar la ruta** (gates y checkpoints medidos en la pista real) y firmar la checklist de `docs/windows-validation.md` en cada estación.
5. Recién entonces armar el scoring (`scoring_armed=True`), con el protocolo deportivo aprobado. Los conos individuales no tienen detección automática porque las mallas del KN5 de FSAEJ están fusionadas; esas penalidades se cargan por revisión en `/admin`.

El código está en el repositorio privado `LucasErcolano/rice-racing-trials`. No se publicó ningún release; los ZIP compilados quedan sólo como artifacts de los runs manuales. Plan para probar el cliente en juego sin el rig: `docs/windows-vm/virtualization-decision.md` (bloqueado hasta activar VT-x y VT-d en el BIOS).
