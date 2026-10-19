# Informe de pruebas (Linux)

Fecha de la última corrida: 2026-10-19.

## Cómo correrlas

Requiere un venv con `lupa` (Lua real dentro de Python) y, para las pruebas de navegador, `playwright` con Chromium:

```sh
V=<venv>/bin/python
$V -m unittest discover -s tests -p '*test*.py'              # servidor, integración, empaquetado, pista, navegador
(cd client/tests && $V -m unittest discover -p 'test_*.py')  # cliente Lua
```

Con el `python3` del sistema (sin `lupa` ni Playwright) las pruebas que los necesitan se saltean o fallan al importar; esa corrida no cuenta como válida.

## Resultado

| Suite | Pruebas | Resultado |
|---|---|---|
| `tests/`: servidor, E2E Lua + HTTP + SQLite, empaquetado, kit de validación, herramienta de pista, paridad de scoring, navegador | 88 | OK |
| `client/tests/`: Lua real vía lupa (LuaJIT 2.1 y Lua 5.4) | 101 | OK |
| Scripts PowerShell (`packaging/*.ps1`, `track/patches/Apply-DummyPatch.ps1`), parseo con pwsh 7 en Linux | 7 archivos | 0 errores de sintaxis |

Las 5 pruebas de navegador usan Playwright con Chromium contra el servidor HTTP real y dejan capturas en `docs/evidence/screenshots/`: ranking 1080p con 16 pilotos sin scroll ni celdas cortadas, auto-refresco, cambio de sesión, servidor caído, banner de no comisionado y flujo completo de `/admin`.

## Qué cubren

- **Scoring:** `rules/scoring-v1.json` evaluado con `server/scoring.py` y `client/scoring.lua`; los mismos casos dan el mismo resultado en Python y en Lua.
- **Cliente:** máquina de outlap (la salida 19.3 m antes de la línea y la primera vuelta completa no se cronometran); reset, teleport y reinicio de sesión (`ac.onCarJumped`, `resetCounter`, `ac.onSessionStart`, salto de posición y ausencia de esas APIs); cola persistente en archivos A/B con checksum y relectura (corte antes de escribir, escritura truncada, relectura distinta, ambas ranuras corruptas con cuarentena y bloqueo de envío, migración desde `ac.storage`); HUD (la falta de evidencia nunca se muestra como 0, y cae a texto plano si el HUD falla); diagnóstico JSONL acotado y apagado por defecto.
- **Servidor:** autenticación (401/403), revisiones de asignación para vueltas enviadas offline, servidor desarmado que no rankea, migraciones de esquema, sesión activa, revisión de penalidades con antes/después, export CSV sin inyección de fórmulas, backup y restore con chequeo de integridad.
- **E2E:** VM Lua real contra HTTP real y SQLite: asignación, heartbeat, vuelta pendiente, respuesta perdida tras commit, reinicio de VM, reenvío byte a byte, idempotencia y reasignación intermedia.
- **Empaquetado y pista:** los ZIP no contienen credenciales, bases de datos ni archivos de terceros (KN5, ACD, auto). El parche candidato de `Dummy.kn5` se genera desde el ZIP original sin modificarlo, cambia sólo la matriz de `AC_START_0` y el resultado se vuelve a leer sin errores.

## No ejecutado

Assetto Corsa y CSP en Windows (las APIs `io`, `web` y `ac.*` sólo se probaron con dobles), el parche de `Dummy.kn5` dentro del juego, detección física de conos y bordes, cronometraje real, comportamiento de `io.save` en Windows y ante cortes de energía, PowerShell, DPAPI y PyInstaller en ejecución real, tarea programada tras un reinicio, varias estaciones en red y un evento completo. Las pruebas de Linux verifican contratos y comportamiento emulado; no certifican nada deportivo. El sistema sigue bloqueado para trials oficiales.
