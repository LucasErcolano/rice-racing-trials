# Informe de pruebas (Linux)

Última integración: `venv` temporal con `lupa`, `python -m unittest discover -s tests -p '*test*.py' -q` y `python -m unittest discover -s client/tests -p 'test_*.py' -q`: **30 pruebas de servidor/integración/empaquetado y 29 de cliente, todas OK**. El intérprete Python del sistema no tiene `lupa`; el test E2E requiere el venv.

La prueba E2E conecta la máquina Lua aislada con HTTP real, SQLite y la API del leaderboard: asignación y heartbeat, una vuelta pendiente, respuesta perdida tras commit, reinicio de VM, reenvío byte a byte, respuesta idempotente, reasignación intermedia y ranking vacío. Otras pruebas cubren el rechazo de revisión no confiable, datos persistidos, inventario read-only, empaquetado sin credenciales ni mods, y revisión histórica de asignaciones. Se verificó además que el `Store` usado por la CLI permanece desarmado para Autocross, Skidpad y Endurance aun ante una vuelta reportada como válida.

**No ejecutado / no aprobado:** Assetto Corsa y CSP en Windows, detección física individual de conos, calibración de bordes/slalom/gates, validación de cronometraje real, durabilidad de `ac.storage` ante fallo de energía, PowerShell/DPAPI/PyInstaller en Windows, tarea programada tras reboot, rig con múltiples estaciones y evento completo. La validación de módulos CSP y del autostart sigue pendiente. Los tests Linux comprueban contratos y comportamiento emulado, no certificación deportiva.

Paquetes Linux en `dist/` son **sólo código fuente**; ver inventario/ZIP audit antes de distribuir. La puntuación matemática activada explícitamente en tests unitarios no habilita el servidor de producción para puntuar. El sistema permanece bloqueado para trials oficiales.
