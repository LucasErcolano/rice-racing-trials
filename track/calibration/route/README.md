# Configuracion de ruta y conos para FSAEJ_Endurance (mod v1.1.0)

Generada a partir de `Dummy.kn5` y la linea AI del release `fsaej-endurance-v1.1.0` (publicado con permiso del creador del mod).

- `FSAEJ_Endurance.route_config.lua` / `.json`: salida/meta en `AC_TIME_0`, **un solo checkpoint** en `AC_TIME_1` (12 m de ancho, sentido -1), `verified = true` aprobado por operador. Probado manejando en la VM de validacion: la vuelta cierra.
- `FSAEJ_Endurance.checkpoints.json`: entrada de `track/tool.py export-route`.
- `cone_config.lua`: cajas de conos, off-track y slalom (`cone-boxes-v1`).

Instalacion: `install.ps1 -RouteConfig FSAEJ_Endurance.route_config.lua -ConeConfig cone_config.lua`.
Regenerar: `docs/track-tool.md` y `docs/cone-candidates.md`.
