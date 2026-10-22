#Requires -Version 5.1
<#
Rebrands the visible Content Manager car name, badge, and available skins without
changing the physical car IDs. Defaults to read-only preflight; use -Apply.
Designed for the existing MFT02 install used by RiceTrials V2.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$AssettoRoot,
    [switch]$Apply,
    [string]$RollbackFrom
)
$ErrorActionPreference = 'Stop'
function Must([bool]$condition, [string]$message) { if (-not $condition) { throw $message } }
$AssettoRoot = (Resolve-Path -LiteralPath $AssettoRoot -ErrorAction Stop).Path
$carRoot = Join-Path $AssettoRoot 'content\cars'
Must (Test-Path -LiteralPath $carRoot -PathType Container) 'Cannot find content/cars at AssettoRoot.'
$sourceRoot = Join-Path $PSScriptRoot 'content\cars'
$backupRoot = Join-Path $AssettoRoot '_RR99_backups'
$ids = @('madformulateam_mft02','madformulateam_mft02_hy','madformulateam_mft02_evo')
if ($RollbackFrom) {
    $snapshot = (Resolve-Path -LiteralPath $RollbackFrom -ErrorAction Stop).Path
    $markerPath = Join-Path $snapshot 'RR99-backup.json'
    Must (Test-Path -LiteralPath $markerPath -PathType Leaf) 'Snapshot lacks RR99-backup.json; refusing to restore unknown files.'
    $data = Get-Content -LiteralPath $markerPath -Raw | ConvertFrom-Json
    Must ($data.type -eq 'rr99-visual-backup-v1') 'Invalid backup format.'
    Must ($data.assetto_root -ieq $AssettoRoot) 'Backup belongs to a different AC installation.'
    Write-Host "Rollback snapshot: $snapshot"
    foreach ($id in $data.cars) {
        Must ($ids -contains $id) "Unexpected car ID $id in backup."
        $target = Join-Path $carRoot $id
        $original = Join-Path $snapshot $id
        Must (Test-Path -LiteralPath (Join-Path $original 'skins') -PathType Container) "Original skins missing for $id"
        Must (Test-Path -LiteralPath (Join-Path $original 'ui\ui_car.json') -PathType Leaf) "Original UI missing for $id"
        Must (Test-Path -LiteralPath $target -PathType Container) "Installed car missing: $id"
    }
    if (-not $Apply) { Write-Host 'PREVIEW ONLY: add -Apply to restore these snapshots.'; return }
    $now = Get-Date -Format yyyyMMdd-HHmmss
    foreach ($id in $data.cars) {
        $target = Join-Path $carRoot $id
        $original = Join-Path $snapshot $id
        $rr = Join-Path $snapshot ("rr99-before-rollback-$now\$id")
        New-Item -ItemType Directory -Path (Join-Path $rr 'ui') -Force | Out-Null
        if (Test-Path -LiteralPath (Join-Path $target 'skins')) {
            Move-Item -LiteralPath (Join-Path $target 'skins') -Destination (Join-Path $rr 'skins')
        }
        foreach ($f in @('ui_car.json','badge.png')) {
            $p = Join-Path (Join-Path $target 'ui') $f
            if (Test-Path -LiteralPath $p) { Copy-Item -LiteralPath $p -Destination (Join-Path (Join-Path $rr 'ui') $f) }
        }
        Move-Item -LiteralPath (Join-Path $original 'skins') -Destination (Join-Path $target 'skins')
        foreach ($f in @('ui_car.json','badge.png')) {
            $p = Join-Path (Join-Path $original 'ui') $f
            if (Test-Path -LiteralPath $p) { Copy-Item -LiteralPath $p -Destination (Join-Path (Join-Path $target 'ui') $f) -Force }
        }
        Write-Host "RESTORED: $id"
    }
    Write-Host "Rollback completed. Previous RR99 visual files saved in $snapshot"
    return
}
Must (Test-Path -LiteralPath $sourceRoot -PathType Container) 'Extract RR99 patch ZIP fully before running the installer.'
$existing = @()
foreach ($id in $ids) {
    $target = Join-Path $carRoot $id
    if (-not (Test-Path -LiteralPath $target -PathType Container)) { continue }
    Must (Test-Path -LiteralPath (Join-Path $target 'madformulateam_mft02.kn5') -PathType Leaf) "Car KN5 missing in $id"
    Must (Test-Path -LiteralPath (Join-Path $target 'data.acd') -PathType Leaf) "Car physics data missing in $id"
    $src = Join-Path $sourceRoot $id
    foreach ($p in @('ui\ui_car.json','ui\badge.png','skins\rr99\skin.dds','skins\rr99\ui_skin.json')) {
        Must (Test-Path -LiteralPath (Join-Path $src $p) -PathType Leaf) "RR99 patch is missing $id/$p"
    }
    Must (Test-Path -LiteralPath (Join-Path $target 'skins') -PathType Container) "Original skins missing for $id."
    $existing += $id
    Write-Host "READY: $id -> visible name RR99; all current skins will be backed up."
}
Must ($existing.Count -gt 0) 'No supported MFT02 car folders found: check -AssettoRoot.'
Write-Host 'Only skin folders and UI badge/metadata will change. All original game geometry and physics stay untouched.'
if (-not $Apply) {
    Write-Host 'PREFLIGHT ONLY. Close Assetto Corsa and Content Manager, then rerun with -Apply.'
    return
}
$snapshot = Join-Path $backupRoot ("RR99-" + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $snapshot -Force | Out-Null
$changed = @()
try {
    foreach ($id in $existing) {
        $target = Join-Path $carRoot $id
        $src = Join-Path $sourceRoot $id
        $save = Join-Path $snapshot $id
        New-Item -ItemType Directory -Path (Join-Path $save 'ui') -Force | Out-Null
        foreach ($f in @('ui_car.json','badge.png')) {
            $p = Join-Path $target ("ui\$f")
            if (Test-Path -LiteralPath $p) { Copy-Item -LiteralPath $p -Destination (Join-Path $save ("ui\$f")) -Force }
        }
        # Move rather than copy potentially gigabytes of original skins. Same AC drive.
        Move-Item -LiteralPath (Join-Path $target 'skins') -Destination (Join-Path $save 'skins') -ErrorAction Stop
        $changed += $id
        Copy-Item -LiteralPath (Join-Path $src 'skins') -Destination (Join-Path $target 'skins') -Recurse -ErrorAction Stop
        foreach ($f in @('ui_car.json','badge.png')) {
            Copy-Item -LiteralPath (Join-Path $src ("ui\$f")) -Destination (Join-Path $target ("ui\$f")) -Force -ErrorAction Stop
        }
        Write-Host "INSTALLED: $id"
    }
    @{ type='rr99-visual-backup-v1'; assetto_root=$AssettoRoot; cars=$existing; created=(Get-Date).ToString('o') } |
        ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $snapshot 'RR99-backup.json') -Encoding UTF8
}
catch {
    Write-Warning "Installation failed: $_. Attempting to restore original skins and UI."
    foreach ($id in $changed) {
        $target = Join-Path $carRoot $id; $save = Join-Path $snapshot $id
        $new = Join-Path $target 'skins'; if (Test-Path -LiteralPath $new) { Remove-Item -LiteralPath $new -Recurse -Force }
        if (Test-Path -LiteralPath (Join-Path $save 'skins')) { Move-Item -LiteralPath (Join-Path $save 'skins') -Destination $new }
        foreach ($f in @('ui_car.json','badge.png')) {
            $p = Join-Path $save ("ui\$f")
            if (Test-Path -LiteralPath $p) { Copy-Item -LiteralPath $p -Destination (Join-Path $target ("ui\$f")) -Force }
        }
    }
    throw
}
Write-Host "SUCCESS: RR99 visual rebrand complete. Backup: $snapshot"
Write-Host "Rollback: .\Install-RR99.ps1 -AssettoRoot '$AssettoRoot' -RollbackFrom '$snapshot' -Apply"
