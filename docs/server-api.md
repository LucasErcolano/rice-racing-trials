# Local test server API

Server: `server/app.py` (stdlib `http.server` + SQLite in WAL mode). Startup:

```
RICE_ADMIN_TOKEN=... RICE_STATION_TOKENS='{"ST01":"...","ST02":"..."}' python3 -m server.app --db trials.sqlite --host 127.0.0.1 --port 8080
```

It listens on localhost by default. On an untrusted network, put TLS in front of it (reverse proxy).

## Fail-closed scoring rule

The production server starts with `scoring_armed=False` and **there is no command-line option to arm it**. Until scoring is commissioned:

- No lap becomes `VALID`/`valid`: every lap the client proposes as valid is stored as `PENDING_REVIEW` without `corrected_ms`.
- `POST /api/admin/reviews` with `decision=approve` returns `409 official scoring not commissioned`.
- `/api/leaderboard` returns `ranking: {}` and `leaders: []` with `scoring_armed: false`; `/tv` shows the banner "SCORING NOT COMMISSIONED: provisional, unofficial results".

Only the tests instantiate `Store(..., scoring_armed=True)` to check the ranking contract.

The server never trusts a `corrected_ms` sent by the client: it always recalculates with `server/scoring.py` (`scoring.corrected_ms`, `scoring.resolve_status`).

## Authentication

| Case | Response |
|---|---|
| No `Authorization` or unknown token | `401 authentication required` |
| Valid token but for another role or station (e.g. a station token on `/api/admin/*`, or ST02 submitting as ST01) | `403 credential not authorized for this resource` |

Header: `Authorization: Bearer <token>`. Admin: `RICE_ADMIN_TOKEN`. Stations: a distinct token per `station_id` in `RICE_STATION_TOKENS`.

## Station endpoints

### `POST /api/laps` (station token)

Required fields: `protocol_version` (=1), `station_id`, `lap_id`, `session_id`, `pilot_id` (identifiers `[A-Za-z0-9_.-]{1,80}`), `event` (`autocross`, `skidpad`, `endurance`), `raw_ms` (integer 1..3600000), `status`, `events`. Endurance also requires `scoring_version` (=1).

Optional fields, validated and stored as-is in the payload:

| Field | Validation |
|---|---|
| `lap_number` | integer 0..100000 |
| `assignment_revision` | integer >= 1, must match the assignment history |
| `rules_version` | integer, must equal `scoring.RULES_VERSION` (otherwise 400) |
| `calibration_version` | integer or text up to 120 characters |
| `client_timestamp` | text up to 64 characters or a finite number |
| `detector` | JSON object or list, serialized <= 4 KB |
| `sync` | JSON object or list, serialized <= 2 KB |
| `track_config`, `detector_ok`, `calibration_verified` | used by the endurance policy |

Responses:

- `200 {"lap_id","corrected_ms","status","duplicate":false}`.
- Identical resend (same `station_id`+`lap_id` and same payload): `200` with `duplicate: true` (idempotent; retries are safe).
- Same `lap_id` with a different payload: `409 lap_id already used with different payload`.
- Out-of-order delivery (`lap_number` 3 before 2) is accepted; order is not inferred from arrival.
- `protocol_version` other than 1: `400 unsupported protocol_version`.
- Mismatched assignment: `409`. A valid but no longer current historical revision: accepted as `PENDING_REVIEW`.

### `POST /api/station/heartbeat` and `GET /api/station/assignment`

Heartbeat with `{"protocol_version":1,"station_id":"ST01"}`. A station counts as online if its last heartbeat is less than 90 s old.

### Sync errors

Every 4xx rejection on `/api/laps`, `/api/station/heartbeat` or `/api/station/assignment` is recorded in the `sync_errors` table (declared station if known, otherwise `unknown`; route, HTTP code, cause, `lap_id`, UTC date). The table is pruned to the last 1000 rows.

## Public endpoints

| Method and route | Description |
|---|---|
| `GET /tv`, `/tv.js`, `/tv.css` | Leaderboard display (1920x1080, refresh every 5 s; `?refresh=N` seconds, `?session_id=X` pins a session, `?event=` event) |
| `GET /api/sessions` | Sessions, `active_session`, `scoring_armed` |
| `GET /api/leaderboard[?session_id=X]` | Without `session_id` it uses the active session. Returns `ranking` per event: `rank`, `pilot_id`, `pilot_name`, `best_corrected_ms`, `raw_ms`, `penalties`, `penalty_ms`, `gap_ms`, `valid_laps`, `lap_id`, `station_id`; plus `laps` (all, including provisional), `diagnostics` (counts per status, session stations with `online`/`last_seen_utc`, `pending_review`) and `generated_utc` |

