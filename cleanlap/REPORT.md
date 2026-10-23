# Offline verification report — 2026-10-22

## Outcome

**The previously unresolved offline FSAEJ model constraints are now satisfied:** all 758 candidate clusters retained, zero planned contacts or margin conflicts, zero outside-road segments, valid directed timing/checkpoint crossings, valid S1/S2/S3 alternation, and peak curvature below the unchanged provisional steering limit. Three consecutive perturbed closed-loop **kinematic-bicycle** laps also pass every modeled cleanliness check. **This is not an Assetto Corsa lap, verified vehicle calibration, official penalty evidence, or live driving authorization.**

No VM was accessed or probed. No source geometry, physics, cone/detector rules, timing/scoring, installed assets, packaging or releases were changed. Only `cleanlap/` and ignored `dist/cleanlap/` were written. No commits or pushes. Branch remains `feat/fsaej-cleanlap-autopilot`, HEAD `332bd956482faa6609d9340c1a123e16a31e64d5`.

## Planner improvement and unchanged acceptance

The original lateral lattice and checked elastic smoothing remain the initial guess. New `optimization.py` refines the **whole periodic loop**, not independent slalom patches:

- Cyclic lateral variables couple incoming/outgoing headings, second differences and curvature, including the start/end seam.
- Sparse nonlinear least squares penalizes curvature beyond 92% of the existing steering bound, road-corner excursions, swept-footprint cone clearance and finite-gate side constraints. Obstacle residuals use the convex hull of incoming/outgoing/end rectangles plus the same rotation sagitta concept as the independent validator. Checking isolated rectangles was insufficient; an earlier development attempt still cut swept corners and was rejected.
- Both alternating phases of each inferred slalom are enumerated, ordered by the initial route's phase agreement, with smooth gate-biased seeds. Search stops once an independently audited feasible combination is found. The final solution uses first-gate signs `[+1,-1,-1]` for S1/S2/S3; S3 is now `- + - +`. Earlier rejected all-eight-phase exploratory results remain in `dist/cleanlap/refinement-debug.json`; they are development diagnostics, not final evidence.
- Soft optimization residuals **never** replace acceptance. Final `validate_path` checks every candidate obstacle, swept rectangle/rotation bound, eroded road, directed gates and curvature. `slalom_evidence` independently checks finite forward crossings, uniqueness/order, 1.1 m center clearance and alternation. All original acceptance thresholds remain unchanged. Optimization reserves extra clearance instead of reducing the 0.20 m acceptance margin.
- The existing curvature/lateral-acceleration speed ceiling and cyclic acceleration/braking passes remain. A new portable regression checks every edge, including the seam, against all three speed constraints. Speed reduction is not used to excuse excessive geometric curvature.

This is a bounded local optimizer, **not** a completeness or global-optimality proof. The final larger-footprint run reached the 100-evaluation budget rather than a solver convergence certificate; independent geometry checks, not optimizer status or objective cost, establish the reported model feasibility. Planning took approximately 136–141 s in the final CLI runs, with concurrent verification work; these are not isolated performance benchmarks or online-replanning claims.

## Actual geometry and host-side vehicle evidence

Track input is the previously extracted `dist/cleanlap/FSAEJ_Endurance.geometry.json`, sourced from `/home/lucas/rr-eval/rice-racing-trials/assets/source/FSAEJ_Endurance.zip`. It retains 608 AI stations, all **758 unconfirmed candidate clusters**, three inferred slalom runs, explicit boundary rings 0/1 and the unchanged checkpoint file. This does not prove installed v1.1.0 identity or surveyed cone/gate interpretation.

Read-only inspection of `/home/lucas/rr-eval/RR99_Trials_Standalone.zip` found identical `collider.kn5` data for the three original IDs (`madformulateam_mft02`, `_hy`, `_evo`). Existing KN5 parsing transformed all 303 collider vertices into the source model frame:

- X bounds: `[-0.7050000429, +0.7050000429]` m.
- Z bounds: `[-1.5162382630, +1.4514336171]` m.
- Source archive SHA-256: `1a4b4e61f38e4575b1410b03f3fcb0d8a8014f1265f58222fa4c79109808e9b4`.
- Collider SHA-256: `aa44e268ebd64f1a7b652f33f45624262344724eb73e1cfecf25aa9c58ea1c62`.

