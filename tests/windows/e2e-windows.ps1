#Requires -Version 5.1
<#
.SYNOPSIS
  End-to-end test of the Windows kit on a real Windows host (CI runner or VM), without Assetto Corsa.
.DESCRIPTION
  Exercises the real scripts of the "complete" ZIP: install.ps1 (preflight, install, ACL,
  update with backup, rollback, uninstall), configure-secrets.ps1 (DPAPI),
  start-server.ps1 (PyInstaller server), server-admin.ps1 (Check, Backup, Restore, FirewallRule,
  NetworkCheck, RegisterTask) and the HTTP API of the frozen server.

  The Assetto Corsa root is a FIXTURE: folders and text files with the names the installer
  inspects. It does not contain the game, CSP, the track or the car, and proves nothing about them.

  The scripts' Read-Host / Get-Credential prompts are answered by harness functions (the scripts
  are unchanged). Tokens are random, valid only for this run and never written to the evidence:
  at the end the evidence folder is scanned and the run fails if any of them appears.
#>
param(
    [Parameter(Mandatory=$true)][string]$CompleteZip,
    [Parameter(Mandatory=$true)][string]$Evidence,
    [string]$Root = 'C:\RiceE2E',
    [int]$Port = 18090,
    [switch]$SkipServiceAccount
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
if ($env:OS -ne 'Windows_NT') { throw 'This harness runs on Windows only' }

New-Item -ItemType Directory -Force $Evidence | Out-Null
$Evidence = (Resolve-Path $Evidence).Path
$results = New-Object System.Collections.Generic.List[object]
$secretValues = New-Object System.Collections.Generic.List[string]
$transcript = Join-Path $Evidence 'e2e-transcript.log'

function New-Token { $t = 'e2e-' + [guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N').Substring(0, 8); $script:secretValues.Add($t); return $t }
function Log([string]$text) { $line = '{0} {1}' -f (Get-Date).ToUniversalTime().ToString('HH:mm:ss'), $text; Write-Host $line; Add-Content -LiteralPath $transcript -Value $line -Encoding UTF8 }
function Step([string]$name, [scriptblock]$body) {
    $watch = [Diagnostics.Stopwatch]::StartNew()
    Log "=== $name"
    try {
        $out = & $body 2>&1 | Out-String
        $watch.Stop()
        if ($out.Trim()) { Add-Content -LiteralPath $transcript -Value $out.TrimEnd() -Encoding UTF8 }
        $script:results.Add([ordered]@{ step = $name; status = 'pass'; seconds = [math]::Round($watch.Elapsed.TotalSeconds, 1) })
        Log "PASS $name"
    } catch {
        $watch.Stop()
        $msg = ($_ | Out-String).Trim()
        Add-Content -LiteralPath $transcript -Value $msg -Encoding UTF8
        $script:results.Add([ordered]@{ step = $name; status = 'fail'; seconds = [math]::Round($watch.Elapsed.TotalSeconds, 1); error = ($_.Exception.Message) })
        Log "FAIL $name : $($_.Exception.Message)"
    }
}
function Assert([bool]$condition, [string]$message) { if (-not $condition) { throw $message } }
function Expect-Throw([scriptblock]$body, [string]$pattern) {
    try { & $body | Out-Null } catch {
        if ($_.Exception.Message -match $pattern) { return "rejected as expected: $($_.Exception.Message)" }
        throw "failed with a different error: $($_.Exception.Message)"
    }
    throw "expected a rejection ($pattern) but the script continued"
}

# Answers to the scripts' interactive prompts (without modifying them).
$global:RiceAnswers = New-Object System.Collections.Generic.Queue[string]
$global:RicePrompts = New-Object System.Collections.Generic.List[string]
function global:Read-Host {
    param([Parameter(Position=0)][object]$Prompt, [switch]$AsSecureString, [switch]$MaskInput)
    $global:RicePrompts.Add([string]$Prompt)
    if ($global:RiceAnswers.Count -eq 0) { throw "Read-Host without a prepared answer: $Prompt" }
    $value = $global:RiceAnswers.Dequeue()
    if ($AsSecureString) { return (ConvertTo-SecureString $value -AsPlainText -Force) }
    return $value
}
function Answer([string[]]$values) { $global:RiceAnswers.Clear(); foreach ($v in $values) { $global:RiceAnswers.Enqueue($v) } }

function Api([string]$method, [string]$path, $body, [string]$token, [string]$hostName = '127.0.0.1') {
    $params = @{ Method = $method; Uri = "http://${hostName}:$Port$path"; UseBasicParsing = $true; TimeoutSec = 15 }
    if ($token) { $params.Headers = @{ Authorization = "Bearer $token" } }
    if ($null -ne $body) { $params.Body = ($body | ConvertTo-Json -Depth 6 -Compress); $params.ContentType = 'application/json' }
    try {
        $r = Invoke-WebRequest @params
        return [pscustomobject]@{ code = [int]$r.StatusCode; text = [string]$r.Content }
    } catch [System.Net.WebException] {
        $resp = $_.Exception.Response
        if ($null -eq $resp) { throw }
        $reader = New-Object IO.StreamReader($resp.GetResponseStream())
        return [pscustomobject]@{ code = [int]$resp.StatusCode; text = $reader.ReadToEnd() }
    }
}
function Wait-Http([string]$hostName = '127.0.0.1', [int]$seconds = 40) {
    $deadline = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $deadline) {
        try { $r = Invoke-WebRequest "http://${hostName}:$Port/tv" -UseBasicParsing -TimeoutSec 3; if ($r.StatusCode -eq 200) { return $true } } catch {}
        Start-Sleep -Milliseconds 500
    }
    return $false
}
function Stop-RiceServer {
    # Launcher first: start-server.ps1 now supervises the exe and would relaunch it.
    if ($script:launcher -and -not $script:launcher.HasExited) { Stop-Process -Id $script:launcher.Id -Force -ErrorAction SilentlyContinue }
    Get-Process RiceTrialsServer -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    $deadline = (Get-Date).AddSeconds(20)
    while ((Get-Date) -lt $deadline -and @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue).Count -gt 0) { Start-Sleep -Milliseconds 300 }
}
function Save-ServerLogs([string]$tag) {
    # Launcher + server stdout/stderr go to evidence (the final leak scan covers them).
    $n = 0
    foreach ($dir in @((Join-Path $data 'logs'), (Join-Path $Root 'launcher'))) {
        if (-not (Test-Path $dir)) { continue }
        foreach ($f in Get-ChildItem $dir -File -Filter '*.log') {
            Copy-Item $f.FullName (Join-Path $Evidence ("$tag-" + $f.Name)) -Force; $n++
            $tail = @(Get-Content $f.FullName -Tail 15 -ErrorAction SilentlyContinue) -join "`n"
            if ($tail) { Log "--- $($f.Name) (tail):`n$tail" }
        }
    }
    Log "server logs copied: $n"
}
function Start-RiceServer([string]$secrets, [string]$hostName = '127.0.0.1', [switch]$Lan) {
    $launchArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$release\start-server.ps1`"", '-SecretsPath', "`"$secrets`"",
                    '-DatabasePath', "`"$db`"", '-HostAddress', $hostName, '-Port', $Port)
    if ($Lan) { $launchArgs += '-AllowLanBind' }
    $ldir = Join-Path $Root 'launcher'; New-Item -ItemType Directory -Force $ldir | Out-Null
    $stamp = Get-Date -Format 'HHmmss'
    $script:launcher = Start-Process powershell.exe -ArgumentList $launchArgs -PassThru -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $ldir "launcher-$stamp.out.log") -RedirectStandardError (Join-Path $ldir "launcher-$stamp.err.log")
    if (-not (Wait-Http $hostName)) {
        $state = if ($script:launcher.HasExited) { "launcher exited with code $($script:launcher.ExitCode)" } else { 'launcher still alive' }
        Save-ServerLogs 'fail'
        throw "the server did not answer /tv on ${hostName}:$Port ($state)"
    }
}
function Tree-Hash([string]$path, [string]$exclude) {
    (Get-ChildItem -LiteralPath $path -Recurse -File | Where-Object { -not $_.FullName.StartsWith($exclude, [StringComparison]::OrdinalIgnoreCase) } |
        Sort-Object FullName | ForEach-Object { $_.FullName.Substring($path.Length) + '=' + (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash }) -join "`n"
}
function Decode-LuaBytes([string]$text, [string]$field) {
    if ($text -notmatch ($field + '="((?:\\\d{3})+)"')) { return $null }
    $bytes = [byte[]]@($Matches[1].Substring(1).Split('\') | ForEach-Object { [byte][int]$_ })
    return [Text.Encoding]::UTF8.GetString($bytes)
}

# ------------------------------------------------------------------ setup
if (Test-Path $Root) { Remove-Item $Root -Recurse -Force }
$release = Join-Path $Root 'release'
$ac = Join-Path $Root 'fixture-ac'
$data = Join-Path $Root 'data'
$backups = Join-Path $Root 'app-backups'
$secretsDir = Join-Path $Root 'secrets'
$db = Join-Path $data 'trials.sqlite'
foreach ($d in @($release, $data, $backups, $secretsDir)) { New-Item -ItemType Directory -Force $d | Out-Null }

$env_info = [ordered]@{
    os = (Get-CimInstance Win32_OperatingSystem | ForEach-Object { "$($_.Caption) $($_.Version) build $($_.BuildNumber)" })
    powershell = $PSVersionTable.PSVersion.ToString(); edition = $PSVersionTable.PSEdition
    user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    zip = (Split-Path -Leaf $CompleteZip); zip_sha256 = (Get-FileHash -LiteralPath $CompleteZip -Algorithm SHA256).Hash.ToLower()
    started_utc = (Get-Date).ToUniversalTime().ToString('o')
}
Log ("Environment: " + ($env_info | ConvertTo-Json -Compress))

Step 'Extract complete ZIP' {
    Expand-Archive -LiteralPath $CompleteZip -DestinationPath $release -Force
    foreach ($f in @('install.ps1','uninstall.ps1','start-server.ps1','configure-secrets.ps1','server-admin.ps1','RiceTrials\RiceTrials.lua','RiceTrials\native_overlay.lua','RiceTrials\native_render.lua','RiceTrialsServer\RiceTrialsServer.exe')) {
        Assert (Test-Path (Join-Path $release $f)) "$f missing from the ZIP"
    }
    Assert (-not (Test-Path (Join-Path $release 'RiceTrials\station_config.lua'))) 'the ZIP ships station_config.lua'
    'contents OK'
}

Step 'AC root fixture (not the game)' {
    $track = Join-Path $ac 'content\tracks\FSAEJ_Endurance'
    $car = Join-Path $ac 'content\cars\madformulateam_mft02\data'
    foreach ($d in @($track, (Join-Path $track 'ui'), $car, (Join-Path $ac 'apps\lua'), (Join-Path $ac 'extension\config'))) { New-Item -ItemType Directory -Force $d | Out-Null }
    Set-Content (Join-Path $track 'FIXTURE_NOT_A_TRACK.kn5') 'fixture: not a KN5 model'
    Set-Content (Join-Path $track 'ui\ui_track.json') '{ "name": "fixture", "version": "fixture-0" }'
    Set-Content (Join-Path $car 'car.ini') '; fixture: not the MFT02 car'
    Set-Content (Join-Path $ac 'dwrite.dll') 'fixture: not the CSP loader'
    Set-Content (Join-Path $ac 'extension\config\data_manifest.ini') "[VERSION]`r`nSHADERS_PATCH=fixture`r`nSHADERS_PATCH_BUILD=0"
    $script:acHashBefore = Tree-Hash $ac (Join-Path $ac 'apps\lua')
    'fixture created'
}

$stationA = New-Token; $adminA = New-Token
$target = Join-Path $ac 'apps\lua\RiceTrials'

# ------------------------------------------------------------------ installer
Step 'install.ps1 refuses to install without source hashes (fail-closed)' {
    Expect-Throw { & "$release\install.ps1" -AssettoRoot $ac -StationId ST01 -ServerUrl "http://127.0.0.1:$Port" } 'Archive hashes required'
}
Step 'install.ps1 rejects HTTP to a public IP' {
    Expect-Throw { & "$release\install.ps1" -AssettoRoot $ac -StationId ST01 -ServerUrl 'http://8.8.8.8:8080' -SkipArchiveHashes -AllowPlainHttpLan } 'requires HTTPS'
}
Step 'install.ps1 verifies SHA-256 and rejects a tampered archive' {
    $fake = Join-Path $Root 'fake-track.zip'; Set-Content $fake 'not the real zip'
    $car = Join-Path $Root 'fake-car.rar'; Set-Content $car 'neither is this'
    $carHash = (Get-FileHash $car -Algorithm SHA256).Hash
    Expect-Throw { & "$release\install.ps1" -AssettoRoot $ac -StationId ST01 -ServerUrl "http://127.0.0.1:$Port" -EnduranceZip $fake -EnduranceSha256 ('0' * 64) -CarArchive $car -CarSha256 $carHash -Preflight } 'SHA-256 mismatch'
}
Step 'install.ps1 -Preflight writes nothing' {
    $report = Join-Path $Evidence 'install-preflight.json'
    & "$release\install.ps1" -AssettoRoot $ac -StationId ST01 -ServerUrl "http://127.0.0.1:$Port" -SkipArchiveHashes -Preflight -ReportPath $report
    Assert (-not (Test-Path $target)) 'preflight created the app folder'
    $r = Get-Content $report -Raw | ConvertFrom-Json
    Assert ($r.csp.version -eq 'fixture') "CSP version not detected: $($r.csp.version)"
    "checks: $(@($r.checks).Count)"
}
Step 'install.ps1 installs with token via Read-Host and restricted ACL' {
    Answer @($stationA)
    $report = Join-Path $Evidence 'install-report.json'
    & "$release\install.ps1" -AssettoRoot $ac -StationId ST01 -ServerUrl "http://127.0.0.1:$Port" -SkipArchiveHashes -EnableDiagnostics -ReportPath $report
    Assert (Test-Path "$target\RiceTrials.lua") 'app not installed'
    foreach ($m in @('native_overlay.lua','native_render.lua')) { Assert (Test-Path "$target\$m") "native overlay not installed: $m" }
    foreach ($m in @('lap_clock.lua','lap_sync.lua')) { Assert (Test-Path "$target\$m") "native lap timing not installed: $m" }
    Assert ((Get-Content "$target\manifest.ini" -Raw) -match 'windowNative') 'manifest lacks the windowNative window'
    Assert ($global:RicePrompts[-1] -match 'Station token') 'did not prompt for the token via Read-Host'
    $config = [IO.File]::ReadAllText("$target\station_config.lua")
    Assert ((Decode-LuaBytes $config 'station_token') -ceq $stationA) 'station_token badly encoded'
    Assert ((Decode-LuaBytes $config 'station_id') -ceq 'ST01') 'station_id badly encoded'
    Assert ($config -match 'diagnostics=true') 'diagnostics=true missing'
    Assert (-not ($config.Contains($stationA))) 'plaintext token (should use decimal escapes)'
    $acl = Get-Acl "$target\station_config.lua"
    Assert $acl.AreAccessRulesProtected 'the ACL inherits permissions'
    $me = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $allowed = @($me, 'S-1-5-32-544', 'S-1-5-18')
    $rules = @($acl.Access | ForEach-Object { [pscustomobject]@{ sid = $_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value; rights = [string]$_.FileSystemRights } })
    foreach ($rule in $rules) { Assert ($allowed -contains $rule.sid) "ACL with a disallowed identity: $($rule.sid)" }
    $mine = @($rules | Where-Object { $_.sid -eq $me })
    Assert (@($mine | Where-Object { $_.rights -match 'Write|Modify|FullControl' }).Count -eq 0 -or $me -eq 'S-1-5-18') 'the user has write access to station_config.lua'
    ($rules | Format-Table -AutoSize | Out-String) | Set-Content (Join-Path $Evidence 'station_config-acl.txt')
    $receipt = Get-Content "$target\install-receipt.json" -Raw | ConvertFrom-Json
    Assert (@($receipt.files).Count -ge 3) 'incomplete receipt'
    "ACL: $(@($rules).Count) rules; receipt with $(@($receipt.files).Count) files"
}
Step 'install.ps1 refuses to update without -BackupDirectory' {
    Expect-Throw { Answer @($stationA); & "$release\install.ps1" -AssettoRoot $ac -StationId ST01 -ServerUrl "http://127.0.0.1:$Port" -SkipArchiveHashes } 'specify -BackupDirectory'
}
Step 'install.ps1 updates with backup, then rollback' {
    $newToken = New-Token
    Answer @($newToken)
    & "$release\install.ps1" -AssettoRoot $ac -StationId ST02 -ServerUrl "http://127.0.0.1:$Port" -SkipArchiveHashes -BackupDirectory $backups
    $saved = @(Get-ChildItem $backups -Directory -Filter 'RiceTrials-*')
    Assert ($saved.Count -eq 1) "expected 1 backup, found $($saved.Count)"
    Assert ((Decode-LuaBytes ([IO.File]::ReadAllText("$target\station_config.lua")) 'station_id') -ceq 'ST02') 'the update was not applied'
    & "$release\install.ps1" -AssettoRoot $ac -Rollback $saved[0].FullName -BackupDirectory $backups
    Assert ((Decode-LuaBytes ([IO.File]::ReadAllText("$target\station_config.lua")) 'station_id') -ceq 'ST01') 'the rollback did not restore ST01'
    "backups after rollback: $(@(Get-ChildItem $backups -Directory).Count)"
}
Step 'uninstall.ps1 preflight, cancellation and uninstall with backup' {
    & "$release\uninstall.ps1" -AssettoRoot $ac -Preflight
    Assert (Test-Path $target) 'preflight deleted the app'
    Expect-Throw { Answer @('no'); & "$release\uninstall.ps1" -AssettoRoot $ac -BackupDirectory $backups } 'Cancelled'
    Assert (Test-Path $target) 'the cancellation deleted the app'
    Answer @('REMOVE RICE TRIALS')
    & "$release\uninstall.ps1" -AssettoRoot $ac -BackupDirectory $backups
    Assert (-not (Test-Path $target)) 'the app is still installed'
    'uninstalled'
}
Step 'Clean reinstall' {
    Answer @($stationA)
    & "$release\install.ps1" -AssettoRoot $ac -StationId ST01 -ServerUrl "http://127.0.0.1:$Port" -SkipArchiveHashes
    Assert (Test-Path "$target\station_config.lua") 'reinstall failed'
    'OK'
}
Step 'AC fixture files left intact' {
    Assert ((Tree-Hash $ac (Join-Path $ac 'apps\lua')) -ceq $script:acHashBefore) 'the installer modified files outside apps\lua'
    'no changes outside apps\lua\RiceTrials'
}

# ------------------------------------------------------------------ DPAPI secrets
$secretsA = Join-Path $secretsDir 'secrets-a.xml'
Step 'configure-secrets.ps1 stores tokens with DPAPI' {
    Answer @($stationA, $adminA)
    & "$release\configure-secrets.ps1" -SecretsPath $secretsA -StationIds @('ST01')
    $raw = [IO.File]::ReadAllText($secretsA)
    Assert (-not $raw.Contains($stationA) -and -not $raw.Contains($adminA)) 'plaintext token in the XML'
    # Export-Clixml serializes the hashtable as <En><S N="Key">AdminToken</S><SS N="Value">hex</SS></En>.
    Assert ($raw -match '<S N="Key">AdminToken</S>\s*<SS N="Value">[0-9a-f]{100,}</SS>') 'AdminToken is not a DPAPI SecureString'
    $back = Import-Clixml $secretsA
    Assert ($back.AdminToken -is [Security.SecureString] -and $back.StationTokens['ST01'] -is [Security.SecureString]) 'Import-Clixml does not return SecureString'
    $back = Import-Clixml $secretsA
    $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($back.AdminToken))
    Assert ($plain -ceq $adminA) 'DPAPI decryption differs'
    'XML encrypted; decrypts for the same user'
}
Step 'configure-secrets.ps1 refuses to overwrite and to save inside the release' {
    Expect-Throw { Answer @('x', 'y'); & "$release\configure-secrets.ps1" -SecretsPath $secretsA -StationIds @('ST01') } 'Refusing to overwrite'
    Expect-Throw { Answer @('x', 'y'); & "$release\configure-secrets.ps1" -SecretsPath (Join-Path $release 's.xml') -StationIds @('ST01') } 'outside release'
}

# ------------------------------------------------------------------ frozen server and API
Step 'start-server.ps1 rejects 0.0.0.0 and a DB inside the release' {
    Expect-Throw { & "$release\start-server.ps1" -SecretsPath $secretsA -DatabasePath $db -HostAddress '0.0.0.0' } 'Bind only|AllowLanBind'
    Expect-Throw { & "$release\start-server.ps1" -SecretsPath $secretsA -DatabasePath (Join-Path $release 'x.sqlite') } 'outside release'
}
Step 'start-server.ps1 starts the PyInstaller exe with DPAPI secrets' {
    Start-RiceServer $secretsA
    Assert ((Get-Process RiceTrialsServer -ErrorAction SilentlyContinue).Count -ge 1) 'process not found'
    $cmd = (Get-CimInstance Win32_Process -Filter "Name='RiceTrialsServer.exe'" | Select-Object -First 1).CommandLine
    Assert (-not $cmd.Contains($adminA) -and -not $cmd.Contains($stationA)) 'token on the server command line'
    'GET /tv 200; tokens kept off the command line'
}
Step 'API: session, pilot, assignment, heartbeat, lap, duplicate, leaderboard, CSV' {
    $r = Api POST '/api/admin/sessions' @{ session_id = 'S1'; name = 'E2E Windows'; active = $true } $adminA; Assert ($r.code -eq 200) "session: $($r.code) $($r.text)"
    $r = Api POST '/api/admin/pilots' @{ pilot_id = 'P1'; name = 'E2E Pilot' } $adminA; Assert ($r.code -eq 200) "pilot: $($r.code) $($r.text)"
    $r = Api POST '/api/admin/assignments' @{ station_id = 'ST01'; session_id = 'S1'; pilot_id = 'P1' } $adminA; Assert ($r.code -eq 200) "assignment: $($r.code) $($r.text)"
    $r = Api GET '/api/station/assignment' $null $stationA; Assert ($r.code -eq 200 -and $r.text -match 'P1') "assignment: $($r.code) $($r.text)"
    $r = Api POST '/api/station/heartbeat' @{ protocol_version = 1; station_id = 'ST01' } $stationA; Assert ($r.code -eq 200) "heartbeat: $($r.code) $($r.text)"
    $lap = @{ protocol_version = 1; station_id = 'ST01'; session_id = 'S1'; pilot_id = 'P1'; lap_id = 'L1'; raw_ms = 61234; events = @{ doo = 0; oc = 0 }; status = 'valid'; event = 'autocross' }
    $r = Api POST '/api/laps' $lap $stationA; Assert ($r.code -eq 200) "lap: $($r.code) $($r.text)"
    $r = Api POST '/api/laps' $lap $stationA; Assert ($r.code -eq 200 -and $r.text -match '"duplicate":\s*true') "retry: $($r.code) $($r.text)"
    $r = Api POST '/api/laps' $lap $adminA; Assert ($r.code -in 401, 403) "wrong token accepted: $($r.code)"
    $r = Api GET '/api/leaderboard?session_id=S1' $null $null; Assert ($r.code -eq 200 -and $r.text -match 'P1') "leaderboard: $($r.code) $($r.text)"
    Assert ($r.text -match '"scoring_armed":\s*false') 'the server does not report scoring_armed=false'
    $r = Api GET '/api/admin/export.csv?table=laps&session_id=S1' $null $adminA; Assert ($r.code -eq 200 -and $r.text -match 'L1') "CSV: $($r.code)"
    $r.text | Set-Content (Join-Path $Evidence 'export-laps.csv') -Encoding UTF8
    $r = Api GET '/api/admin/sessions' $null $null; Assert ($r.code -eq 401) "admin without token: $($r.code)"
    foreach ($page in @('/tv', '/admin')) { $r = Api GET $page $null $null; Assert ($r.code -eq 200) "$page : $($r.code)" }
    'full flow OK'
}
Step 'server-admin.ps1 Backup (token via Read-Host) and Check' {
    Answer @($adminA)
    $out = & "$release\server-admin.ps1" -Action Backup -Port $Port 6>&1 | Out-String
    $script:backupFile = @(Get-ChildItem (Join-Path $data 'backups') -Filter '*.sqlite' | Sort-Object LastWriteTime -Descending)[0].FullName
    Assert (Test-Path $script:backupFile) 'the backup was not created'
    & "$release\server-admin.ps1" -Action Check -DatabasePath $db
    "backup: $(Split-Path -Leaf $script:backupFile)"
}
Step 'Lap after the backup (to test the restore)' {
    $r = Api POST '/api/laps' @{ protocol_version = 1; station_id = 'ST01'; session_id = 'S1'; pilot_id = 'P1'; lap_id = 'L2-after-backup'; raw_ms = 59999; events = @{ doo = 0; oc = 0 }; status = 'valid'; event = 'autocross' } $stationA
    Assert ($r.code -eq 200) "lap 2: $($r.code) $($r.text)"
    'L2 recorded'
}
Step 'server-admin.ps1 Restore refuses while the server is running' {
    Expect-Throw { Answer @('RESTORE'); & "$release\server-admin.ps1" -Action Restore -DatabasePath $db -BackupFile $script:backupFile -Port $Port } 'still listens'
}
Step 'server-admin.ps1 Restore (server stopped) and verification' {
    Stop-RiceServer
    Answer @('RESTORE')
    & "$release\server-admin.ps1" -Action Restore -DatabasePath $db -BackupFile $script:backupFile -Port $Port
    Start-RiceServer $secretsA
    $r = Api GET '/api/admin/export.csv?table=laps&session_id=S1' $null $adminA
    Assert ($r.text -match 'L1' -and $r.text -notmatch 'L2-after-backup') 'the restore did not return to the backup state'
    Stop-RiceServer
    'L1 present, L2 absent: restore correct'
}
Step 'Server restart keeps the leaderboard' {
    Start-RiceServer $secretsA
    $r = Api GET '/api/leaderboard?session_id=S1' $null $null
    Assert ($r.text -match 'P1') 'leaderboard empty after restart'
    Stop-RiceServer
    'persistence OK'
}
Step 'LAN bind to own private IPv4 (-AllowLanBind)' {
    $ip = @(Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -match '^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)' -and $_.AddressState -eq 'Preferred' })[0].IPAddress
    if (-not $ip) { throw 'the runner has no private IPv4' }
    Start-RiceServer $secretsA $ip -Lan
    $r = Api GET '/api/leaderboard?session_id=S1' $null $null $ip
    Stop-RiceServer
    Assert ($r.code -eq 200) "LAN: $($r.code)"
    "server reachable on ${ip}:$Port"
}
Step 'server-admin.ps1 FirewallRule (create, idempotent, remove) and NetworkCheck' {
    $exe = "$release\RiceTrialsServer\RiceTrialsServer.exe"
    & "$release\server-admin.ps1" -Action FirewallRule -Port $Port -ServerExe $exe
    & "$release\server-admin.ps1" -Action FirewallRule -Port $Port -ServerExe $exe
    $rule = Get-NetFirewallRule -DisplayName "RiceTrials Server TCP $Port"
    Assert ($rule.Profile -eq 'Private') "unexpected profile: $($rule.Profile)"
    & "$release\server-admin.ps1" -Action NetworkCheck -Port $Port *>&1 | Out-String | Set-Content (Join-Path $Evidence 'network-check.txt')
    & "$release\server-admin.ps1" -Action FirewallRule -Port $Port -Remove
    Assert (-not (Get-NetFirewallRule -DisplayName "RiceTrials Server TCP $Port" -ErrorAction SilentlyContinue)) 'the rule is still present'
    'Private rule created and removed'
}

# ------------------------------------------------------------------ service account + scheduled task
if (-not $SkipServiceAccount) {
    $svcName = 'ricesvc'
    $svcPassword = 'Aa1!' + [guid]::NewGuid().ToString('N'); $script:secretValues.Add($svcPassword)
    $svcSecure = ConvertTo-SecureString $svcPassword -AsPlainText -Force
    $svcCred = New-Object System.Management.Automation.PSCredential("$env:COMPUTERNAME\$svcName", $svcSecure)
    $secretsSvc = Join-Path $secretsDir 'secrets-svc.xml'
    $stationSvc = New-Token; $adminSvc = New-Token
    $harnessDir = Join-Path $Root 'harness'

    Step 'Create dedicated local account and folder permissions' {
        if (Get-LocalUser -Name $svcName -ErrorAction SilentlyContinue) { Remove-LocalUser -Name $svcName }
        New-LocalUser -Name $svcName -Password $svcSecure -PasswordNeverExpires -AccountNeverExpires -Description 'RiceTrials E2E (temporary)' | Out-Null
        New-Item -ItemType Directory -Force $harnessDir | Out-Null
        foreach ($d in @($release, $data, $secretsDir, $harnessDir)) { & icacls $d /grant "${svcName}:(OI)(CI)M" /T /Q | Out-Null }
        "account $svcName created"
    }
    Step 'configure-secrets.ps1 run by the service account (that account DPAPI)' {
        $answers = Join-Path $harnessDir 'answers.txt'
        [IO.File]::WriteAllLines($answers, @($stationSvc, $adminSvc))
        $helper = Join-Path $harnessDir 'make-secrets.ps1'
        @'
param([string]$Answers, [string]$Script, [string]$Out)
$ErrorActionPreference = 'Stop'
$global:Q = New-Object System.Collections.Generic.Queue[string]
foreach ($l in [IO.File]::ReadAllLines($Answers)) { $global:Q.Enqueue($l) }
function global:Read-Host { param([Parameter(Position=0)][object]$Prompt, [switch]$AsSecureString) $v = $global:Q.Dequeue(); if ($AsSecureString) { ConvertTo-SecureString $v -AsPlainText -Force } else { $v } }
& $Script -SecretsPath $Out -StationIds @('ST01')
'@ | Set-Content -LiteralPath $helper -Encoding UTF8
        $log = Join-Path $harnessDir 'make-secrets.log'
        $p = Start-Process powershell.exe -Credential $svcCred -LoadUserProfile -Wait -PassThru -WindowStyle Hidden -WorkingDirectory $harnessDir `
            -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$helper`"", '-Answers', "`"$answers`"", '-Script', "`"$release\configure-secrets.ps1`"", '-Out', "`"$secretsSvc`"") `
            -RedirectStandardError $log
        Remove-Item $answers -Force
        Assert ($p.ExitCode -eq 0) "configure-secrets as $svcName exited with $($p.ExitCode): $(Get-Content $log -Raw)"
        Assert (Test-Path $secretsSvc) 'secrets-svc.xml was not created'
        "secrets created by $svcName"
    }
    Step 'Another account (the runner admin) CANNOT decrypt the service secrets' {
        try {
            $x = Import-Clixml $secretsSvc
            $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($x.AdminToken))
            if ($plain -ceq $adminSvc) { throw 'DECRYPTED by another account: DPAPI is not bound to the user' }
            'does not decrypt (different value)'
        } catch {
            if ($_.Exception.Message -match 'DECRYPTED') { throw }
            "rejected by DPAPI: $($_.Exception.Message)"
        }
    }
    Step 'server-admin.ps1 RegisterTask + Start-ScheduledTask (dedicated account)' {
        function global:Get-Credential { param([Parameter(Position=0)]$Message) return $svcCred }
        & "$release\server-admin.ps1" -Action RegisterTask -DatabasePath $db -SecretsPath $secretsSvc -Port $Port
        Remove-Item Function:\global:Get-Credential
        $rights = Join-Path $harnessDir 'rights-after.inf'; & secedit /export /areas USER_RIGHTS /cfg $rights | Out-Null
        $svcSid = (New-Object Security.Principal.NTAccount($svcCred.UserName)).Translate([Security.Principal.SecurityIdentifier]).Value
        $batchLine = [string]((Get-Content $rights | Where-Object { $_ -match '^SeBatchLogonRight' }) -join ' ')
        Log "rights after RegisterTask: $batchLine (SID of $svcName = $svcSid)"
        # secedit lists resolvable local accounts by name and the rest as *SID: accept either form.
        $members = @(($batchLine -replace '^SeBatchLogonRight\s*=\s*', '') -split ',' | ForEach-Object { $_.Trim().TrimStart('*') })
        Assert ($members -contains $svcSid -or $members -contains $svcName -or $members -contains "$env:COMPUTERNAME\$svcName") 'RegisterTask did not grant SeBatchLogonRight to the dedicated account'
        $task = Get-ScheduledTask -TaskName RiceTrialsServer
        Assert ($task.Principal.UserId -match $svcName) "unexpected principal: $($task.Principal.UserId)"
        Assert ($task.Triggers[0].CimClass.CimClassName -eq 'MSFT_TaskBootTrigger') 'the trigger is not AtStartup'
        # The Task Scheduler operational log is disabled by default: enable it so failures leave history.
        & wevtutil sl Microsoft-Windows-TaskScheduler/Operational /e:true | Out-Null
        Start-ScheduledTask -TaskName RiceTrialsServer
        if (-not (Wait-Http '127.0.0.1' 60)) {
            $info = Get-ScheduledTaskInfo -TaskName RiceTrialsServer
            Log "task state: $((Get-ScheduledTask -TaskName RiceTrialsServer).State); LastRunTime=$($info.LastRunTime)"
            Log ((& schtasks /query /tn RiceTrialsServer /v /fo list 2>&1) -join "`n")
            try { $rights = Join-Path $harnessDir 'rights.inf'; & secedit /export /areas USER_RIGHTS /cfg $rights | Out-Null
                  Log ((Get-Content $rights | Select-String 'SeBatchLogonRight|SeDenyBatchLogonRight|SeServiceLogonRight') -join "`n") } catch { Log "secedit: $($_.Exception.Message)" }
            Save-ServerLogs 'task'
            try { Get-WinEvent -LogName 'Microsoft-Windows-TaskScheduler/Operational' -MaxEvents 30 -ErrorAction Stop | Where-Object { $_.Message -match 'RiceTrials|ricesvc' } |
                    ForEach-Object { Log "event $($_.Id): $($_.Message -replace '\s+',' ')" } } catch { Log "no task history: $($_.Exception.Message)" }
            throw "the task did not start the server; LastTaskResult=$($info.LastTaskResult)"
        }
        $owner = (Get-CimInstance Win32_Process -Filter "Name='RiceTrialsServer.exe'" | Select-Object -First 1 | Invoke-CimMethod -MethodName GetOwner).User
        Assert ($owner -eq $svcName) "the server runs as $owner"
        "server started by the task as $owner"
    }
    Step 'Task server: previous data kept and service tokens valid' {
        $r = Api GET '/api/leaderboard?session_id=S1' $null $null
        Assert ($r.text -match 'P1') 'leaderboard without previous data'
        $r = Api POST '/api/laps' @{ protocol_version = 1; station_id = 'ST01'; session_id = 'S1'; pilot_id = 'P1'; lap_id = 'L3-task'; raw_ms = 60500; events = @{ doo = 0; oc = 0 }; status = 'valid'; event = 'autocross' } $stationSvc
        Assert ($r.code -eq 200) "lap with the service token: $($r.code) $($r.text)"
        $r = Api POST '/api/laps' @{ protocol_version = 1; station_id = 'ST01'; session_id = 'S1'; pilot_id = 'P1'; lap_id = 'L4-old-token'; raw_ms = 60500; events = @{ doo = 0; oc = 0 }; status = 'valid'; event = 'autocross' } $stationA
        Assert ($r.code -in 401, 403) "old token accepted: $($r.code)"
        'OK'
    }
    Step 'Exe crash: start-server.ps1 relaunches it (supervisor, 10 s)' {
        Get-Process RiceTrialsServer | Stop-Process -Force
        Start-Sleep -Seconds 5
        $info = Get-ScheduledTaskInfo -TaskName RiceTrialsServer
        Log "after killing the exe: state=$((Get-ScheduledTask -TaskName RiceTrialsServer).State) LastTaskResult=$($info.LastTaskResult)"
        if (-not (Wait-Http '127.0.0.1' 150)) {
            $info = Get-ScheduledTaskInfo -TaskName RiceTrialsServer
            throw "did not recover within 150 s (state=$((Get-ScheduledTask -TaskName RiceTrialsServer).State), LastTaskResult=$($info.LastTaskResult))"
        }
        $pid2 = (Get-Process RiceTrialsServer | Select-Object -First 1).Id
        $owner = (Get-CimInstance Win32_Process -Filter "ProcessId=$pid2" | Invoke-CimMethod -MethodName GetOwner).User
        Assert ($owner -eq $svcName) "the relaunched exe runs as $owner"
        Assert ((Get-ScheduledTask -TaskName RiceTrialsServer).State -eq 'Running') 'the task is no longer Running'
        "the server came back by itself (pid $pid2, as $owner)"
    }
    Step 'server-admin.ps1 Stop halts task, supervisor and exe (no relaunch)' {
        & "$release\server-admin.ps1" -Action Stop -Port $Port
        Start-Sleep -Seconds 25   # longer than RestartDelaySeconds: if the supervisor were still alive the exe would come back
        Assert (-not (Get-Process RiceTrialsServer -ErrorAction SilentlyContinue)) 'the exe came back after Stop'
        Assert (@(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue).Count -eq 0) "something listens on $Port"
        Assert ((Get-ScheduledTask -TaskName RiceTrialsServer).State -ne 'Running') 'the task is still Running'
        Start-ScheduledTask -TaskName RiceTrialsServer
        Assert (Wait-Http '127.0.0.1' 60) 'the task did not start again after Stop'
        & "$release\server-admin.ps1" -Action Stop -Port $Port
        'port free 25 s after Stop; the task starts again with Start-ScheduledTask'
    }
    Step 'UnregisterTask and account cleanup' {
        Stop-ScheduledTask -TaskName RiceTrialsServer -ErrorAction SilentlyContinue
        Stop-RiceServer
        & "$release\server-admin.ps1" -Action UnregisterTask
        Assert (-not (Get-ScheduledTask -TaskName RiceTrialsServer -ErrorAction SilentlyContinue)) 'the task is still registered'
        Remove-LocalUser -Name $svcName
        'clean'
    }
}

