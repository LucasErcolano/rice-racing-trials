# API del servidor local de pruebas

Servidor: `server/app.py` (stdlib `http.server` + SQLite en modo WAL). Arranque:

```
RICE_ADMIN_TOKEN=... RICE_STATION_TOKENS='{"ST01":"...","ST02":"..."}' python3 -m server.app --db trials.sqlite --host 127.0.0.1 --port 8080
```

Escucha en localhost por defecto. Para una red no confiable hay que poner TLS delante (proxy inverso).

## Regla fail-closed de scoring

El servidor de producción arranca con `scoring_armed=False` y **no existe opción de línea de comandos para armarlo**. Mientras no esté comisionado:

- Ninguna vuelta queda `VALID`/`valid`: toda vuelta que el cliente proponga como válida se guarda como `PENDING_REVIEW` sin `corrected_ms`.
- `POST /api/admin/reviews` con `decision=approve` devuelve `409 official scoring not commissioned`.
- `/api/leaderboard` devuelve `ranking: {}` y `leaders: []` con `scoring_armed: false`; `/tv` muestra el banner "SCORING NO COMISIONADO: resultados provisionales, no oficiales".

Sólo los tests instancian `Store(..., scoring_armed=True)` para verificar el contrato de ranking.

El servidor nunca confía en `corrected_ms` enviado por el cliente: siempre recalcula con `server/scoring.py` (`scoring.corrected_ms`, `scoring.resolve_status`).

## Autenticación

| Caso | Respuesta |
|---|---|
| Sin `Authorization` o token desconocido | `401 authentication required` |
| Token válido pero de otro rol o estación (p. ej. token de estación en `/api/admin/*`, o ST02 enviando por ST01) | `403 credential not authorized for this resource` |

Encabezado: `Authorization: Bearer <token>`. Admin: `RICE_ADMIN_TOKEN`. Estaciones: un token distinto por `station_id` en `RICE_STATION_TOKENS`.

## Endpoints de estación

### `POST /api/laps` (token de la estación)

Campos obligatorios: `protocol_version` (=1), `station_id`, `lap_id`, `session_id`, `pilot_id` (identificadores `[A-Za-z0-9_.-]{1,80}`), `event` (`autocross`, `skidpad`, `endurance`), `raw_ms` (entero 1..3600000), `status`, `events`. Endurance requiere además `scoring_version` (=1).

Campos opcionales validados y guardados tal cual en el payload:

| Campo | Validación |
|---|---|
| `lap_number` | entero 0..100000 |
| `assignment_revision` | entero >= 1, debe coincidir con el historial de asignaciones |
| `rules_version` | entero, debe ser igual a `scoring.RULES_VERSION` (si no: 400) |
| `calibration_version` | entero o texto de hasta 120 caracteres |
| `client_timestamp` | texto de hasta 64 caracteres o número finito |
| `detector` | objeto o lista JSON, serializado <= 4 KB |
| `sync` | objeto o lista JSON, serializado <= 2 KB |
| `track_config`, `detector_ok`, `calibration_verified` | usados por la política de endurance |

Respuestas:

- `200 {"lap_id","corrected_ms","status","duplicate":false}`.
- Reenvío idéntico (mismo `station_id`+`lap_id` y mismo payload): `200` con `duplicate: true` (idempotente; reintentos seguros).
- Mismo `lap_id` con payload distinto: `409 lap_id already used with different payload`.
- Entrega fuera de orden (`lap_number` 3 antes que 2) se acepta; el orden no se infiere del arribo.
- `protocol_version` distinto de 1: `400 unsupported protocol_version`.
- Asignación no coincidente: `409`. Revisión histórica válida pero no vigente: se acepta como `PENDING_REVIEW`.

### `POST /api/station/heartbeat` y `GET /api/station/assignment`

Heartbeat con `{"protocol_version":1,"station_id":"ST01"}`. Una estación se considera en línea si su último heartbeat tiene menos de 90 s.

### Errores de sincronización

Todo rechazo 4xx en `/api/laps`, `/api/station/heartbeat` o `/api/station/assignment` se registra en la tabla `sync_errors` (estación declarada si es conocida, si no `unknown`; ruta, código HTTP, causa, `lap_id`, fecha UTC). La tabla se poda a las últimas 1000 filas.

## Endpoints públicos

| Método y ruta | Descripción |
|---|---|
| `GET /tv`, `/tv.js`, `/tv.css` | Pantalla de clasificación (1920x1080, refresco cada 5 s; `?refresh=N` segundos, `?session_id=X` fija sesión, `?event=` prueba) |
| `GET /api/sessions` | Sesiones, `active_session`, `scoring_armed` |
| `GET /api/leaderboard[?session_id=X]` | Sin `session_id` usa la sesión activa. Devuelve `ranking` por prueba: `rank`, `pilot_id`, `pilot_name`, `best_corrected_ms`, `raw_ms`, `penalties`, `penalty_ms`, `gap_ms`, `valid_laps`, `lap_id`, `station_id`; además `laps` (todas, provisionales incluidas), `diagnostics` (conteos por estado, estaciones de la sesión con `online`/`last_seen_utc`, `pending_review`) y `generated_utc` |

