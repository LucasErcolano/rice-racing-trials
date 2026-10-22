# Joint distribution: RiceTrials V2 + overlays + RR99

RR99 is an authorized visual rebrand of the MFT02. The binary material stays out of the Git history and is attached as separate assets of the public pre-release `v0.3.0-rc.2`, under the creator's temporary permission for the trials. The repository contains the audit tool, tests and the variant installer to update existing MFT02 installations.

Software base: the final tested commit of the `v0.3.0-rc.2` pre-release (SHA given in its assets/notes), which includes offline recovery and the native overlays. The RR99 binaries are not stored in Git; they are attached as versioned release assets.

## What the patch does and does not do

- Replaces the skins of the three cars and the `ui/` metadata (`ui_car.json`, `badge.png`). Adds `skins/rr99/`.
- Does not touch `data.acd`, `collider.kn5`, the car KN5, `.ini` files, audio or animations.
- The three internal IDs do not change: `madformulateam_mft02`, `madformulateam_mft02_hy`, `madformulateam_mft02_evo`. RiceTrials only uses `madformulateam_mft02` (`install.ps1` preflight).
- Contains no RiceTrials files. Client and server are not modified.

Reproducible audit against the original MFT02 and the full mod (folders extracted by the operator):

```powershell
python tools\rr99_audit.py --orig <MFT02_extracted> --full <RR99_standalone_extracted> --patch <RR99_patch_extracted> `
  --client-zip RiceTrials-0.3.0-rc.2-client-win64.zip --server-zip RiceTrials-0.3.0-rc.2-server-win64.zip --output audit.json
```

## Installation order at the station (Windows)

Close Assetto Corsa and Content Manager before starting.

1. Verify the package hashes against `SHA256SUMS.txt`:
   `Get-FileHash -Algorithm SHA256 <file>` and compare by hand.
2. Install RiceTrials first, following `docs/v2-field-testing.md` (`install.ps1 -Preflight`, then without `-Preflight`, with `-BackupDirectory`). Keep the backup folder it prints.
3. Extract `RR99_Trials_Rebrand_PATCH.zip` into a new folder (do not run from inside the ZIP).
4. Preview, no changes on disk:
   `.\Install-RR99.ps1 -AssettoRoot 'C:\...\assettocorsa'`
5. Apply:
   `.\Install-RR99.ps1 -AssettoRoot 'C:\...\assettocorsa' -Apply`
   This creates a snapshot in `<AssettoRoot>\_RR99_backups\RR99-<date>-<guid>` with `RR99-backup.json`. **This removes the previous skins** of the three cars (they are kept in the snapshot).
6. Reopen Content Manager and check that the car appears as RR99 with the `rr99` skin.
7. Run the RiceTrials `install.ps1 -Preflight` again: it must still PASS for `MFT02 installed ID`.

If you install by drag and drop in Content Manager instead of the script, there is no backup and the previous skins are not removed. This path is untested.

## Rollback (each component is independent)

- **RR99**: `.\Install-RR99.ps1 -AssettoRoot '...' -RollbackFrom '<AssettoRoot>\_RR99_backups\RR99-...'` (preview), then again with `-Apply`. Restores the original skins and `ui/`, first saving the RR99 state in another snapshot. Refuses folders without `RR99-backup.json`.
- **RiceTrials**: `.\install.ps1 -AssettoRoot '...' -Rollback '<RiceTrials backup folder>'`, or `uninstall.ps1`.
- Rolling back one does not affect the other.

## Verification results

See the delivery report. In short: audit 11/11 gates OK; installation, idempotence, exact rollback (542/542 identical files) and injected failure tested with PowerShell 7.6.7 on Linux in a test directory; **not tested on Windows or in Content Manager**.

## Credits and time limit

- Base MFT02 car: MAD Formula Team / O21 Modelling. We claim no authorship of the model, physics or audio.
- RR99 visual rebrand adapted with the creator's express permission for the trials. Thanks to Mad Racing for the authorization and collaboration; the team will also be thanked at the in-person event.
- Permission to publish RR99 is temporary and limited to the trials. Afterwards: remove the RR99 assets from this release and, if appropriate, delete the pre-release or restrict the repository; publish an update without RR99 only if the software is to be kept. Removing the asset/release does not delete copies already downloaded, mirrors or caches.
- This distribution does not make the pre-release official competition software. Review the authorization before any redistribution after the trials.
