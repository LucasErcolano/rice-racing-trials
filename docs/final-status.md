# Estado de la entrega V2 de campo

## Alcance autorizado

Velocity confirmó CSP 2.11, instalación de nuestra app Lua, LAN entre estaciones y autorización para ejecutar nuestro servidor y leaderboard en la TV. **No se requiere integrar el leaderboard de Velocity.** La pre-release V2 es para pruebas reales y no queda bloqueada por esa integración ni por calibraciones pendientes.

**`Dummy.kn5`: VALIDADO EN JUEGO por el usuario.** No se distribuye el archivo de terceros. Se conserva la herramienta que genera y aplica el parche desde el material local del operador.

## Entregado

- Dos journals A/B, payloads, colas, reintentos y endpoints separados: captura nativa de AC y resultado penalizado con evidencia local. Un error en los detectores no debe bloquear la captura o el envío nativo.
- Identidad estable `(station_id, session_id, lap_id)` respaldada por el journal. Reintentos byte a byte, reconciliación en ambos órdenes, una sola fila por vuelta y conflictos persistidos para revisión.
- Nativa sola: bruto provisional con `PENALTIES_PENDING`. Penalizada: resultado calculado por servidor desde eventos y ajustes locales, separado del bruto y marcado pendiente de revisión mientras no esté verificado.
- Verificación administrativa de campo autenticada, con motivo, hash de la evidencia y auditoría persistente. Permite mostrar una corrección verificada **para pruebas**, sin convertirla en resultado oficial. Los flags del cliente nunca verifican una vuelta por sí solos.
- Monitor propio `/v2/field`, auto-refresco, export JSON y estado de cada canal. `/admin`, `/tv` y el ranking V1 mantienen su comportamiento previo.
- Conos: el contacto geométrico suficiente sigue penalizando, como en V1. La condición OR de contacto o trayectoria próxima no puede borrar ese contacto. Los gates de ruta no calibrados se omiten; siguen disponibles los canales independientes de conos, bordes y slalom.
- Instalador Windows con `-ProtocolV2`, servidor portable con `-EnablePenaltiesV2` y persistencia de ese flag al registrar la tarea. V1 sigue siendo el default.
- Smoke de procesos y HTTP real para fuente y exe compilado, pruebas de cliente Lua, servidor, geometría, navegador y empaquetado. Los resultados de cada build Windows se publican como evidencia de GitHub Actions.

## Límites explícitos

Las dos colas son independientes, **no** dos procesos, equipos o runtimes: ambos productores comparten AC/CSP y el disco. La caída de AC, CSP, la estación o el almacenamiento puede afectar a ambos. La recuperación por journals no prueba tolerancia a un corte físico de energía.

La V2 de campo no agrega vueltas provisionales al ranking oficial: `official=false` y `corrected_ms=null` incluso después de una verificación manual de campo. La cifra corregida se muestra en `corrected_preview_ms` (alias `preview_ms`) con su estado. La validación deportiva y la calibración en pista siguen pendientes, pero no bloquean instalar esta pre-release.

Pendiente de comprobar durante las pruebas: comportamiento real de los journals y APIs CSP 2.11, HUD, detección de conos/bordes/slalom, recuperación de red, concurrencia de estaciones y calibración física de gates. **No se declara que V2 completa ya fue validada en juego.** La confirmación de `Dummy.kn5` sí queda registrada como validación del usuario.

## Instalar y probar

Ver [guía de campo V2](v2-field-testing.md). Recomendado: puerto y SQLite exclusivos de V2 para no tocar la V1 operativa. Los ZIP instalables se generan en Windows; el paquete completo incluye el servidor compilado y el cliente, pero no Assetto Corsa, CSP, auto, pista, KN5, credenciales ni bases de datos.
