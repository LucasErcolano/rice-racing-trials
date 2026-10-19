# Authorized asset intake (offline audit)

These operator-supplied archives remain under `assets/source/`; do not redistribute or add them to source packages. This is an archive/geometry comparison, **not** validation in Assetto Corsa or authorization for official scoring.

| Archive | Bytes | SHA-256 | Archive verification |
|---|---:|---|---|
| `FSAEJ_Skidpad.zip` | 79,640,354 | `23812281bf93df63257eb9c3fa388d42fd62e51e64c1a4e2d7bab92ec1344237` | 27 ZIP entries; `ZipFile.testzip()` passed |
| `FSAEJ_Endurance.zip` | 119,253,521 | `38598b5a465508a206643fb4e6a2b83cd80a85a688b356ebf421067391bbb117` | 31 ZIP entries; `ZipFile.testzip()` passed |
| `MFT02_v1.42.rar` | 184,721,485 | `f73a4f992a9e989e6daf0aca24da9c7f61992af4a86385cbd566f824770e78c5` | RAR5, 636 listed entries, not encrypted. **Integrity verified** afterward with official `unrar-nonfree` 7.0.7 (`unrar t`: `All OK`). Local 7-Zip 23.01 cannot decompress 413 entries (`Unsupported Method`), so use a RAR5-capable extractor. |

The RAR contains three distinct car folders: `content/cars/madformulateam_mft02`, `madformulateam_mft02_evo`, and `madformulateam_mft02_hy` (plus EVO oval setups and readmes). These exact directory names, rather than the archive basename `MFT02_v1.42`, are the selectable car IDs. Each has its own `data.acd`, KN5 LODs and `ui/ui_car.json`. Whole-archive integrity is now verified by `unrar t`; embedded UI/physics values and individual-file hashes were not separately analyzed. Select the standard `madformulateam_mft02` variant specified in the handoff and validate its installed identity in-game. No nested ZIP/RAR/7z was listed.

## Track evidence

The original handoff ZIP's `tools/kn5.py` was extracted to scratch for read-only parsing; no mod binaries were copied into deliverables. Both ZIPs have `models.ini`, `Physics.kn5`, `Dummy.kn5`, AI lane, surface configuration and UI metadata. The parser reads KN5 versions 5/6 and reports:

| Track | Physics SHA-256 | Dummy SHA-256 | Timing dummy endpoints in AC `(X,Y,Z)` |
|---|---|---|---|
| Skidpad | `6860d67fa19e5510e5be8b721dc272ffa07c7cfbe731e2e6d6a72f8bf3836916` | `ded0d93e87975946c4d8cd0bd66f1cd152eda5d19069b8dc396e046f873e5fee` | `AC_TIME_0_L=(-32.070,-0.139,4.188)`, `AC_TIME_0_R=(-28.753,0,8.311)` |
| Endurance | `62cab78119788f40a45c4226b572d0b8bc1304bfae93d1523b288d373ac55b5b` | `737f3f2676656c25f3a07f83981bea336810ee6d0cba4854cf55148cef2864d1` | `AC_TIME_0_L=(-108.581,1,42.438)`, `AC_TIME_0_R=(-115.773,1,42.438)` |

All four hashes match the `measured_from` fields of the corresponding handoff `tracks/fsaej_*/course_definition.json` exactly. Endurance's start/finish in the definition is MAP `(108.5807,42.4376)` to `(115.7726,42.4376)`, consistent with MAP `x=-X_ac,y=Z_ac` and the timing dummies to rounding precision. Skidpad's timing segment in canonical coordinates is `(-2.645757,0.006816)` to `(2.645756,0.008042)`; applying the definition's `world_frame` to the rounded dummy positions gives approximately `(-2.645613,0.006788)` and `(2.646042,0.007836)`. The small discrepancy follows rounding of the printed AC coordinates. These are evidence-backed *timing landmarks*, not permission to equate a crossing with a completed route.

The skidpad Physics mesh contains `1PYLON_0` (1,392 vertices), `_2` (1,104), `_1` (1,008); endurance contains `1PYLON_0` (8,760). They are aggregate meshes with vertices spanning large sections of the course, **not individual cone dummies**. The skidpad definition asserts 16 inner/13 outer pylons per circle and a virtual 3 m entrance lane; the endurance definition lists 730 measured cone positions and 1,800 stations. The matching physics-file hashes strongly identify the provenance of those measurements, but this audit did **not** independently segment individual cone instances or remeasure the full driving corridor. Do not treat mesh vertex counts as cone counts or infer a collision event from cone proximity.

`track/calibration/timing-landmarks.json` records only the extracted timing landmarks with file hashes. It is intentionally not an active scoring-gate configuration. Before activating gates: validate against installed file hashes (not just archive names), independently inspect cone mesh components and directional crossing, drive the right/right/left/left skidpad procedure and one full endurance loop on the target rig, check native lap alignment and map-frame telemetry, and retain fail-closed route/cone validation. Existing Rice fallback assets are not interchangeable with these JSAE tracks. The simulator and target rig were not available for this audit.