The old 1.48 m symmetric half-length did not enclose that source collider. A RED/GREEN regression now requires enclosure; defaults were increased to **half-length 1.52 m and half-width 0.706 m**, then all final tests/episodes were rerun. This tightens rather than weakens the body model. Full bounds, per-variant `data.acd` hashes and null unknowns are in `dist/cleanlap/car-source-inspection.json`.

Only packed `data.acd`, not unpacked `suspensions.ini`/`car.ini`, was present. No ACD decoder was used and no physics were edited. Wheelbase, road-wheel steering transfer/limit, steered-wheel envelope, model-to-feedback reference transform and installed car identity remain **unverified**, not measured by these bounds. The 1.6 m wheelbase and 0.60 rad steering limit remain explicitly provisional. These calibration unknowns block real-car authorization; they no longer stand in for an unresolved offline planner search.

## Final measured results

Use **`verified-refined-*`**, not the earlier `verified-final-*` or exploratory `refined-*` directories. All final CLI episodes used rings 0/1, the existing checkpoint file, dt 0.02 s, 0.20 m margin and the enlarged source-enclosing body. None used `--diagnostic-unsafe`. CLI exit **2** remains intentional NOT AUTHORIZED even on a clean modeled run.

| Directory under `dist/cleanlap/` | Configuration | Timed bicycle laps (s) | Contacts / margin conflicts / outside-road samples | Minimum driven clearance | Maximum lateral error |
|---|---|---|---|---|---|
| `verified-refined-six` | 6 m/s ceiling, nominal | 205.80 | 0 / 0 / 0 | 0.24001 m | 0.24358 m |
| `verified-refined-slow` | 3 m/s ceiling, nominal | 310.08 | 0 / 0 / 0 | 0.29331 m | 0.23990 m |
| `verified-refined-three` | 6 m/s ceiling; initial offset .25 m/.04 rad; steering gain .95; acceleration gain .85 | 207.38 / 206.68 / 206.68 | 0 / 0 / 0 across the entire episode | 0.24278 m | 0.25509 m |

All three episodes report `completed=true`, `clean_model_lap=true`, `plan_violations=[]`, every timed lap's S1/S2/S3 sequence valid, and `authorized=false`. Elapsed episode times are respectively 207.60, 311.90 and 622.70 s; timed laps exclude the explicit outlap/start interval.

Final plan (identical points across those three runs): minimum full-sweep candidate clearance **0.29994918 m**, peak curvature **0.3934216140 m⁻¹**, versus provisional limit **0.4275855052 m⁻¹**. Independent speed-profile checks measure maxima of 2.0 m/s² lateral acceleration, 1.5 m/s² acceleration and 2.0 m/s² braking to floating-point tolerance. No 1:00–1:10 or competitive-performance claim is justified.

The prior implementation had two planned margin conflicts, peak curvature 0.6274245353 m⁻¹ and S3 `- + + +`; its diagnostic runs were explicitly not clean. Those artifacts remain preserved. A clean model trajectory now exists without deleting candidates, changing geometry or bypassing the detector/auditor.

## Tests, provenance and reproduction

Interpreter `P=/home/lucas/.hermes/cache/scratch/cleanlap-venv/bin/python`, repository `/home/lucas/rr-eval/rr-cleanlap-autopilot`. SciPy 1.18.1 was installed **only** into ignored `dist/cleanlap/deps` with `pip --target ... --no-deps`; the shared venv was not modified. The dependency is pinned in `cleanlap/requirements.txt` for ordinary new installations.

```sh
PYTHONPATH=dist/cleanlap/deps CLEANLAP_GEOMETRY=dist/cleanlap/FSAEJ_Endurance.geometry.json \
  $P -m unittest discover -s cleanlap/tests -v
# 21 tests, 235.567 s, OK, no skips
PYTHONPATH=dist/cleanlap/deps $P -m unittest discover -s cleanlap/tests -q
# 21 tests, 28.696 s, OK (one proprietary-geometry test skipped)
git diff --check
# exit 0
```

Because `cleanlap/` is untracked, a separate `git diff --no-index --check /dev/null FILE` audit was also run over all 15 non-cache source/doc files: no whitespace errors. `git diff --name-only` is empty, HEAD is unchanged, and `git check-ignore` confirms the final derivatives are ignored.

