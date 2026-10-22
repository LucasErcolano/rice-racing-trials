# Cone candidates (cone-boxes-v1)

**Advisory** geometric detection. A contact is a *candidate* for the reviewer, never an official penalty.

- Oriented car box (MFT02 collider: 1.41 x 3.0 m) shrunk by `shrink_m` per side (default 0.15 m) against the
  center of each cone (radius 0.125 m, minimum penetration 0.03 m). It interpolates every 0.1 m between frames, so
  it does not pass through cones at high speed; a jump larger than 20 m (reset/teleport) does not sweep the track.
- Only counts during the timed lap in progress (the out lap is discarded and it restarts on every route reset).
  Each cone counts once per lap.
- Reported in `payload.detector` (`cone_source=geometric_candidate_uncertified`, `cone_candidates`, `cone_candidate_count`,
  `cone_shrink_m`) and visible in /admin. `events` stays `{}`: there are no automatic penalties.
- 730 cone centers come from the KN5 clusters (unconfirmed candidates; some may be pit or decorative pylons).

## Generate and install
```
python3 track/tool.py export-cones --geometry dist/track-calibration/geometry/FSAEJ_Endurance.geometry.json \
    --output dist/track-calibration/route/cone_config.lua [--shrink 0.15]
.\install.ps1 ... -RouteConfig route_config.lua -ConeConfig cone_config.lua
```
Without `cone_config.lua` the client behaves as before. To disable it on a station, delete that file.

## Pending calibration (requires real driving)
`shrink_m` is a heuristic. Still to do: drive a clean lap (0 candidates expected) and another touching cones
on purpose, then adjust `--shrink` or `--min-depth` based on the result. Until then the value is **not verified in game**.

## Automatic penalties (off-course and slalom, `auto_penalties = true`)

`export-cones` now emits three geometric detectors in `cone_config.lua`. With `auto_penalties = true` they fill `events` of a `PENDING_REVIEW` lap (keys `cone`, `off_course`, `slalom`). A lap never becomes official on its own: scoring stays disarmed and the admin can edit each count. `--no-auto` leaves them only as candidates in `payload.detector`.

| Detector | Rule | Tolerance (default) |
|---|---|---|
| Cones | car box shrunk 0.15 m per side against the cone | penetration >= 0.03 m; max. 25 per lap |
| Off-course | all 4 wheels outside the asphalt ring (`IS_VALID_TRACK`) | 0.30 m margin beyond the edge, sustained 0.15 s; one event per excursion, re-armed after 0.5 s back inside |
| Slalom | crossing of each gate (perpendicular to travel, through the pylon) on the side expected from the reference AI line | one infraction per gate; `half_m` 5.5 m |

Rules against false positives:
- Cones that the AI line itself passes through (21 in FSAEJ, pit and start pylons) stay as candidates and do not add to `events.cone`.
- An unconfigured detector omits its key: 0 is never reported for "unknown".
- A slalom pass with uncrossed gates is *incomplete*: it is reported in `detector.slalom_incomplete` and not penalized.
- Invalid data, `dt` out of range or a frame without orientation returns `unknown`. Without `look` the direction of travel is used.
- Only counts during the timed lap in progress; the out lap is discarded.

Slalom on FSAEJ_Endurance: 2 passes of 4 and 5 pylons were detected (S1 near x 56..84; S2 at x -21..15). A third one (x -103..-75) was discarded because the AI line nearly passes through it (0.13 m confidence): it needs manual review.

**Uncertified**: everything was calibrated in simulation with the AI line, not with real driving. It must be validated with a clean lap (expected 0 off-course, 0 slalom, ~2 cones) and another with deliberate infractions.
