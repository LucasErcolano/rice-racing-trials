# Validación del cliente dentro del juego (`rice-win11`, AC + CSP reales)

2026-10-20, por Lucas (agente Hermes), VM `rice-win11` (RTX 3060 en passthrough hacia la TV).

## Qué se confirmó

El cliente Lua `RiceTrials` carga dentro de Assetto Corsa a través de CSP 0.2.11 y
llega al servidor de punta a punta sobre el runtime real:

- `acs.exe` lanzado directo (`-e madformulateam_mft02 -t FSAEJ_Endurance -s 0`), motor
  corriendo (PID 7992 en la captura, CPU acumulando).
- CSP cargó (`dwrite.dll` presente; se creó `Documents\Assetto Corsa\logs\py_log.txt`).
- El autostart del cliente corrió: leyó `station_config.lua`, se autenticó con el token
  de estación, obtuvo la asignación (ST01 -> S1 -> P01) y está latiendo (heartbeat).

## Prueba (capturada ~22:25 UTC del 2026-10-20)

`GET /api/station/assignment` (token de estación):

```json
{"station_id":"ST01","session_id":"S1","pilot_id":"P01",
 "assignment_revision":1,"online":true,
 "last_seen_utc":"2026-10-20T22:25:23.434161+00:00"}
```

`online:true` con un `last_seen_utc` que se renueva cada ~30 s es la prueba de lectura
de que el cliente está vivo y hablando con el servidor, no un seed estático.

## Lo que esto NO prueba (todavía pendiente)

- `io.save` / `io.load` escribiendo estado en `apps/lua/RiceTrials/state/` (no se observaron
  archivos todavía; el journal escribe en eventos de sesión).
- El HUD renderizando en la TV.
- `ac.onCarJumped` / `resetCounter` disparando como asume el mock.
- Cronometraje en pista, detección de vuelta o scoring (requiere manejar de verdad; el
  scoring sigue desarmado, `scoring_armed=False`).

## Configuración de VM usada (dev/prueba, no el despliegue empaquetado)

- Servidor: `python -m server.app --db C:\rr\test.sqlite --host 127.0.0.1 --port 8080`
  corrido por la tarea programada `RiceTrialsServer` de `SYSTEM` (AtStartup), no el camino
  empaquetado `start-server.ps1` + DPAPI + cuenta dedicada de `docs/deployment.md`.
- `secrets.env` en texto plano sólo para la VM (lectura de `SYSTEM` otorgada con un
  `icacls` dirigido).
- Prerrequisitos de runtime de AC instalados: VC++ 2010/2012/2013/2015-2022 (x64) y
  DirectX June 2010 (`d3dx9_43.dll`); sin ellos `acs.exe` muere con `0xc000007b`.
- Autologin de `riceadmin` habilitado (registro) para que la VM arranque en la sesión
  interactiva dueña de la GPU.
