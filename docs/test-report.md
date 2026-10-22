# Informe de pruebas (Linux)

Fecha de la última corrida V2 de campo: 2026-10-21.

## V2 de campo

Última ejecución Linux: 149 tests de servidor, integración, navegador y empaquetado (OK; 4 omitidos por prerrequisitos opcionales), 121 tests de cliente Lua (OK) y 25 escenarios MOCK (24 OK; 1 omitido por falta de geometría local).

El smoke OR usa procesos y HTTP reales: autenticación, ambos órdenes de llegada, duplicados, conflicto, reinicio, persistencia y exclusión del ranking oficial. Chromium prueba el monitor V2, su auto-refresco, export y revisión administrativa. La evidencia Windows se genera en el workflow del paquete, incluyendo este mismo smoke contra el exe compilado.

`Dummy.kn5` está validado en juego por confirmación del usuario. Estas suites no implican validación en juego del protocolo V2 completo.

El resto de este informe conserva el detalle y los resultados históricos de V1.

## Cómo correrlas

Requiere un venv con `lupa` (Lua real dentro de Python) y, para las pruebas de navegador, `playwright` con Chromium:

```sh
V=<venv>/bin/python
$V -m unittest discover -s tests -p '*test*.py'              # servidor, integración, empaquetado, pista, navegador
(cd client/tests && $V -m unittest discover -p 'test_*.py')  # cliente Lua
$V sim/run_evidence.py --out <carpeta>                       # escenarios MOCK de CSP
```

Con el `python3` del sistema (sin `lupa` ni Playwright) las pruebas que los necesitan se saltean o fallan al importar; esa corrida no cuenta como válida.

## Resultado

| Suite | Pruebas | Resultado |
|---|---|---|
| `tests/`: servidor, E2E Lua + HTTP + SQLite, empaquetado, kit de validación, herramienta de pista, paridad de scoring, navegador | 88 | OK |
| `client/tests/`: Lua real vía lupa (LuaJIT 2.1 y Lua 5.4) | 104 | OK |
| `sim/`: escenarios en el entorno **MOCK** de CSP (cliente sin modificar, LuaJIT 2.1, servidor real por HTTP); ver `docs/csp-mock.md` | 25 (24 sin la geometría FSAEJ) | OK |
| Scripts PowerShell (`packaging/*.ps1`, `track/patches/Apply-DummyPatch.ps1`), parseo con pwsh 7 en Linux | 7 archivos | 0 errores de sintaxis |

Las 5 pruebas de navegador usan Playwright con Chromium contra el servidor HTTP real y dejan capturas en `docs/evidence/screenshots/`: ranking 1080p con 16 pilotos sin scroll ni celdas cortadas, auto-refresco, cambio de sesión, servidor caído, banner de no comisionado y flujo completo de `/admin`.

## Qué cubren

- **Scoring:** `rules/scoring-v1.json` evaluado con `server/scoring.py` y `client/scoring.lua`; los mismos casos dan el mismo resultado en Python y en Lua.
- **Cliente:** máquina de outlap (la salida 19.3 m antes de la línea y la primera vuelta completa no se cronometran); reset, teleport y reinicio de sesión (`ac.onCarJumped`, `resetCounter`, `ac.onSessionStart`, salto de posición y ausencia de esas APIs); cola persistente en archivos A/B con checksum y relectura (corte antes de escribir, escritura truncada, relectura distinta, ambas ranuras corruptas con cuarentena y bloqueo de envío, migración desde `ac.storage`); HUD (la falta de evidencia nunca se muestra como 0, y cae a texto plano si el HUD falla); diagnóstico JSONL acotado y apagado por defecto.
- **Servidor:** autenticación (401/403), revisiones de asignación para vueltas enviadas offline, servidor desarmado que no rankea, migraciones de esquema, sesión activa, revisión de penalidades con antes/después, export CSV sin inyección de fórmulas, backup y restore con chequeo de integridad.
- **E2E:** VM Lua real contra HTTP real y SQLite: asignación, heartbeat, vuelta pendiente, respuesta perdida tras commit, reinicio de VM, reenvío byte a byte, idempotencia y reasignación intermedia.
- **Empaquetado y pista:** los ZIP no contienen credenciales, bases de datos ni archivos de terceros (KN5, ACD, auto). El parche candidato de `Dummy.kn5` se genera desde el ZIP original sin modificarlo, cambia sólo la matriz de `AC_START_0` y el resultado se vuelve a leer sin errores.

## Entorno MOCK de CSP (`sim/`)

El cliente de producción corre sin cambios sobre una simulación de la superficie de CSP (`ac`, `io`, `web`, `ui`, cdata del auto y de la sesión), con disco y red reales con fallas inyectadas y un piloto sintético que da el tiempo verdadero de cada vuelta. Encontró cinco bugs reales del cliente, ya corregidos (lista en `docs/csp-mock.md`). Evidencia en `docs/evidence/csp-mock/`: Linux, Windows 11 con el servidor en proceso y Windows 11 contra el `RiceTrialsServer.exe` compilado. **Es evidencia sobre un mock, no validación de CSP.**

## Windows (GitHub Actions)

`.github/workflows/windows-package.yml` corre en un runner Windows real: las dos suites de arriba, el parseo de todos los `.ps1`, la compilación con PyInstaller, la inspección de los ZIP, un smoke test del exe y `tests/windows/e2e-windows.ps1` contra el paquete compilado. Run `39334554771` (commit `ddeb289`): todo verde, 32/32 pasos del E2E. Detalle en `docs/windows-validation.md`. Run `39400713102` (commit `4efcb7f`): todo verde, ahora con los 24 escenarios MOCK de CSP (sin la geometría FSAEJ, que no se distribuye) contra `server/app.py` y contra el exe compilado.

## No ejecutado

V2 completa dentro de Assetto Corsa/CSP en Windows, detección física de conos y bordes, cronometraje real, comportamiento de `io.save` ante cortes de energía, tarea programada tras un reinicio real, varias estaciones en red y un evento completo. El parche `Dummy.kn5` sí fue validado en juego por el usuario. Las pruebas de Linux verifican contratos y comportamiento emulado; no certifican nada deportivo. Esto no bloquea la pre-release para pruebas de campo; los resultados provisionales siguen fuera del ranking oficial.
