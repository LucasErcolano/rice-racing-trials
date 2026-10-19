# Remove only this CSP Lua app. Preserve it first when -BackupDirectory is supplied.
param(
    [Parameter(Mandatory=$true)][string]$AssettoRoot,
    [string]$BackupDirectory,
    [switch]$Preflight
)
$ErrorActionPreference = 'Stop'
$target = Join-Path $AssettoRoot 'apps\lua\RiceTrials'
if (-not (Test-Path -LiteralPath (Join-Path $target 'RiceTrials.lua') -PathType Leaf)) { throw "Not a RiceTrials app folder: $target" }
if ($BackupDirectory) {
    if (-not (Test-Path -LiteralPath $BackupDirectory -PathType Container)) { throw "BackupDirectory missing: $BackupDirectory" }
    $backupRoot = (Resolve-Path -LiteralPath $BackupDirectory).Path
    $appRoot = (Resolve-Path -LiteralPath (Join-Path $AssettoRoot 'apps\lua')).Path
    if ($backupRoot -eq $appRoot -or $backupRoot.StartsWith($appRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'BackupDirectory must be outside Lua apps' }
    $backup = Join-Path $backupRoot ('RiceTrials-uninstalled-' + (Get-Date -Format 'yyyyMMddTHHmmss') + '-' + [guid]::NewGuid().ToString('N'))
}
if ($Preflight) { Write-Host "PASS uninstall preflight: $target; no files changed"; return }
$answer = Read-Host "Remove $target? Type REMOVE RICE TRIALS"
if ($answer -cne 'REMOVE RICE TRIALS') { throw 'Cancelled' }
if ($BackupDirectory) {
    Move-Item -LiteralPath $target -Destination $backup -ErrorAction Stop
    Write-Host "Preserved app at $backup"
} else {
    Remove-Item -LiteralPath $target -Recurse -Force -ErrorAction Stop
}
Write-Host 'CSP app only removed; server database and external AC assets unchanged.'
