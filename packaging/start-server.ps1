# Foreground server launcher. Secrets are DPAPI encrypted under this Windows account.
param(
    [Parameter(Mandatory=$true)][string]$SecretsPath,
    [Parameter(Mandatory=$true)][string]$DatabasePath,
    [string]$ServerExe = (Join-Path $PSScriptRoot 'RiceTrialsServer\RiceTrialsServer.exe'),
    [string]$HostAddress = '127.0.0.1',
    [ValidateRange(1,65535)][int]$Port = 8080
)
$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') { throw 'Windows DPAPI required' }
if (-not [IO.Path]::IsPathRooted($DatabasePath)) { throw 'DatabasePath must be absolute and outside the release bundle' }
if (-not (Test-Path -LiteralPath (Split-Path -Parent $DatabasePath) -PathType Container)) { throw 'Database parent directory missing' }
$dbParent = (Resolve-Path -LiteralPath (Split-Path -Parent $DatabasePath)).Path
$releaseRoot = (Resolve-Path -LiteralPath $PSScriptRoot).Path
if ($dbParent -eq $releaseRoot -or $dbParent.StartsWith($releaseRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'Database must live outside release directory' }
if (-not (Test-Path -LiteralPath $ServerExe -PathType Leaf)) { throw "Server executable missing: $ServerExe" }
if (-not (Test-Path -LiteralPath $SecretsPath -PathType Leaf)) { throw "Secrets missing: $SecretsPath" }
if ($HostAddress -ne '127.0.0.1') { throw 'Bind only to 127.0.0.1; use an authenticated TLS reverse proxy for remote access' }
function Reveal([Security.SecureString]$secure) {
    if ($null -eq $secure) { throw 'Invalid protected token' }
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
}
try {
    $secrets = Import-Clixml -LiteralPath $SecretsPath
    $admin = Reveal $secrets.AdminToken
    $stations = @{}
    foreach ($id in $secrets.StationTokens.Keys) {
        if ($id -cnotmatch '^[A-Za-z0-9_.-]{1,80}$') { throw 'Invalid station ID' }
        $stations[$id] = Reveal $secrets.StationTokens[$id]
    }
    if (-not $admin -or $stations.Count -eq 0 -or @($stations.Values | Where-Object { -not $_ -or $_ -ceq $admin }).Count -gt 0 -or @($stations.Values | Select-Object -Unique).Count -ne $stations.Count) { throw 'Tokens must be nonempty and distinct' }
    $env:RICE_ADMIN_TOKEN = $admin
    $env:RICE_STATION_TOKENS = ConvertTo-Json -InputObject $stations -Compress -Depth 3
    & $ServerExe --db $DatabasePath --host $HostAddress --port $Port
    if ($LASTEXITCODE -ne 0) { throw "Server exited with code $LASTEXITCODE" }
} finally {
    Remove-Item Env:RICE_ADMIN_TOKEN -ErrorAction SilentlyContinue
    Remove-Item Env:RICE_STATION_TOKENS -ErrorAction SilentlyContinue
    $admin = $null
    $stations = $null
    $secrets = $null
}
