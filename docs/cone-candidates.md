# Candidatos de conos (cone-boxes-v1)

Detección geométrica **asesora**. Un contacto es un *candidato* para el revisor, nunca una penalidad oficial.

- Caja orientada del auto (collider MFT02: 1,41 x 3,0 m) reducida `shrink_m` por lado (default 0,15 m) contra el
  centro de cada cono (radio 0,125 m, penetración mínima 0,03 m). Se interpola cada 0,1 m entre frames, así que
  no atraviesa conos a alta velocidad; un salto mayor a 20 m (reset/teleport) no barre la pista.
- Solo cuenta con la vuelta cronometrada en curso (se descarta la outlap y se reinicia con cada reset de ruta).
  Cada cono cuenta una vez por vuelta.
- Sale en `payload.detector` (`cone_source=geometric_candidate_uncertified`, `cone_candidates`, `cone_candidate_count`,
  `cone_shrink_m`) y se ve en /admin. `events` queda `{}`: no hay penalidades automáticas.
- 730 centros de cono salen de los clusters del KN5 (candidatos no confirmados; algunos pueden ser pilones de pits o decorativos).

## Generar e instalar
```
python3 track/tool.py export-cones --geometry dist/track-calibration/geometry/FSAEJ_Endurance.geometry.json \
    --output dist/track-calibration/route/cone_config.lua [--shrink 0.15]
.\install.ps1 ... -RouteConfig route_config.lua -ConeConfig cone_config.lua
```
Sin `cone_config.lua` el cliente se comporta como antes. Para desactivarlo en una estación, borrar ese archivo.

## Calibración pendiente (requiere conducción real)
`shrink_m` es una heurística. Falta: conducir una vuelta limpia (esperado 0 candidatos) y otra tocando conos
a propósito, y ajustar `--shrink` o `--min-depth` según el resultado. Hasta entonces el valor es **no verificado en juego**.
