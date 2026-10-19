# Auditoría del ZIP suministrado

## Alcance y método

Fuente original (no modificada): `/home/lucas/.hermes/attachments/Rice_Driver_Trials_v1.0_Hardware_Human_Handoff.zip`, 3 161 198 bytes; SHA-256 **46b33cf4085d7e4e8f003010fc3e75903596601531e7d047f20ec026c27f7574**. Se recorrieron las 755 entradas mediante `zipfile`, se descomprimieron para `testzip()` (sin error), se buscaron miembros `.zip` y firmas ZIP `PK\x03\x04` al inicio de cada miembro (ninguno), y se comprobaron nombres duplicados (ninguno). Por tanto **no hay ZIP anidado dentro de este archivo**; los `downloads/FSAEJ_*.zip` mencionados en configuración son referencias externas, no miembros. Esta comprobación no demuestra ausencia de contenedores incrustados en posiciones no iniciales ni prueba seguridad de contenido ejecutable.

Distribución por extensión: 564 JSON, 62 Python, 54 Markdown, 32 INI, 16 PNG, 14 BAT, 5 CSV, 3 LOG, 2 SVG, 2 KN5 y 1 TXT. Directorios principales por cantidad de archivos: `commissioning/` 513, `sessions/` 63, `tools/` 40, `tracks/` 29, `docs/` 16, `config/` 13, `analysis/` 9, `tests/` 9, `evidence/` 4, `setups/` 2, `telemetry/` 2, `cars/` 1; el resto está en raíz. No se extrajo ni sobrescribió el original.

## Qué contiene realmente

- **Código reutilizable:** `trials.py`, `rc_console.py`; nueve módulos `analysis/` (geometría, eventos, tiempos, intentos, protocolo, sesiones), dos `telemetry/`, cuarenta utilidades `tools/` (incluyen `trackgen.py`, `kn5.py`, `public_tracks.py`, `rice_preflight.py`, `operator_runtime.py`, `install_tracks.py`, `validate_fsae_2027_tracks.py`), nueve pruebas `tests/` y 14 lanzadores BAT. Son fuentes, no una ejecución verificada en este entorno.
- **Circuitos incluidos:** dos circuitos *Rice* alternativos completos para Assetto Corsa, bajo `tracks/assetto/RICE_FSAE_2027_SKIDPAD/` y `...AUTOCROSS_V1/`, con `.kn5`, `data/*.ini`, mapas e interfaz. Sus definiciones editables están en `tracks/rice_fsae_2027_*/course_definition.json`; mapas SVG/PNG en `docs/`. Los `.kn5` comienzan con firma `sc6969`, no son ZIP.
- **Circuitos oficiales seleccionados:** `FSAEJ_Skidpad` y `FSAEJ_Endurance` **no tienen modelos KN5 ni carpetas Assetto en este paquete**. Solo existen definiciones medidas `tracks/fsaej_*/course_definition.json`, evidencias de comisionamiento, mapas/imágenes y hashes esperados de archivos externos. El segundo es un circuito de *endurance* 2019 usado como proxy de autocross de una vuelta, no el autocross Michigan 2027.
- **Automóvil:** `cars/` contiene únicamente `VEHICLE_CONTRACT.md`; no hay modelo, física ni sonidos del MAD Formula Team MFT02. `config/FROZEN_TRIAL_CONFIGURATION.json` exige `madformulateam_mft02` versión 1.42, archivo externo SHA-256 `f73a4f992a9e989e6daf0aca24da9c7f61992af4a86385cbd566f824770e78c5`; ese hash es una *declaración registrada*, no comprobable contra un auto ausente.
- **Otros activos:** `commissioning/` incluye informes, JSON de validación, PNG y telemetría CSV de sesiones; `sessions/` contiene tres intentos de comisionamiento y configuraciones; `config/` concentra protocolo/bloqueos; `setups/RICE_TRIALS_V1.ini` es configuración, no vehículo. Los reportes históricos no sustituyen una prueba de hardware y pilotos actual.

## Hashes calculados directamente sobre miembros del ZIP

| Miembro (relativo a la carpeta raíz interna) | SHA-256 |
|---|---|
| `tracks/assetto/RICE_FSAE_2027_SKIDPAD/RICE_FSAE_2027_SKIDPAD.kn5` | `8e385f824f423061c86548c0e21906f352bf46153d855dec2d46b4d834116a63` |
| `tracks/assetto/RICE_FSAE_2027_AUTOCROSS_V1/RICE_FSAE_2027_AUTOCROSS_V1.kn5` | `88c1b0127396c524c0ef156ce0a0cc567da394edbf82cbb80e15060817300880` |
| `tracks/rice_fsae_2027_skidpad/course_definition.json` | `4b9c75f9f18aed0115cefe1a98ac56ca93fc8980e217601937a896578a88d8bb` |
| `tracks/rice_fsae_2027_autocross_v1/course_definition.json` | `ee760d9e41036ff065d1b7e9a6fa6811528b82bb228f137b088d42b803ad0a8f` |
| `tracks/fsaej_skidpad/course_definition.json` | `d2529b793642049539123eb9b6a7937566c1d766cf8f7cf834b7ebf62eaff743` |
| `tracks/fsaej_endurance/course_definition.json` | `277480128706b3019d524f77169a57b10da986ad967c6ddfec39a0d53b2e64a7` |
| `config/FROZEN_TRIAL_CONFIGURATION.json` | `9dd561245eddcb791a3a61468224bcd779b74463a4960cb2c51db0f8ffa6e289` |

Estos SHA-256 son hashes de **bytes de archivos individuales**, no equivalen a los `installed_track_hash` de árboles de instalación registrados en `config/track_lock.json` (`relative_path_size_sha256_v1`/política del proyecto). Tampoco se verificaron las descargas externas ni las afirmaciones de procedencia/licencia contra el editor externo.

## Límites y decisiones para reutilización

`STATUS.md` y la configuración congelada coinciden en `official_enabled=false`, `hardware_commissioned=false`, `human_dry_run_completed=false`; aprobación del protocolo todavía `DRAFT`. `tracks/TRACKS.md` conserva una descripción histórica que llama oficiales a los circuitos Rice, contradicha por la selección posterior en `TRACK_SELECTION_FREEZE.md` y `config/FROZEN_TRIAL_CONFIGURATION.json`: para este handoff los Rice son **FALLBACK_ONLY / NOT OPERATOR_SELECTABLE**, los JSAE son los seleccionados. El informe `COMMISSIONING_REPORT.md` refleja una etapa anterior; no interpretarlo como estado final. Reutilizar análisis/código/mapas y circuitos Rice sujeto a validación; adquirir por separado Assetto Corsa autorizado, MFT02 y archivos JSAE, comprobar hashes y derechos antes de operarlos. No se corrió simulador, no se verificaron dinámicas/FFB ni se declaró listo un ensayo oficial.
