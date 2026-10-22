# Audit of the supplied ZIP

## Scope and method

Original source (unmodified): `/home/lucas/.hermes/attachments/Rice_Driver_Trials_v1.0_Hardware_Human_Handoff.zip`, 3 161 198 bytes; SHA-256 **46b33cf4085d7e4e8f003010fc3e75903596601531e7d047f20ec026c27f7574**. All 755 entries were walked with `zipfile` and decompressed for `testzip()` (no error); `.zip` members and ZIP signatures `PK\x03\x04` at the start of each member were searched for (none), and duplicate names were checked (none). Therefore **there is no nested ZIP inside this archive**; the `downloads/FSAEJ_*.zip` mentioned in the configuration are external references, not members. This check does not prove the absence of containers embedded at non-initial offsets, nor does it prove that executable content is safe.

Distribution by extension: 564 JSON, 62 Python, 54 Markdown, 32 INI, 16 PNG, 14 BAT, 5 CSV, 3 LOG, 2 SVG, 2 KN5 and 1 TXT. Main directories by file count: `commissioning/` 513, `sessions/` 63, `tools/` 40, `tracks/` 29, `docs/` 16, `config/` 13, `analysis/` 9, `tests/` 9, `evidence/` 4, `setups/` 2, `telemetry/` 2, `cars/` 1; the rest is at the root. The original was neither extracted nor overwritten.

## What it actually contains

- **Reusable code:** `trials.py`, `rc_console.py`; nine `analysis/` modules (geometry, events, times, attempts, protocol, sessions), two `telemetry/`, forty `tools/` utilities (including `trackgen.py`, `kn5.py`, `public_tracks.py`, `rice_preflight.py`, `operator_runtime.py`, `install_tracks.py`, `validate_fsae_2027_tracks.py`), nine `tests/` and 14 BAT launchers. These are sources, not a verified run in this environment.
- **Included tracks:** two complete alternative *Rice* tracks for Assetto Corsa, under `tracks/assetto/RICE_FSAE_2027_SKIDPAD/` and `...AUTOCROSS_V1/`, with `.kn5`, `data/*.ini`, maps and UI. Their editable definitions are in `tracks/rice_fsae_2027_*/course_definition.json`; SVG/PNG maps in `docs/`. The `.kn5` files start with the `sc6969` signature; they are not ZIPs.
- **Selected official tracks:** `FSAEJ_Skidpad` and `FSAEJ_Endurance` **have no KN5 models or Assetto folders in this package**. There are only measured definitions `tracks/fsaej_*/course_definition.json`, commissioning evidence, maps/images and expected hashes of external files. The second is a 2019 *endurance* track used as a one-lap autocross proxy, not the 2027 Michigan autocross.
- **Car:** `cars/` contains only `VEHICLE_CONTRACT.md`; there is no model, physics or sound for the MAD Formula Team MFT02. `config/FROZEN_TRIAL_CONFIGURATION.json` requires `madformulateam_mft02` version 1.42, external file SHA-256 `f73a4f992a9e989e6daf0aca24da9c7f61992af4a86385cbd566f824770e78c5`; that hash is a *recorded declaration* and cannot be checked against an absent car.
- **Other assets:** `commissioning/` includes reports, validation JSON, PNG and CSV session telemetry; `sessions/` holds three commissioning attempts and configurations; `config/` holds protocol/locks; `setups/RICE_TRIALS_V1.ini` is a setup, not a vehicle. The historical reports do not replace a current hardware and driver test.

## Hashes computed directly on ZIP members

| Member (relative to the inner root folder) | SHA-256 |
|---|---|
| `tracks/assetto/RICE_FSAE_2027_SKIDPAD/RICE_FSAE_2027_SKIDPAD.kn5` | `8e385f824f423061c86548c0e21906f352bf46153d855dec2d46b4d834116a63` |
| `tracks/assetto/RICE_FSAE_2027_AUTOCROSS_V1/RICE_FSAE_2027_AUTOCROSS_V1.kn5` | `88c1b0127396c524c0ef156ce0a0cc567da394edbf82cbb80e15060817300880` |
| `tracks/rice_fsae_2027_skidpad/course_definition.json` | `4b9c75f9f18aed0115cefe1a98ac56ca93fc8980e217601937a896578a88d8bb` |
| `tracks/rice_fsae_2027_autocross_v1/course_definition.json` | `ee760d9e41036ff065d1b7e9a6fa6811528b82bb228f137b088d42b803ad0a8f` |
| `tracks/fsaej_skidpad/course_definition.json` | `d2529b793642049539123eb9b6a7937566c1d766cf8f7cf834b7ebf62eaff743` |
| `tracks/fsaej_endurance/course_definition.json` | `277480128706b3019d524f77169a57b10da986ad967c6ddfec39a0d53b2e64a7` |
| `config/FROZEN_TRIAL_CONFIGURATION.json` | `9dd561245eddcb791a3a61468224bcd779b74463a4960cb2c51db0f8ffa6e289` |

These SHA-256 values are hashes of **individual file bytes**; they are not equivalent to the `installed_track_hash` of installation trees recorded in `config/track_lock.json` (`relative_path_size_sha256_v1`/project policy). The external downloads and the provenance/license claims were not verified against the external publisher either.

## Limits and decisions for reuse

`STATUS.md` and the frozen configuration agree on `official_enabled=false`, `hardware_commissioned=false`, `human_dry_run_completed=false`; protocol approval is still `DRAFT`. `tracks/TRACKS.md` keeps a historical description that calls the Rice tracks official, contradicted by the later selection in `TRACK_SELECTION_FREEZE.md` and `config/FROZEN_TRIAL_CONFIGURATION.json`: for this handoff the Rice tracks are **FALLBACK_ONLY / NOT OPERATOR_SELECTABLE** and the JSAE tracks are the selected ones. The `COMMISSIONING_REPORT.md` report reflects an earlier stage; do not read it as the final state. Reuse the analysis/code/maps and Rice tracks subject to validation; acquire licensed Assetto Corsa, the MFT02 and the JSAE files separately, and check hashes and rights before operating them. No simulator was run, dynamics/FFB were not verified, and no official trial was declared ready.
