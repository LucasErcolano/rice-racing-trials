# Windows DPAPI-bound secrets: run once under the same account that launches the server.
param(
    [Parameter(Mandatory=$true)][string]$SecretsPath,
    [Parameter(Mandatory=$true)][string[]]$StationIds
)
$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') { throw 'Windows DPAPI required' }
if (-not [IO.Path]::IsPathRooted($SecretsPath)) { throw 'SecretsPath must be absolute and outside release directory' }
if (Test-Path -LiteralPath $SecretsPath) { throw 'Refusing to overwrite existing secrets' }
if (-not $StationIds -or $StationIds.Count -eq 0) { throw 'At least one station required' }
$stations = @{}
foreach ($id in $StationIds) {
    if ($id -cnotmatch '^[A-Za-z0-9_.-]{1,80}$' -or $stations.ContainsKey($id)) { throw "Invalid or duplicate station ID: $id" }
    $stations[$id] = Read-Host "Token for station $id" -AsSecureString
}
$admin = Read-Host 'Admin token' -AsSecureString
$secrets = @{ AdminToken = $admin; StationTokens = $stations }
$parent = Split-Path -Parent $SecretsPath
if (-not (Test-Path -LiteralPath $parent -PathType Container)) { throw "Secrets parent directory missing: $parent" }
$secretParent = (Resolve-Path -LiteralPath $parent).Path
$releaseRoot = (Resolve-Path -LiteralPath $PSScriptRoot).Path
if ($secretParent -eq $releaseRoot -or $secretParent.StartsWith($releaseRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'Secrets must live outside release directory' }
$secrets | Export-Clixml -LiteralPath $SecretsPath -NoClobber
Write-Host 'DPAPI secrets saved for this Windows user/machine; restrict file ACL, back up separately, never put in dist/ or release ZIP.'
