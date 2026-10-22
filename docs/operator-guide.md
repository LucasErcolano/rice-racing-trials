# Operator guide: station dry-run

**Commissioning only** until Rice authorizes the final protocol, physical rig and official-mode release. Do not manually edit configuration, setup, track or result files. The historical handoff ZIP is reference material, not proof that this newly packaged application is ready.

1. At startup, check that the wheel and pedals are safe and calibrated, emergency stop works, AC/CSP and the approved external MFT02 and JSAE course content are installed, disk space is sufficient and the recovery drive is connected. If any preflight or freeze/hash check fails, **stop** and call the technician. Do not bypass it.
2. Launch **Rice Trials** from the desktop shortcut. Enter an assigned driver ID (never a real name in result folders). Choose **Skidpad** or **Autocross**. Confirm the current baseline and that this is a commissioning run. Start the restore/preflight flow and wait until the application says to drive.
3. For Skidpad, follow the course entry, right/right then left/left laps and exit. For Autocross, complete one timed course lap from the staged start. Keep focus on the simulator while driving. Use **Stop capture** if the run cannot finish; do not delete a failed attempt.
4. After capture, have an independent human reviewer record cones down/out (DOO), off-course (OC) and missed slaloms where applicable, reviewer ID and evidence/reason. Automatic detections are advisory. Mark a DNF/incomplete attempt invalid. Save the reviewed result and check that a report is generated; never invent a missing time or reclassify synthetic data as real.
5. Before the next driver, confirm the attempt is saved and start a fresh driver session. At the end, export results with checksums, confirm backup/restore on the approved storage, then close the application gracefully. Preserve raw timing, telemetry and audit history.

**If an attempt or station fails:** stop capture, secure the rig, preserve the attempt and logs, and ask the technician to run the documented recovery/preflight process. Never repeatedly restart or alter frozen files to make a check pass. A wheel safety issue, repeated failure or unexpected hash mismatch ends the session pending review.

## Server, TV display and admin panel

Full technical reference: `docs/server-api.md`.

**Scoring status.** The server ships disarmed (fail-closed): no lap becomes VALID or enters the ranking until Rice commissions official scoring. The TV display shows this with the banner "SCORING NOT COMMISSIONED: provisional, unofficial results" and shows no ranking; raw times appear only under "Last Laps", marked "(prov.)". Do not try to work around it.

**TV display (`http://server:8080/tv`).** Open it in the TV's browser in full screen (F11). By default it shows the session marked active, with no typing needed. Columns: position, driver, best corrected lap, raw time, penalties, gap to the leader and number of valid laps. Top right: the browser's connection status with the server (green "Connected to server", red "Server Offline" with the time of the last data) and the time of the last refresh. On the right: stations (Online, or Offline with the time of the last heartbeat) and the count of laps pending review. It refreshes itself every 5 seconds.

**Admin panel (`http://server:8080/admin`).** Only on a trusted computer. Paste the admin token into "Admin token" and press **Sign in**: it is stored only in that tab and is cleared with **Sign out** or when the tab is closed. Typical flow:

1. **Sessions:** enter an ID and name, leave "Mark active" checked and press **Create / rename**. Another session can be activated later with **Mark active** in the table. "Working session" selects which session is reviewed and exported.
2. **Drivers:** ID (a code, never a real name in result folders) and name; **Register**.
3. **Station assignment:** choose station and driver; **Assign to working session**. The history highlights each reassignment in color, with the previous driver.
4. **Stations and detectors:** connection, last heartbeat, last lap, detector status and summary, sync errors. A station Offline for more than 90 s: check the network and the client.
5. **Calibration:** record the inspection report reference, the inspector and the track configuration. Without an active admin calibration, no endurance lap can be approved.
6. **Lap review:** the table lists pending laps and detector errors (uncheck the filter to see all). **Select** loads the lap into the forms.
   - **Penalties:** correct cones, off-track, missed slaloms (or DOO/OC), with reviewer and reason. The server recalculates the corrected time; it never changes the lap status.
   - **Decision:** approve or reject with reviewer and reason. Approval only works with commissioned scoring.
   - Everything is recorded in **Review audit** with before and after values.
7. **Sync errors:** server rejections of station requests (token, protocol, assignment, data). An incompatible protocol version means an outdated client: call the technician.
8. **Export and backup:** CSV of laps, reviews, assignments and calibrations, or a full JSON export (of the working session, or of all sessions with "All sessions"). **Create SQLite backup** saves a verified copy in the `backups` folder next to the database.

**Stopping the server (Windows).** Run (technician): `powershell -File .\server-admin.ps1 -Action Stop`. It stops the scheduled task, the script that supervises the server and the server itself, and confirms that the port is free. **Do not close it from Task Manager:** if only `RiceTrialsServer.exe` is killed, the script treats it as a crash and brings it back in about 10 seconds. To start it again: `Start-ScheduledTask -TaskName RiceTrialsServer`.

**Restoring a backup.** On Windows (technician): `powershell -File .\server-admin.ps1 -Action Restore -DatabasePath D:\protected\trials.sqlite -BackupFile D:\protected\backups\archivo.sqlite`. It stops the server, asks you to type `RESTORE` to confirm, checks integrity, saves a copy of the current database first, replaces it only if everything is fine and restarts the task if it was running. With the server running from source: stop it and run `python3 server/app.py --db trials.sqlite --restore backups/archivo.sqlite`. In both cases, check the active session and the counts in **Server diagnostics** afterwards.

**Scope note:** exact buttons, command names, server synchronization and data locations must be reconciled with the final application build before printing this guide for operators. The original handoff's `OPERATOR_GUIDE.md`, `DOO_OC_REVIEWER_GUIDE.md`, `VENUE_STARTUP_CHECKLIST.md`, `VENUE_SHUTDOWN_CHECKLIST.md` and `RECOVERY_PLAYBOOK.md` remain the detailed source for legacy behavior; they assume a Python-based handoff and should not be followed verbatim for deployment of a new executable.
