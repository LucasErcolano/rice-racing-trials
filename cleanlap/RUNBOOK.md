# Standalone runtime preparation and supervised commissioning

## Status and separation

This is an **offline-prepared, not AC-commissioned** driver. Do not use it while
the VM is reserved. No VM access, probing, installation or driver installation
was performed in this milestone. All commands below involving AC/Windows are
**future operator actions after explicit release**, on that local Windows host.
The existing `python -m cleanlap` offline entry point is unchanged. There is no
RiceTrials client/server, scoring, timing, packaging or game-physics modification.

Components:

- `csp/CleanlapTelemetry`: read-only CSP Lua app; listens **only** on
  `127.0.0.1:9877`; emits a response to a fresh 128-bit nonce, not an unsolicited
  delayed stream. It exports the car origin/heading/speed, source clock,
  car/track/lap identity, gear, live/replay/pause status, reset epoch and optional
  four-wheel observations. No `ac.set*`, simulated keyboard or gamepad writes.
- `runtime.py`: strict loopback peer/nonce, monotonic sequence/source clock,
  identity, lap/session/reset, motion, swept footprint, route-progress, heading,
  traction-envelope and stale-data admission around the existing controller.
- `actuator.py`: real `vgamepad.VX360Gamepad` API mapping; steering calibration
  maps road radians to left stick, right trigger is throttle, left is brake.
  A separate process owns the device and **latches release after 150 ms without
  a fresh valid command**. A stale buffered command cannot extend that lease.
  The guard inside that process also has a watchdog thread. No automatic re-arm.
- `startup.py`: stationary pit connector, sampled swept-body/cone/boundary,
  endpoint-heading and curvature checks. Never crosses an unverified pit apron.
- `drive.py`: bounded, synchronous supervisor; observation is the no-control
  mode. Stop file, Ctrl-C, disconnect, timeout, reset and admission errors release
  controls; no blind recovery or UI automation.
- `capture.py`: executable read-only wheel-envelope acquisition analysis.
- `diagnostics.py`: geometric cones/offtrack/slalom/checkpoint diagnostics and a
  **separate** read-only validator for real V2 dual-channel server exports.

## Offline checks (available now, no VM)

From the repository root:

```sh
export PYTHONPATH=dist/cleanlap/deps
PY=/home/lucas/.hermes/cache/scratch/cleanlap-venv/bin/python
CLEANLAP_GEOMETRY=dist/cleanlap/FSAEJ_Endurance.geometry.json \
  "$PY" -m unittest discover -s cleanlap/tests -v
"$PY" -m cleanlap.offline_runtime
"$PY" -m cleanlap.car_evidence /home/lucas/rr-eval/RR99_Trials_Standalone.zip \
  --output dist/cleanlap/new-car-evidence.json
```

Optional dependencies are separate in `requirements-runtime.txt`: pinned Lupa
and `acd.py` for host tests/acquisition, and Windows-only `vgamepad`. Review the
upstream ViGEmBus dependency and driver-install behavior before an eventual
Windows installation; no driver is installed by this repository's commands on
this Linux host. A fake backend passing tests does not prove Windows XInput.

## Read-only telemetry acquisition, only after release

1. Operator confirms VM release; do not infer release from connectivity.
2. Manually copy the **standalone** `CleanlapTelemetry` directory to
   `<AC>/apps/lua/CleanlapTelemetry`. Enable that app in the installed CSP UI.
   Keep the existing RiceTrials app and geometry untouched. Confirm the installed
   CSP supports the app APIs. Unsupported APIs intentionally yield no telemetry,
   not fallback fabricated state. Do not expose or forward port 9877.
3. Start a stationary practice session with the exact installed car and track.
   Keep an immediate manual pause/brake available. Do not run the gamepad yet.
4. Run on that same host (paths below are repository-relative; use its Python):

