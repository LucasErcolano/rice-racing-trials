# Track analysis tool (`track/tool.py`)

Python 3 stdlib only; works the same on Linux and Windows (`py -3` on Windows). The mod files are untrusted data: run with `python -I` and point `--source` at an extracted copy outside the repository. The tool refuses to write inside the track folder.

```sh
python -I track/tool.py extract   --source <copy>/FSAEJ_Endurance --track FSAEJ_Endurance --output dist/track-calibration/geometry
python -I track/tool.py render    --geometry dist/track-calibration/geometry/FSAEJ_Endurance.geometry.json \
                                  --output dist/track-calibration/FSAEJ_Endurance.html --svg dist/track-calibration/FSAEJ_Endurance.preview.svg \
                                  [--telemetry rice_diagnostics.jsonl] [--route route_config.json]
python -I track/tool.py telemetry --geometry <geometry.json> --input <trace.csv|.jsonl> [--route <route_config.json>] [--output report.json]
python -I track/tool.py export-route --geometry <geometry.json> --output dist/track-calibration/route/FSAEJ_Endurance.route_config.lua \
                                  [--checkpoints gates.json | --ai-candidates 6] [--approve --operator "First Last"]
python -I track/tool.py dummy-patch --source <copy>/FSAEJ_Endurance --track FSAEJ_Endurance \
                                  --output dist/track-calibration/patches/FSAEJ_Endurance-dummy-start-after8 [--nodes AC_START_0 AC_PIT_0]
```

`--source` accepts the track folder or the original ZIP (read in memory, never extracted or modified).

## What it extracts (`extract`)

| Layer | Source | Status |
| --- | --- | --- |
| Timing gates | Nodes `AC_TIME_0_L/R`, `AC_START_0`, `AC_HOTLAP_START_0`, `AC_PIT_0` from any KN5 listed in `models.ini` (in FSAEJ they are in `Dummy.kn5`). Crossing direction inferred from `ai/fast_lane.ai` | Measured from the file; direction **not verified in game** |
| Boundaries | Border edges of physics meshes whose surface (`data/surfaces.ini`, by mesh name prefix `<n><KEY>`) has `IS_VALID_TRACK=1`; simplified (Douglas-Peucker, tolerance `--tolerance`) | Derived from geometry; if there are no valid surfaces: `not available` |
| Cones | Meshes whose node or material name contains `pylon`/`cone`. In FSAEJ they are **merged**: vertices are grouped into spatial clusters (Endurance 758, Skidpad 150 clusters) | Always `unconfirmed candidate` (no individual cones are invented) |
| AI line | `ai/fast_lane.ai` (x, z, distance) | Reference, not a boundary |
| Slalom | `--slalom <json>` (format `slalom-gates-v1`, see `docs/geometry-development.md`) | Without a file: empty list with a warning |

The resulting JSON includes the SHA-256 of each KN5 read (`asset_hash` of `Physics.kn5`) and explicit warnings.

## 2D view (`render`)

Self-contained HTML (inline SVG, CSS, no external JavaScript or server) with toggleable layers: boundaries, cone clusters, AI line, native gates, spawns, slalom, route checkpoints and telemetry. Scale bar in meters; AC X axis to the right and Z upward (XZ projection).

## Telemetry

- CSV with a header that includes the columns `t`, `x` and `z` (case-insensitive).
- JSONL with `{"t":..,"x":..,"z":..}` or the CSP client diagnostics format (`client/diagnostics.lua`): lines `{"t":..,"kind":"sample","pos":{"x":..,"y":..,"z":..},...}`. Lines without a position are ignored and counted.

`telemetry` reports crossings of the native line (with direction) and of the route checkpoints, using the same half-open convention as `client/core.lua` (a point exactly on the line counts only once).

## Exporting `route_config.lua`

Generates the table the client consumes (`start` + `checkpoints` with `a`, `b`, `direction`, `label`; plus `track_id` and `asset_hash`). By default `verified = false` and `approved_by = nil`: the client does not arm the route. Only with `--approve --operator "<name>"` is `verified = true` written; this is an **operator attestation**, not a proof. The `--ai-candidates N` checkpoints come from the AI line and are marked as candidates. A suite test runs the exported route on the real `client/core.lua` state machine (via `lupa`).

## Where the derived files live (decision)

The JSON/HTML/SVG generated from the real assets are **derived from the mod's proprietary geometry**. They are stored in `dist/track-calibration/` (ignored by git) and only ship in `RiceRacing_Track_Calibration.zip`, for internal use. The repository only versions `docs/evidence/track/FSAEJ_Endurance.preview.png` and `FSAEJ_Skidpad.preview.png`: reduced screenshots (675x365 px) that allow reviewing the calibration without handing out usable coordinates. Original KN5 files are never versioned or packaged.

Generated on this machine (Linux) from the ZIPs in `assets/source/`:

| Track | Gates | Spawn to line distance | Cone clusters | Boundaries |
| --- | --- | --- | --- | --- |
| FSAEJ_Endurance | AC_TIME_0_L/R, START, HOTLAP_START, PIT; direction -1 per `fast_lane.ai` | 19.291 m | 758 candidate clusters | 3 border polylines |
| FSAEJ_Skidpad | AC_TIME_0_L/R, START, PIT; assumed direction (spawn side) | 28.241 m | 150 candidate clusters | 18 border polylines |

## Tests

`tests/test_track_tool.py` builds minimal synthetic KN5 files (no mod assets): byte-for-byte writer round trip, extraction of gates/boundaries/cones, CSV telemetry and diagnostics JSONL, export with and without approval, render, Dummy candidate, and the contract test with `client/core.lua`.
