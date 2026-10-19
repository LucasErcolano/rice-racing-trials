# Persistencia de la cola local (client/journal.lua)

Estado: probado en Linux con lupa (LuaJIT 2.1, igual que CSP) sobre un filesystem
simulado en memoria (`client/tests/test_journal.py`). Pendiente de validar en CSP sobre
Windows. Integrado en `RiceTrials.lua`: todo el estado del cliente (entradas, nonce, next_id, números de vuelta) es un único registro del journal en `apps/lua/RiceTrials/state/`; la clave vieja de `ac.storage` se migra una vez y se borra tras confirmar el archivo.

## Garantías reales

- Una cola guardada con `j:save(items)` que devolvió `true` sobrevive a un reinicio de
  la VM Lua o de Assetto Corsa: `true` significa que el archivo se escribió, se volvió a
  leer con `io.load` y el contenido leído es idéntico y tiene checksum válido.
- Si una escritura falla, queda truncada o el readback no coincide, la ranura anterior
  no se toca y la última cola confirmada sigue recuperable.
- `false` significa "no confirmado", no "no escrito". Si los bytes quedaron bien en
  disco pese a un readback fallido, la próxima carga puede devolver esa versión nueva.
  El llamador debe tratar `false` como "estado incierto" y reintentar el guardado.
- NO se declara resistencia a corte de energía ni a caída del sistema operativo: la API
  de CSP no expone fsync, así que un readback correcto puede venir de la caché del SO.
- El modo degradado (`ac.storage`) es más débil: CSP vuelca ac.storage de forma
  asíncrona, así que el readback sólo confirma la memoria del proceso.

## Formato en disco

Dos ranuras en `<dir>/<name>.a.json` y `<dir>/<name>.b.json` (por defecto
`name = 'queue'`). Cada una contiene un único objeto JSON:

```json
{"checksum":"bf9cf968","items":"[{\"id\":\"L1\"}]","seq":7,"v":1}
```

- `v`: versión del formato (1).
- `seq`: entero creciente; la ranura válida con mayor `seq` es la vigente.
- `items`: el array de items codificado como string JSON. Va embebido como string
  para que el checksum cubra exactamente esos bytes.
- `checksum`: FNV-1a de 32 bits en hexadecimal (8 dígitos, minúsculas) del string
  `items`. Usa `bit` de LuaJIT si existe y aritmética modular en Lua 5.1/5.4 si no.
  Vectores probados: `''` = 811c9dc5, `'a'` = e40c292c, `'foobar'` = bf9cf968. Es un
  detector de corrupción accidental, no una firma: no protege contra edición deliberada.

`save` escribe siempre la ranura que no tiene el mayor `seq` válido, con
`seq = max + 1`. Nunca usa `io.move` (su comportamiento sobre un destino existente no
está documentado) ni append (no existe).

## Estados (`j:load()` devuelve `items, state`)

| Estado | Significado | `can_send()` |
|---|---|---|
| `ok` | Se cargó la ranura válida con mayor `seq` (la otra puede estar rota; queda en `last_error`). | sí |
| `empty` | No hay ranuras ni clave legacy. Cola vacía. | sí |
| `migrated` | No había ranuras; se importó `rice_trials_pending_review_v1` de ac.storage, se guardó con readback confirmado y recién entonces se borró la clave vieja. | sí |
| `degraded` | `io` ausente o `io.save` falla: se usa `ac.storage['rice_trials_journal_v2']` con el mismo sobre. También si la migración no pudo confirmarse en archivo (la clave legacy se conserva). | sí |
| `corrupt` | Existe algún archivo (o la copia degradada) pero ninguno es válido. Items vacío, `save` rechazado. | no |

La clave legacy se acepta como array JSON o con el sobre actual de `RiceTrials.lua`
(`{version, nonce, next_id, entries}`); en ese caso `j.legacy_meta` guarda
`nonce`, `next_id` y `version` para que el orquestador los conserve.

`j:state()` devuelve `{state, seq, slot ('a'|'b'|'storage'|nil), last_error,
quarantined (ruta del primer archivo en cuarentena o nil), quarantined_all, can_send}`.

## Recuperación manual (estado `corrupt`)

El cliente queda en modo fail-closed: no debe transmitir ni descartar vueltas, porque
no sabe qué había en la cola.

1. Cerrar AC. Los contenidos crudos inválidos se copiaron a
   `<dir>/<name>.quarantine.<n>.json` (n crece; nunca se sobrescribe una cuarentena
   existente). Los archivos originales `.a.json` y `.b.json` no se modifican.
2. Inspeccionar las ranuras y las copias en cuarentena; rescatar a mano los items
   recuperables y registrarlos fuera de banda (planilla del oficial de pista).
3. Una vez preservada la evidencia, mover `<name>.a.json` y `<name>.b.json` fuera de la
   carpeta (y borrar `rice_trials_journal_v2` de ac.storage si estaba corrupta).
4. Al reiniciar el estado será `empty` y la cola vuelve a funcionar.

## API exacta

```lua
local journal = require('journal')
local j = journal.new({dir = <string>, name = 'queue'}, {
  io = <tabla io>,                 -- por defecto el io global
  json = {encode = fn, decode = fn}, -- por defecto JSON.stringify / JSON.parse
  storage = <ac.storage o similar>, -- opcional: migración y modo degradado
  legacy_key = 'rice_trials_pending_review_v1',
  -- opcionales: degraded_key (por defecto 'rice_trials_journal_v2'),
  --             io_disabled = true (no usar el io global)
})
local items, state = j:load()
local ok, err = j:save(items)   -- persiste el array completo
local s = j:state()
local allowed = j:can_send()
journal.fnv1a(str)              -- también fnv1a_pure, FNV_IMPL ('bit' | 'pure')
```

`save` llama a `load` si todavía no se cargó. Los items deben ser un array Lua denso.

## Falta verificar en Windows / CSP

- Ruta real del directorio (p. ej. `ac.getFolder(ac.FolderID.Root)..'/apps/lua/RiceTrials/state'`
  o la carpeta de documentos) y que `io.createDir` la cree.
- Permisos de escritura en esa ruta con AC instalado en `Program Files` o Steam.
- Qué hace `io.save` ante disco lleno, archivo bloqueado o antivirus: si devuelve
  `false`, lanza error o deja un archivo truncado. El módulo maneja los tres casos,
  pero sólo se probaron con dobles.
- Si `io.load` lee de caché o del disco, y si un `io.save` interrumpido por cierre de
  AC puede dejar contenido parcial (lo que motiva las dos ranuras).
- Comportamiento de `JSON.stringify` de CSP con tablas vacías (`{}` frente a `[]`; el
  módulo normaliza `{}` a `[]`), números grandes (hasta 2^53) y UTF-8.
- Si asignar `nil` en `ac.storage` borra realmente la clave legacy.
