# Velocity: alcance de una integración de tiempos

Consulta realizada el **21 de octubre de 2026**, hora local UTC-03. Revisión de código sobre `feature/server-penalties-v2`, HEAD `03b4958`, con cambios de otros agentes en curso. Este documento describe el estado observado, no certifica una instalación de campo.

## Confirmación del operador para las pruebas V2

Velocity confirmó al usuario CSP 2.11, autorización para instalar nuestra app Lua, conectividad LAN entre estaciones y permiso para ejecutar nuestro servidor Rice y leaderboard en la TV. La V2 usa estos recursos y NO depende de integrar ni reemplazar el leaderboard de Velocity. La falta de identificación de una API externa no bloquea la publicación ni las pruebas. Compatibilidad exacta del cliente con ese runtime: pendiente de la primera sesión de campo.

## Investigación histórica de API externa (fuera de alcance de V2)

**No está confirmada una API directa de tiempos del producto que el proyecto llama “Velocity”. Una eventual integración externa necesitaría proveedor y versión exactos; no es una dependencia ni un bloqueo de la V2.**

El contexto recibido no identifica fabricante, sitio oficial, edición, versión ni manual del producto instalado. La búsqueda del repositorio y de su historial disponible no encontró una referencia que resuelva esa identidad. Las coincidencias con `velocity` en vectores de telemetría no identifican un proveedor.

No corresponde elegir una API por similitud de nombre, inventar endpoints ni confundir soporte para Assetto Corsa con autorización para leer o escribir los tiempos de este proyecto. Tampoco se confirmó que Velocity permita importar el tiempo final penalizado de Rice, obtener cada vuelta en tiempo real, recuperar vueltas tras una caída o funcionar sin Rice/CSP.

## Investigación pública, sólo fuentes primarias

Se inspeccionaron páginas públicas de los propios proveedores/operadores. Los resultados de terceros aparecidos en búsquedas generales no se usaron como evidencia técnica. No se contactó a proveedores, no se ingresó a cuentas, no se usaron credenciales y no se consultó la VM.

| Fuente oficial consultada | Lo que efectivamente permite afirmar | Lo que no demuestra |
|---|---|---|
| Velocity Pro Sims, portada | Anuncia una plataforma propia de timing/scoring, registro de participantes y leaderboard.[1] | No identifica el producto instalado en Rice ni aporta en la página consultada un contrato de API para integrarlo. |
| Velocity Sim Racing Lounge, página de 610 Challenge | Describe su experiencia de carreras virtuales y tiempos de vuelta.[2] | No establece que sea el proveedor de Rice ni documenta allí una interfaz de extracción/importación. |
| Sim Racing Limited, VMS API Reference v0.1 | Documenta consulta de vueltas y exportación de telemetría, autenticación por API key y permisos según licencia.[3] | **No establece que VMS sea Velocity.** Sus rutas y credenciales no deben trasladarse a una supuesta API Velocity. |
| Sim Racing Limited, Downloads | Publica soporte de Assetto Corsa y recolección de vueltas/sectores/telemetría/resultados en su propio producto.[4] | Compatibilidad de VMS con AC no prueba identidad, instalación, licencia ni compatibilidad de Velocity con Rice. |

La documentación API de Sim Racing Limited se recuperó desde `/docs/`, que redirigió a `/docs/v0.1/`. Es una fuente de contraste para descartar una atribución equivocada, **no una API elegida para implementar**.[3]

En particular, el marketing de una plataforma de tiempos no equivale a una API pública. Tampoco la ausencia de documentación encontrada prueba que no exista una API privada, un SDK, una exportación autorizada o un producto diferente con el mismo nombre.

### Registro de búsquedas

Consultas ejecutadas literalmente en esta revisión:

```text
"Velocity" "Assetto Corsa" timing API
"Velocity" "sim racing" "API"
"Velocity" "timing software" "simulator"
"Velocity" "Assetto Corsa" "timing" software
site:velocityprosims.com "API" "timing"
site:velocitysimlounge.com "API"
"Velocity" "Assetto Corsa" "management software"
"Velocity" "lap timing" "API"
```

