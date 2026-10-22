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
