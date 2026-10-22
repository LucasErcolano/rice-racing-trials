# Route and cone configuration for FSAEJ_Endurance (mod v1.1.0)

Generated from `Dummy.kn5` and the AI line of the `fsaej-endurance-v1.1.0` release (published with the mod creator's permission).

- `FSAEJ_Endurance.route_config.lua` / `.json`: start/finish at `AC_TIME_0`, **a single checkpoint** at `AC_TIME_1` (12 m wide, direction -1), `verified = true` approved by the operator. Tested by driving in the validation VM: the lap closes.
- `FSAEJ_Endurance.checkpoints.json`: input for `track/tool.py export-route`.
- `cone_config.lua`: cone boxes, off-track and slalom (`cone-boxes-v1`).

Installation: `install.ps1 -RouteConfig FSAEJ_Endurance.route_config.lua -ConeConfig cone_config.lua`.
Regenerate: `docs/track-tool.md` and `docs/cone-candidates.md`.
