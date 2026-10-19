# Herramienta de análisis de pista (`track/tool.py`)

Python 3 stdlib únicamente; funciona igual en Linux y Windows (`py -3` en Windows). Los archivos del mod son datos no confiables: ejecutar con `python -I` y apuntar `--source` a una copia extraída fuera del repositorio. La herramienta rechaza escribir dentro de la carpeta de la pista.

```sh
python -I track/tool.py extract   --source <copia>/FSAEJ_Endurance --track FSAEJ_Endurance --output dist/track-calibration/geometry
python -I track/tool.py render    --geometry dist/track-calibration/geometry/FSAEJ_Endurance.geometry.json \
                                  --output dist/track-calibration/FSAEJ_Endurance.html --svg dist/track-calibration/FSAEJ_Endurance.preview.svg \
                                  [--telemetry rice_diagnostics.jsonl] [--route route_config.json]
python -I track/tool.py telemetry --geometry <geometry.json> --input <traza.csv|.jsonl> [--route <route_config.json>] [--output informe.json]
python -I track/tool.py export-route --geometry <geometry.json> --output dist/track-calibration/route/FSAEJ_Endurance.route_config.lua \
                                  [--checkpoints puertas.json | --ai-candidates 6] [--approve --operator "Nombre Apellido"]
python -I track/tool.py dummy-patch --source <copia>/FSAEJ_Endurance --track FSAEJ_Endurance \
                                  --output dist/track-calibration/patches/FSAEJ_Endurance-dummy-start-after8 [--nodes AC_START_0 AC_PIT_0]
```

`--source` acepta la carpeta de la pista o el ZIP original (se lee en memoria, nunca se extrae ni modifica).

## Qué extrae (`extract`)

| Capa | Fuente | Estado |
| --- | --- | --- |
| Timing gates | Nodos `AC_TIME_0_L/R`, `AC_START_0`, `AC_HOTLAP_START_0`, `AC_PIT_0` de cualquier KN5 listado en `models.ini` (en FSAEJ están en `Dummy.kn5`). Sentido de cruce inferido de `ai/fast_lane.ai` | Medido del archivo; sentido **no verificado en juego** |
| Límites | Aristas de borde de las mallas físicas cuya superficie (`data/surfaces.ini`, por prefijo de nombre de malla `<n><KEY>`) tiene `IS_VALID_TRACK=1`; simplificadas (Douglas-Peucker, tolerancia `--tolerance`) | Derivado de geometría; si no hay superficies válidas: `no disponible` |
| Conos | Mallas cuyo nombre de nodo o material contiene `pylon`/`cone`. En FSAEJ están **fusionadas**: se agrupan vértices en clusters espaciales (Endurance 758, Skidpad 150 clusters) | Siempre `candidato no confirmado` (no se inventan conos individuales) |
| Línea AI | `ai/fast_lane.ai` (x, z, distancia) | Referencia, no es un límite |
| Slalom | `--slalom <json>` (formato `slalom-gates-v1`, ver `docs/geometry-development.md`) | Si no hay archivo: lista vacía con aviso |

El JSON resultante incluye los SHA-256 de cada KN5 leído (`asset_hash` del `Physics.kn5`) y advertencias explícitas.

## Vista 2D (`render`)

HTML autocontenido (SVG en línea, CSS sin JavaScript externo ni servidor) con capas conmutables: límites, clusters de conos, línea AI, gates nativos, spawns, slalom, checkpoints de ruta y telemetría. Barra de escala en metros; eje X de AC hacia la derecha y Z hacia arriba (proyección XZ).

## Telemetría

- CSV con cabecera que incluya las columnas `t`, `x` y `z` (sin distinguir mayúsculas).
- JSONL con `{"t":..,"x":..,"z":..}` o el formato del diagnóstico del cliente CSP (`client/diagnostics.lua`): líneas `{"t":..,"kind":"sample","pos":{"x":..,"y":..,"z":..},...}`. Las líneas sin posición se ignoran y se cuentan.

`telemetry` informa cruces de la línea nativa (con sentido) y de los checkpoints de la ruta, con la misma convención semiabierta que `client/core.lua` (un punto exactamente sobre la línea cuenta una sola vez).

## Exportar `route_config.lua`

Genera la tabla que consume el cliente (`start` + `checkpoints` con `a`, `b`, `direction`, `label`; más `track_id` y `asset_hash`). Por defecto `verified = false` y `approved_by = nil`: el cliente no arma la ruta. Sólo con `--approve --operator "<nombre>"` se escribe `verified = true`; esto es una **atestación del operador**, no una prueba. Los checkpoints `--ai-candidates N` salen de la línea AI y están marcados como candidatos. Un test de la suite ejecuta la ruta exportada sobre la máquina de estados real de `client/core.lua` (vía `lupa`).

## Dónde quedan los derivados (decisión)

Los JSON/HTML/SVG generados con los activos reales son **derivados de geometría propietaria** del mod. Se guardan en `dist/track-calibration/` (ignorado por git) y sólo viajan en `RiceRacing_Track_Calibration.zip`, de uso interno. En el repositorio sólo se versionan `docs/evidence/track/FSAEJ_Endurance.preview.png` y `FSAEJ_Skidpad.preview.png`: capturas reducidas (675x365 px) que permiten revisar la calibración sin entregar coordenadas utilizables. Nunca se versionan ni empaquetan KN5 originales.

Generados en esta máquina (Linux) con los ZIP de `assets/source/`:

| Pista | Gates | Distancia spawn a línea | Clusters de conos | Límites |
| --- | --- | --- | --- | --- |
| FSAEJ_Endurance | AC_TIME_0_L/R, START, HOTLAP_START, PIT; sentido -1 según `fast_lane.ai` | 19.291 m | 758 clusters candidatos | 3 polilíneas de borde |
| FSAEJ_Skidpad | AC_TIME_0_L/R, START, PIT; sentido supuesto (lado del spawn) | 28.241 m | 150 clusters candidatos | 18 polilíneas de borde |

## Tests

`tests/test_track_tool.py` construye KN5 sintéticos mínimos (sin activos del mod): round-trip byte a byte del escritor, extracción de gates/límites/conos, telemetría CSV y JSONL de diagnóstico, exportación con y sin aprobación, render, candidato de Dummy y la prueba de contrato con `client/core.lua`.