Logs: `dist/cleanlap/verification/refined-tests-final.log` and `refined-portable-tests-final.log`. The strengthened real regression was first observed FAIL on `[204,587]` margin conflicts before optimization. It now requires zero margins/contacts/offtrack, bounded curvature, all slaloms, and three perturbed clean closed-loop laps. Source-envelope and SciPy-provenance regressions were separately observed RED before their changes. The portable synthetic slalom regression additionally exercises refinement and checks the full speed envelope. Existing controlled-single-cone, unsafe-diagnostic, HOLD/stale-state, geometry binding and timing-order tests still pass.

Each final episode contains `plan.json`, `report.json`, `telemetry.csv`, `trajectory.png`, `signals.png` and `manifest.json`. Manifests include source/checkpoint/code/artifact hashes and Python/NumPy/Shapely/Matplotlib/**SciPy** versions. All final artifact hashes **and current planning/runtime code hashes** were independently recomputed and matched. Aggregate measurements are in `dist/cleanlap/refined-final-summary.json`. The three-lap overview was visually inspected: driven and planned traces follow the full circuit and all three weaving sections, with explicit offline labeling; small gate labels need zooming. Numerical sweep/audit evidence, not the plot alone, supports cleanliness.

Exact final arguments are retained in each manifest. Example:

```sh
PYTHONPATH=dist/cleanlap/deps $P -m cleanlap \
  --geometry dist/cleanlap/FSAEJ_Endurance.geometry.json --ring-indices 0 1 \
  --checkpoints track/calibration/route/FSAEJ_Endurance.checkpoints.json \
  --output dist/cleanlap/new-refined-three --laps 3 --lateral-offset .25 \
  --heading-offset .04 --steering-gain .95 --acceleration-gain .85 \
  --dt .02 --max-seconds 900
# Expected process exit: 2, NOT AUTHORIZED; inspect clean_model_lap separately.
```

## Changed files and remaining gates

Continuation changes: new `cleanlap/optimization.py`; modified `planning.py`, `__main__.py`, `requirements.txt`, `README.md`, `REPORT.md`, `tests/test_planning.py`, `tests/test_real_geometry.py`, `tests/test_artifacts.py`. No changes to `control.py`, geometry ingestion, validation/detector rules, source track or vehicle physics were needed. All source files remain under the existing untracked `cleanlap/` delivery; ignored derivatives are not for release packaging.

Remaining blockers are real integration/calibration, not the three former offline route violations: verified installed asset identities; road/cone/slalom survey; full body/wheel/reference-frame and steering calibration; a verified pit connector; actual feedback/input adapter, watchdog and manual override; explicit VM release and supervised in-game testing. Native AC timing, actual contacts/offtracks, complete penalty receipts, single-cone +2 s behavior and `/v2/field` delivery remain **not performed/unknown**. `scoring_armed=False` must remain unchanged. The synthetic one-cone test is not an actual detector or receipt test. Passing these offline laps does not establish real P0–P2 or authorize physical/live actuation.

## Standalone driving-preparation milestone — 2026-10-23

The preceding sections are the historical offline baseline. This continuation adds
an actual standalone adapter/runtime, but **does not commission it for AC driving**.
No VM connection/probe/SSH/guest-agent/libvirt/mount, installation, commit, push,
release or scoring write was performed. Branch/HEAD remain
`feat/fsaej-cleanlap-autopilot` / `5059c5b`. Only `cleanlap/` source/docs/new tests
and ignored `dist/cleanlap/` evidence are changed.

### Implemented

- CSP Lua app with read-only player/live/identity/reset/wheel telemetry over
  loopback UDP. The Python transport exercises real sockets and nonce challenge
  freshness; runtime latches reset, non-monotonic, stale, disconnected, mismatched
  identity and implausible-motion faults. Native timing fields are observations,
  not fabricated official receipts. SDK `shared/socket` import and indexed
  `ac.onCarJumped(0, callback)` signature were checked and regression-tested.
- Real vgamepad backend plus calibrated stick/trigger mapping. A separate device
  process and inner watchdog independently release and latch after a 150 ms command
  lease expires. Linux fake-device calls, not Windows/ViGEmBus, are verified.
- Production bounded supervisor with explicit release/arm/calibration gates,
  fresh post-start input, stop file/Ctrl-C/disconnect handling, non-actuating
  observation and automatic post-release diagnostics. No automated re-arm or
  blind recovery. Neutral release is not a physical-brake guarantee.
- Verified Hermite pit connector and limited-speed merge, rejected on
  boundary/cone/swept-body/heading/curvature conflict. Unsurveyed aprons remain
  inaccessible; operator recovery is required outside the verified envelope.
- Pre-launch controls preparation with exact-byte backups, target/content hashes,
  applied-profile verification and conflict-refusing/idempotent restoration.
  The runtime does not pretend editing a running game's INI reloads its controls.
- Scalar packed-physics evidence and read-only wheel-observation analyzer.
  Sampled cylinder-envelope bounds are explicitly not a full-lock certificate.
- Geometric candidate contacts/offtrack/slalom/checkpoints with unknown native and
  official penalty values; read-only V2 receipts using actual schema/validator
  definitions and exact five-part lap identity, matching revisions/time, both
  channels, valid native evidence, detector evidence and server-approved penalty
  hash. Missing/incomplete counts stay null. Official always remains false.

### Actual verification

Executed with the existing scratch venv and `PYTHONPATH=dist/cleanlap/deps`, real
geometry enabled:

```text
python -m unittest discover -s cleanlap/tests -v
Ran 40 tests in 265.097s
OK
```

Log: `dist/cleanlap/verification/runtime-full-tests-final.log`. All **21 original tests**
remain byte-identical and pass; 19 new tests cover standalone preparation. The
original real-geometry candidate/trajectory regression ran, not skipped. No
acceptance geometry, original controller/planner/validator or original test was
weakened or edited.

The actual Lua source executes in Lupa/LuaJIT with mocked CSP/socket/JSON APIs,
including pause/replay/reset and optional wheel-data behavior. Real loopback
socket tests exercise production admission/controller and command loop through a
clearly labeled fake actuator and synthetic bicycle. The executable synthetic
socket lap completed **125.6716000000342 m**; it is **not AC evidence**. Stale
commands, out-of-order/replayed data, teleport/reset and disconnect release/latch.
A real forked owner-process test expires the command lease without main-loop
progress, observes fake-pad neutral reset, and joins the child. Default observe
CLI is exercised against a local producer and emits automatic diagnostics without
importing/creating a gamepad. Controls-prepare/restore CLI tests use temporary
synthetic files and verify exact original bytes plus conflict refusal.

On the preserved real geometry, a read-only runtime benchmark admitted the plan
in 0.927 s before actuator creation and ten stationary supervision steps took
approximately 0.040–0.042 s each on this host. This is not a Windows timing bound.
Initialization is deliberately performed before the watchdog device starts.

The three preserved validated artifact sets (`verified-refined-three`,
`verified-refined-six`, `verified-refined-slow`) match **all** existing manifest
artifact and original-code hashes; no mismatch. Verification receipt:
`dist/cleanlap/verification/preserved-artifacts.json`. The three/six plan SHA-256
remains `ded8d260f063930588ecebb4887109f0b10870f70911e5be1d21d8b47f347b8b`;
slow plan remains
`88666fa7bdd04c3842f9b0b974be4d070fab0d8496ce8402db1820637b034f2e`.

### Host physics evidence and explicit unknowns

Read `content/cars/madformulateam_mft02/data.acd` from the supplied standalone ZIP
using `acd.py` 1.1.0, unpacked **in memory only**. No physics was modified or
redistributed. Scalar evidence is in `dist/cleanlap/car-physics-evidence.json`,
with archive/ACD/collider/per-source-INI SHA-256 provenance:

- wheelbase 1.545 m; front/rear track 1.17 m; CG_LOCATION 0.45;
- STEER_LOCK 150 degrees, STEER_RATIO 4.95, linear steer-rod ratio 0.001221;
- front/rear tyre width 0.1905 m and radius 0.2286 m.

A lock/ratio estimate is about 0.528888 rad, **not measured road-wheel lock**:
Ackermann, toe/camber, suspension motion, body reference and X360 transfer remain
unverified. Full wheel envelope and verified road-wheel limit are **null**.
Installed VM identity is unknown. The preserved plan's provisional vehicle values
must not be promoted to real calibrated values: obtain observations, replan into
new artifacts and revalidate before arm. There is no fabricated live calibration.

ACD SHA-256:
`85a0b4a3043aa8cde398a7ee53ecddb9fb63b290ae83e62c6abee1e8b8315c39`.
Archive SHA-256:
`1a4b4e61f38e4575b1410b03f3fcb0d8a8014f1265f58222fa4c79109808e9b4`.

### Remaining gates and operational handoff

See English `RUNBOOK.md` for acquisition, calibration, controls preparation,
supervised startup, safe-fail pit admission, manual recovery, stop/restore,
diagnostics, API provenance and exact runnable commands. Runtime optional
dependencies are isolated in `requirements-runtime.txt`; no Windows installation
was attempted. Upstream primary CSP socket and vgamepad sources were fetched
read-only; community-generated API declarations are labeled as such, not proof
of the installed CSP version. The Hermes search backend reported an unavailable
provider fallback; direct upstream retrieval supplied the API source instead.

Still **not verified**: actual Windows XInput/ViGEmBus calls, CSP app loading and
installed API behavior, configured AC steering gain/filter/lag/deadzone, physical
manual stop/override, full tyre/body sweep and reference transform, surveyed pit
apron and installed asset identity. There are **no actual native/penalty receipts,
no actual clean lap, no actual one-cone +2 s validation and no scoring changes**.
The runtime remains disabled by default and these gaps must not be bypassed.

## Review corrections — 2026-10-23 (current verification)

Three independently reproduced safety defects were confirmed against the source
before changes. The historical 40-test result above is superseded by **52 tests,
267.169 s, OK, no skips**, including all 21 byte-identical original tests and the
real-geometry regression. Command:

```sh
PYTHONPATH=dist/cleanlap/deps CLEANLAP_GEOMETRY=dist/cleanlap/FSAEJ_Endurance.geometry.json \
  /home/lucas/.hermes/cache/scratch/cleanlap-venv/bin/python \
  -m unittest discover -s cleanlap/tests -v
```

Log: `dist/cleanlap/verification/review-full-tests.log`. The 12 focused review
regressions pass in 2.795 s. TDD RED was observed for the missing commissioned
identity constructor contract, missing reported footprint configuration, seven
false-positive complete traversals (protocol/car/track/epoch/sequence/paused/replay),
and default-vehicle traversal qualification, followed by focused GREEN runs.
The live continuous synthetic traversal positive control also passes.

- **P1 first-frame identity:** `drive.py` binds `Runtime` to the calibration car
  and admitted preflight epoch before gamepad initialization. A fresh frame is
  still requested after initialization; neither preflight command nor stale
  preflight frame is sent. Car/epoch mismatch on that first frame latches STOP
  and releases before any actuator send; restoration of the old identity does
  not re-arm. Separate car and epoch regressions assert zero sends and latching.
  CSP session-start/jump callbacks define the epoch boundary.
- **P2 footprint:** runtime/observe diagnostics now use the exact plan Vehicle,
  not default dimensions. Reports expose all vehicle parameters, and the parent
  runtime report binds the plan hash. Standalone diagnose requires a matching
  geometry-bound `--plan` and reports its canonical hash. Direct Python fallback
  is prominently provisional and cannot qualify checkpoint completion. The
  nondefault-width regression demonstrates different offtrack results at the
  same pose and checks the reported full configuration.
- **P2 traversal:** protocol, live/replay/pause status, car/track/epoch, integer
  sequence and source clock are validated. A discontinuity breaks adjacent
  swept-body/checkpoint segments and invalidates whole-trace checkpoint
  completion; no mixing of sessions or stitching around invalid frames yields
  an unqualified complete traversal. Candidate geometry/slalom remains separate
  from genuine native/official receipts.

Recomputed all 12 artifact/original-code/checkpoint hashes for each of the three
preserved validated artifact directories: **36 matching checks, zero mismatches**.
Manifest hashes also match the prior preservation receipt. Original six test
files match HEAD byte-for-byte. Receipt:
`dist/cleanlap/verification/review-artifact-hashes.json`. No geometry, original
controller/planner/validator, original tests or validated artifacts were modified.
`git diff --check` and individual new-source whitespace checks pass.

Changed in this review: `cleanlap/runtime.py`, `cleanlap/drive.py`,
`cleanlap/diagnostics.py`, new `cleanlap/tests/test_review_safety.py`,
`cleanlap/REPORT.md`, `cleanlap/RUNBOOK.md`; verification logs/receipt only under
ignored `dist/cleanlap/verification/`. No VM access/probe, commit or push.

These are Linux-tested corrections, **not Windows commissioning**. Actual CSP
loading/session/reset API behavior, installed car/track identity, Windows
XInput/ViGEmBus and multiprocessing startup/latency, AC control transfer and
manual stop/override, surveyed pit/road/cones/slaloms, full tyre/body/reference
calibration, native timing and genuine dual-channel receipts remain unverified.
There is still no actual AC clean lap or one-cone +2 s validation; scoring remains
unchanged and default actuation remains disabled.
