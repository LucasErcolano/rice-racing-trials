# V2: pruebas de campo, resultados provisionales

Esta versión es opt-in y no reemplaza ni despliega la V1 operativa. No está lista para competencia oficial. Usar una base SQLite, puerto y directorio separados para V2. El scoring oficial permanece desarmado.

## Instalación breve en Windows

1. Descargar el ZIP **complete-win64**, verificar su SHA-256 con `Get-FileHash -Algorithm SHA256` contra `SHA256SUMS.txt` y extraerlo a una carpeta nueva. No ejecutar desde el ZIP.
2. Mantener la instalación V1 y respaldar la app antes de instalar. Cerrar AC antes de cambiar la app para no perder una vuelta por hot reload.
3. Preparar credenciales DPAPI con `configure-secrets.ps1` como indica `deployment.md`; el archivo de credenciales y la base deben estar fuera del directorio de release. Nunca enviar tokens en reportes ni capturas.
4. Lanzar una instancia de pruebas, con base nueva y puerto separado:

```powershell
.\start-server.ps1 -SecretsPath 'C:\RiceV2Data\secrets.xml' -DatabasePath 'C:\RiceV2Data\field.sqlite' -Port 8082 -EnablePenaltiesV2
```

Para registrar una tarea de pruebas usar un `-TaskName RiceTrialsV2Field` separado y pasar `-EnablePenaltiesV2` a `server-admin.ps1 -Action RegisterTask`. No modificar la tarea de V1.

5. Crear sesión, piloto y asignación de pruebas en `/admin` de esta instancia. Instalar el cliente con backup explícito:

```powershell
.\install.ps1 -AssettoRoot 'C:\...\assettocorsa' -StationId ST01 -ServerUrl 'http://<IP-LAN-del-servidor>:8082' -BackupDirectory 'C:\RiceV2Data\backups' -SkipArchiveHashes -RouteConfig '.\track-config\FSAEJ_Endurance.route_config.lua' -ConeConfig '.\track-config\cone_config.lua' -EnableDiagnostics -ProtocolV2
```

`-SkipArchiveHashes` omite la verificación de origen de los mods instalados; no valida sus bytes. V2 está apagado por defecto sin `-ProtocolV2` y sin `-EnablePenaltiesV2`. Nunca copiar `station_config.lua` a un ZIP ni a GitHub. La pista y el auto se obtienen aparte, no están incluidos.

## Protocolo OR y revisión

Los canales nativo y penalizado tienen persistencia y reintentos separados. El canal nativo no depende de que el análisis o envío de penalizaciones funcione. Una nativa sin evidencia de penalizaciones se conserva como `PENALTIES_PENDING`, nunca se interpreta como cero penalizaciones. Las vueltas se reconcilian por identidad estable, no por cercanía de tiempos. Contradicciones, evidencia incompleta o identidad ambigua requieren revisión manual. Los tiempos calculados son previsualizaciones provisionales, no clasificación oficial.

La independencia es de colas y transporte dentro del cliente: ambos canales comparten AC, CSP, la app, el disco y el equipo. Si ese runtime no captura la vuelta, no hay un segundo cronometraje externo garantizado. El journal nativo es el fallback local disponible, no una integración confirmada con Velocity.

Cada fila trae `penalty_evidence_state` (columna "Penalizaciones" en la TV) y `disabled_penalties`:

| Estado | Significado |
| --- | --- |
| `ABSENT` | Sólo llegó la nativa. Penalizaciones pendientes, nunca cero. |
| `INCOMPLETE` | Llegó evidencia pero falta o es inválida (detector, calibración, eventos sin resolver). |
| `ZERO_INCIDENTS` | Evidencia completa y sin incidentes penalizables. |
| `PENALIZED` | Evidencia completa con penalización calculada en el servidor. |
| `PENALTIES_DISABLED` | Hubo incidentes registrados, pero su penalización estaba deshabilitada. La evidencia se conserva. |
| `CONFLICT` | Los mensajes se contradicen. Va a revisión manual sin tiempo corregido. |

## Pantalla propia y verificación de campo

Abrir `http://<IP-LAN-del-servidor>:8082/v2/field` en la TV. Ingresar el token admin de la instancia V2 (no va en la URL). Se conserva sólo en memoria. La pantalla actualiza cada 3 segundos y muestra una única fila por estación/sesión/vuelta, bruto nativo, previsualización corregida y razones de revisión. `/admin` y `/tv` conservan la V1 y NO son el monitor provisional. Los paquetes incluyen rutas y conos de Endurance para empezar; pueden ajustarse durante las pruebas. No hace falta una API de Velocity.

La previsualización se recalcula en el servidor desde eventos y settings, incluso sin calibración final. No equivale a evidencia validada: ningún `detector_ok` o `calibration_verified` enviado por el cliente verifica la vuelta por sí solo.

Después de inspeccionar la evidencia exportada, el operador puede dar una **atestación manual de campo**, sin habilitar clasificación oficial:

- `POST /api/admin/v2/field-review` con Bearer admin y JSON `{"station_id":"ST01","session_id":"S1","lap_id":"<id-exacto>","approved":true,"reviewer":"operador","reason":"Motivo concreto de la revisión de la evidencia"}`.
- Pasa a `VERIFIED_PROVISIONAL`, `evidence_verified=true`, conserva `corrected_ms=null`, `final_ms=null` y `official=false`. La pantalla muestra la corregida mediante `preview_ms`. Esto certifica sólo la revisión humana declarada, no una calibración física automática.
- `approved:false` revoca la atestación. Cada decisión persiste con fecha, motivo, revisor y SHA-256 del mensaje penalizado exacto. Historial: `GET /api/admin/v2/field-reviews?station_id=ST01&session_id=S1&lap_id=<id-exacto>`.
- Una nativa sin penalizaciones no se puede verificar. Las inconsistencias invalidan cualquier atestación y permanecen en revisión, sin previsualización ni promoción oficial.

Exportación completa: `GET /api/admin/v2/field-export.json` o `.csv`, con Bearer admin. Los dos mensajes originales y la evidencia se conservan. No se deducen ceros de penalización de evidencia ausente.

## Diagnóstico y reporte

Conservar el directorio `apps\lua\RiceTrials\state` y los logs de diagnóstico del cliente, además de los logs del servidor en el directorio de datos externo. Antes de compartir, quitar `station_config.lua`, credenciales DPAPI, tokens y datos personales. Para un bug registrar estación, sesión, piloto, circuito, lap_id, bruto nativo, bruto del detector, eventos locales, configuración de penalizaciones, estado de las dos colas y hora. Exportar las vueltas provisionales con la evidencia almacenada. No editar manualmente los journals A/B.

## Rollback

Detener sólo la tarea V2 con `server-admin.ps1 -Action Stop -TaskName RiceTrialsV2Field -Port 8082` y los parámetros de datos de esa instancia. No matar únicamente el exe: el supervisor lo vuelve a levantar. Con AC cerrado, restaurar el backup de app mediante `install.ps1 -Rollback '<backup>' -BackupDirectory '<carpeta existente>'`. Mantener la base de pruebas como evidencia; no abrirla con V1 ni sobrescribir la base operativa. No se modifican archivos de pista, auto o CSP.

## Gates de validación

`Dummy.kn5`: validado en AC por confirmación explícita del usuario. Esto no valida los detectores V2 ni la calibración física. Las pruebas automatizadas Linux y Windows no sustituyen una sesión real CSP. La calibración final no bloquea recopilar vueltas, pero sí su uso oficial. Ver `test-report.md`, `windows-validation.md` y `velocity-integration.md` para evidencia y límites de esta entrega.