Las consultas generales devolvieron principalmente usos de “velocity” como velocidad física, otros productos y páginas comerciales. La consulta restringida a `velocityprosims.com` no devolvió resultados; la de `velocitysimlounge.com` devolvió páginas del local, no una especificación API identificada. Ninguna consulta estableció el proveedor/versión usado por Rice. Es un relevamiento acotado de contenido público, no una búsqueda exhaustiva ni evidencia de inexistencia.

La extracción web configurada falló por ser un backend sólo de búsqueda. Se recuperaron las cuatro fuentes oficiales con HTTP GET público usando `curl -L --fail`, y se leyó el HTML convertido a texto. No se ejecutaron ejemplos de API ni solicitudes autenticadas.

## Qué hace falta para desbloquear la integración

Estos son requisitos de diseño y aceptación, **no capacidades atribuidas a Velocity**:

1. **Identidad:** nombre comercial completo, proveedor y sitio oficial, edición/licencia y versión/build. Sirve una captura sin datos sensibles de “About/Acerca de”, o el enlace al manual del producto exacto. No hace falta compartir contraseñas ni tokens.
2. **Contrato oficial de la interfaz:** API/SDK/exportación soportada para esa versión; dirección del flujo (leer vueltas, escribir resultados o ambas); formatos, unidades, IDs, permisos, límites, retención, paginación y recuperación histórica. No asumir REST, webhooks, CSV ni acceso directo a su base.
3. **Compatibilidad concreta:** Assetto Corsa original, modo usado por las estaciones, pista/layout FSAEJ y auto MFT02. Confirmar el origen del cronometraje y cuándo publica una vuelta, cómo trata outlap, vuelta inválida, reset, pausa y cambio de piloto.
4. **Identidades y tiempo:** mapear estación, piloto, sesión, pista/layout, vehículo y vuelta estable; distinguir duración en milisegundos de timestamp y zona horaria. Resolver cambios de asignación y evitar atribuir vueltas antiguas al piloto nuevo.
5. **Semántica de scoring:** conservar tiempo bruto nativo, validez nativa y tiempo penalizado Rice por separado, con procedencia. Una bandera de validez nativa no acredita conos/off-course/slalom FSAE. Una vuelta externa sin evidencia no se vuelve oficial automáticamente.
6. **Fiabilidad y aislamiento:** definir deduplicación, ACK, reintento, persistencia, replay tras caída, conflictos y autoridad de cada fuente. Si se exige sobrevivir a una falla del Lua de Rice, la captura debe ocurrir fuera de ese mismo runtime, con supervisión y almacenamiento propios. Otro proceso no elimina por sí solo la dependencia del juego o del equipo.
7. **Validación posterior autorizada:** ejecutar y documentar vuelta limpia, inválida, outlap, reset, cambio de piloto, desconexión/reconexión, reinicio, duplicados y replay histórico. Verificar el registro persistido, no sólo el HTTP 200 o el HUD. Obtener acceso autorizado por un canal seguro recién cuando exista una interfaz concreta.

Decisión actual: mantener Velocity como integración pendiente. Con identidad y contrato confirmados, evaluar un adaptador; si sólo hay exportación documentada, evaluar importación diferida. No prometer sincronización bidireccional o tiempo real antes de esas verificaciones.

## Respaldo nativo Lua: independencia real y límites

### Implementación observada

Fuentes locales inspeccionadas: `client/native_laps.lua`, `client/journal.lua`, `client/RiceTrials.lua` y `client/tests/test_native_laps.py`. El historial contiene `b1ea32c`, que agregó el respaldo nativo local.

