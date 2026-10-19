# HUD del cliente (`client/hud.lua`)

Estado: **probado en Linux con dobles de `ui` (lupa, Lua 5.4 y LuaJIT 2.1)**. No se ejecutó dentro de Assetto Corsa/CSP. Todavía no está integrado en `RiceTrials.lua`; la integración la hace el orquestador.

## Separación modelo / render

- `hud.model(state, config) -> model`: función pura. No lee `ac`, `ui` ni `io`; sólo usa `state`, `config` y `scoring.corrected_ms` (módulo `scoring` cargado con `require` una vez; se puede inyectar asignando `hud.scoring`). Devuelve:
  ```lua
  {schema=1, title='Rice Trials - PROVISIONAL / CSP no validado',
   banner={text=..., level=...} | nil,   -- aviso más grave, primero de su nivel
   lines={{text=..., level='ok'|'info'|'warn'|'error'}, ...},
   layout={scale=, x=, y=, max_errors=}}
  ```
- `hud.draw(model, config) -> boolean`: dibuja el modelo. Nunca lanza errores (todo bajo `pcall`); devuelve `false` si `ui` falta o falla.
- `hud.cache(interval)`: objeto con `:update(dt, state, config) -> model` que recalcula el modelo como máximo cada `interval` segundos (default 0,25 s). Así el callback de ventana por frame sólo dibuja una tabla ya armada (sin trabajo pesado por frame).
- `hud.format_ms(ms)`: `m:ss.mmm`, o `--` si el valor no es un entero válido.

Los textos del HUD son ASCII a propósito (sin tildes): la cobertura de glifos de las fuentes de CSP no está verificada.

## Esquema de `state` (todos los campos toleran `nil`)

```lua
state = {
  assignment = {pilot_id='p1', pilot_name='Ana', session_id='s1'} | nil,
  event = 'endurance' | 'autocross' | 'skidpad' | nil,
  phase = 'waiting' | 'outlap' | 'timed' | nil,   -- nil: ruta no calibrada
  lap = {
    elapsed_ms = 70123,          -- entero; sólo se muestra con phase == 'timed'
    penalties = {                 -- por clave: nil = SIN EVIDENCIA (nunca 0 implícito)
      cone = 0..100,              -- endurance
      off_course = 0..100,        -- endurance
      slalom = {'S1', 'S4'},      -- endurance: ids de puertas falladas; {} = evidencia de 0
      doo = 0..100, oc = 0..100,  -- autocross
    },
  },
  last_official = {lap_id=, status='VALID'|'PENDING_REVIEW'|'INVALID'|'OUTLAP'|'DETECTOR_ERROR',
                   raw_ms=, corrected_ms=} | nil,  -- tal cual lo devolvió el servidor
  best = {status='VALID', corrected_ms=} | nil,     -- sólo se muestra si status == 'VALID'
  sync = {
    online = true | false | nil,                    -- nil: desconocido
    pending = 0,                                    -- entradas en cola local
    last_ack = {lap_id='ST01-x-3', age_s=12} | nil,
    persistence = 'ok' | 'degraded' | 'corrupt' | 'unavailable' | nil,
  },
  detector_errors = {'texto', ...} | nil,          -- errores críticos del detector
  status = 'texto libre del cliente' | nil,         -- p. ej. el status actual de RiceTrials.lua
}
```

Correspondencia sugerida con `RiceTrials.lua` actual: `assignment` = `assignment` autenticado; `phase` = `route.phase` (`waiting`/`outlap`/`timed`) o `nil` si no hay `calibration`; `lap.elapsed_ms` = `(elapsed-started)*1000` redondeado; `sync.pending` = `#queue.entries`; `sync.persistence`: `'corrupt'` si `status` contiene `QUEUE_CORRUPT`, `'unavailable'` si contiene `STORAGE_UNAVAILABLE`, `'degraded'` si contiene `STORAGE_UNVERIFIED`; `sync.online` según la última respuesta de heartbeat/assignment; `last_ack` cuando un `POST /api/laps` es reconocido. Hoy el servidor sólo responde `PENDING_REVIEW` al cliente, por lo que `best` queda en `--` hasta que exista un endpoint que devuelva resultados `VALID`.

## Reglas de presentación

