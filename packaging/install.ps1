# Install only the CSP Lua app. External game assets are inspected, never copied.
param(
    [Parameter(Mandatory=$true)][string]$AssettoRoot,
    [Parameter(Mandatory=$true)][string]$EnduranceZip,
    [Parameter(Mandatory=$true)][string]$EnduranceSha256,
    [Parameter(Mandatory=$true)][string]$SkidpadZip,
    [Parameter(Mandatory=$true)][string]$SkidpadSha256,
    [Parameter(Mandatory=$true)][string]$CarArchive,
    [Parameter(Mandatory=$true)][string]$CarSha256,
    [switch]$Preflight,
    [string]$BackupDirectory,
    [Parameter(Mandatory=$true)][string]$StationId,
    [Parameter(Mandatory=$true)][string]$ServerUrl
)
$ErrorActionPreference = 'Stop'
$source = Join-Path $PSScriptRoot 'RiceTrials'
$target = Join-Path $AssettoRoot 'apps\lua\RiceTrials'

function Assert-ArchiveHash($label, $path, $expected) {
    if ($expected -cnotmatch '^[0-9a-fA-F]{64}$') { throw "$label expected SHA-256 must be 64 hexadecimal characters" }
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "$label archive missing: $path" }
    $actual = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash
    if ($actual -ine $expected) { throw "$label archive SHA-256 mismatch: expected $expected, actual $actual" }
    Write-Host "PASS $label archive SHA-256: $actual"
}
function Assert-InstalledAsset($label, $folder, $id, $kind) {
    if (-not (Test-Path -LiteralPath $folder -PathType Container)) { throw "$label installed ID missing: $id ($folder)" }
    if ((Split-Path -Leaf $folder) -cne $id) { throw "$label installed ID mismatch: expected $id" }
    if ($kind -eq 'track') {
        $models = @(Get-ChildItem -LiteralPath $folder -Filter '*.kn5' -File)
        if ($models.Count -eq 0) { throw "$label installed ID $id has no root KN5 model" }
    } else {
        $acd = Test-Path -LiteralPath (Join-Path $folder 'data.acd') -PathType Leaf
        $unpacked = Test-Path -LiteralPath (Join-Path $folder 'data\car.ini') -PathType Leaf
        if (-not ($acd -or $unpacked)) { throw "$label installed ID $id has neither data.acd nor data\car.ini" }
    }
    Write-Host "PASS $label installed ID: $id"
}

