# Technical summary

**Implemented:** audit of the original material and hashes; SQLite/HTTP server with per-station driver assignment, tokens, heartbeat, idempotent duplicates, leaderboard by corrected time for `VALID` results, backup and CSV export; basic public and admin web interface; provisional crossing-based CSP Lua client with HUD and fail-closed behavior without a complete route; source packaging and preliminary Windows installers.

**Tested on Linux:** nine server tests and twelve isolated Lua client tests, plus a ZIP integrity check. Simulated messages to the server were tested, not laps from Assetto Corsa.

**Not implemented / pending:** verifiable detection of knocked-down cones, off-course and slalom on the real track; authenticated submission from the CSP client to Package B; a queue with proven atomic durability; full Lua-server parity; an automatic server startup service; repair of the native Best Lap. The client has a provisional route/out lap state machine based on gates that require calibration, and a review queue in `ac.storage`, not automatic certification. No feature is validated inside Assetto Corsa on Windows. The ZIPs with the requested names contain source only and are not operational packages ready for trials.

**Assets received later:** the FSAEJ Endurance, Skidpad and MFT02 files were downloaded from the shared Drive and verified by SHA-256. The mere availability of the files does not show that the gates match the installed model or that the cones expose physical collision; that requires cross-checking and testing in the simulator. A calibration declared by the client never replaces server-side validation of the installed asset.

See `docs/limitations.md` and `docs/windows-validation.md` before any demonstration; do not publish results as official.