| Situación | Línea | Nivel |
|---|---|---|
| Sin asignación | `Piloto: SIN ASIGNACION (no se puntua)` + banner | warn |
| Outlap | `OUTLAP: vuelta de salida, NO cronometrada`; tiempo `--`; sin proyección | warn |
| Vuelta sin evidencia | `Conos: sin evidencia`, etc.; `Proyectado: sin evidencia (...)` | warn |
| Evidencia parcial | sólo se muestran las conocidas; **no** se proyecta | warn |
| Evidencia completa | `Proyectado PROVISIONAL: m:ss.mmm` vía `scoring.corrected_ms` | info |
| Penalidad malformada o `corrected_ms` nil | `Proyectado: no calculable` | error |
| Skidpad | `Proyectado: no disponible` (requiere splits por lado) | warn |
| Última oficial `VALID` | `Ultima oficial: <corregido> VALID` | ok |
| Última `PENDING_REVIEW` u otro | `Ultima: <bruto> bruto <ESTADO> (no oficial)` | warn/info/error |
| Estado de servidor desconocido | `Ultima oficial: estado desconocido (...)` | error |
| Mejor corregida | sólo con `best.status == 'VALID'`; si no `--` | ok/info |
| Offline con cola | `Servidor: OFFLINE`, `Cola pendiente: N`, banner | warn |
| Persistencia corrupta / no disponible | línea + banner | error |
| Persistencia degradada | línea + banner | warn |
| Errores de detector | hasta `hud_max_errors` (default 3) + `(+N errores mas)` + banner | error |

Los textos que vienen del servidor o del operador se sanean (caracteres de control reemplazados) y se recortan (40 caracteres).

## Configuración

| Clave | Default | Rango |
|---|---|---|
| `hud_scale` | 1 | 0,5 a 3 (tamaño de fuente 15 px por escala) |
| `hud_x`, `hud_y` | 8, 8 | 0 a 10000 (px desde la esquina de la ventana) |
| `hud_max_errors` | 3 | 1 a 10 |
| `hud_font` | `Segoe UI;Weight=Bold` | nombre DWrite |

Valores no numéricos vuelven al default; fuera de rango se recortan.

## Render y APIs

Sólo `ui.*` de `docs/csp-api-reference.md`, cada una comprobada con `type(ui.x) == 'function'` antes de usarla:

- Fondo negro al 80 % con `ui.drawRectFilled(vec2, vec2, rgbm, 4)`, ancho tomado de `ui.windowSize()` si existe.
- Texto con `ui.pushDWriteFont` + `ui.dwriteText(text, size, color)`; posición con `ui.setCursor(vec2)`. Colores de alto contraste: verde (ok), blanco (info), amarillo (warn), rojo (error).
- `ui.popDWriteFont` **no figura** en `docs/csp-api-reference.md`; se usa sólo si existe en runtime. Si no existe, no se usa DWrite (para no dejar la pila de fuentes desbalanceada) y se cae a `ui.textColored`, y luego a `ui.text`.
- Si DWrite falla en medio del dibujo, `popDWriteFont` se llama igual.
- No verificado en juego: si las coordenadas de `drawRectFilled` son relativas a la ventana, el aspecto real de las fuentes y el costo por frame.

## Cerrar la ventana no detiene el scoring

El cronometraje, la cola y el envío viven en `script.update(dt)`, que CSP llama independientemente de que la ventana esté visible. `script.windowMain` (o el callback que use el orquestador) sólo debe llamar `hud.draw(cache:update(dt, state, config), config)`. Si la ventana está cerrada, CSP no llama al callback y simplemente no se dibuja nada. Esto está documentado en el SDK pero **no probado en juego** (ver `docs/limitations.md`, punto 7).

## manifest.ini

No se modificó. Hoy declara `[WINDOW_MAIN]` con `FUNCTION_MAIN = windowMain` y `FLAGS = MAIN`. **No está verificado en juego** que el HUD aparezca automáticamente al cargar la sesión; puede requerir que el piloto u operador lo abra desde la barra de apps de CSP. Comprobarlo en el Windows Validation Kit.

## Integración sugerida (para el orquestador)

```lua
local hud = require('hud')
local hudCache = hud.cache(0.25)
function script.windowMain(dt)
  hud.draw(hudCache:update(dt, buildHudState(), settings), settings)
end
```

`buildHudState()` debe ser barato (sólo copiar referencias ya calculadas en `script.update`).

## Tests

`client/tests/test_hud.py`: modelo con estado nil/vacío, sin asignación, outlap, vuelta sin evidencia (no muestra 0 ni proyección), evidencia parcial, penalidades combinadas (fixture `endurance combined` = 2:34.500), evidencia explícita de 0, penalidad inválida, skidpad, offline con cola, persistencia corrupta/degradada, `PENDING_REVIEW` vs `VALID` (última y mejor), estado desconocido, errores de detector acotados, saneo de texto, configuración acotada, cache cada 0,25 s, render DWrite balanceado y escalado, fallback a `textColored`, `ui` ausente o fallando, pureza del modelo y smoke en LuaJIT 2.1.