- `native_laps.lua` observa incrementos de `ac.getCar(0).lapCount`; toma `previousLapTimeMs` e `isLastLapValid` si existen, junto con IDs de contexto y momento de captura. La primera lectura y las bajadas del contador son referencia, no vueltas nuevas.
- Guarda borradores en un journal **distinto** del de la cola principal: `native_laps.a.json` / `native_laps.b.json`, bajo el mismo directorio de estado. Usa el mismo módulo `journal.lua`, ranuras A/B, secuencia, checksum y lectura de confirmación. Hay respaldo degradado en `ac.storage` cuando no hay modo archivo; no equivale a la garantía del journal en disco.
- En el código observado, son borradores locales: no se suben a `/api/laps` y no hay transporte HTTP propio en `native_laps.lua`. La asociación con una vuelta de ruta usa sesión interna y cercanía de tiempo bruto, con tolerancia de 2000 ms; es una heurística local, no un identificador externo inequívoco.
- Conserva hasta 500 borradores; al recortar prefiere quitar uno reconciliado y, si no hay, quita el más antiguo. No es un archivo histórico ilimitado.
- Si el contador salta varias vueltas, captura la última disponible y marca cuántas faltaron; no reconstruye los tiempos intermedios. Un tiempo/campo ausente puede quedar sin valor. Lo no observado durante una caída del Lua no se recupera mágicamente al cargarlo otra vez.

### Matriz de independencia

| Falla o dependencia | Alcance del respaldo observado |
|---|---|
| Detector geométrico no completa una vuelta | Puede conservar la vuelta nativa si AC incrementa el contador y el callback sigue ejecutándose. |
| Cola/journal principal | Sus archivos y colección son separados. Esto no aísla fallas comunes de biblioteca, directorio, permisos, disco o proceso. |
| Red/servidor Rice caído | La captura local no requiere un ACK de red, pero tampoco entrega esa vuelta al servidor por sí sola. |
| Error Lua | Hay `pcall` alrededor de carga/observación y algunas operaciones. No hay aislamiento total: antes de observar la vuelta, el mismo `script.update` ejecuta otras funciones. Un error previo puede impedir la observación. |
| App Rice deshabilitada, no cargada, bloqueada o runtime CSP detenido | No captura nuevas vueltas. Corre dentro de la misma app, callback y runtime; no es otro proceso ni otro juego. |
| AC detenido o colgado, equipo/disco caído | Comparte ese dominio de falla. No es un cronómetro externo independiente. |
| Reinicio después de escritura confirmada | El diseño conserva el registro confirmado; no garantiza rescatar una vuelta que nunca se guardó. No ofrece garantía ante corte eléctrico: no hay `fsync` en esta implementación. |
| Penalizaciones/evidencia | No captura por sí solo evidencia de conos, off-course o slalom. No reemplaza el análisis V2 ni habilita scoring oficial. |

**Separar la cola o agregarle un envío propio mejora el desacople lógico y los reintentos, no crea un proceso separado, otro juego ni otro runtime.** Tampoco convierte ese flujo en una integración Velocity. Un receptor y una cola nativa del lado servidor requieren su propia validación y no se certifican aquí. La integración de envíos paralela debe actualizar la descripción de transporte anterior cuando se revise y pruebe; la limitación de aislamiento permanece.

### Verificación ejecutada

Desde `client/tests`, se ejecutó:

```text
/home/lucas/rr-eval/venvs/rice-tests/bin/python -m unittest test_native_laps -v
Ran 11 tests in 0.026s
OK
```

Incluye persistencia simulada entre runtimes Lua, deduplicación básica, reinicio de contador, valores ausentes/inválidos, falla de escritura y reconciliación. La prueba de integración confirma que el código observado no publica el borrador nativo a `/api/laps`. Son pruebas Linux con Lupa y dobles de CSP/almacenamiento/red, no una ejecución de AC/Windows ni una prueba de Velocity. No se ejecutó ninguna prueba de campo o contra infraestructura del usuario en esta investigación.

## Sources

[1] https://www.velocityprosims.com | Velocity Pro Sims: sitio oficial
[2] https://velocitysimlounge.com/blog/houston-610-sim-race | Velocity Sim Racing Lounge: 610 Challenge
[3] https://api.simracing.co.uk/docs/v0.1 | Sim Racing Limited VMS API Reference
[4] https://www.simracing.co.uk/downloads.php | Sim Racing Limited: Downloads
