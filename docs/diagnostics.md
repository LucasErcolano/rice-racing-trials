# Diagnóstico del cliente (`client/diagnostics.lua`)

Estado: **probado en Linux con un filesystem doble (dict) en lupa, Lua 5.4 y LuaJIT 2.1**. No se ejecutó dentro de CSP; el comportamiento real de `io.*` en Windows queda para el Windows Validation Kit. Todavía no está integrado en `RiceTrials.lua`.

## Activación

Sólo con `config.diagnostics == true`. En cualquier otro caso `diagnostics.new` devuelve un objeto inerte (`update`/`event` no hacen nada, `flush` devuelve `false`, `stats()` devuelve `{enabled=false}`) que no toca `io`.

## API

```lua
local diagnostics = require('diagnostics')
local d = diagnostics.new(config, deps)
-- config: diagnostics=true, diagnostics_dir, diagnostics_file, diagnostics_max_bytes,
--         diagnostics_buffer_max, diagnostics_flush_s, diagnostics_sample_hz
-- deps (todo opcional):
--   io    = tabla con save/load/fileExists/createDir/deleteFile/move (default: global io)
--   env   = tabla con ac/io/web/JSON/ui para el informe de APIs y versiones (default: _G)
--   clock = fun(): segundos de simulación (default: suma de dt recibidos en update)
--   dir   = carpeta si config.diagnostics_dir falta
d:update(dt, car, sim, extra)   -- desde script.update; car = ac.getCar(0), sim = ac.getSim()
d:event(kind, fields)           -- registro puntual (p. ej. error de red)
d:flush() -> boolean            -- fuerza escritura; también se llama cada flush_s
d:stats() -> table              -- copia de contadores
```

`extra` (opcional): `{detector = <tabla o string>, connection = <tabla>, errors = {'texto', ...}}`. `detector` y `connection` se registran sólo cuando cambian (comparados a la frecuencia de muestreo, no por frame); cada elemento de `errors` se registra siempre.

`stats()`: `enabled, buffered, dropped, lines_written, file_bytes, flushes, rotations, io_errors, encode_errors, samples, last_error, path, create_dir_ok`.

Si `diagnostics_dir` falta, se intenta `ac.getFolder(ac.FolderID.Root)..'/apps/lua/RiceTrials/diagnostics'` (`FolderID.Root` es el único ID confirmado en `docs/csp-api-reference.md`). Si tampoco hay carpeta, cada flush cuenta un error de io y las líneas quedan en el buffer acotado.

## Configuración y límites

| Clave | Default | Rango | Efecto |
|---|---|---|---|
| `diagnostics_max_bytes` | 2 MB | 4 KB a 64 MB | tamaño máximo de cada archivo |
| `diagnostics_buffer_max` | 1000 | 10 a 100000 | líneas pendientes en memoria; al llenarse se descarta la más vieja y se cuenta en `dropped` |
| `diagnostics_flush_s` | 10 | 1 a 300 | segundos de simulación entre escrituras |
| `diagnostics_sample_hz` | 2 | 0,1 a 20 | muestras de posición por segundo |
| `diagnostics_file` | `rice_diagnostics` | `[%w_-]+` | nombre base |

Archivos: `<dir>/<file>.jsonl` (actual) y `<dir>/<file>.1.jsonl` (anterior). Disco usado como máximo: 2 x `max_bytes`.

## Por qué el archivo actual vive en memoria

`io.save(filename, data)` reescribe el archivo completo y la referencia de CSP no documenta append. Para no perder líneas anteriores, el módulo guarda en memoria el texto del archivo actual (acotado por `max_bytes`) y en cada flush escribe `segmento + líneas nuevas`. Alternativas descartadas:

- **Releer con `io.load` y concatenar**: misma memoria pico y además lectura de disco en cada flush.
- **Un archivo por flush (segmentos chicos)**: cientos de archivos por sesión, y la rotación necesitaría `io.scanDir`, que no está verificado.

Costo: memoria pico de unas 2 x `max_bytes` durante el flush (4 MB con defaults) y una escritura de hasta 2 MB cada 10 s. Si eso resulta caro en el rig, bajar `diagnostics_max_bytes` (por ejemplo 512 KB) reduce ambos.

## Rotación

1. Antes de la primera escritura de la sesión: `io.createDir(dir)` (su resultado no se cuenta como error porque puede devolver `false` si la carpeta ya existe; decide `io.save`). Si ya existe un archivo actual de una sesión anterior, se rota para no pisarlo.
2. Si `segmento + nuevas` superaría `max_bytes`: si existe `.1.jsonl` se borra con `io.deleteFile` **antes** de `io.move(actual, .1.jsonl)`, porque `io.move` sobre un destino existente no está documentado (en Windows `MoveFileEx` sin `REPLACE_EXISTING` falla). Si `move` falla igual, se borra el actual para mantener el tope. Luego se empieza un segmento vacío.
3. Si un solo flush supera `max_bytes`, se conservan las líneas más nuevas y las cortadas se suman a `dropped`.

## Nunca bloquea

