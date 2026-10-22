#Requires -Version 5.1
<#
.SYNOPSIS
  Installs only the CSP Lua app "RiceTrials" into <AC>\apps\lua\RiceTrials. Never copies or modifies
  game, CSP, track or car files (they are inspected, never written).
.DESCRIPTION
  1. Detects Assetto Corsa: -AssettoRoot, or the Steam registry (Steam App 244210, SteamPath +
     libraryfolders.vdf), or typical paths.
  2. Identifies CSP: dwrite.dll (loader) + extension\config\data_manifest.ini ([VERSION] SHADERS_PATCH).
  3. Checks that madformulateam_mft02 and FSAEJ_Endurance (and FSAEJ_Skidpad) are installed and detects
     versions (ui_car.json / ui_track.json when present). Optional: SHA-256 of the original downloaded archives.
  4. -Preflight: writes nothing. Without -Preflight: staging, token via Read-Host -AsSecureString
     (never on the command line), station_config.lua with a restricted ACL, backup and rollback.
  5. -Rollback <backup folder>: restores a previous app version (saves the current one first).
  The HUD does not open by itself: CSP documents no manifest flag to open the window on load
  (see docs/deployment.md, manual step in the apps bar).
#>
param(
    [string]$AssettoRoot,
    [string]$EnduranceZip,
    [string]$EnduranceSha256,
    [string]$SkidpadZip,
    [string]$SkidpadSha256,
    [string]$CarArchive,
    [string]$CarSha256,
    [switch]$SkipArchiveHashes,
    [switch]$RequireSkidpad,
    [switch]$Preflight,
    [string]$BackupDirectory,
    [string]$StationId,
    [string]$ServerUrl,
    [switch]$AllowPlainHttpLan,
    [string]$RouteConfig,
    [string]$ConeConfig,
    [switch]$EnableDiagnostics,
    [switch]$ProtocolV2,
    [string]$Rollback,
    [string]$ReportPath
)
$ErrorActionPreference = 'Stop'
$CarId = 'madformulateam_mft02'
$source = Join-Path $PSScriptRoot 'RiceTrials'
$onWindows = ($env:OS -eq 'Windows_NT')
$report = [ordered]@{ tool = 'install.ps1'; utc = (Get-Date).ToUniversalTime().ToString('o'); preflight = [bool]$Preflight; checks = @() }

function P([string]$base, [string[]]$parts) {
    # Portable path join (also lets the Linux pwsh harness exercise this script).
    $result = $base
    foreach ($part in $parts) { $result = [IO.Path]::Combine($result, $part) }
    return $result
}
function Pass([string]$name, [string]$detail) {
    Write-Host "PASS $name : $detail"
    $script:report.checks += [ordered]@{ name = $name; status = 'pass'; detail = $detail }
}
function Warn([string]$name, [string]$detail) {
    Write-Warning "$name : $detail"
    $script:report.checks += [ordered]@{ name = $name; status = 'warn'; detail = $detail }
}
function Save-Report {
    if ($ReportPath) {
        $script:report | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $ReportPath -Encoding UTF8
        Write-Host "Report: $ReportPath"
    }
}
function Read-IniValue([string]$path, [string]$section, [string]$key) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { return $null }
    $current = ''
    foreach ($line in [IO.File]::ReadAllLines($path)) {
        $text = ($line -replace '[;].*$', '').Trim()
        if ($text -match '^\[(.+)\]$') { $current = $Matches[1].Trim(); continue }
        if ($current -ieq $section -and $text -match '^([^=]+)=(.*)$' -and $Matches[1].Trim() -ieq $key) { return $Matches[2].Trim() }
    }
    return $null
}
function Read-JsonVersion([string]$path) {
    # Mod ui_*.json files are often hand edited (tabs, trailing commas): extract only "version".
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { return $null }
    $text = [IO.File]::ReadAllText($path)
    if ($text -match '"version"\s*:\s*"([^"]{1,40})"') { return $Matches[1] }
    return $null
}

