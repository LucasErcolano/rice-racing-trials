# Operator guide: station dry-run

**Commissioning only** until Rice authorizes the final protocol, physical rig and official-mode release. Do not manually edit configuration, setup, track or result files. The historical handoff ZIP is reference material, not proof that this newly packaged application is ready.

1. At startup, check that the wheel and pedals are safe and calibrated, emergency stop works, AC/CSP and the approved external MFT02 and JSAE course content are installed, disk space is sufficient and the recovery drive is connected. If any preflight or freeze/hash check fails, **stop** and call the technician. Do not bypass it.
2. Launch **Rice Trials** from the desktop shortcut. Enter an assigned driver ID (never a real name in result folders). Choose **Skidpad** or **Autocross**. Confirm the current baseline and that this is a commissioning run. Start the restore/preflight flow and wait until the application says to drive.
3. For Skidpad, follow the course entry, right/right then left/left laps and exit. For Autocross, complete one timed course lap from the staged start. Keep focus on the simulator while driving. Use **Stop capture** if the run cannot finish; do not delete a failed attempt.
4. After capture, have an independent human reviewer record cones down/out (DOO), off-course (OC) and missed slaloms where applicable, reviewer ID and evidence/reason. Automatic detections are advisory. Mark a DNF/incomplete attempt invalid. Save the reviewed result and check that a report is generated; never invent a missing time or reclassify synthetic data as real.
5. Before the next driver, confirm the attempt is saved and start a fresh driver session. At the end, export results with checksums, confirm backup/restore on the approved storage, then close the application gracefully. Preserve raw timing, telemetry and audit history.

**If an attempt or station fails:** stop capture, secure the rig, preserve the attempt and logs, and ask the technician to run the documented recovery/preflight process. Never repeatedly restart or alter frozen files to make a check pass. A wheel safety issue, repeated failure or unexpected hash mismatch ends the session pending review.

## Servidor, pantalla TV y panel de administración

Referencia técnica completa: `docs/server-api.md`.

**Estado de scoring.** El servidor se entrega desarmado (fail-closed): ninguna vuelta pasa a VALID ni entra al ranking hasta que Rice comisione el scoring oficial. La pantalla TV lo indica con el banner "SCORING NO COMISIONADO: resultados provisionales, no oficiales" y no muestra ranking; los tiempos brutos aparecen sólo en "Últimas vueltas" marcados "(prov.)". No hay que intentar evitarlo.

**Pantalla TV (`http://servidor:8080/tv`).** Abrir en el navegador del televisor a pantalla completa (F11). Muestra por defecto la sesión marcada activa, sin tipear nada. Columnas: posición, piloto, mejor vuelta corregida, tiempo bruto, penalidades, diferencia con el líder y cantidad de vueltas válidas. Arriba a la derecha: estado de conexión del navegador con el servidor (verde "Conectado", rojo "SIN CONEXIÓN" con la hora del último dato) y hora de la última actualización. A la derecha: estaciones (en línea o DESCONECTADA con la hora del último heartbeat) y conteo de vueltas pendientes de revisión. Se actualiza sola cada 5 segundos.

**Panel de administración (`http://servidor:8080/admin`).** Sólo en una computadora de confianza. Pegar el token admin en "Token admin" y pulsar **Entrar**: queda guardado sólo en esa pestaña y se borra con **Salir** o al cerrarla. Flujo típico:

1. **Sesiones:** escribir ID y nombre, dejar tildado "Marcar activa" y pulsar **Crear / renombrar**. Otra sesión puede activarse luego con **Marcar activa** en la tabla. "Sesión de trabajo" elige qué sesión se revisa y exporta.
2. **Pilotos:** ID (código, nunca nombre real en carpetas de resultados) y nombre; **Registrar**.
3. **Asignación de estaciones:** elegir estación y piloto; **Asignar a la sesión de trabajo**. El historial resalta en color cada reasignación con el piloto anterior.
4. **Estaciones y detectores:** conexión, último heartbeat, última vuelta, estado y resumen del detector, errores de sincronización. Una estación DESCONECTADA por más de 90 s: revisar red y cliente.
5. **Calibración:** registrar la referencia del acta de inspección, el inspector y la configuración de pista. Sin calibración admin activa ninguna vuelta de endurance puede aprobarse.
6. **Revisión de vueltas:** la tabla lista pendientes y errores de detector (destildar el filtro para ver todas). **Seleccionar** carga la vuelta en los formularios.
   - **Penalidades:** corregir conos, fuera de pista, slalom perdidos (o DOO/OC), con revisor y motivo. El servidor recalcula el tiempo corregido; nunca cambia el estado de la vuelta.
   - **Decisión:** aprobar o rechazar con revisor y motivo. Aprobar sólo funciona con scoring comisionado.
   - Todo queda en **Auditoría de revisiones** con antes y después.
7. **Errores de sincronización:** rechazos del servidor a las estaciones (token, protocolo, asignación, datos). Una versión de protocolo incompatible indica cliente desactualizado: llamar al técnico.
8. **Exportación y backup:** CSV de vueltas, revisiones, asignaciones y calibraciones, o exportación completa JSON (de la sesión de trabajo, o de todas con "Todas las sesiones"). **Crear backup SQLite** guarda una copia verificada en la carpeta `backups` junto a la base.

**Detener el servidor (Windows).** Ejecutar (técnico): `powershell -File .\server-admin.ps1 -Action Stop`. Detiene la tarea programada, el script que vigila al servidor y el servidor, y confirma que el puerto quedó libre. **No cerrarlo desde el Administrador de tareas:** si sólo se mata `RiceTrialsServer.exe`, el script lo toma como una caída y lo vuelve a levantar en unos 10 segundos. Para volver a arrancarlo: `Start-ScheduledTask -TaskName RiceTrialsServer`.

**Restaurar un backup.** En Windows (técnico): `powershell -File .\server-admin.ps1 -Action Restore -DatabasePath D:\protected\trials.sqlite -BackupFile D:\protected\backups\archivo.sqlite`. Detiene el servidor, pide escribir `RESTORE` para confirmar, verifica la integridad, guarda una copia previa de la base actual, sólo reemplaza si todo está bien y vuelve a arrancar la tarea si estaba corriendo. Con el servidor en código fuente: detenerlo y ejecutar `python3 server/app.py --db trials.sqlite --restore backups/archivo.sqlite`. En los dos casos, comprobar después en **Diagnóstico del servidor** la sesión activa y los conteos.

**Scope note:** exact buttons, command names, server synchronization and data locations must be reconciled with the final application build before printing this guide for operators. The original handoff's `OPERATOR_GUIDE.md`, `DOO_OC_REVIEWER_GUIDE.md`, `VENUE_STARTUP_CHECKLIST.md`, `VENUE_SHUTDOWN_CHECKLIST.md` and `RECOVERY_PLAYBOOK.md` remain the detailed source for legacy behavior; they assume a Python-based handoff and should not be followed verbatim for deployment of a new executable.