```sh
python -m cleanlap.drive observe \
  --geometry dist/cleanlap/FSAEJ_Endurance.geometry.json --ring-indices 0 1 \
  --checkpoints track/calibration/route/FSAEJ_Endurance.checkpoints.json \
  --plan dist/cleanlap/verified-refined-three/plan.json \
  --seconds 30 --output dist/cleanlap/observed-01.jsonl
python -m cleanlap.capture dist/cleanlap/observed-01.jsonl \
  --output dist/cleanlap/observed-01.wheels.json
```

Observe/template return **2** to emphasize NOT AUTHORIZED; this is not an error
claiming a live driver was armed. Every output path must be new. Default 30 s;
maximum run duration is 900 s. Observe does not import/create a gamepad or change
controls. Trace/report files contain raw received telemetry and diagnostics;
`live=false`/missing wheel observations cannot certify dimensions.

Wheel acquisition records four world-position centres, look/up vectors, tyre
radius/width and the car reference basis. The analyzer projects cylinder support
onto the car axes, reporting *sampled* half-width/front/rear offsets, median
wheelbase and sampled front-wheel angle range. It never sets full-envelope or
steering verification true. Verify wheel indices and reference transform against
the installed car; collect centre, intermediate and both full-lock steering
positions while stationary. Inspect visual/physical tyre extent and add measured
uncertainty. Body, swept tyres and non-level surfaces need separate verification;
sampled tyre cylinders alone do not certify the complete vehicle body envelope.

## Commissioning gates — do not fill these with guesses

Generate a new calibration template with the same geometry, checkpoints and plan:

```sh
python -m cleanlap.drive template \
  --geometry dist/cleanlap/FSAEJ_Endurance.geometry.json --ring-indices 0 1 \
  --checkpoints track/calibration/route/FSAEJ_Endurance.checkpoints.json \
  --plan dist/cleanlap/verified-refined-three/plan.json \
  --output dist/cleanlap/calibration-DRAFT.json
```

It contains **false/null** attestations, not permission to drive. Evidence must
identify the installed car/assets, surveyed boundaries/cones/slaloms, wheel and
body envelope including steering extremes, car-origin reference transform,
steering sign and stick-per-road-radian transfer, manual override and stop, and
actual CSP telemetry. Add operator identity and `evidence_paths` entries
`{"path":"<local measurement file>","sha256":"<its SHA-256>"}`. The runtime checks
those files and hashes, all attestations, exact car/track, canonical geometry and
plan hashes, and exact plan vehicle dimensions. Attestations remain a human
commissioning responsibility, not independently authenticated measurements.

The host `data.acd` evidence gives wheelbase **1.545 m**, steering input lock
**150 degrees** and ratio **4.95**; the simple lock/ratio estimate is about
**0.528888 rad**, **not a verified road-wheel limit or stick transfer**. The
preserved trajectory uses provisional geometry/steering assumptions. **Replan
into a new output directory with the measured vehicle envelope and usable
steering limit; re-run the unchanged validator and real-geometry regression.**
Do not relabel the existing trajectory as commissioned or edit its vehicle fields
without recomputing curvature/clearance. Missing wheel envelopes stay null.
Survey blockers in the old report remain blockers until actual evidence clears
them; flipping booleans is not commissioning.

Prove neutral, signed steering, throttle/brake and manual override at rest first;
check deadzones, gain, filters, rate limits and response latency in the actual AC
X360 profile. Calibrate a linear stick mapping only if evidence supports it.
The runtime refuses unrepresentable plan steering; it does not clip a bad gain
and silently invalidate curvature. Configure automatic clutch/appropriate gear
manually; it never sends gear, ignition, pit-exit or blind recovery buttons.

## Controls backup, startup and supervised run (future Windows only)

Close AC before changing its input method. Prepare a **new backup directory**:

```sh
python -m cleanlap.drive controls-apply --arm --vm-released \
  --controls "<Documents>/Assetto Corsa/cfg/controls.ini" \
  --backup "<local-new-backup-directory>" --output "<new-preparation-record.json>"
```

