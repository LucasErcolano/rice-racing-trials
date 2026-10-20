# Entorno MOCK de Assetto Corsa / CSP (`sim/`)

**Esto no es validación de CSP.** Pasar esta suite prueba que el código de producción se comporta bien ante el comportamiento que *suponemos* de CSP. Lo que CSP hace de verdad sólo se confirma dentro del juego (ver `docs/windows-validation.md`).

## Qué es real y qué es simulado

| Pieza | Real o simulada |
|---|---|
| `client/*.lua` (`RiceTrials`, `core`, `journal`, `hud`, `diagnostics`, `scoring`) | **Real, sin modificar.** Se instalan en un `apps/lua/RiceTrials/` en disco igual que lo hace `install.ps1` (incluido un `station_config.lua` generado) y se cargan por `require` desde esa carpeta. |
| Máquina virtual Lua | **Real:** LuaJIT 2.1 (`lupa.luajit21`), la misma familia que embebe CSP. |
| JSON | **Real:** `JSON.parse`/`JSON.stringify` copiados tal cual del SDK oficial (`sim/vendor/csp_json.lua`, licencia MIT en `sim/vendor/acc-lua-sdk.LICENSE`), incluida la semántica de tabla vacía. |
| Servidor | **Real:** `server/app.py` sobre HTTP real (en proceso, o el exe de PyInstaller). |
| Disco | **Real:** archivos y carpetas del sistema operativo anfitrión, con fallas inyectadas (escritura cortada a la mitad, disco lleno, error de E/S). |
| Superficie `ac`, `io`, `web`, `ui`, `script` (`sim/csp_prelude.lua`) | **Simulada.** Firmas tomadas de `acc-lua-sdk` y `acc-lua-internal`. |
| Estructuras del auto y de la sesión | **Simuladas** como cdata FFI: leer un campo que no existe da error, como en CSP. Hay un perfil "moderno" y uno "legacy" sin `resetCounter` ni `collisionDepth`. |
| Momento en que llegan las respuestas de `web.*` | **Simulado:** cola por frames, latencia configurable, servidor caído, respuesta perdida, 500, respuesta basura, límite de pedidos simultáneos. |
| Cuándo CSP llama a `script.update` en pausa, replay, menú y reinicio de sesión | **Simulado**, y es la principal suposición: ver la tabla de abajo. |
| Física del auto | **Simulada:** un piloto cinemático sigue la línea AI (sintética, o la `fast_lane` real de FSAEJ si se pasa `RICE_FSAEJ_GEOMETRY`), integrado a 1 kHz para calcular el tiempo verdadero de cada vuelta. |

## Suposiciones sobre CSP que hay que confirmar en el juego

| Suposición | Qué hace el cliente si es falsa |
|---|---|
| `ac.getSim()` expone `isReplayActive`, `isPaused`, `isInMainMenu` | Lee los campos de forma segura: si faltan no bloquea, pero tampoco protege contra el replay. Confirmar en el juego. |
| Durante la pausa `script.update` puede recibir `dt > 0` | El cliente descarta la vuelta en curso al ver `isPaused` y exige un outlap nuevo, así que el resultado es correcto con `dt = 0` o con `dt > 0`. |
| `car.resetCounter` existe en la versión de CSP fijada | Si falta, se usa la detección por salto de posición y `ac.onCarJumped`. |
| `web.post` entrega `response.status` numérico y `err` en fallas de transporte | Si una falla no deja ni `err` ni un `status` 0/5xx, la asignación se borra (más restrictivo). |
| `io.save` + relectura detecta escrituras incompletas | El journal A/B con checksum lo cubre; el comportamiento real de Windows ante un corte de energía sigue sin probar. |

## Escenarios (`sim/test_sim_scenarios.py`)

Vueltas limpias contra el tiempo verdadero (error menor a 3 ms con 30, 60, 144 fps y con frames irregulares); replay, menú y pausa (con `dt` en cero o avanzando); resets por `resetCounter`, por `ac.onCarJumped` y teleport sin `resetCounter`; reinicio de sesión; reversa y atajos; red caída y recuperación sin pérdidas ni duplicados; respuesta perdida reenviada idéntica; 500 y captive portal que no cuentan como confirmación; pedidos simultáneos acotados; token incorrecto; reinicio del juego con cola offline; escritura cortada; disco lleno; journal corrupto con bloqueo y diagnóstico correcto; APIs opcionales ausentes; sin `io`; JSONL de diagnóstico válido; vueltas sobre la línea AI real de FSAEJ.

## Cómo correrlo

```sh
python -m pip install lupa==2.8
python sim/run_evidence.py --out <carpeta>                    # 24 escenarios + 1 omitido
RICE_FSAEJ_GEOMETRY=<geometry.json> python sim/run_evidence.py --out <carpeta>   # 25
```

La geometría de FSAEJ se genera con `track/tool.py` desde el ZIP del mod y **no se distribuye** (derivada de assets de terceros). `run_evidence.py` escribe `summary.json` (commit, hashes SHA-256 del cliente y del servidor, versión de LuaJIT, resultado de cada test) y `unittest.log`. La evidencia guardada está en `docs/evidence/csp-mock/`.

## Bugs reales que encontró este entorno (corregidos)

1. `RiceTrials.lua` leía `car.resetCounter` directamente: en un CSP sin ese campo, la lectura da error en cada frame y el cronometraje se apaga del todo. Ahora se lee con `pcall`.
2. El cliente no miraba `isReplayActive`, `isPaused` ni `isInMainMenu`: un replay generaba vueltas falsas y una pausa con `dt > 0` inflaba el tiempo (43,5 s contra 23,5 s). Ahora descarta la vuelta en curso y exige un outlap nuevo.
3. El cruce de la línea se tomaba al final del frame: hasta 33 ms de error por cruce a 30 fps. Ahora se interpola dentro del frame (`core.crossed` devuelve la fracción); el error medido baja a menos de 3 ms.
4. Una caída breve de la red borraba la asignación autenticada y las vueltas siguientes no se registraban. Ahora sólo una falla de transporte (sin respuesta o 5xx) conserva la última asignación, como máximo 600 s de juego. Esas vueltas llevan `assignment_revision` y el servidor las deja `PENDING_REVIEW`. Cualquier respuesta del servidor que no sea una asignación válida la borra.
5. Con la cola corrupta, el estado mostraba `STATION_CONFIG_INVALID` y tapaba `QUEUE_CORRUPT`. Ahora queda visible la causa real.