# ------------------------------------------------------------------ wrap-up and leak check
Stop-RiceServer
$leaks = @()
foreach ($file in Get-ChildItem -LiteralPath $Evidence -Recurse -File) {
    $text = [IO.File]::ReadAllText($file.FullName)
    foreach ($s in $secretValues) { if ($text.Contains($s)) { $leaks += $file.Name } }
}
$results.Add([ordered]@{ step = 'Evidence free of tokens and passwords'; status = $(if ($leaks.Count) { 'fail' } else { 'pass' }); seconds = 0; error = $(if ($leaks.Count) { "found in: $(($leaks | Select-Object -Unique) -join ', ')" } else { $null }) })
if ($leaks.Count) { foreach ($f in ($leaks | Select-Object -Unique)) { Remove-Item (Join-Path $Evidence $f) -Force } }

$failed = @($results | Where-Object { $_.status -eq 'fail' })
$summary = [ordered]@{ environment = $env_info; finished_utc = (Get-Date).ToUniversalTime().ToString('o'); passed = $results.Count - $failed.Count; failed = $failed.Count; steps = $results;
                       note = 'AC root = text fixture. This does NOT validate Assetto Corsa, CSP, the track or the car. A real Windows reboot was not tested.' }
$summary | ConvertTo-Json -Depth 6 | Set-Content (Join-Path $Evidence 'e2e-summary.json') -Encoding UTF8
$md = @("# E2E Windows: $($summary.passed) OK, $($summary.failed) failed", '', "Environment: $($env_info.os); PowerShell $($env_info.powershell) $($env_info.edition); ZIP $($env_info.zip) (sha256 $($env_info.zip_sha256))", '', '| Step | Result | s | Error |', '|---|---|---|---|')
foreach ($r in $results) { $md += "| $($r.step) | $($r.status) | $($r.seconds) | $(([string]$r.error) -replace '\|', '/') |" }
$md += ''; $md += $summary.note
$md -join "`n" | Set-Content (Join-Path $Evidence 'e2e-summary.md') -Encoding UTF8
Log "RESULT: $($summary.passed) OK, $($summary.failed) failed"
if ($failed.Count) { exit 1 }
