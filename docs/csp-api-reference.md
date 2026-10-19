# Referencia de APIs CSP usadas (verificada en documentación, no en runtime)

Fuentes consultadas el 2026-10-19:
- Definiciones generadas del SDK (`dabinn/ac-lua-sdk-api`, `ac_apps/README.md`, derivado de `acc-lua-sdk`).
- Documentación de `ac.StateCar` (`lint069.github.io/csp-lua-api-docs/ac/objects/state-car`).
- Uso real en `ac-custom-shaders-patch/acc-lua-internal` (`lua-module/src/hotlap.lua`: patrón `io.save` a `.tmp` + `io.move`).

**Estado:** firmas confirmadas en documentación. Ninguna se ejecutó dentro de Assetto Corsa. La versión mínima de CSP que expone cada campo no está confirmada; el cliente debe comprobar existencia (`type(x) == 'function'` / campo no nil) y degradar a fail-closed.

## Archivos (`io`)
| Función | Firma | Retorno | Uso en Rice Trials |
|---|---|---|---|
| `io.save(filename, data)` | string, string | `boolean` | escribir journal temporal |
| `io.move(existing, new)` | string, string | `boolean` | rename del temporal al definitivo |
| `io.load(filename, fallback)` | string, string? | `string\|nil` | leer journal |
| `io.fileExists(filename)` | string | `boolean` | recuperación |
| `io.createDir(path)` | string | `boolean` (crea padres) | carpeta de estado |
| `io.deleteFile(filename)` | string | `boolean` | limpiar temporales |
| `io.scanDir(dir, mask, cb)` | | | no usado |
| `ac.getFolder(ac.FolderID.X)` | `ac.FolderID` | `string` | raíz de datos; `ac.FolderID.Root` confirmado; otros IDs no confirmados en esta fuente |

No hay `fsync` documentado. `io.move` sobre un destino existente: comportamiento no documentado (en Windows `MoveFileEx` sin `REPLACE_EXISTING` falla). Por eso el journal usa **dos ranuras alternadas** (A/B) con número de secuencia y checksum, y nunca depende de reemplazar un archivo existente.

## Red (`web`)
`web.get(url, headers?, callback)` y `web.post(url, headers?, data, callback)`; callback `fun(err: string, response: WebResponse)`; `response.status`, `response.body`. Máximo **dos solicitudes simultáneas** por script (documentado). Asíncrono: no bloquea el frame.

## Estado del auto (`ac.getCar(index)` → `ac.StateCar`)
| Campo | Tipo | Descripción documentada |
|---|---|---|
| `position`, `look`, `velocity` | vec3 | posición, frente normalizado, velocidad m/s |
| `speedKmh` | number | |
| `lapTimeMs` | integer | tiempo de la vuelta actual |
| `previousLapTimeMs` / `bestLapTimeMs` | integer | última / mejor de la sesión |
| `lapCount` | integer | vueltas completas, incluye spoiled |
| `sessionLapCount` | integer | vueltas de la sesión actual |
| `isLapValid` / `isLastLapValid` | boolean | validez nativa |
| `lastLapCutsCount` | integer | cortes en la última vuelta |
| `currentSplits` | integer[] | splits de la vuelta actual |
| `splinePosition` | number | 0 línea de salida, 1 llegada |
| `wheelsOutside` | integer | ruedas fuera del área permitida |
| `collidedWith` | integer | 0 pista, distinto de 0 autos |
| `collisionPosition` | vec3 | punto de colisión en espacio del auto |
| `collisionDepth` | number | profundidad de colisión actual (m) |
| `resetCounter` | integer | +1 por cada reset |
| `isInPit` / `isInPitlane` | boolean | |

`wheelsOutside` depende de las superficies `IS_VALID_TRACK` del mod; **no es** prueba de off-course según reglamento FSAE hasta calibrarlo. `collidedWith` + `collisionDepth` es una señal de contacto, **no** identifica un cono (las mallas de conos del KN5 están fusionadas).

## Eventos
- `ac.onCarJumped(carIndex, fun(carIndex))`: teleport/reset del auto.
- `ac.onSessionStart(fun(sessionIndex, restarted))`: nueva sesión o restart.
- `ac.getSim()` → `ac.StateSim` (barato).
- `ac.getPatchVersion()` → string; `ac.getPatchVersionCode()` → integer.
- `ac.getTrackFullID(sep?)`, `ac.getTrackID()`, `ac.getCarID(index)`.

## Best Lap nativo
- `ac.markLapAsSpoiled()`: «Marks current lap done by player as spoiled. **Works only during the race**».
- `physics.markLapAsSpoiled(carIndex)`: misma restricción, contexto de física.
La documentación dice que sólo funciona en carrera, por lo que **no se asume** que invalide el outlap en Practice/Hotlap. Queda como experimento del Windows Validation Kit (paso medido, no corrección declarada).

## UI
`ui.transparentWindow(id, pos, size, noPadding, content)`, `ui.toolWindow(...)`, `ui.pushDWriteFont(name)`, `ui.dwriteText(text, size, color)`, `ui.dwriteDrawText(text, size, pos, color)`, `ui.drawRectFilled(p1, p2, color, rounding)`, `ui.textColored(text, color)`, `ui.windowSize()`, `ui.setCursor(v)`, `ac.setWindowOpen(windowID, opened)`. El scoring corre en `script.update`, independiente de que la ventana del HUD esté abierta.