This changes only HEADER input method to X360 and disables X360 keyboard combining;
other settings are preserved semantically, original bytes preserved exactly in
`original.ini`. Existing backups are never overwritten. The JSON transaction
contains target and before/after hashes. It is checked by reading back the file.
Do not let another process edit it concurrently. Keep the manual pause channel
independent of keyboard mixing. Start AC only after the profile is prepared and
confirm it actually loaded X360; changing the INI in a running game is not a
working input-method switch. `run` verifies this pre-launch transaction without
rewriting the live profile, then restores original file bytes at exit.

Park on the verified route start, near its heading, in forward gear, stationary.
If parked in a surveyed pit area, `--pit-connector` generates a low-speed Hermite
merge to route start; it **fails closed** if the road polygon excludes that apron,
if cone/boundary clearance is insufficient or curvature/heading cannot be met.
There is no safe automatic departure from an unsurveyed pit. Hand-position the car
under supervision instead. The connector is generated from actual telemetry, not
a guessed pit coordinate. Runtime merge speed is limited to 1 m/s.

```sh
python -m cleanlap.drive run --arm --vm-released \
  --geometry "<commissioned geometry>" --ring-indices 0 1 \
  --checkpoints "<matching checkpoint file>" --plan "<revalidated new plan>" \
  --calibration "<completed measured calibration.json>" \
  --controls "<Documents>/Assetto Corsa/cfg/controls.ini" \
  --backup "<the pre-launch backup-directory>" \
  --stop-file "<local emergency-stop-file>" --seconds 10 \
  --output "<new runtime-trace.jsonl>"
```

Do not add `--pit-connector` until its surveyed footprint is approved. Start with
a short supervised run, not a full unattended lap. Windows process/device startup
occurs only after all gates and geometric admission. The post-start frame is
fresh; preflight telemetry is never used to drive. Before device initialization,
the runtime binds the commissioned calibration car and the geometrically admitted
preflight epoch (CSP increments the epoch on session start/car jump). The first
fresh post-init frame must retain both identities, as must all later frames;
a change latches STOP and releases before any actuator send. The old preflight
frame is not replayed into the controller. STOP requires a new process/preflight,
not a return to the old car/epoch. Real and fake-pad traces are
separately labeled. Successful diagnostic traversal is not an official lap.

## Stop, recovery and restore

Create the configured stop file, press Ctrl-C, or use the independent manual pause.
The supervisor releases neutral, latches STOP, closes the device owner, and attempts
conflict-safe controls restoration. The device process separately releases if the
supervisor stalls/dies/disconnects; no blind throttling, reversing, teleporting,
rejoining or automatic re-arm is implemented. **Neutral release is not a physical
brake or proof that a moving car stopped.** The operator must immediately pause
and/or brake. No finite software watchdog protects against OS/driver hangs; the
actual override and stop behavior still require Windows commissioning.

After any reset, stale stream, excessive heading/lateral error or envelope escape,
remain stopped. Operator inspects and hand-positions inside the verified envelope;
start a new process, new output and explicit arm/preflight. Never extend the road
polygon or loosen margins just to admit recovery. Close AC and confirm the
restored profile remains on disk; an in-game profile is not hot-reloaded by this
file restore. AC/CM saving settings later can overwrite a restored file.

After interruption, run:

```sh
python -m cleanlap.drive restore --vm-released \
  --controls "<same controls.ini>" --backup "<same backup-directory>" \
  --output "<new restore-result.json>"
```

Restore is idempotent and checks original/applied/target hashes. If controls changed
externally, it **refuses to overwrite them**: preserve current bytes and original,
close competing writers, review/merge manually. There is intentionally no force
flag. Keep backups and trace/report hashes. Automatic report
`controls_restored=true` means byte-readback, not verification of an AC UI state.

## Diagnostics and read-only V2 receipts

