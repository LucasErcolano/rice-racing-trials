#Requires -Version 5.1
<#
.SYNOPSIS
  Applies or reverts a Dummy.kn5 CANDIDATE NOT VERIFIED IN GAME.
.DESCRIPTION
  -Apply: verifies that the installed Dummy.kn5 has the manifest original SHA-256,
  saves a copy to <track>\RiceTrials_backup\Dummy.kn5.<sha>.bak (if not present) and copies the
  candidate after verifying its SHA-256. -Revert: restores the backup after verifying its hash.
  -Status: reports which version is installed. Never touches other mod files.
.EXAMPLE
  .\Apply-DummyPatch.ps1 -AcRoot 'C:\Program Files (x86)\Steam\steamapps\common\assettocorsa' -Apply
#>
[CmdletBinding(DefaultParameterSetName = 'Status')]
param(
    [Parameter(Mandatory = $true)][string]$AcRoot,
    [string]$Track = 'FSAEJ_Endurance',
    [string]$PatchDir = $PSScriptRoot,
    [Parameter(ParameterSetName = 'Apply')][switch]$Apply,
    [Parameter(ParameterSetName = 'Revert')][switch]$Revert,
    [Parameter(ParameterSetName = 'Status')][switch]$Status
)
$ErrorActionPreference = 'Stop'

function Get-Sha256([string]$Path) { (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() }

$manifestPath = Join-Path $PatchDir 'manifest.json'
$candidate = Join-Path $PatchDir 'Dummy.kn5.candidate'
if (-not (Test-Path -LiteralPath $manifestPath) -or -not (Test-Path -LiteralPath $candidate)) {
    throw "Missing manifest.json or Dummy.kn5.candidate in $PatchDir"
}
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
if ($manifest.track -ne $Track) { throw "The manifest is for $($manifest.track), not for $Track" }
$trackDir = Join-Path $AcRoot "content\tracks\$Track"
$target = Join-Path $trackDir 'Dummy.kn5'
if (-not (Test-Path -LiteralPath $target)) { throw "Not found: $target" }
$backupDir = Join-Path $trackDir 'RiceTrials_backup'
$backup = Join-Path $backupDir "Dummy.kn5.$($manifest.original_sha256).bak"
$installed = Get-Sha256 $target
$state = switch ($installed) {
    $manifest.original_sha256 { 'original' }
    $manifest.candidate_sha256 { 'candidate' }
    default { 'unknown' }
}
Write-Host "Label: $($manifest.label)"
Write-Host "Installed: $installed ($state)"

if ($Apply) {
    if ($state -eq 'candidate') { Write-Host 'The candidate is already installed.'; exit 0 }
    if ($state -ne 'original') { throw 'The installed Dummy.kn5 does not match the manifest original: not applying.' }
    if ((Get-Sha256 $candidate) -ne $manifest.candidate_sha256) { throw 'The candidate does not match its SHA-256: file corrupted.' }
    New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
    if (-not (Test-Path -LiteralPath $backup)) { Copy-Item -LiteralPath $target -Destination $backup }
    if ((Get-Sha256 $backup) -ne $manifest.original_sha256) { throw 'The backup does not match the original: aborted.' }
    Copy-Item -LiteralPath $candidate -Destination $target -Force
    if ((Get-Sha256 $target) -ne $manifest.candidate_sha256) {
        Copy-Item -LiteralPath $backup -Destination $target -Force
        throw 'The candidate copy failed verification: the original was restored.'
    }
    Write-Host "Candidate applied. Backup: $backup"
    Write-Host 'If Content Manager or an online server validates checksums, this modified track may be rejected.'
}
elseif ($Revert) {
    if ($state -eq 'original') { Write-Host 'The original is already installed.'; exit 0 }
    if (-not (Test-Path -LiteralPath $backup)) { throw "No backup at $backup" }
    if ((Get-Sha256 $backup) -ne $manifest.original_sha256) { throw 'The backup does not match the original SHA-256.' }
    Copy-Item -LiteralPath $backup -Destination $target -Force
    if ((Get-Sha256 $target) -ne $manifest.original_sha256) { throw 'The restore did not verify.' }
    Write-Host 'Original restored and verified.'
}
