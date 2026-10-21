#Requires -Version 5.1
# Remove only this CSP Lua app. Preserve it first when -BackupDirectory is supplied.
# Never touches AC, CSP, tracks, cars or the server database.
param(
    [Parameter(Mandatory=$true)][string]$AssettoRoot,
    [string]$BackupDirectory,
    [switch]$Preflight,
    [switch]$Force
)
$ErrorActionPreference = 'Stop'
function P([string]$base, [string[]]$parts) { $r = $base; foreach ($p in $parts) { $r = [IO.Path]::Combine($r, $p) }; return $r }
$apps = P $AssettoRoot @('apps', 'lua')
$target = P $apps @('RiceTrials')
if (-not (Test-Path -LiteralPath (P $target @('RiceTrials.lua')) -PathType Leaf)) { throw "Not a RiceTrials app folder: $target" }
# Files the installer did not create (e.g. a user note) are reported so they are never lost silently.
$receiptPath = P $target @('install-receipt.json')
$known = @('install-receipt.json', 'station_config.lua', 'route_config.lua', 'cone_config.lua')
if (Test-Path -LiteralPath $receiptPath -PathType Leaf) {
    $receipt = Get-Content -LiteralPath $receiptPath -Raw | ConvertFrom-Json
    $known += @($receipt.files | ForEach-Object { $_.name })
} else {
    Write-Warning 'install-receipt.json ausente (instalación anterior a este instalador): se usan nombres conocidos'
}
$targetFull = (Resolve-Path -LiteralPath $target).Path
$foreign = @(Get-ChildItem -LiteralPath $targetFull -Recurse -File | Where-Object {
    $relative = $_.FullName.Substring($targetFull.Length).TrimStart('\', '/')
    -not (($known -contains $relative) -or ($relative -notmatch '[\\/]' -and $relative -like '*.lua') -or $relative -eq 'manifest.ini' -or $relative -like 'diagnostics*')
})
if ($foreign.Count -gt 0) {
    Write-Warning ('Archivos no creados por el instalador: ' + (($foreign | ForEach-Object { $_.FullName.Substring($targetFull.Length).TrimStart('\', '/') }) -join ', '))
    if (-not $BackupDirectory -and -not $Force) { throw 'Foreign files present: use -BackupDirectory (recommended) or -Force' }
}
if ($BackupDirectory) {
    if (-not (Test-Path -LiteralPath $BackupDirectory -PathType Container)) { throw "BackupDirectory missing: $BackupDirectory" }
    $backupRoot = (Resolve-Path -LiteralPath $BackupDirectory).Path
    $appRoot = (Resolve-Path -LiteralPath $apps).Path
    if ($backupRoot -eq $appRoot -or $backupRoot.StartsWith($appRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'BackupDirectory must be outside Lua apps' }
    $backup = P $backupRoot @('RiceTrials-uninstalled-' + (Get-Date -Format 'yyyyMMddTHHmmss') + '-' + [guid]::NewGuid().ToString('N'))
}
if ($Preflight) { Write-Host "PASS uninstall preflight: $target; no files changed"; return }
$answer = Read-Host "Remove $target? Type REMOVE RICE TRIALS"
if ($answer -cne 'REMOVE RICE TRIALS') { throw 'Cancelled' }
if ($BackupDirectory) {
    Move-Item -LiteralPath $target -Destination $backup -ErrorAction Stop
    Write-Host "Preserved app at $backup (contains station_config.lua: protect or destroy it)"
} else {
    Remove-Item -LiteralPath $target -Recurse -Force -ErrorAction Stop
}
Write-Host 'CSP app only removed; server database and external AC assets unchanged.'