function ConvertTo-LuaByteString([string]$value) {
    # Decimal byte escapes: no interpolated source text, quotes or newlines can break Lua syntax.
    $encoded = ([Text.Encoding]::UTF8.GetBytes($value) | ForEach-Object { '\' + $_.ToString('D3') }) -join ''
    return '"' + $encoded + '"'
}
function Protect-StationConfig([string]$path) {
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

# Fail closed before any write. Hashes must come from independently approved publisher/operator records.
if (-not (Test-Path -LiteralPath $AssettoRoot -PathType Container)) { throw "AC root missing: $AssettoRoot" }
if (-not (Test-Path -LiteralPath $source -PathType Container)) { throw "Extract the client or complete ZIP first: $source" }
if (Test-Path -LiteralPath (Join-Path $source 'station_config.lua')) { throw 'Source package contains a station secret; refusing to install or copy it' }
if ($StationId -cnotmatch '^[A-Za-z0-9_.-]{1,80}$') { throw 'Invalid station ID' }
if ($ServerUrl -cnotmatch '^https?://[A-Za-z0-9.-]+(:[0-9]{1,5})?$') { throw 'ServerUrl must match client endpoint syntax, without path or credentials' }
$serverUri = [Uri]$ServerUrl
if ($serverUri.Port -lt 1 -or $serverUri.Port -gt 65535) { throw 'Invalid server port' }
if ($serverUri.Scheme -ne 'https' -and $serverUri.Host -notin @('127.0.0.1','localhost')) { throw 'Non-loopback server requires HTTPS' }
foreach ($name in @('RiceTrials.lua','core.lua','manifest.ini')) {
    if (-not (Test-Path -LiteralPath (Join-Path $source $name) -PathType Leaf)) { throw "Client package missing $name" }
}
$apps = Join-Path $AssettoRoot 'apps\lua'
if (-not (Test-Path -LiteralPath $apps -PathType Container)) { throw "CSP Lua apps folder not found: $apps" }
if (-not (Test-Path -LiteralPath (Join-Path $AssettoRoot 'dwrite.dll') -PathType Leaf)) { throw 'CSP loader dwrite.dll missing; CSP runtime must still be verified in-game' }
Assert-ArchiveHash 'Endurance' $EnduranceZip $EnduranceSha256
Assert-ArchiveHash 'Skidpad' $SkidpadZip $SkidpadSha256
Assert-ArchiveHash 'MFT02' $CarArchive $CarSha256
Assert-InstalledAsset 'Endurance' (Join-Path $AssettoRoot 'content\tracks\FSAEJ_Endurance') 'FSAEJ_Endurance' 'track'
Assert-InstalledAsset 'Skidpad' (Join-Path $AssettoRoot 'content\tracks\FSAEJ_Skidpad') 'FSAEJ_Skidpad' 'track'
Assert-InstalledAsset 'MFT02' (Join-Path $AssettoRoot 'content\cars\madformulateam_mft02') 'madformulateam_mft02' 'car'
$existing = Test-Path -LiteralPath $target
if ($existing -and -not $BackupDirectory) { throw "Existing CSP app at $target; specify -BackupDirectory for a reversible upgrade." }
if ($BackupDirectory) {
    if (-not (Test-Path -LiteralPath $BackupDirectory -PathType Container)) { throw "BackupDirectory must exist: $BackupDirectory" }
    $backupRoot = (Resolve-Path -LiteralPath $BackupDirectory).Path
    $appRoot = (Resolve-Path -LiteralPath $apps).Path
    if ($backupRoot -eq $appRoot -or $backupRoot.StartsWith($appRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'BackupDirectory must be outside the Lua apps directory'
    }
    $backup = Join-Path $backupRoot ("RiceTrials-" + (Get-Date -Format 'yyyyMMddTHHmmss') + '-' + [guid]::NewGuid().ToString('N'))
}
if ($Preflight) { Write-Host 'PASS preflight; no files changed. Archive hashes do not prove installed bytes or commissioning.'; return }
$staging = Join-Path $apps ('.RiceTrials-install-' + [guid]::NewGuid().ToString('N'))
$oldMoved = $false
try {
    New-Item -ItemType Directory -Path $staging -ErrorAction Stop | Out-Null
    foreach ($file in @(Get-ChildItem -LiteralPath $source -File | Where-Object { $_.Extension -eq '.lua' -or $_.Name -eq 'manifest.ini' })) {
        Copy-Item -LiteralPath $file.FullName -Destination $staging -ErrorAction Stop
    }
    foreach ($name in @('RiceTrials.lua','core.lua','manifest.ini')) {
        if (-not (Test-Path -LiteralPath (Join-Path $staging $name) -PathType Leaf)) { throw "Incomplete staged install: $name" }
    }
    Protect-StagingDirectory $staging
    $secret = Read-Host 'Station token (entered locally; never passed on command line)' -AsSecureString
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
    try {
        $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
        if (-not $plain) { throw 'Station token cannot be empty' }
        $config = 'return {server_url=' + (ConvertTo-LuaByteString $ServerUrl) +
                  ', station_id=' + (ConvertTo-LuaByteString $StationId) +
                  ', station_token=' + (ConvertTo-LuaByteString $plain) + "}`n"
        $configPath = Join-Path $staging 'station_config.lua'
        [IO.File]::WriteAllText($configPath, $config, [Text.UTF8Encoding]::new($false))
        Protect-StationConfig $configPath
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
        $plain = $null
        $config = $null
        $secret = $null
    }
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
if ($oldMoved) { Write-Host "Previous app backed up to $backup" }
Write-Host "Installed CSP Lua app to $target; enable and validate in CSP. No external assets changed."