Each completed runtime/observe loop automatically writes a `.report.json` with
geometric candidate cone contacts, margin conflicts, offtrack samples, slalom
candidate evidence, directed checkpoint crossings and source quality gaps.
Diagnostics run **after** controls release, not in the watchdog timing path.
Automatic diagnostics use exactly `Vehicle(**plan['vehicle'])`, the same footprint
admitted by the runtime and matched against calibration at preflight. Reports
include all vehicle fields; the supervisor report binds the plan SHA-256. The
standalone `diagnose` command requires `--plan`, checks geometry identity and
reports the canonical plan SHA-256. An explicit plan is not itself commissioning.
Direct Python calls without vehicle evidence are marked
`configuration_status=PROVISIONAL_DEFAULT_VEHICLE`, `provisional_vehicle=true`,
and cannot report `checkpoints_complete=true`.

Checkpoint completion requires the ordered directed start/checkpoint/finish
sequence with valid `cleanlap-1`, live frames, nonempty car/track/epoch identity,
matching track, increasing integer sequence and finite source clock with gaps
in (0, 150 ms]. Replay/paused flags, invalid/missing fields, car/track/epoch
changes, sequence regression or time gaps break adjacent sweeps/crossings and
invalidate completion for the **entire trace**, even if a later segment is clean.
Inspect `quality_gaps`; acquire a new uninterrupted trace rather than combining
sessions. Slalom output remains candidate geometry, never live/official proof.
They are model/geometric evidence, not the official FSAE penalty detector; body
outside the polygon is not equivalent to the official all-wheels rule. Re-run:

```sh
python -m cleanlap.drive diagnose --geometry "<matching geometry>" \
  --ring-indices 0 1 --checkpoints "<matching checkpoints>" \
  --plan "<exact plan used for this trace>" \
  --trace "<runtime-trace.jsonl>" --output "<new diagnostics.json>"
python -m cleanlap.drive receipts --export "<unchanged V2 field-export.json>" \
  --identity "<exact lap-identity.json>" --output "<new receipt.json>"
```

Identity is the five strings `station_id`, `session_id`, `lap_id`, `pilot_id`,
`track_config`. Only a single exact export row can match. The validator imports
actual `server.field_v2` definitions **read-only**, checks both schema-valid channel
payloads, native time/validity, equal assignment/rules revisions, conflicts,
enabled detectors and evidence, plus server approval bound to the exact penalty
payload hash. A received flag alone is insufficient. Missing counts are **null,
not zero**. Official remains false, as in the real V2 protocol; no API review,
assignment, verification, scoring or replay write is issued. `fetch_export()` is an
optional GET-only loopback helper with redirects disabled, not an automatic VM
connection. Obtain the unchanged export through the existing authorized operator
workflow after release. No official/native/penalty receipt was generated here.

## API provenance and remaining integration gates

Sources inspected directly from upstream, not treated as Windows runtime proof:

- CSP's own shared socket module:
  https://github.com/ac-custom-shaders-patch/acc-lua-internal/blob/main/lua-shared/socket.lua
  (`require('shared/socket')`, UDP bind/nonblocking receivefrom/sendto).
- CSP Lua SDK source/examples:
  https://github.com/ac-custom-shaders-patch/acc-lua-sdk
- Generated SDK API declarations (community extraction, **not runtime truth**):
  https://github.com/dabinn/ac-lua-sdk-api/blob/master/ac_apps/lib.lua
  (`StateCar`, `StateWheel`, `StateSim`, millisecond clock, `ac.onCarJumped(0, callback)`,
  `ac.onSessionStart(callback)`). Verify installed CSP behavior before commissioning.
- vgamepad author's API/examples:
  https://github.com/yannbouteiller/vgamepad
  (`VX360Gamepad`, float stick/triggers, `reset()` followed by `update()`).
- Read-only ACD parser author's implementation:
  https://github.com/philippkosarev/acd.py

No CSP UI load, native XInput/ViGEmBus, AC control gain/latency, full tyre/body
sweep, pit apron, physical stop/override or genuine dual-channel receipt has been
verified in the reserved VM. Lupa tests execute the **actual app source** with
mocked CSP/socket/JSON interfaces; Python tests use actual loopback UDP. Those
facts must not be described as an AC-driven clean lap.
