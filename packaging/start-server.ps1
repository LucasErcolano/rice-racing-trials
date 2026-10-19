#Requires -Version 5.1
# Foreground server launcher. Secrets are DPAPI encrypted under this Windows account.
# Logs: one stdout/stderr pair per start in -LogDirectory (outside the release folder), oldest pruned.
param(
    [Parameter(Mandatory=$true)][string]$SecretsPath,
    [Parameter(Mandatory=$true)][string]$DatabasePath,
    [string]$ServerExe = (Join-Path $PSScriptRoot 'RiceTrialsServer\RiceTrialsServer.exe'),
    [string]$HostAddress = '127.0.0.1',
    [ValidateRange(1,65535)][int]$Port = 8080,
    [switch]$AllowLanBind,
    [string]$LogDirectory,
    [ValidateRange(1,1000)][int]$KeepLogs = 30
)
$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') { throw 'Windows DPAPI required' }
function Assert-OutsideRelease([string]$path, [string]$what) {
    $resolved = (Resolve-Path -LiteralPath $path).Path
    $releaseRoot = (Resolve-Path -LiteralPath $PSScriptRoot).Path
    if ($resolved -eq $releaseRoot -or $resolved.StartsWith($releaseRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw "$what must live outside release directory" }
    return $resolved
}
if (-not [IO.Path]::IsPathRooted($DatabasePath)) { throw 'DatabasePath must be absolute and outside the release bundle' }
if (-not (Test-Path -LiteralPath (Split-Path -Parent $DatabasePath) -PathType Container)) { throw 'Database parent directory missing' }
$dbParent = Assert-OutsideRelease (Split-Path -Parent $DatabasePath) 'Database'
if (-not (Test-Path -LiteralPath $ServerExe -PathType Leaf)) { throw "Server executable missing: $ServerExe" }
if (-not (Test-Path -LiteralPath $SecretsPath -PathType Leaf)) { throw "Secrets missing: $SecretsPath" }
if ($HostAddress -ne '127.0.0.1') {
    # LAN bind only on explicit request, only to a private IPv4 address owned by this machine.
    # Station tokens then cross the LAN in clear HTTP: use an isolated event network, or TLS reverse proxy.
    if (-not $AllowLanBind) { throw 'Bind only to 127.0.0.1, or pass -AllowLanBind with a private IPv4 of this machine (or use a TLS reverse proxy)' }
    $ip = $null
    if (-not [Net.IPAddress]::TryParse($HostAddress, [ref]$ip) -or $ip.AddressFamily -ne 'InterNetwork') { throw 'HostAddress must be an IPv4 literal' }
    $b = $ip.GetAddressBytes()
    if (-not ($b[0] -eq 10 -or ($b[0] -eq 172 -and $b[1] -ge 16 -and $b[1] -le 31) -or ($b[0] -eq 192 -and $b[1] -eq 168))) { throw 'HostAddress must be a private (RFC 1918) address' }
    $local = @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop | ForEach-Object { $_.IPAddress })
    if ($local -notcontains $HostAddress) { throw "HostAddress $HostAddress is not assigned to this machine (local: $($local -join ', '))" }
    Write-Warning "LAN bind on ${HostAddress}:$Port over plain HTTP; tokens are visible to anyone on this network segment."
}
if (-not $LogDirectory) { $LogDirectory = Join-Path $dbParent 'logs' }
if (-not (Test-Path -LiteralPath $LogDirectory -PathType Container)) { New-Item -ItemType Directory -Path $LogDirectory | Out-Null }
$LogDirectory = Assert-OutsideRelease $LogDirectory 'LogDirectory'
$stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
$outLog = Join-Path $LogDirectory "server-$stamp.out.log"
$errLog = Join-Path $LogDirectory "server-$stamp.err.log"
Get-ChildItem -LiteralPath $LogDirectory -Filter 'server-*.log' -File | Sort-Object LastWriteTime -Descending |
    Select-Object -Skip ($KeepLogs * 2) | Remove-Item -Force -ErrorAction SilentlyContinue
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
    # The child process inherits these variables; they never appear on its command line.
    $env:RICE_ADMIN_TOKEN = $admin
    $env:RICE_STATION_TOKENS = ConvertTo-Json -InputObject $stations -Compress -Depth 3
    Write-Host "Starting $ServerExe on ${HostAddress}:$Port; logs: $outLog"
    # Start-Process redirection avoids PowerShell 5.1 turning native stderr into terminating errors.
    $proc = Start-Process -FilePath $ServerExe -ArgumentList @('--db', "`"$DatabasePath`"", '--host', $HostAddress, '--port', $Port) `
        -NoNewWindow -Wait -PassThru -RedirectStandardOutput $outLog -RedirectStandardError $errLog
    if ($proc.ExitCode -ne 0) { throw "Server exited with code $($proc.ExitCode); see $errLog" }
} finally {
    Remove-Item Env:RICE_ADMIN_TOKEN -ErrorAction SilentlyContinue
    Remove-Item Env:RICE_STATION_TOKENS -ErrorAction SilentlyContinue
    $admin = $null
    $stations = $null
    $secrets = $null
}
