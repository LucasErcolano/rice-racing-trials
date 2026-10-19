#Requires -Version 5.1
<#
.SYNOPSIS
  Instala sólo la app Lua de CSP "RiceTrials" en <AC>\apps\lua\RiceTrials. Nunca copia ni modifica
  archivos del juego, de CSP, de la pista ni del auto (se inspeccionan, no se escriben).
.DESCRIPTION
  1. Detecta Assetto Corsa: -AssettoRoot, o registro de Steam (Steam App 244210, SteamPath +
     libraryfolders.vdf), o rutas típicas.
  2. Identifica CSP: dwrite.dll (cargador) + extension\config\data_manifest.ini ([VERSION] SHADERS_PATCH).
  3. Verifica madformulateam_mft02 y FSAEJ_Endurance (y FSAEJ_Skidpad) instalados y detecta versiones
     (ui_car.json / ui_track.json si existen). Opcional: SHA-256 de los archivos originales descargados.
  4. -Preflight: no escribe nada. Sin -Preflight: staging, token con Read-Host -AsSecureString
     (nunca por línea de comandos), station_config.lua con ACL restringida, backup y rollback.
  5. -Rollback <carpeta de backup>: restaura una versión anterior de la app (guarda la actual antes).
  El HUD no se abre solo: CSP no documenta un flag de manifest para abrir la ventana al cargar
  (ver docs/deployment.md, paso manual en la barra de apps).
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
    [switch]$EnableDiagnostics,
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
        Write-Host "Informe: $ReportPath"
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
        $dummyHash = if (Test-Path -LiteralPath $dummy -PathType Leaf) { (Get-FileHash -LiteralPath $dummy -Algorithm SHA256).Hash.ToLower() } else { 'ausente' }
        Pass "$label installed ID" "$id (KN5 en raíz: $($models.Count); versión ui_track.json: $(if ($version) { $version } else { 'no declarada' }); Dummy.kn5 SHA-256: $dummyHash)"
    } else {
        $acd = Test-Path -LiteralPath (P $folder @('data.acd')) -PathType Leaf
        $unpacked = Test-Path -LiteralPath (P $folder @('data', 'car.ini')) -PathType Leaf
        if (-not ($acd -or $unpacked)) { throw "$label installed ID $id has neither data.acd nor data\car.ini" }
        $version = Read-JsonVersion (P $folder @('ui', 'ui_car.json'))
        Pass "$label installed ID" "$id (datos: $(if ($acd) { 'data.acd' } else { 'data\car.ini' }); versión ui_car.json: $(if ($version) { $version } else { 'no declarada' }))"
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
    if (-not $onWindows) { Warn 'ACL' "no es Windows: ACL no aplicada a $path (sólo arnés de pruebas)"; return }
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
    if (-not $onWindows) { Warn 'ACL' "no es Windows: ACL no aplicada a $path (sólo arnés de pruebas)"; return }
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
                           note = 'station_config.lua contiene una credencial: no copiar este directorio fuera del equipo.' }
    $receipt | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (P $folder @('install-receipt.json')) -Encoding UTF8
}

# ---------------------------------------------------------------- detection (read-only)
if (-not $AssettoRoot) {
    $AssettoRoot = Find-AssettoRoot
    if (-not $AssettoRoot) { throw 'AC root not found (Steam registry, libraryfolders.vdf, typical paths); pass -AssettoRoot' }
    Pass 'AC detectado' $AssettoRoot
}
if (-not (Test-Path -LiteralPath $AssettoRoot -PathType Container)) { throw "AC root missing: $AssettoRoot" }
$AssettoRoot = (Resolve-Path -LiteralPath $AssettoRoot).Path
$report.assetto_root = $AssettoRoot
$exe = P $AssettoRoot @('AssettoCorsa.exe')
if (Test-Path -LiteralPath $exe -PathType Leaf) {
    $acVersion = (Get-Item -LiteralPath $exe).VersionInfo.FileVersion
    Pass 'AssettoCorsa.exe' "versión de archivo: $(if ($acVersion) { $acVersion } else { 'no declarada' })"
} else { Warn 'AssettoCorsa.exe' 'no encontrado en la raíz indicada' }
$apps = P $AssettoRoot @('apps', 'lua')
$target = P $apps @('RiceTrials')