Ranking: sólo vueltas con estado `VALID`/`valid` y servidor armado. Orden determinista: tiempo corregido ascendente, luego bruto ascendente, luego vuelta más temprana (`created_utc`, `lap_id`), luego `pilot_id`.

## Endpoints de administración (token admin)

| Método y ruta | Descripción |
|---|---|
| `GET /admin`, `/admin.js`, `/admin.css` | Panel web |
| `GET /api/admin/sessions` | Lista y sesión activa |
| `POST /api/admin/sessions` | `{"session_id","name","active":bool}` crea o renombra; `active:true` la marca activa (única) |
| `POST /api/admin/sessions/active` | `{"session_id"}` marca activa (404 si no existe) |
| `GET/POST /api/admin/pilots` | Lista / registra `{"pilot_id","name"}` |
| `GET /api/admin/assignments[?station_id=]` | `current` y `history` (cada fila con `previous_pilot_id`, `previous_session_id`, `reassignment`) |
| `POST /api/admin/assignments` | `{"station_id","session_id","pilot_id"}`; devuelve `assignment_revision` |
| `GET /api/admin/stations` | Todas las estaciones configuradas: `online`, `offline`, `last_seen_utc`, asignación, última vuelta, `detector_ok`, `detector` (último resumen), `calibration_version`, `detector_error_laps`, `sync_error_count` |
| `GET /api/admin/sync-errors[?station_id=]` | Últimos 200 rechazos |
| `GET/POST /api/admin/calibrations` | Provenance de calibración inspeccionada `{"station_id","track_config","reference","operator","active"}` |
| `GET /api/admin/laps[?session_id=]` | Vueltas con metadatos del cliente (`lap_number`, `detector`, `sync`, `client_timestamp`, `calibration_version`, `client_status`, `client_corrected_ms_ignored`) |
| `POST /api/admin/reviews` | `{"station_id","lap_id","decision":"approve"|"reject","reviewer","reason"}`. Aprobar exige servidor armado, detector OK, calibración verificada y calibración admin activa |
| `POST /api/admin/penalties` | `{"station_id","lap_id","events":{...},"reviewer","reason"}`. Sólo claves de penalidad (`cone`, `off_course`, `slalom` en endurance; `doo`, `oc` en autocross/skidpad). Se fusiona con los eventos guardados, el servidor recalcula con `scoring.corrected_ms` y registra en `reviews` (`kind=penalties`, `before_events`, `after_events`, `before_corrected_ms`, `after_corrected_ms`). **Nunca cambia el estado**: `corrected_ms` sólo queda oficial si la vuelta ya era VALID; si no, la respuesta trae `corrected_preview_ms` y `official:false` |
| `GET /api/admin/reviews?session_id=`, `GET /api/admin/reviews.csv?session_id=` | Auditoría completa |
| `GET /api/admin/export.json[?session_id=]` | Exportación completa: sesiones, pilotos, vueltas, revisiones, asignaciones, historial, calibraciones, errores de sync, reglas y penalidades |
| `GET /api/admin/export.csv?table=T[&session_id=]` | `T` en `laps` (por defecto), `reviews`, `assignment_history`, `assignments`, `calibrations`, `sessions`, `pilots`, `sync_errors`. Celdas que empiezan con `= + - @` se prefijan con `'` (protección contra inyección de fórmulas) |
| `GET /api/admin/diagnostics` | Estado de scoring, reglas, versión de esquema, modo de journal, conteos, estaciones, backups recientes |
| `POST /api/admin/backup` | Copia consistente con la API de backup de SQLite en `<carpeta de la DB>/backups/`, verificada con `integrity_check` |

## Persistencia, migraciones y recuperación

- SQLite en modo WAL, `busy_timeout` de 10 s, una conexión por operación.
- Migraciones idempotentes controladas por `PRAGMA user_version` (esquema actual: 2). Una base legacy sin versión se migra al abrirla; una base con versión mayor que la del servidor se rechaza.
- Tras un reinicio, los reenvíos del cliente vuelven a ser idempotentes porque la deduplicación vive en la base.

## Backup y restauración (servidor detenido)

```
python3 server/app.py --db trials.sqlite --check                      # integrity_check de la base actual
python3 server/app.py --db trials.sqlite --restore backups/archivo.sqlite
```

`--restore` valida el archivo origen (`PRAGMA integrity_check`, tablas requeridas, versión de esquema no mayor), guarda una copia previa en `backups/<db>-pre-restore-<fecha>.sqlite`, copia por la API de backup a un archivo temporal, lo verifica, lo reemplaza atómicamente, aplica migraciones y vuelve a verificar. Imprime JSON con `sha256` del origen. Código de salida 2 si falla (la base actual queda intacta).