function Find-AssettoRoot {
    $candidates = New-Object System.Collections.Generic.List[string]
    if ($onWindows) {
        foreach ($key in @('HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Steam App 244210',
                           'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Steam App 244210')) {
            $item = Get-ItemProperty -LiteralPath $key -ErrorAction SilentlyContinue
            if ($item -and $item.InstallLocation) { $candidates.Add([string]$item.InstallLocation) }
        }
        $steamRoots = @()
        $hkcu = Get-ItemProperty -LiteralPath 'HKCU:\Software\Valve\Steam' -ErrorAction SilentlyContinue
        if ($hkcu -and $hkcu.SteamPath) { $steamRoots += ([string]$hkcu.SteamPath -replace '/', '\') }
        foreach ($key in @('HKLM:\SOFTWARE\WOW6432Node\Valve\Steam', 'HKLM:\SOFTWARE\Valve\Steam')) {
            $item = Get-ItemProperty -LiteralPath $key -ErrorAction SilentlyContinue
            if ($item -and $item.InstallPath) { $steamRoots += [string]$item.InstallPath }
        }
        foreach ($steam in ($steamRoots | Select-Object -Unique)) {
            $libraries = @($steam)
            $vdf = P $steam @('steamapps', 'libraryfolders.vdf')
            if (Test-Path -LiteralPath $vdf -PathType Leaf) {
                foreach ($match in [regex]::Matches([IO.File]::ReadAllText($vdf), '"path"\s+"([^"]+)"')) {
                    $libraries += ($match.Groups[1].Value -replace '\\\\', '\')
                }
            }
            foreach ($library in $libraries) { $candidates.Add((P $library @('steamapps', 'common', 'assettocorsa'))) }
        }
        foreach ($typical in @("${env:ProgramFiles(x86)}\Steam\steamapps\common\assettocorsa",
                               "$env:ProgramFiles\Steam\steamapps\common\assettocorsa",
                               'C:\SteamLibrary\steamapps\common\assettocorsa', 'D:\SteamLibrary\steamapps\common\assettocorsa',
                               'E:\SteamLibrary\steamapps\common\assettocorsa')) { $candidates.Add($typical) }
    }
    foreach ($candidate in ($candidates | Select-Object -Unique)) {
        if ($candidate -and (Test-Path -LiteralPath (P $candidate @('content', 'tracks')) -PathType Container)) { return $candidate }
    }
    return $null
}

function Assert-ArchiveHash($label, $path, $expected) {
    if ($expected -cnotmatch '^[0-9a-fA-F]{64}$') { throw "$label expected SHA-256 must be 64 hexadecimal characters" }
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "$label archive missing: $path" }
    $actual = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash
    if ($actual -ine $expected) { throw "$label archive SHA-256 mismatch: expected $expected, actual $actual" }
    Pass "$label archive SHA-256" $actual
}
function Assert-InstalledAsset($label, $folder, $id, $kind) {
    if (-not (Test-Path -LiteralPath $folder -PathType Container)) { throw "$label installed ID missing: $id ($folder)" }
    if ((Split-Path -Leaf $folder) -cne $id) { throw "$label installed ID mismatch: expected $id" }
    if ($kind -eq 'track') {
        $models = @(Get-ChildItem -LiteralPath $folder -Filter '*.kn5' -File)
        if ($models.Count -eq 0) { throw "$label installed ID $id has no root KN5 model" }
        $version = Read-JsonVersion (P $folder @('ui', 'ui_track.json'))
        $dummy = P $folder @('Dummy.kn5')
        $dummyHash = if (Test-Path -LiteralPath $dummy -PathType Leaf) { (Get-FileHash -LiteralPath $dummy -Algorithm SHA256).Hash.ToLower() } else { 'absent' }
        Pass "$label installed ID" "$id (root KN5 models: $($models.Count); ui_track.json version: $(if ($version) { $version } else { 'not declared' }); Dummy.kn5 SHA-256: $dummyHash)"
    } else {
        $acd = Test-Path -LiteralPath (P $folder @('data.acd')) -PathType Leaf
        $unpacked = Test-Path -LiteralPath (P $folder @('data', 'car.ini')) -PathType Leaf
        if (-not ($acd -or $unpacked)) { throw "$label installed ID $id has neither data.acd nor data\car.ini" }
        $version = Read-JsonVersion (P $folder @('ui', 'ui_car.json'))
        Pass "$label installed ID" "$id (data: $(if ($acd) { 'data.acd' } else { 'data\car.ini' }); ui_car.json version: $(if ($version) { $version } else { 'not declared' }))"
    }
}
function Test-PrivateHost([string]$hostName) {
    $ip = $null
    if (-not [Net.IPAddress]::TryParse($hostName, [ref]$ip)) { return $false }
    $b = $ip.GetAddressBytes()
    return ($b.Length -eq 4) -and ($b[0] -eq 10 -or ($b[0] -eq 172 -and $b[1] -ge 16 -and $b[1] -le 31) -or ($b[0] -eq 192 -and $b[1] -eq 168))
}
function Assert-OutsideAppFolder([string]$path, [string]$apps, [string]$what) {
    $resolved = (Resolve-Path -LiteralPath $path).Path
    $appRoot = (Resolve-Path -LiteralPath $apps).Path
    if ($resolved -eq $appRoot -or $resolved.StartsWith($appRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw "$what must be outside the Lua apps directory"
    }
    return $resolved
}

function ConvertTo-LuaByteString([string]$value) {
    # Decimal byte escapes: no interpolated source text, quotes or newlines can break Lua syntax.
    $encoded = ([Text.Encoding]::UTF8.GetBytes($value) | ForEach-Object { '\' + $_.ToString('D3') }) -join ''
    return '"' + $encoded + '"'
}
function Protect-StationConfig([string]$path) {
    if (-not $onWindows) { Warn 'ACL' "not Windows: ACL not applied to $path (test harness only)"; return }
    $acl = Get-Acl -LiteralPath $path
    $acl.SetAccessRuleProtection($true, $false)
    $user = [Security.Principal.WindowsIdentity]::GetCurrent().User
    $admins = [Security.Principal.SecurityIdentifier]::new('S-1-5-32-544')
    $system = [Security.Principal.SecurityIdentifier]::new('S-1-5-18')
    $acl.SetAccessRule([Security.AccessControl.FileSystemAccessRule]::new($user, 'ReadAndExecute', 'Allow'))
    $acl.SetAccessRule([Security.AccessControl.FileSystemAccessRule]::new($admins, 'FullControl', 'Allow'))
    $acl.SetAccessRule([Security.AccessControl.FileSystemAccessRule]::new($system, 'FullControl', 'Allow'))
    Set-Acl -LiteralPath $path -AclObject $acl -ErrorAction Stop
}
function Protect-StagingDirectory([string]$path) {
    if (-not $onWindows) { Warn 'ACL' "not Windows: ACL not applied to $path (test harness only)"; return }
    $acl = Get-Acl -LiteralPath $path
    $acl.SetAccessRuleProtection($true, $false)
    $user = [Security.Principal.WindowsIdentity]::GetCurrent().User
    $admins = [Security.Principal.SecurityIdentifier]::new('S-1-5-32-544')
    $system = [Security.Principal.SecurityIdentifier]::new('S-1-5-18')
    foreach ($sid in @($user, $admins, $system)) {
        $acl.SetAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
            $sid, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow'))
    }
    Set-Acl -LiteralPath $path -AclObject $acl -ErrorAction Stop
}
function Write-Receipt([string]$folder) {
    # Lets uninstall.ps1 confirm it removes only files this installer created.
    $files = @(Get-ChildItem -LiteralPath $folder -File | Where-Object { $_.Name -ne 'install-receipt.json' } | ForEach-Object {
        [ordered]@{ name = $_.Name; sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLower() } })
    $receipt = [ordered]@{ product = 'RiceTrials'; installed_utc = (Get-Date).ToUniversalTime().ToString('o'); files = $files;
                           note = 'station_config.lua contains a credential: do not copy this folder off this machine.' }
    $receipt | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (P $folder @('install-receipt.json')) -Encoding UTF8
}

# ---------------------------------------------------------------- detection (read-only)
if (-not $AssettoRoot) {
    $AssettoRoot = Find-AssettoRoot
    if (-not $AssettoRoot) { throw 'AC root not found (Steam registry, libraryfolders.vdf, typical paths); pass -AssettoRoot' }
    Pass 'AC detected' $AssettoRoot
}
if (-not (Test-Path -LiteralPath $AssettoRoot -PathType Container)) { throw "AC root missing: $AssettoRoot" }
$AssettoRoot = (Resolve-Path -LiteralPath $AssettoRoot).Path
$report.assetto_root = $AssettoRoot
$exe = P $AssettoRoot @('AssettoCorsa.exe')
if (Test-Path -LiteralPath $exe -PathType Leaf) {
    $acVersion = (Get-Item -LiteralPath $exe).VersionInfo.FileVersion
    Pass 'AssettoCorsa.exe' "file version: $(if ($acVersion) { $acVersion } else { 'not declared' })"
} else { Warn 'AssettoCorsa.exe' 'not found in the given root' }
$apps = P $AssettoRoot @('apps', 'lua')
$target = P $apps @('RiceTrials')

if ($Rollback) {
    if (-not (Test-Path -LiteralPath (P $Rollback @('RiceTrials.lua')) -PathType Leaf)) { throw "Rollback source is not a RiceTrials backup: $Rollback" }
    $rollbackRoot = Assert-OutsideAppFolder $Rollback $apps 'Rollback source'
    if (-not $BackupDirectory) { throw 'Rollback requires -BackupDirectory to preserve the current app first' }
    $backupRoot = Assert-OutsideAppFolder $BackupDirectory $apps 'BackupDirectory'
    if ($Preflight) { Pass 'rollback preflight' "would restore $rollbackRoot to $target"; Save-Report; return }
    $saved = $null
    if (Test-Path -LiteralPath $target) {
        $saved = P $backupRoot @('RiceTrials-before-rollback-' + (Get-Date -Format 'yyyyMMddTHHmmss') + '-' + [guid]::NewGuid().ToString('N'))
        Move-Item -LiteralPath $target -Destination $saved -ErrorAction Stop
    }
    try {
        Copy-Item -LiteralPath $rollbackRoot -Destination $target -Recurse -ErrorAction Stop
        # Copy-Item creates new files that inherit the apps\lua ACL: re-protect the credential file.
        $restoredConfig = P $target @('station_config.lua')
        if (Test-Path -LiteralPath $restoredConfig -PathType Leaf) { Protect-StationConfig $restoredConfig }
    }
    catch {
        if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Recurse -Force }
        if ($saved) { Move-Item -LiteralPath $saved -Destination $target }
        throw "Rollback failed; previous app restored: $_"
    }
    Write-Host "Rollback OK: $target <- $rollbackRoot$(if ($saved) { "; previous app saved to $saved" })"
    return
}

if (-not $StationId -or -not $ServerUrl) { throw 'StationId and ServerUrl are required' }
if (-not (Test-Path -LiteralPath $source -PathType Container)) { throw "Extract the client or complete ZIP first: $source" }
if (Test-Path -LiteralPath (P $source @('station_config.lua'))) { throw 'Source package contains a station secret; refusing to install or copy it' }
if ($StationId -cnotmatch '^[A-Za-z0-9_.-]{1,80}$') { throw 'Invalid station ID' }
if ($ServerUrl -cnotmatch '^https?://[A-Za-z0-9.-]+(:[0-9]{1,5})?$') { throw 'ServerUrl must match client endpoint syntax, without path or credentials' }
$serverUri = [Uri]$ServerUrl
if ($serverUri.Port -lt 1 -or $serverUri.Port -gt 65535) { throw 'Invalid server port' }
if ($serverUri.Scheme -ne 'https' -and $serverUri.Host -notin @('127.0.0.1','localhost')) {
    if (-not ($AllowPlainHttpLan -and (Test-PrivateHost $serverUri.Host))) { throw 'Non-loopback server requires HTTPS (or -AllowPlainHttpLan with a private IPv4 address)' }
    Warn 'HTTP on LAN' "the token travels unencrypted inside the private network to $($serverUri.Host)"
}
foreach ($name in @('RiceTrials.lua','core.lua','manifest.ini')) {
    if (-not (Test-Path -LiteralPath (P $source @($name)) -PathType Leaf)) { throw "Client package missing $name" }
}
if (-not (Test-Path -LiteralPath $apps -PathType Container)) { throw "CSP Lua apps folder not found: $apps" }
if (-not (Test-Path -LiteralPath (P $AssettoRoot @('dwrite.dll')) -PathType Leaf)) { throw 'CSP loader dwrite.dll missing; CSP runtime must still be verified in-game' }
$cspManifest = P $AssettoRoot @('extension', 'config', 'data_manifest.ini')
$cspVersion = Read-IniValue $cspManifest 'VERSION' 'SHADERS_PATCH'
$cspBuild = Read-IniValue $cspManifest 'VERSION' 'SHADERS_PATCH_BUILD'
if (-not (Test-Path -LiteralPath (P $AssettoRoot @('extension', 'config')) -PathType Container)) { throw 'CSP extension\config folder missing (dwrite.dll alone is not CSP)' }
if ($cspVersion) { Pass 'CSP' "dwrite.dll + extension\config; version $cspVersion (build $cspBuild)" }
else { Warn 'CSP' 'dwrite.dll + extension\config present; version not readable from extension\config\data_manifest.ini' }
$report.csp = [ordered]@{ version = $cspVersion; build = $cspBuild }

if (-not $SkipArchiveHashes) {
    if (-not ($EnduranceZip -and $EnduranceSha256 -and $CarArchive -and $CarSha256)) {
        throw 'Archive hashes required (-EnduranceZip/-EnduranceSha256/-CarArchive/-CarSha256), or pass -SkipArchiveHashes explicitly'
    }
    Assert-ArchiveHash 'Endurance' $EnduranceZip $EnduranceSha256
    Assert-ArchiveHash 'MFT02' $CarArchive $CarSha256
    if ($SkidpadZip -or $SkidpadSha256) { Assert-ArchiveHash 'Skidpad' $SkidpadZip $SkidpadSha256 }
} else { Warn 'archive hashes' 'skipped by -SkipArchiveHashes: no evidence of where the installed mods came from' }
Assert-InstalledAsset 'Endurance' (P $AssettoRoot @('content', 'tracks', 'FSAEJ_Endurance')) 'FSAEJ_Endurance' 'track'
$skid = P $AssettoRoot @('content', 'tracks', 'FSAEJ_Skidpad')
if ($RequireSkidpad -or (Test-Path -LiteralPath $skid)) { Assert-InstalledAsset 'Skidpad' $skid 'FSAEJ_Skidpad' 'track' }
else { Warn 'Skidpad' 'FSAEJ_Skidpad not installed (use -RequireSkidpad to require it)' }
Assert-InstalledAsset 'MFT02' (P $AssettoRoot @('content', 'cars', $CarId)) $CarId 'car'
if ($RouteConfig) {
    if (-not (Test-Path -LiteralPath $RouteConfig -PathType Leaf) -or (Split-Path -Leaf $RouteConfig) -notlike '*.lua') { throw "RouteConfig must be an existing .lua file: $RouteConfig" }
    $routeText = [IO.File]::ReadAllText($RouteConfig)
    if ($routeText -notmatch '(?m)^\s*verified\s*=\s*(true|false)\s*,') { throw 'RouteConfig lacks an explicit verified=true/false (generate it with track/tool.py export-route)' }
    if ($Matches[1] -eq 'true') { Pass 'route_config' 'verified=true (approved by operator)' }
    else { Warn 'route_config' 'verified=false: the client does not arm the route (diagnostics only)' }
}
if ($ConeConfig) {
    if (-not (Test-Path -LiteralPath $ConeConfig -PathType Leaf) -or (Split-Path -Leaf $ConeConfig) -notlike '*.lua') { throw "ConeConfig must be an existing .lua file: $ConeConfig" }
    if ([IO.File]::ReadAllText($ConeConfig) -notmatch "version\s*=\s*'cone-boxes-v1'") { throw 'ConeConfig is not cone-boxes-v1 (generate it with track/tool.py export-cones)' }
    Warn 'cone_config' 'geometric cone candidates: a reviewer aid, NEVER official penalties'
}
$existing = Test-Path -LiteralPath $target
if ($existing -and -not $BackupDirectory) { throw "Existing CSP app at $target; specify -BackupDirectory for a reversible upgrade." }
if ($BackupDirectory) {
    if (-not (Test-Path -LiteralPath $BackupDirectory -PathType Container)) { throw "BackupDirectory must exist: $BackupDirectory" }
    $backupRoot = Assert-OutsideAppFolder $BackupDirectory $apps 'BackupDirectory'
    $backup = P $backupRoot @("RiceTrials-" + (Get-Date -Format 'yyyyMMddTHHmmss') + '-' + [guid]::NewGuid().ToString('N'))
}
if ($Preflight) {
    Pass 'preflight' 'no files changed. Archive hashes do not prove installed bytes or commissioning.'
    Save-Report
    return
}

# ---------------------------------------------------------------- install (staging + atomic swap + rollback)
$staging = P $apps @('.RiceTrials-install-' + [guid]::NewGuid().ToString('N'))
$oldMoved = $false
try {
    New-Item -ItemType Directory -Path $staging -ErrorAction Stop | Out-Null
    foreach ($file in @(Get-ChildItem -LiteralPath $source -File | Where-Object { ($_.Extension -eq '.lua' -or $_.Name -eq 'manifest.ini') -and $_.Name -notin @('station_config.lua', 'route_config.lua', 'cone_config.lua') })) {
        Copy-Item -LiteralPath $file.FullName -Destination $staging -ErrorAction Stop
    }
    foreach ($name in @('RiceTrials.lua','core.lua','manifest.ini')) {
        if (-not (Test-Path -LiteralPath (P $staging @($name)) -PathType Leaf)) { throw "Incomplete staged install: $name" }
    }
    if ($RouteConfig) { Copy-Item -LiteralPath $RouteConfig -Destination (P $staging @('route_config.lua')) -ErrorAction Stop }
    if ($ConeConfig) { Copy-Item -LiteralPath $ConeConfig -Destination (P $staging @('cone_config.lua')) -ErrorAction Stop }
    Protect-StagingDirectory $staging
    $secret = Read-Host 'Station token (entered locally; never passed on command line)' -AsSecureString
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
    try {
        $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
        if (-not $plain) { throw 'Station token cannot be empty' }
        $extra = if ($EnableDiagnostics) { ', diagnostics=true' } else { '' }
        $extra += if ($ProtocolV2) { ', protocol_v2=true' } else { '' }
        $config = 'return {server_url=' + (ConvertTo-LuaByteString $ServerUrl) +
                  ', station_id=' + (ConvertTo-LuaByteString $StationId) +
                  ', station_token=' + (ConvertTo-LuaByteString $plain) + $extra + "}`n"
        $configPath = P $staging @('station_config.lua')
        [IO.File]::WriteAllText($configPath, $config, [Text.UTF8Encoding]::new($false))
        Protect-StationConfig $configPath
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
        $plain = $null
        $config = $null
        $secret = $null
    }
    Write-Receipt $staging
    if ($existing) {
        Move-Item -LiteralPath $target -Destination $backup -ErrorAction Stop
        $oldMoved = $true
    }
    Move-Item -LiteralPath $staging -Destination $target -ErrorAction Stop
} catch {
    if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
    if ($oldMoved) {
        if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Recurse -Force }
        Move-Item -LiteralPath $backup -Destination $target -ErrorAction Stop
    }
    throw "Install failed; Restore previous app if present: $_"
}
if ($oldMoved) { Write-Host "Previous app backed up to $backup (rollback: install.ps1 -Rollback '$backup' -BackupDirectory <dir>)" }
Save-Report
Write-Host "Installed CSP Lua app to $target; enable and validate in CSP. No external assets changed."
Write-Host 'Manual step: in AC open the apps bar (right edge) and enable "Rice Trials" (CSP documents no automatic opening).'
