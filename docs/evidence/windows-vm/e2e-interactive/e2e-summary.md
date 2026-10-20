# E2E Windows: 33 OK, 0 fallos

Entorno: Microsoft Windows 11 Pro 10.0.26300 build 26300; PowerShell 5.1.26100.9444 Desktop; ZIP RiceTrials-0.2.0-vm-complete-win64.zip (sha256 3963a85780f387204524ba2df3a26b967902494b3d2180507c6a0792149481cf)

| Paso | Resultado | s | Error |
|---|---|---|---|
| Extraer ZIP complete | pass | 2.8 |  |
| Fixture de raíz AC (no es el juego) | pass | 0.4 |  |
| install.ps1 rechaza instalar sin hashes de origen (fail-closed) | pass | 0.6 |  |
| install.ps1 rechaza HTTP hacia IP pública | pass | 0.2 |  |
| install.ps1 verifica SHA-256 y rechaza un archivo alterado | pass | 0.2 |  |
| install.ps1 -Preflight no escribe nada | pass | 0.6 |  |
| install.ps1 instala con token por Read-Host y ACL restringida | pass | 4.9 |  |
| install.ps1 rechaza actualizar sin -BackupDirectory | pass | 0.2 |  |
| install.ps1 actualiza con backup y luego rollback | pass | 2.7 |  |
| uninstall.ps1 preflight, cancelación y desinstalación con backup | pass | 0.4 |  |
| Reinstalación limpia | pass | 1.1 |  |
| Los archivos del fixture AC quedaron intactos | pass | 1.4 |  |
| configure-secrets.ps1 guarda tokens con DPAPI | pass | 9.9 |  |
| configure-secrets.ps1 se niega a sobrescribir y a guardar dentro del release | pass | 0.1 |  |
| start-server.ps1 rechaza 0.0.0.0 y DB dentro del release | pass | 0.7 |  |
| start-server.ps1 levanta el exe PyInstaller con secretos DPAPI | pass | 15 |  |
| API: sesión, piloto, asignación, heartbeat, vuelta, duplicado, leaderboard, CSV | pass | 0.5 |  |
| server-admin.ps1 Backup (token por Read-Host) y Check | pass | 1.6 |  |
| Vuelta posterior al backup (para probar el restore) | pass | 0.1 |  |
| server-admin.ps1 Restore rechaza con el servidor corriendo | pass | 11.4 |  |
| server-admin.ps1 Restore (servidor detenido) y verificación | pass | 10.3 |  |
| Reinicio del servidor conserva el leaderboard | pass | 5.4 |  |
| Bind LAN a IPv4 privada propia (-AllowLanBind) | pass | 7.3 |  |
| server-admin.ps1 FirewallRule (crear, idempotente, quitar) y NetworkCheck | pass | 28.8 |  |
| Crear cuenta local dedicada y permisos de carpeta | pass | 1.4 |  |
| configure-secrets.ps1 ejecutado por la cuenta de servicio (DPAPI de esa cuenta) | pass | 14.3 |  |
| Otra cuenta (el admin del runner) NO puede descifrar los secretos del servicio | pass | 0.1 |  |
| server-admin.ps1 RegisterTask + Start-ScheduledTask (cuenta dedicada) | pass | 10.1 |  |
| Servidor de la tarea: datos previos conservados y tokens del servicio válidos | pass | 0.1 |  |
| Caída del exe: start-server.ps1 lo relanza (supervisor, 10 s) | pass | 13.6 |  |
| server-admin.ps1 Stop detiene tarea, supervisor y exe (sin relanzar) | pass | 36 |  |
| UnregisterTask y limpieza de la cuenta | pass | 3.7 |  |
| Evidencia sin tokens ni contraseñas | pass | 0 |  |

Raíz AC = fixture de texto. Esto NO valida Assetto Corsa, CSP, la pista ni el auto. No se probó un reinicio real de Windows.
