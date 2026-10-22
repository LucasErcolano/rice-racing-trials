# Cronometraje nativo: análisis reproducible y validación de Dummy.kn5

## Estado confirmado para V2

El usuario confirmó que el `Dummy.kn5` seleccionado ya fue probado en Assetto Corsa y funciona. Se marca como validado en juego por confirmación del usuario, no como una nueva prueba automatizada de esta entrega. Esa validación no comisiona la geometría de penalizaciones ni habilita scoring oficial. Las variantes históricas candidatas descritas abajo conservan sus propios límites y no heredan esta validación automáticamente. No se modifica ni se reinstala la pista en esta entrega.

## Hechos medidos (Linux, sin ejecutar Assetto)

`python3 -m timing.audit assets/source/FSAEJ_Endurance.zip FSAEJ_Endurance --output timing/endurance-native-evidence.json` lee `Dummy.kn5` sin extraer ni modificar la pista. El hash del dummy coincide con `track/calibration/timing-landmarks.json`. Marcadores en coordenadas AC (m): `AC_START_0=(-127.292,1,30.452)`, `AC_HOTLAP_START_0=(-59.698,1,31.651)`, `AC_PIT_0=(-153.783,1,32.611)`; `AC_TIME_0_L=(-108.581,1,42.438)` y `AC_TIME_0_R=(-115.773,1,42.438)`. La distancia plana entre `AC_START_0` y el punto medio de la línea es **19.291 m**: el spawn queda antes de la línea, lo que explica (sin probarlo) una primera vuelta de unos 18 s que AC toma como Best Lap.

`Dummy.kn5` es un archivo separado de 577 bytes listado en `models.ini`; contiene sólo nodos dummy. `track/kn5.py` lo lee y lo vuelve a escribir **idéntico byte a byte** (comprobado sobre los 7 KN5 de Endurance y Skidpad, incluidos `Physics.kn5` y `Ecopa.kn5`).

## Alternativas, de menor a mayor intervención

1. **Sin tocar nada (operación)**: probar Practice desde boxes/starting line y Hotlap con la pista intacta, anotando `LapCount`, `LapTime` y Best Lap. El scoring propio ya descarta el outlap por estado y secuencia de checkpoints (`client/core.lua`), así que el problema afecta sólo a la pantalla nativa.
2. **API CSP**: `ac.markLapAsSpoiled()` está documentada como «works only during the race» (ver `docs/csp-api-reference.md`). No se asume que funcione en Practice: es el paso `mark_spoiled` del Validation Kit. La app Python del autor del mod (`ac.ext_markLapAsSpoiled`) no tiene equivalente Lua confirmado.
3. **Configuración CSP de pista (`ext_config.ini`)**: en la documentación oficial descargada (`acc-extension-config` wiki «Tracks – General options» y `_doc_config_tracks.ini`) **no** aparece ninguna clave para mover `AC_START_*`/`AC_PIT_*` o la línea de tiempo. Lo único relacionado con boxes es `[BASIC] PITBOXES` (cantidad de boxes, no posición). Queda **a investigar**; no se inventan claves.
4. **Parche binario mínimo y reversible de `Dummy.kn5`** (este documento): cambia únicamente la matriz de transformación de `AC_START_0` (y opcionalmente `AC_PIT_0`). Es la menor intervención binaria posible: no toca `Physics.kn5` ni la geometría visual.
5. Mover `AC_TIME_0_L/R` (la línea) se descarta: cambiaría el punto de cronometraje de todas las vueltas y el hash esperado por la calibración.

## Parche candidato

Generado con `python -I track/tool.py dummy-patch` (ver `docs/track-tool.md`) en `dist/track-calibration/patches/` (git-ignorado):

| Variante | Cambio | SHA-256 candidato |
| --- | --- | --- |
| `FSAEJ_Endurance-dummy-start-after8` | `AC_START_0` a 8 m **después** de la línea, sobre la línea AI, orientado según la AI; superficie bajo el nuevo punto: `ASPHALT` válida | `e45a213b51a63b9812229c3ce2498c3d1831b7c2549fb1941a96e25f5fa4babc` |
| `FSAEJ_Endurance-dummy-start-pit-after8` | Lo anterior + `AC_PIT_0` a 16 m después de la línea | `dc60ea332eb5c3c98bc324ff3e9dabae4409b339506fe2045906c0b9e7ee14fb` |

Original esperado: `Dummy.kn5` de 577 bytes, SHA-256 `737f3f2676656c25f3a07f83981bea336810ee6d0cba4854cf55148cef2864d1`. El original **no** se copia en el paquete (es del mod); sólo se registra su hash.

Fundamento: si el auto aparece ya pasada la línea, la primera vuelta que AC cierra es una vuelta completa, en vez de un tramo de ~19 m. Se descartó poner el spawn más atrás (antes de la línea): seguiría generando un tramo corto cronometrado (~1 s) en lugar de eliminarlo.

Verificación hecha en Linux (en `manifest.json` de cada variante): round-trip del original byte a byte; el candidato se vuelve a parsear; los bytes que difieren están todos dentro de las matrices de los nodos elegidos; las nuevas posiciones coinciden; los demás landmarks no cambian; mismo tamaño (577 bytes).

**No verificado** (sólo se confirma en juego): que AC use la nueva posición en Practice, que desaparezca la vuelta de ~18 s, que la primera vuelta completa se registre bien y el efecto sobre checksums de servidores online.

## Aplicar y revertir (Windows, AC cerrado)

```powershell
cd <carpeta de la variante>
powershell -ExecutionPolicy Bypass -File .\Apply-DummyPatch.ps1 -AcRoot 'C:\...\assettocorsa' -Status
powershell -ExecutionPolicy Bypass -File .\Apply-DummyPatch.ps1 -AcRoot 'C:\...\assettocorsa' -Apply
powershell -ExecutionPolicy Bypass -File .\Apply-DummyPatch.ps1 -AcRoot 'C:\...\assettocorsa' -Revert
```

`-Apply` exige que el `Dummy.kn5` instalado tenga el SHA-256 original, guarda un backup verificado en `content\tracks\FSAEJ_Endurance\RiceTrials_backup\Dummy.kn5.<sha256>.bak` y verifica la copia del candidato (si falla, restaura). `-Revert` restaura el backup verificando su hash. Probado con `pwsh` 7.4.6 en Linux contra un árbol de AC simulado (apply, revert, rechazo ante hash distinto); no probado en Windows.

Procedimiento de prueba: paso `dummy_candidate` del Validation Kit (`docs/windows-validation.md`). El archivo JSON de evidencia no activa scoring ni calibración.
