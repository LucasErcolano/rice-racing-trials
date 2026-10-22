# Limits and acceptance gates

1. The original archive includes measured FSAEJ Endurance definitions, **not** the installable KN5. The FSAEJ Endurance, FSAEJ Skidpad and MFT02 files were later downloaded from the authorized Drive. They are not included in the distributed packages and still need to be checked inside the game. The Rice tracks in the original archive are alternatives and do not replace the selected one.
2. Assetto Corsa and CSP were not run on Windows in this environment. Isolated Lua tests verify pure algorithms, not the real API or autoload.
3. There is no sufficiently reliable physical evidence of cone knock-downs: proximity alone does not allow a penalty. If the physics API does not expose a verifiable state change, every contact requires human review.
4. Off-course and slalom require versioned geometry and gates from the real installation. Historical definitions must be checked against the downloaded KN5 and validated inside the game. Without calibration, the status is `PENDING_REVIEW` or `DETECTOR_ERROR`, never `VALID`.
5. The first timing gate crossing from the spawn is an out lap; the native Best Lap is not declared corrected. A custom HUD does not change the game's internal times.
6. Plain HTTP with a token on the LAN does not provide confidentiality against other network users. Isolate the LAN or add TLS termination and operational controls.
7. The Windows pipeline is not the same as a Windows executable that was actually built and tested. Check installation, autoload, continuity without the HUD, persistence after shutdown and rollback.
8. Linux delivery tests are not enough to enable driver trials: run a human dry-run and sign the checklist in `windows-validation.md`.

Statuses are kept distinct as **implemented**, **tested on Linux** and **validated in Assetto Corsa/Windows**. They are never conflated.
