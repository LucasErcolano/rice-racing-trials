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

## Penalizaciones automáticas (off-course y slalom, `auto_penalties = true`)

`export-cones` ahora emite en `cone_config.lua` tres detectores geométricos. Con `auto_penalties = true` llenan `events` de una vuelta `PENDING_REVIEW` (claves `cone`, `off_course`, `slalom`). Nunca se vuelve oficial sola: el scoring sigue desarmado y el admin puede editar cada conteo. `--no-auto` los deja solo como candidatos en `payload.detector`.

| Detector | Regla | Tolerancia (por defecto) |
|---|---|---|
| Conos | caja del auto reducida 0,15 m por lado contra el cono | penetración ≥ 0,03 m; máx. 25 por vuelta |
| Off-course | las 4 ruedas fuera del anillo de asfalto (`IS_VALID_TRACK`) | margen 0,30 m más allá del borde, 0,15 s sostenido; un evento por salida, rearmado tras 0,5 s adentro |
| Slalom | cruce de cada compuerta (perpendicular a la marcha, por el pilón) del lado esperado por la línea AI de referencia | una infracción por compuerta; `half_m` 5,5 m |

Reglas anti falsos positivos:
- Conos que la propia línea AI atraviesa (21 en FSAEJ, pilones de boxes y salida) quedan como candidato, no suman a `events.cone`.
- Un detector sin configurar omite su clave: nunca se informa 0 por "no sé".
- Una pasada de slalom con compuertas sin cruzar es *incompleta*: se reporta en `detector.slalom_incomplete` y no se penaliza.
- Dato inválido, `dt` fuera de rango o frame sin orientación devuelve `unknown`. Sin `look` se usa la dirección de marcha.
- Solo cuenta con la vuelta cronometrada en curso; la outlap se descarta.

Slalom en FSAEJ_Endurance: se detectaron 2 pasadas de 4 y 5 pilones (S1 cerca de x 56..84; S2 en x -21..15). Una tercera (x -103..-75) se descartó porque la línea AI casi la atraviesa (confianza 0,13 m): requiere revisión manual.

**Sin certificar**: todo se calibró en simulación con la línea AI, no con manejo real. Hay que validar con una vuelta limpia (esperado 0 off-course, 0 slalom, ~2 conos) y otra con infracciones deliberadas.
