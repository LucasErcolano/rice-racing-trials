# Independent clean-lap offline engineering tool

**Not an Assetto Corsa driver release. No live inputs, VM connection, scoring writes or HTTP calls exist.** `python -m cleanlap` always returns exit status **2 (NOT AUTHORIZED)**, even for a clean synthetic circle. Read `REPORT.md` for clean real-geometry **bicycle-model** results and unresolved calibration/live-integration gates. Nothing in this directory is automatically packaged with RiceTrials.

## Reproduce

Run from this repository root (Python 3.12 tested):

```sh
python3 -m venv /path/to/cleanlap-venv
/path/to/cleanlap-venv/bin/python -m pip install -r cleanlap/requirements.txt
/path/to/cleanlap-venv/bin/python -m unittest discover -s cleanlap/tests -v
```

The ordinary suite uses synthetic geometry; the real-asset regression is skipped unless explicitly enabled. Obtain the legally available track locally; it is not bundled with these tests. Extraction reads but does not modify the source ZIP:

```sh
/path/to/cleanlap-venv/bin/python -m track.tool extract \
  --source /path/to/FSAEJ_Endurance.zip --track FSAEJ_Endurance \
  --output dist/cleanlap/FSAEJ_Endurance.geometry.json
CLEANLAP_GEOMETRY=dist/cleanlap/FSAEJ_Endurance.geometry.json \
  /path/to/cleanlap-venv/bin/python -m unittest discover -s cleanlap/tests -v
/path/to/cleanlap-venv/bin/python -m cleanlap \
  --geometry dist/cleanlap/FSAEJ_Endurance.geometry.json \
  --ring-indices 0 1 \
  --checkpoints track/calibration/route/FSAEJ_Endurance.checkpoints.json \
  --output dist/cleanlap/new-hold --max-seconds 400
```

The current real-geometry candidate passes all offline geometry checks and completes clean modeled laps. This is still **not authorization for AC**: vehicle calibration, installed identity, surveyed slaloms and live integration remain unresolved. Rejected candidates still STOP at time zero. To inspect a rejected candidate without claiming authorization, append `--diagnostic-unsafe` and choose a **new empty output directory**. Diagnostics never count as clean. Available perturbations: `--lateral-offset`, `--heading-offset`, `--steering-gain`, `--acceleration-gain`, `--dt` (at most 0.05 s), `--max-speed`, `--margin`, and `--laps`. Negative results remain evidence, not success.

To run a synthetic three-lap episode:

```sh
/path/to/cleanlap-venv/bin/python -c "import json,sys;sys.path.insert(0,'cleanlap/tests');from test_geometry import fixture;open('dist/cleanlap/synthetic.geometry.json','w',encoding='utf-8').write(json.dumps(fixture()))"
/path/to/cleanlap-venv/bin/python -m cleanlap \
  --geometry dist/cleanlap/synthetic.geometry.json --output dist/cleanlap/new-three \
  --laps 3 --lateral-offset .25 --heading-offset .04 \
  --steering-gain .95 --acceleration-gain .85 --dt .04
```

## Implementation

- `geometry.py`: finite input validation; explicit outer/inner boundary selection; closed-road polygon; retains **every** candidate cone cluster, AI reference, directed timing gates, inferred slaloms and unresolved provenance. Digest binds geometry, chosen rings and checkpoints. Coordinates are AC metres, X/Z ground plane; heading is `atan2(dz,dx)`.
- `planning.py`: approximately 1 m cyclic resampling, lateral lattice at 0.3 m spacing over ±3 m, collision-checked dynamic programming, checked elastic smoothing, full rectangle sweeps including turn-corner rotation bound, road erosion and cone inflation. Reference AI is not treated as a valid path. Final independent checks can reject the lattice output. This finite local search is **not complete** and cannot prove no valid solution exists.
- `optimization.py`: sparse periodic nonlinear refinement couples neighboring headings/curvature, full swept-footprint cone clearance, road corners and finite slalom side constraints. Both legal phases per run are available; independently feasible results stop the search. Soft residuals never override the original final checks. The final enlarged-body solution reached the 100-evaluation budget but passed every independent constraint; this is not a convergence/global-optimality certificate. `plan.json` retains trial phases, costs, evaluation counts and audit scores.
- `speed_profile`: curvature/lateral-acceleration ceiling, cyclic forward acceleration and backward braking passes. A curvature violation rejects the plan independently of speed. No tire, aero, banking, slip or drivetrain model is claimed.
- `control.py`: local causal path projection, speed-dependent pure pursuit, steering saturation/rate limit and proportional throttle/brake. Invalid/stale/discontinuous state, tracking-envelope escape, progress jump and stuck conditions latch STOP. Default controller mode always HOLDs; only explicit in-process bicycle simulation produces commands. There is **no live adapter**.
- `validation.py`: finite directed gate crossings and checkpoint order, outlap/start distinct from completed lap, minimum progress before finish; candidate slalom finite forward crossings, duplicate/missing gates, clearance and alternating sides. Both alternating phases are acceptable. These checks are not RiceTrials penalty calculations.

Vehicle defaults: half-width **0.706 m**, half-length **1.52 m**, conservatively enclosing all transformed vertices of the host-source MFT02 collider (full steered wheel envelope/reference transform still unverified; see REPORT); **provisional** wheelbase 1.6 m; steering 0.60 rad; lateral acceleration 2 m/s²; acceleration 1.5 m/s²; braking 2 m/s²; speed ceiling 6 m/s. Required clearance is 0.20 m; initial lattice/smoothing reserve another 0.30 m, and refinement targets an additional 0.10 m cone reserve plus a rotation bound. Cones are conservative circular envelopes of cluster extent. This does not certify installed collider geometry. Unknown wheelbase, wheel footprint, steering transfer and installed assets block live use.

## Artifacts

Every run produces `plan.json`, `report.json`, `telemetry.csv`, `trajectory.png`, `signals.png`, and `manifest.json`; malformed geometry produces `rejection.json`. The manifest records arguments, source/checkpoint/code hashes, dependency versions and artifact hashes. Preserve directories instead of overwriting. `report.json` separates episode totals, timed-lap reports and synthetic evidence from null native AC/V2 fields. Outlap incidents remain in conservative episode totals. CSV records actual closed-loop states and commands, not a replay of the target path. Synthetic contacts are deduplicated by cluster index, **not confirmed individual cones or official penalties**. CSV contact IDs serialize as Python-list strings; JSON reports provide structured lists. Offline lap times have integration-step resolution, not native AC timing accuracy.

Plots label synthetic provenance, reference/planned/driven trajectories, limits, candidates and gates; overview labels are small, so zoom the PNG. Signals show speed, target, errors, controls and clearance. The trajectory's incident highlight remains active after the first episode incident (cumulative counter); use CSV `contact_ids`/`offtrack` to localize individual samples.

## Remaining supervised gates

No VM was accessed or probed. Before any in-game use: obtain explicit VM release; preserve timing agent installation and control backups; establish actual installed track/version identity; survey road boundaries and all cone/slalom identities; validate full body/wheel envelope and physical steering/wheelbase; implement and test actual feedback/input adapter and emergency/manual override; plan a verified pit-to-course connector (pit spawn is not assumed on the loop); then validate A clean lap with both native and penalty receipts, B exactly one deliberate safe cone contact with observed +2 s, C three real consecutive clean laps, and only then D speed. Restore manual controls afterwards. Keep `scoring_armed=False`. None of A–D is established by this tool's offline results.