if ($Rollback) {
    if (-not (Test-Path -LiteralPath (P $Rollback @('RiceTrials.lua')) -PathType Leaf)) { throw "Rollback source is not a RiceTrials backup: $Rollback" }
    $rollbackRoot = Assert-OutsideAppFolder $Rollback $apps 'Rollback source'
    if (-not $BackupDirectory) { throw 'Rollback requires -BackupDirectory to preserve the current app first' }
    $backupRoot = Assert-OutsideAppFolder $BackupDirectory $apps 'BackupDirectory'
    if ($Preflight) { Pass 'rollback preflight' "restauraría $rollbackRoot en $target"; Save-Report; return }
    $saved = $null
    if (Test-Path -LiteralPath $target) {
        $saved = P $backupRoot @('RiceTrials-before-rollback-' + (Get-Date -Format 'yyyyMMddTHHmmss') + '-' + [guid]::NewGuid().ToString('N'))
        Move-Item -LiteralPath $target -Destination $saved -ErrorAction Stop
    }
    try { Copy-Item -LiteralPath $rollbackRoot -Destination $target -Recurse -ErrorAction Stop }
    catch {
        if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Recurse -Force }
        if ($saved) { Move-Item -LiteralPath $saved -Destination $target }
        throw "Rollback failed; previous app restored: $_"
    }
    Write-Host "Rollback OK: $target <- $rollbackRoot$(if ($saved) { "; app anterior guardada en $saved" })"
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
    Warn 'HTTP en LAN' "el token viaja sin cifrar dentro de la red privada hacia $($serverUri.Host)"
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
if ($cspVersion) { Pass 'CSP' "dwrite.dll + extension\config; versión $cspVersion (build $cspBuild)" }
else { Warn 'CSP' 'dwrite.dll + extension\config presentes; versión no legible en extension\config\data_manifest.ini' }
$report.csp = [ordered]@{ version = $cspVersion; build = $cspBuild }

if (-not $SkipArchiveHashes) {
    if (-not ($EnduranceZip -and $EnduranceSha256 -and $CarArchive -and $CarSha256)) {
        throw 'Archive hashes required (-EnduranceZip/-EnduranceSha256/-CarArchive/-CarSha256), or pass -SkipArchiveHashes explicitly'
    }
    Assert-ArchiveHash 'Endurance' $EnduranceZip $EnduranceSha256
    Assert-ArchiveHash 'MFT02' $CarArchive $CarSha256
    if ($SkidpadZip -or $SkidpadSha256) { Assert-ArchiveHash 'Skidpad' $SkidpadZip $SkidpadSha256 }
} else { Warn 'hashes de archivos' 'omitidos por -SkipArchiveHashes: no hay evidencia del origen de los mods instalados' }
Assert-InstalledAsset 'Endurance' (P $AssettoRoot @('content', 'tracks', 'FSAEJ_Endurance')) 'FSAEJ_Endurance' 'track'
$skid = P $AssettoRoot @('content', 'tracks', 'FSAEJ_Skidpad')
if ($RequireSkidpad -or (Test-Path -LiteralPath $skid)) { Assert-InstalledAsset 'Skidpad' $skid 'FSAEJ_Skidpad' 'track' }
else { Warn 'Skidpad' 'FSAEJ_Skidpad no instalado (use -RequireSkidpad para exigirlo)' }
Assert-InstalledAsset 'MFT02' (P $AssettoRoot @('content', 'cars', $CarId)) $CarId 'car'
if ($RouteConfig) {
    if (-not (Test-Path -LiteralPath $RouteConfig -PathType Leaf) -or (Split-Path -Leaf $RouteConfig) -notlike '*.lua') { throw "RouteConfig must be an existing .lua file: $RouteConfig" }
    $routeText = [IO.File]::ReadAllText($RouteConfig)
    if ($routeText -notmatch '(?m)^\s*verified\s*=\s*(true|false)\s*,') { throw 'RouteConfig lacks an explicit verified=true/false (generate it with track/tool.py export-route)' }
    if ($Matches[1] -eq 'true') { Pass 'route_config' 'verified=true (aprobada por operador)' }
    else { Warn 'route_config' 'verified=false: el cliente no arma la ruta (sólo diagnóstico)' }
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
    foreach ($file in @(Get-ChildItem -LiteralPath $source -File | Where-Object { ($_.Extension -eq '.lua' -or $_.Name -eq 'manifest.ini') -and $_.Name -notin @('station_config.lua', 'route_config.lua') })) {
        Copy-Item -LiteralPath $file.FullName -Destination $staging -ErrorAction Stop
    }
    foreach ($name in @('RiceTrials.lua','core.lua','manifest.ini')) {
        if (-not (Test-Path -LiteralPath (P $staging @($name)) -PathType Leaf)) { throw "Incomplete staged install: $name" }
    }
    if ($RouteConfig) { Copy-Item -LiteralPath $RouteConfig -Destination (P $staging @('route_config.lua')) -ErrorAction Stop }
    Protect-StagingDirectory $staging
    $secret = Read-Host 'Station token (entered locally; never passed on command line)' -AsSecureString
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
    try {
        $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
        if (-not $plain) { throw 'Station token cannot be empty' }
        $extra = if ($EnableDiagnostics) { ', diagnostics=true' } else { '' }
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
Write-Host 'Paso manual: en AC abrir la barra de apps (borde derecho) y activar "Rice Trials" (CSP no documenta apertura automática).'