- Toda llamada a `io` va dentro de `pcall`; una excepción, un `false` devuelto o una función ausente suman `io_errors` y guardan `last_error`. Nada se propaga a `script.update`.
- Si `io.save` falla, las líneas quedan en el buffer (acotado) y se reintenta en el próximo flush.
- Los campos del auto se leen con `pcall`: en CSP `ac.StateCar` es cdata FFI y leer un campo inexistente lanza error.
- Por frame sólo se comparan 4 escalares (colisión y `lapCount`); muestreo, detector y conexión van a 2 Hz; encoding y escritura sólo al hacer flush o al registrar.
- JSON propio (determinístico, sin depender de `JSON.stringify` de CSP, que puede codificar `{}` como `[]`). NaN e infinitos se escriben como `null`; texto con comillas, barras y caracteres de control se escapa.

## Formato JSONL

Una línea por objeto, siempre con `t` (segundos de simulación) y `kind` primero; el resto de las claves en orden alfabético. Tipos de línea:

| `kind` | Campos | Cuándo |
|---|---|---|
| `session_start` | `format=1`, `csp_version` (`ac.getPatchVersion()`), `csp_code` (`ac.getPatchVersionCode()`), `car_id` (`ac.getCarID(0)`), `track_id` (`ac.getTrackFullID('/')`), `api` (nombre -> bool), `config` | primera llamada a `update` |
| `car_fields` | `available` (`car.<campo>` -> bool) | primera llamada con auto |
| `sample` | `pos={x,y,z}`, `look={x,y,z}`, `speedKmh`, `lapTimeMs`, `lapCount`, `isLapValid`, `splinePosition` | cada 1/`sample_hz` s con auto |
| `collision` | `collidedWith`, `wheelsOutside`, `collisionDepth` | sólo cuando cambia `collidedWith`, `wheelsOutside` o el estado de contacto (`collisionDepth > 0`) |
| `lap` | `lapCount`, `previousLapTimeMs`, `isLastLapValid` | cuando cambia `lapCount` |
| `detector` / `connection` | `state` | cuando cambian |
| `sim` | `isPaused` | cuando cambia (si `sim` lo expone) |
| `error` | `message` | por cada `extra.errors` |
| `car_missing` | | cuando `car` pasa a nil |
| cualquier otro | lo que se pase a `:event(kind, fields)` | a demanda |

Ejemplo de muestra (la herramienta de análisis de pista importa estas líneas):

```json
{"t":12.5,"kind":"sample","isLapValid":true,"lapCount":0,"lapTimeMs":1000,"look":{"x":0,"y":0,"z":1},"pos":{"x":1.5,"y":0.25,"z":-3},"speedKmh":50,"splinePosition":0.1}
```

Campos ausentes (API no expuesta por la versión de CSP) simplemente no aparecen en la línea; `pos`/`look` se omiten si alguna componente no es un número finito.

APIs informadas en `api`: `ac.getCar`, `ac.getSim`, `ac.getPatchVersion`, `ac.getPatchVersionCode`, `ac.getCarID`, `ac.getTrackFullID`, `ac.onCarJumped`, `ac.onSessionStart`, `ac.getFolder`, `ac.storage`, `io.save`, `io.load`, `io.move`, `io.createDir`, `io.deleteFile`, `io.fileExists`, `web.get`, `web.post`, `JSON.parse`, `JSON.stringify`, `ui.pushDWriteFont`, `ui.popDWriteFont`, `ui.dwriteText`, `ui.drawRectFilled`, `ui.textColored`, `ui.windowSize`, `ui.setCursor`.

## Integración sugerida (para el orquestador)

```lua
local diagnostics = require('diagnostics')
local diag = diagnostics.new(settings, {})   -- settings.diagnostics == true para activar
function script.update(dt)
  -- ... scoring existente ...
  diag:update(dt, ac.getCar(0), ac.getSim and ac.getSim() or nil,
    {connection = {online = online, pending = #queue.entries}, detector = detectorState})
end
```

Se recomienda llamar `diag:event('server', {...})` desde los callbacks de `web` para registrar respuestas no reconocidas. No hay hook de cierre verificado en CSP, así que al descargar la app se pierden como máximo `flush_s` segundos de líneas.

## No verificado

- Ruta real de `ac.getFolder(ac.FolderID.Root)` y permisos de escritura en `apps/lua/RiceTrials/diagnostics`.
- Resultado de `io.createDir` sobre carpeta existente, de `io.move` sobre destino existente y atomicidad de `io.save`.
- Costo real de un `io.save` de 2 MB en el rig (posible micro-tirón cada 10 s).

## Tests

`client/tests/test_diagnostics.py`: inactivo por defecto, encabezado y JSON válido por línea (incluye escapes), muestreo a 2 Hz, colisiones sólo al cambiar, flush periódico (no por frame) que conserva líneas previas, tope del buffer con descarte del más viejo, rotación por tamaño con 2 archivos acotados y orden temporal, rotación de la sesión anterior con borrado antes del move, `io` ausente, `io` lanzando errores y recuperación posterior, `io.save` devolviendo `false`, auto ausente y `dt` inválido, detector/conexión sólo al cambiar, reloj inyectado y carpeta por `ac.getFolder`, auto tipo cdata que lanza en campos inexistentes, y smoke en LuaJIT 2.1.