Ranking: only laps with status `VALID`/`valid` and an armed server. Deterministic order: corrected time ascending, then raw time ascending, then earliest lap (`created_utc`, `lap_id`), then `pilot_id`.

## Admin endpoints (admin token)

| Method and route | Description |
|---|---|
| `GET /admin`, `/admin.js`, `/admin.css` | Web panel |
| `GET /api/admin/sessions` | List and active session |
| `POST /api/admin/sessions` | `{"session_id","name","active":bool}` creates or renames; `active:true` marks it active (only one) |
| `POST /api/admin/sessions/active` | `{"session_id"}` marks it active (404 if it does not exist) |
| `GET/POST /api/admin/pilots` | Lists / registers `{"pilot_id","name"}` |
| `GET /api/admin/assignments[?station_id=]` | `current` and `history` (each row with `previous_pilot_id`, `previous_session_id`, `reassignment`) |
| `POST /api/admin/assignments` | `{"station_id","session_id","pilot_id"}`; returns `assignment_revision` |
| `GET /api/admin/stations` | All configured stations: `online`, `offline`, `last_seen_utc`, assignment, last lap, `detector_ok`, `detector` (latest summary), `calibration_version`, `detector_error_laps`, `sync_error_count` |
| `GET /api/admin/sync-errors[?station_id=]` | Last 200 rejections |
| `GET/POST /api/admin/calibrations` | Provenance of an inspected calibration `{"station_id","track_config","reference","operator","active"}` |
| `GET /api/admin/laps[?session_id=]` | Laps with client metadata (`lap_number`, `detector`, `sync`, `client_timestamp`, `calibration_version`, `client_status`, `client_corrected_ms_ignored`) |
| `POST /api/admin/reviews` | `{"station_id","lap_id","decision":"approve"|"reject","reviewer","reason"}`. Approval requires an armed server, detector OK, verified calibration and an active admin calibration |
| `POST /api/admin/penalties` | `{"station_id","lap_id","events":{...},"reviewer","reason"}`. Penalty keys only (`cone`, `off_course`, `slalom` for endurance; `doo`, `oc` for autocross/skidpad). Merged with the stored events; the server recalculates with `scoring.corrected_ms` and records it in `reviews` (`kind=penalties`, `before_events`, `after_events`, `before_corrected_ms`, `after_corrected_ms`). **Never changes the status**: `corrected_ms` only becomes official if the lap was already VALID; otherwise the response carries `corrected_preview_ms` and `official:false` |
| `GET /api/admin/reviews?session_id=`, `GET /api/admin/reviews.csv?session_id=` | Full audit |
| `GET /api/admin/export.json[?session_id=]` | Full export: sessions, drivers, laps, reviews, assignments, history, calibrations, sync errors, rules and penalties |
| `GET /api/admin/export.csv?table=T[&session_id=]` | `T` in `laps` (default), `reviews`, `assignment_history`, `assignments`, `calibrations`, `sessions`, `pilots`, `sync_errors`. Cells starting with `= + - @` are prefixed with `'` (formula injection protection) |
| `GET /api/admin/diagnostics` | Scoring status, rules, schema version, journal mode, counts, stations, recent backups |
| `POST /api/admin/backup` | Consistent copy via the SQLite backup API into `<DB folder>/backups/`, verified with `integrity_check` |

## Persistence, migrations and recovery

- SQLite in WAL mode, 10 s `busy_timeout`, one connection per operation.
- Idempotent migrations controlled by `PRAGMA user_version` (current schema: 2). A legacy database without a version is migrated when opened; a database with a higher version than the server's is rejected.
- After a restart, client resends stay idempotent because deduplication lives in the database.

## Backup and restore (server stopped)

```
python3 server/app.py --db trials.sqlite --check                      # integrity_check of the current database
python3 server/app.py --db trials.sqlite --restore backups/archivo.sqlite
```

`--restore` validates the source file (`PRAGMA integrity_check`, required tables, schema version not higher), saves a prior copy in `backups/<db>-pre-restore-<date>.sqlite`, copies through the backup API to a temporary file, verifies it, replaces atomically, applies migrations and verifies again. It prints JSON with the source `sha256`. Exit code 2 on failure (the current database stays intact).
