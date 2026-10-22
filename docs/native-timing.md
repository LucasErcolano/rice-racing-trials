# Native timing: reproducible analysis and Dummy.kn5 validation

## Confirmed status for V2

The user confirmed that the selected `Dummy.kn5` has already been tested in Assetto Corsa and works. It is marked as validated in game by user confirmation, not as a new automated test of this delivery. That validation does not commission the penalty geometry or enable official scoring. The historical candidate variants described below keep their own limits and do not inherit this validation automatically. The track is neither modified nor reinstalled in this delivery.

## Measured facts (Linux, without running Assetto)

`python3 -m timing.audit assets/source/FSAEJ_Endurance.zip FSAEJ_Endurance --output timing/endurance-native-evidence.json` reads `Dummy.kn5` without extracting or modifying the track. The dummy hash matches `track/calibration/timing-landmarks.json`. Markers in AC coordinates (m): `AC_START_0=(-127.292,1,30.452)`, `AC_HOTLAP_START_0=(-59.698,1,31.651)`, `AC_PIT_0=(-153.783,1,32.611)`; `AC_TIME_0_L=(-108.581,1,42.438)` and `AC_TIME_0_R=(-115.773,1,42.438)`. The flat distance between `AC_START_0` and the midpoint of the line is **19.291 m**: the spawn sits before the line, which explains (without proving it) a first lap of about 18 s that AC takes as Best Lap.

`Dummy.kn5` is a separate 577-byte file listed in `models.ini`; it only contains dummy nodes. `track/kn5.py` reads it and writes it back **byte-identical** (checked on the 7 KN5 files of Endurance and Skidpad, including `Physics.kn5` and `Ecopa.kn5`).

## Alternatives, from least to most intervention

1. **Change nothing (operations)**: test Practice from the pits/starting line and Hotlap with the track untouched, recording `LapCount`, `LapTime` and Best Lap. Our own scoring already discards the out lap by state and checkpoint sequence (`client/core.lua`), so the problem only affects the native display.
2. **CSP API**: `ac.markLapAsSpoiled()` is documented as "works only during the race" (see `docs/csp-api-reference.md`). It is not assumed to work in Practice: it is the `mark_spoiled` step of the Validation Kit. The mod author's Python app (`ac.ext_markLapAsSpoiled`) has no confirmed Lua equivalent.
3. **CSP track configuration (`ext_config.ini`)**: the downloaded official documentation (`acc-extension-config` wiki "Tracks - General options" and `_doc_config_tracks.ini`) has **no** key to move `AC_START_*`/`AC_PIT_*` or the timing line. The only pit-related key is `[BASIC] PITBOXES` (number of pit boxes, not their position). It remains **to be investigated**; no keys are invented.
4. **Minimal, reversible binary patch of `Dummy.kn5`** (this document): changes only the transform matrix of `AC_START_0` (and optionally `AC_PIT_0`). It is the smallest possible binary intervention: it does not touch `Physics.kn5` or the visual geometry.
5. Moving `AC_TIME_0_L/R` (the line) is rejected: it would change the timing point of every lap and the hash expected by the calibration.

## Candidate patch

Generated with `python -I track/tool.py dummy-patch` (see `docs/track-tool.md`) into `dist/track-calibration/patches/` (git-ignored):

| Variant | Change | Candidate SHA-256 |
| --- | --- | --- |
| `FSAEJ_Endurance-dummy-start-after8` | `AC_START_0` 8 m **after** the line, on the AI line, oriented along the AI; surface under the new point: valid `ASPHALT` | `e45a213b51a63b9812229c3ce2498c3d1831b7c2549fb1941a96e25f5fa4babc` |
| `FSAEJ_Endurance-dummy-start-pit-after8` | The above + `AC_PIT_0` 16 m after the line | `dc60ea332eb5c3c98bc324ff3e9dabae4409b339506fe2045906c0b9e7ee14fb` |

Expected original: 577-byte `Dummy.kn5`, SHA-256 `737f3f2676656c25f3a07f83981bea336810ee6d0cba4854cf55148cef2864d1`. The original is **not** copied into the package (it belongs to the mod); only its hash is recorded.

Rationale: if the car spawns already past the line, the first lap AC closes is a full lap instead of a ~19 m stretch. Moving the spawn further back (before the line) was rejected: it would still produce a short timed stretch (~1 s) instead of eliminating it.

Verification done on Linux (in each variant's `manifest.json`): byte-for-byte round trip of the original; the candidate parses again; all differing bytes are inside the matrices of the chosen nodes; the new positions match; the other landmarks do not change; same size (577 bytes).

**Not verified** (only confirmable in game): that AC uses the new position in Practice, that the ~18 s lap disappears, that the first full lap is recorded correctly, and the effect on online server checksums.

## Apply and revert (Windows, AC closed)

```powershell
cd <variant folder>
powershell -ExecutionPolicy Bypass -File .\Apply-DummyPatch.ps1 -AcRoot 'C:\...\assettocorsa' -Status
powershell -ExecutionPolicy Bypass -File .\Apply-DummyPatch.ps1 -AcRoot 'C:\...\assettocorsa' -Apply
powershell -ExecutionPolicy Bypass -File .\Apply-DummyPatch.ps1 -AcRoot 'C:\...\assettocorsa' -Revert
```

`-Apply` requires the installed `Dummy.kn5` to have the original SHA-256, stores a verified backup in `content\tracks\FSAEJ_Endurance\RiceTrials_backup\Dummy.kn5.<sha256>.bak` and verifies the copy of the candidate (restoring on failure). `-Revert` restores the backup after checking its hash. Tested with `pwsh` 7.4.6 on Linux against a simulated AC tree (apply, revert, rejection on a different hash); not tested on Windows.

Test procedure: the `dummy_candidate` step of the Validation Kit (`docs/windows-validation.md`). The JSON evidence file does not enable scoring or calibration.
