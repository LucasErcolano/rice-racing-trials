#Requires -Version 5.1
<#
.SYNOPSIS
  Prueba de punta a punta del kit Windows sobre un Windows real (runner de CI o VM), sin Assetto Corsa.
.DESCRIPTION
  Ejercita los scripts reales del ZIP "complete": install.ps1 (preflight, instalación, ACL,
  actualización con backup, rollback, desinstalación), configure-secrets.ps1 (DPAPI),
  start-server.ps1 (servidor PyInstaller), server-admin.ps1 (Check, Backup, Restore, FirewallRule,
  NetworkCheck, RegisterTask) y la API HTTP del servidor congelado.

  La raíz de Assetto Corsa es un FIXTURE: carpetas y archivos de texto con los nombres que el
  instalador inspecciona. No contiene el juego, CSP, la pista ni el auto, y no prueba nada de eso.

  Los Read-Host / Get-Credential de los scripts se responden con funciones del arnés (los scripts
  no cambian). Los tokens son aleatorios, valen sólo para esta corrida y nunca se escriben en la
  evidencia: al final se escanea la carpeta de evidencia y la corrida falla si aparece alguno.
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
if ($env:OS -ne 'Windows_NT') { throw 'Este arnés corre sólo en Windows' }

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
        if ($_.Exception.Message -match $pattern) { return "rechazado como se esperaba: $($_.Exception.Message)" }
        throw "falló con otro error: $($_.Exception.Message)"
    }
    throw "se esperaba un rechazo ($pattern) y el script siguió"
}

# Respuestas a los prompts interactivos de los scripts (sin modificarlos).
$global:RiceAnswers = New-Object System.Collections.Generic.Queue[string]
$global:RicePrompts = New-Object System.Collections.Generic.List[string]
function global:Read-Host {
    param([Parameter(Position=0)][object]$Prompt, [switch]$AsSecureString, [switch]$MaskInput)
    $global:RicePrompts.Add([string]$Prompt)
    if ($global:RiceAnswers.Count -eq 0) { throw "Read-Host sin respuesta preparada: $Prompt" }
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
            if ($tail) { Log "--- $($f.Name) (cola):`n$tail" }
        }
    }
    Log "logs del servidor copiados: $n"
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
        $state = if ($script:launcher.HasExited) { "lanzador terminó con código $($script:launcher.ExitCode)" } else { 'lanzador sigue vivo' }
        Save-ServerLogs 'fail'
        throw "el servidor no respondió /tv en ${hostName}:$Port ($state)"
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

# ------------------------------------------------------------------ preparación
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
Log ("Entorno: " + ($env_info | ConvertTo-Json -Compress))

Step 'Extraer ZIP complete' {
    Expand-Archive -LiteralPath $CompleteZip -DestinationPath $release -Force
    foreach ($f in @('install.ps1','uninstall.ps1','start-server.ps1','configure-secrets.ps1','server-admin.ps1','RiceTrials\RiceTrials.lua','RiceTrialsServer\RiceTrialsServer.exe')) {
        Assert (Test-Path (Join-Path $release $f)) "falta $f en el ZIP"
    }
    Assert (-not (Test-Path (Join-Path $release 'RiceTrials\station_config.lua'))) 'el ZIP trae station_config.lua'
    'contenido OK'
}

Step 'Fixture de raíz AC (no es el juego)' {
    $track = Join-Path $ac 'content\tracks\FSAEJ_Endurance'
    $car = Join-Path $ac 'content\cars\madformulateam_mft02\data'
    foreach ($d in @($track, (Join-Path $track 'ui'), $car, (Join-Path $ac 'apps\lua'), (Join-Path $ac 'extension\config'))) { New-Item -ItemType Directory -Force $d | Out-Null }
    Set-Content (Join-Path $track 'FIXTURE_NOT_A_TRACK.kn5') 'fixture: no es un modelo KN5'
    Set-Content (Join-Path $track 'ui\ui_track.json') '{ "name": "fixture", "version": "fixture-0" }'
    Set-Content (Join-Path $car 'car.ini') '; fixture: no es el auto MFT02'
    Set-Content (Join-Path $ac 'dwrite.dll') 'fixture: no es el cargador de CSP'
    Set-Content (Join-Path $ac 'extension\config\data_manifest.ini') "[VERSION]`r`nSHADERS_PATCH=fixture`r`nSHADERS_PATCH_BUILD=0"
    $script:acHashBefore = Tree-Hash $ac (Join-Path $ac 'apps\lua')
    'fixture creado'
}

$stationA = New-Token; $adminA = New-Token
$target = Join-Path $ac 'apps\lua\RiceTrials'

# ------------------------------------------------------------------ instalador
Step 'install.ps1 rechaza instalar sin hashes de origen (fail-closed)' {
    Expect-Throw { & "$release\install.ps1" -AssettoRoot $ac -StationId ST01 -ServerUrl "http://127.0.0.1:$Port" } 'Archive hashes required'
}
Step 'install.ps1 rechaza HTTP hacia IP pública' {
    Expect-Throw { & "$release\install.ps1" -AssettoRoot $ac -StationId ST01 -ServerUrl 'http://8.8.8.8:8080' -SkipArchiveHashes -AllowPlainHttpLan } 'requires HTTPS'
}
Step 'install.ps1 verifica SHA-256 y rechaza un archivo alterado' {
    $fake = Join-Path $Root 'fake-track.zip'; Set-Content $fake 'no es el zip real'
    $car = Join-Path $Root 'fake-car.rar'; Set-Content $car 'tampoco'
    $carHash = (Get-FileHash $car -Algorithm SHA256).Hash
    Expect-Throw { & "$release\install.ps1" -AssettoRoot $ac -StationId ST01 -ServerUrl "http://127.0.0.1:$Port" -EnduranceZip $fake -EnduranceSha256 ('0' * 64) -CarArchive $car -CarSha256 $carHash -Preflight } 'SHA-256 mismatch'
}
Step 'install.ps1 -Preflight no escribe nada' {
    $report = Join-Path $Evidence 'install-preflight.json'
    & "$release\install.ps1" -AssettoRoot $ac -StationId ST01 -ServerUrl "http://127.0.0.1:$Port" -SkipArchiveHashes -Preflight -ReportPath $report
    Assert (-not (Test-Path $target)) 'preflight creó la carpeta de la app'
    $r = Get-Content $report -Raw | ConvertFrom-Json
    Assert ($r.csp.version -eq 'fixture') "versión CSP no detectada: $($r.csp.version)"
    "checks: $(@($r.checks).Count)"
}
Step 'install.ps1 instala con token por Read-Host y ACL restringida' {
    Answer @($stationA)
    $report = Join-Path $Evidence 'install-report.json'
    & "$release\install.ps1" -AssettoRoot $ac -StationId ST01 -ServerUrl "http://127.0.0.1:$Port" -SkipArchiveHashes -EnableDiagnostics -ReportPath $report
    Assert (Test-Path "$target\RiceTrials.lua") 'app no instalada'
    Assert ($global:RicePrompts[-1] -match 'Station token') 'no pidió el token por Read-Host'
    $config = [IO.File]::ReadAllText("$target\station_config.lua")
    Assert ((Decode-LuaBytes $config 'station_token') -ceq $stationA) 'station_token mal codificado'
    Assert ((Decode-LuaBytes $config 'station_id') -ceq 'ST01') 'station_id mal codificado'
    Assert ($config -match 'diagnostics=true') 'falta diagnostics=true'
    Assert (-not ($config.Contains($stationA))) 'token en texto plano (debería ir en escapes decimales)'
    $acl = Get-Acl "$target\station_config.lua"
    Assert $acl.AreAccessRulesProtected 'la ACL hereda permisos'
    $me = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $allowed = @($me, 'S-1-5-32-544', 'S-1-5-18')
    $rules = @($acl.Access | ForEach-Object { [pscustomobject]@{ sid = $_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value; rights = [string]$_.FileSystemRights } })
    foreach ($rule in $rules) { Assert ($allowed -contains $rule.sid) "ACL con identidad no permitida: $($rule.sid)" }
    $mine = @($rules | Where-Object { $_.sid -eq $me })
    Assert (@($mine | Where-Object { $_.rights -match 'Write|Modify|FullControl' }).Count -eq 0 -or $me -eq 'S-1-5-18') 'el usuario tiene escritura sobre station_config.lua'
    ($rules | Format-Table -AutoSize | Out-String) | Set-Content (Join-Path $Evidence 'station_config-acl.txt')
    $receipt = Get-Content "$target\install-receipt.json" -Raw | ConvertFrom-Json
    Assert (@($receipt.files).Count -ge 3) 'recibo incompleto'
    "ACL: $(@($rules).Count) reglas; recibo con $(@($receipt.files).Count) archivos"
}
Step 'install.ps1 rechaza actualizar sin -BackupDirectory' {
    Expect-Throw { Answer @($stationA); & "$release\install.ps1" -AssettoRoot $ac -StationId ST01 -ServerUrl "http://127.0.0.1:$Port" -SkipArchiveHashes } 'specify -BackupDirectory'
}
Step 'install.ps1 actualiza con backup y luego rollback' {
    $newToken = New-Token
    Answer @($newToken)
    & "$release\install.ps1" -AssettoRoot $ac -StationId ST02 -ServerUrl "http://127.0.0.1:$Port" -SkipArchiveHashes -BackupDirectory $backups
    $saved = @(Get-ChildItem $backups -Directory -Filter 'RiceTrials-*')
    Assert ($saved.Count -eq 1) "backups esperados 1, hay $($saved.Count)"
    Assert ((Decode-LuaBytes ([IO.File]::ReadAllText("$target\station_config.lua")) 'station_id') -ceq 'ST02') 'la actualización no aplicó'
    & "$release\install.ps1" -AssettoRoot $ac -Rollback $saved[0].FullName -BackupDirectory $backups
    Assert ((Decode-LuaBytes ([IO.File]::ReadAllText("$target\station_config.lua")) 'station_id') -ceq 'ST01') 'el rollback no restauró ST01'
    "backups tras rollback: $(@(Get-ChildItem $backups -Directory).Count)"
}
Step 'uninstall.ps1 preflight, cancelación y desinstalación con backup' {
    & "$release\uninstall.ps1" -AssettoRoot $ac -Preflight
    Assert (Test-Path $target) 'preflight borró la app'
    Expect-Throw { Answer @('no'); & "$release\uninstall.ps1" -AssettoRoot $ac -BackupDirectory $backups } 'Cancelled'
    Assert (Test-Path $target) 'la cancelación borró la app'
    Answer @('REMOVE RICE TRIALS')
    & "$release\uninstall.ps1" -AssettoRoot $ac -BackupDirectory $backups
    Assert (-not (Test-Path $target)) 'la app sigue instalada'
    'desinstalada'
}
Step 'Reinstalación limpia' {
    Answer @($stationA)
    & "$release\install.ps1" -AssettoRoot $ac -StationId ST01 -ServerUrl "http://127.0.0.1:$Port" -SkipArchiveHashes
    Assert (Test-Path "$target\station_config.lua") 'reinstalación falló'
    'OK'
}
Step 'Los archivos del fixture AC quedaron intactos' {
    Assert ((Tree-Hash $ac (Join-Path $ac 'apps\lua')) -ceq $script:acHashBefore) 'el instalador modificó archivos fuera de apps\lua'
    'sin cambios fuera de apps\lua\RiceTrials'
}

# ------------------------------------------------------------------ secretos DPAPI
$secretsA = Join-Path $secretsDir 'secrets-a.xml'
Step 'configure-secrets.ps1 guarda tokens con DPAPI' {
    Answer @($stationA, $adminA)
    & "$release\configure-secrets.ps1" -SecretsPath $secretsA -StationIds @('ST01')
    $raw = [IO.File]::ReadAllText($secretsA)
    Assert (-not $raw.Contains($stationA) -and -not $raw.Contains($adminA)) 'token en texto plano en el XML'
    # Export-Clixml serializa la hashtable como <En><S N="Key">AdminToken</S><SS N="Value">hex</SS></En>.
    Assert ($raw -match '<S N="Key">AdminToken</S>\s*<SS N="Value">[0-9a-f]{100,}</SS>') 'AdminToken no está como SecureString DPAPI'
    $back = Import-Clixml $secretsA
    Assert ($back.AdminToken -is [Security.SecureString] -and $back.StationTokens['ST01'] -is [Security.SecureString]) 'Import-Clixml no devuelve SecureString'
    $back = Import-Clixml $secretsA
    $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($back.AdminToken))
    Assert ($plain -ceq $adminA) 'descifrado DPAPI distinto'
    'XML cifrado; descifra para el mismo usuario'
}
Step 'configure-secrets.ps1 se niega a sobrescribir y a guardar dentro del release' {
    Expect-Throw { Answer @('x', 'y'); & "$release\configure-secrets.ps1" -SecretsPath $secretsA -StationIds @('ST01') } 'Refusing to overwrite'
    Expect-Throw { Answer @('x', 'y'); & "$release\configure-secrets.ps1" -SecretsPath (Join-Path $release 's.xml') -StationIds @('ST01') } 'outside release'
}

# ------------------------------------------------------------------ servidor congelado y API
Step 'start-server.ps1 rechaza 0.0.0.0 y DB dentro del release' {
    Expect-Throw { & "$release\start-server.ps1" -SecretsPath $secretsA -DatabasePath $db -HostAddress '0.0.0.0' } 'Bind only|AllowLanBind'
    Expect-Throw { & "$release\start-server.ps1" -SecretsPath $secretsA -DatabasePath (Join-Path $release 'x.sqlite') } 'outside release'
}
Step 'start-server.ps1 levanta el exe PyInstaller con secretos DPAPI' {
    Start-RiceServer $secretsA
    Assert ((Get-Process RiceTrialsServer -ErrorAction SilentlyContinue).Count -ge 1) 'proceso no encontrado'
    $cmd = (Get-CimInstance Win32_Process -Filter "Name='RiceTrialsServer.exe'" | Select-Object -First 1).CommandLine
    Assert (-not $cmd.Contains($adminA) -and -not $cmd.Contains($stationA)) 'token en la línea de comandos del servidor'
    'GET /tv 200; tokens fuera de la línea de comandos'
}
Step 'API: sesión, piloto, asignación, heartbeat, vuelta, duplicado, leaderboard, CSV' {
    $r = Api POST '/api/admin/sessions' @{ session_id = 'S1'; name = 'E2E Windows'; active = $true } $adminA; Assert ($r.code -eq 200) "sesión: $($r.code) $($r.text)"
    $r = Api POST '/api/admin/pilots' @{ pilot_id = 'P1'; name = 'Piloto E2E' } $adminA; Assert ($r.code -eq 200) "piloto: $($r.code) $($r.text)"
    $r = Api POST '/api/admin/assignments' @{ station_id = 'ST01'; session_id = 'S1'; pilot_id = 'P1' } $adminA; Assert ($r.code -eq 200) "asignación: $($r.code) $($r.text)"
    $r = Api GET '/api/station/assignment' $null $stationA; Assert ($r.code -eq 200 -and $r.text -match 'P1') "assignment: $($r.code) $($r.text)"
    $r = Api POST '/api/station/heartbeat' @{ protocol_version = 1; station_id = 'ST01' } $stationA; Assert ($r.code -eq 200) "heartbeat: $($r.code) $($r.text)"
    $lap = @{ protocol_version = 1; station_id = 'ST01'; session_id = 'S1'; pilot_id = 'P1'; lap_id = 'L1'; raw_ms = 61234; events = @{ doo = 0; oc = 0 }; status = 'valid'; event = 'autocross' }
    $r = Api POST '/api/laps' $lap $stationA; Assert ($r.code -eq 200) "vuelta: $($r.code) $($r.text)"
    $r = Api POST '/api/laps' $lap $stationA; Assert ($r.code -eq 200 -and $r.text -match '"duplicate":\s*true') "reintento: $($r.code) $($r.text)"
    $r = Api POST '/api/laps' $lap $adminA; Assert ($r.code -in 401, 403) "token equivocado aceptado: $($r.code)"
    $r = Api GET '/api/leaderboard?session_id=S1' $null $null; Assert ($r.code -eq 200 -and $r.text -match 'P1') "leaderboard: $($r.code) $($r.text)"
    Assert ($r.text -match '"scoring_armed":\s*false') 'el servidor no reporta scoring_armed=false'
    $r = Api GET '/api/admin/export.csv?table=laps&session_id=S1' $null $adminA; Assert ($r.code -eq 200 -and $r.text -match 'L1') "CSV: $($r.code)"
    $r.text | Set-Content (Join-Path $Evidence 'export-laps.csv') -Encoding UTF8
    $r = Api GET '/api/admin/sessions' $null $null; Assert ($r.code -eq 401) "admin sin token: $($r.code)"
    foreach ($page in @('/tv', '/admin')) { $r = Api GET $page $null $null; Assert ($r.code -eq 200) "$page : $($r.code)" }
    'flujo completo OK'
}
Step 'server-admin.ps1 Backup (token por Read-Host) y Check' {
    Answer @($adminA)
    $out = & "$release\server-admin.ps1" -Action Backup -Port $Port 6>&1 | Out-String
    $script:backupFile = @(Get-ChildItem (Join-Path $data 'backups') -Filter '*.sqlite' | Sort-Object LastWriteTime -Descending)[0].FullName
    Assert (Test-Path $script:backupFile) 'no se creó el backup'
    & "$release\server-admin.ps1" -Action Check -DatabasePath $db
    "backup: $(Split-Path -Leaf $script:backupFile)"
}
Step 'Vuelta posterior al backup (para probar el restore)' {
    $r = Api POST '/api/laps' @{ protocol_version = 1; station_id = 'ST01'; session_id = 'S1'; pilot_id = 'P1'; lap_id = 'L2-after-backup'; raw_ms = 59999; events = @{ doo = 0; oc = 0 }; status = 'valid'; event = 'autocross' } $stationA
    Assert ($r.code -eq 200) "vuelta 2: $($r.code) $($r.text)"
    'L2 registrada'
}
Step 'server-admin.ps1 Restore rechaza con el servidor corriendo' {
    Expect-Throw { Answer @('RESTORE'); & "$release\server-admin.ps1" -Action Restore -DatabasePath $db -BackupFile $script:backupFile -Port $Port } 'still listens'
}
Step 'server-admin.ps1 Restore (servidor detenido) y verificación' {
    Stop-RiceServer
    Answer @('RESTORE')
    & "$release\server-admin.ps1" -Action Restore -DatabasePath $db -BackupFile $script:backupFile -Port $Port
    Start-RiceServer $secretsA
    $r = Api GET '/api/admin/export.csv?table=laps&session_id=S1' $null $adminA
    Assert ($r.text -match 'L1' -and $r.text -notmatch 'L2-after-backup') 'el restore no volvió al estado del backup'
    Stop-RiceServer
    'L1 presente, L2 ausente: restore correcto'
}
Step 'Reinicio del servidor conserva el leaderboard' {
    Start-RiceServer $secretsA
    $r = Api GET '/api/leaderboard?session_id=S1' $null $null
    Assert ($r.text -match 'P1') 'leaderboard vacío tras reiniciar'
    Stop-RiceServer
    'persistencia OK'
}
Step 'Bind LAN a IPv4 privada propia (-AllowLanBind)' {
    $ip = @(Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -match '^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)' -and $_.AddressState -eq 'Preferred' })[0].IPAddress
    if (-not $ip) { throw 'el runner no tiene IPv4 privada' }
    Start-RiceServer $secretsA $ip -Lan
    $r = Api GET '/api/leaderboard?session_id=S1' $null $null $ip
    Stop-RiceServer
    Assert ($r.code -eq 200) "LAN: $($r.code)"
    "servidor accesible en ${ip}:$Port"
}
Step 'server-admin.ps1 FirewallRule (crear, idempotente, quitar) y NetworkCheck' {
    $exe = "$release\RiceTrialsServer\RiceTrialsServer.exe"
    & "$release\server-admin.ps1" -Action FirewallRule -Port $Port -ServerExe $exe
    & "$release\server-admin.ps1" -Action FirewallRule -Port $Port -ServerExe $exe
    $rule = Get-NetFirewallRule -DisplayName "RiceTrials Server TCP $Port"
    Assert ($rule.Profile -eq 'Private') "perfil inesperado: $($rule.Profile)"
    & "$release\server-admin.ps1" -Action NetworkCheck -Port $Port *>&1 | Out-String | Set-Content (Join-Path $Evidence 'network-check.txt')
    & "$release\server-admin.ps1" -Action FirewallRule -Port $Port -Remove
    Assert (-not (Get-NetFirewallRule -DisplayName "RiceTrials Server TCP $Port" -ErrorAction SilentlyContinue)) 'la regla sigue'
    'regla Private creada y eliminada'
}

# ------------------------------------------------------------------ cuenta de servicio + tarea programada
if (-not $SkipServiceAccount) {
    $svcName = 'ricesvc'
    $svcPassword = 'Aa1!' + [guid]::NewGuid().ToString('N'); $script:secretValues.Add($svcPassword)
    $svcSecure = ConvertTo-SecureString $svcPassword -AsPlainText -Force
    $svcCred = New-Object System.Management.Automation.PSCredential("$env:COMPUTERNAME\$svcName", $svcSecure)
    $secretsSvc = Join-Path $secretsDir 'secrets-svc.xml'
    $stationSvc = New-Token; $adminSvc = New-Token
    $harnessDir = Join-Path $Root 'harness'

    Step 'Crear cuenta local dedicada y permisos de carpeta' {
        if (Get-LocalUser -Name $svcName -ErrorAction SilentlyContinue) { Remove-LocalUser -Name $svcName }
        New-LocalUser -Name $svcName -Password $svcSecure -PasswordNeverExpires -AccountNeverExpires -Description 'RiceTrials E2E (temporal)' | Out-Null
        New-Item -ItemType Directory -Force $harnessDir | Out-Null
        foreach ($d in @($release, $data, $secretsDir, $harnessDir)) { & icacls $d /grant "${svcName}:(OI)(CI)M" /T /Q | Out-Null }
        "cuenta $svcName creada"
    }
    Step 'configure-secrets.ps1 ejecutado por la cuenta de servicio (DPAPI de esa cuenta)' {
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
        Assert ($p.ExitCode -eq 0) "configure-secrets como $svcName salió con $($p.ExitCode): $(Get-Content $log -Raw)"
        Assert (Test-Path $secretsSvc) 'no se creó secrets-svc.xml'
        "secretos creados por $svcName"
    }
    Step 'Otra cuenta (el admin del runner) NO puede descifrar los secretos del servicio' {
        try {
            $x = Import-Clixml $secretsSvc
            $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($x.AdminToken))
            if ($plain -ceq $adminSvc) { throw 'DESCIFRADO por otra cuenta: DPAPI no está atado al usuario' }
            'no descifra (valor distinto)'
        } catch {
            if ($_.Exception.Message -match 'DESCIFRADO') { throw }
            "rechazado por DPAPI: $($_.Exception.Message)"
        }
    }
    Step 'server-admin.ps1 RegisterTask + Start-ScheduledTask (cuenta dedicada)' {
        function global:Get-Credential { param([Parameter(Position=0)]$Message) return $svcCred }
        & "$release\server-admin.ps1" -Action RegisterTask -DatabasePath $db -SecretsPath $secretsSvc -Port $Port
        Remove-Item Function:\global:Get-Credential
        $rights = Join-Path $harnessDir 'rights-after.inf'; & secedit /export /areas USER_RIGHTS /cfg $rights | Out-Null
        $svcSid = (New-Object Security.Principal.NTAccount($svcCred.UserName)).Translate([Security.Principal.SecurityIdentifier]).Value
        $batchLine = [string]((Get-Content $rights | Where-Object { $_ -match '^SeBatchLogonRight' }) -join ' ')
        Log "derechos tras RegisterTask: $batchLine (SID de $svcName = $svcSid)"
        # secedit lists resolvable local accounts by name and the rest as *SID: accept either form.
        $members = @(($batchLine -replace '^SeBatchLogonRight\s*=\s*', '') -split ',' | ForEach-Object { $_.Trim().TrimStart('*') })
        Assert ($members -contains $svcSid -or $members -contains $svcName -or $members -contains "$env:COMPUTERNAME\$svcName") 'RegisterTask no otorgó SeBatchLogonRight a la cuenta dedicada'
        $task = Get-ScheduledTask -TaskName RiceTrialsServer
        Assert ($task.Principal.UserId -match $svcName) "principal inesperado: $($task.Principal.UserId)"
        Assert ($task.Triggers[0].CimClass.CimClassName -eq 'MSFT_TaskBootTrigger') 'el disparador no es AtStartup'
        # The Task Scheduler operational log is disabled by default: enable it so failures leave history.
        & wevtutil sl Microsoft-Windows-TaskScheduler/Operational /e:true | Out-Null
        Start-ScheduledTask -TaskName RiceTrialsServer
        if (-not (Wait-Http '127.0.0.1' 60)) {
            $info = Get-ScheduledTaskInfo -TaskName RiceTrialsServer
            Log "estado de la tarea: $((Get-ScheduledTask -TaskName RiceTrialsServer).State); LastRunTime=$($info.LastRunTime)"
            Log ((& schtasks /query /tn RiceTrialsServer /v /fo list 2>&1) -join "`n")
            try { $rights = Join-Path $harnessDir 'rights.inf'; & secedit /export /areas USER_RIGHTS /cfg $rights | Out-Null
                  Log ((Get-Content $rights | Select-String 'SeBatchLogonRight|SeDenyBatchLogonRight|SeServiceLogonRight') -join "`n") } catch { Log "secedit: $($_.Exception.Message)" }
            Save-ServerLogs 'task'
            try { Get-WinEvent -LogName 'Microsoft-Windows-TaskScheduler/Operational' -MaxEvents 30 -ErrorAction Stop | Where-Object { $_.Message -match 'RiceTrials|ricesvc' } |
                    ForEach-Object { Log "evento $($_.Id): $($_.Message -replace '\s+',' ')" } } catch { Log "sin historial de tareas: $($_.Exception.Message)" }
            throw "la tarea no levantó el servidor; LastTaskResult=$($info.LastTaskResult)"
        }
        $owner = (Get-CimInstance Win32_Process -Filter "Name='RiceTrialsServer.exe'" | Select-Object -First 1 | Invoke-CimMethod -MethodName GetOwner).User
        Assert ($owner -eq $svcName) "el servidor corre como $owner"
        "servidor iniciado por la tarea como $owner"
    }
    Step 'Servidor de la tarea: datos previos conservados y tokens del servicio válidos' {
        $r = Api GET '/api/leaderboard?session_id=S1' $null $null
        Assert ($r.text -match 'P1') 'leaderboard sin datos previos'
        $r = Api POST '/api/laps' @{ protocol_version = 1; station_id = 'ST01'; session_id = 'S1'; pilot_id = 'P1'; lap_id = 'L3-task'; raw_ms = 60500; events = @{ doo = 0; oc = 0 }; status = 'valid'; event = 'autocross' } $stationSvc
        Assert ($r.code -eq 200) "vuelta con token del servicio: $($r.code) $($r.text)"
        $r = Api POST '/api/laps' @{ protocol_version = 1; station_id = 'ST01'; session_id = 'S1'; pilot_id = 'P1'; lap_id = 'L4-old-token'; raw_ms = 60500; events = @{ doo = 0; oc = 0 }; status = 'valid'; event = 'autocross' } $stationA
        Assert ($r.code -in 401, 403) "token viejo aceptado: $($r.code)"
        'OK'
    }
    Step 'Caída del exe: start-server.ps1 lo relanza (supervisor, 10 s)' {
        Get-Process RiceTrialsServer | Stop-Process -Force
        Start-Sleep -Seconds 5
        $info = Get-ScheduledTaskInfo -TaskName RiceTrialsServer
        Log "tras matar el exe: estado=$((Get-ScheduledTask -TaskName RiceTrialsServer).State) LastTaskResult=$($info.LastTaskResult)"
        if (-not (Wait-Http '127.0.0.1' 150)) {
            $info = Get-ScheduledTaskInfo -TaskName RiceTrialsServer
            throw "no se recuperó en 150 s (estado=$((Get-ScheduledTask -TaskName RiceTrialsServer).State), LastTaskResult=$($info.LastTaskResult))"
        }
        $pid2 = (Get-Process RiceTrialsServer | Select-Object -First 1).Id
        $owner = (Get-CimInstance Win32_Process -Filter "ProcessId=$pid2" | Invoke-CimMethod -MethodName GetOwner).User
        Assert ($owner -eq $svcName) "el exe relanzado corre como $owner"
        Assert ((Get-ScheduledTask -TaskName RiceTrialsServer).State -eq 'Running') 'la tarea dejó de estar Running'
        "el servidor volvió solo (pid $pid2, como $owner)"
    }
    Step 'UnregisterTask y limpieza de la cuenta' {
        Stop-ScheduledTask -TaskName RiceTrialsServer -ErrorAction SilentlyContinue
        Stop-RiceServer
        & "$release\server-admin.ps1" -Action UnregisterTask
        Assert (-not (Get-ScheduledTask -TaskName RiceTrialsServer -ErrorAction SilentlyContinue)) 'la tarea sigue registrada'
        Remove-LocalUser -Name $svcName
        'limpio'
    }
}

# ------------------------------------------------------------------ cierre y control de fugas
Stop-RiceServer
$leaks = @()
foreach ($file in Get-ChildItem -LiteralPath $Evidence -Recurse -File) {
    $text = [IO.File]::ReadAllText($file.FullName)
    foreach ($s in $secretValues) { if ($text.Contains($s)) { $leaks += $file.Name } }
}
$results.Add([ordered]@{ step = 'Evidencia sin tokens ni contraseñas'; status = $(if ($leaks.Count) { 'fail' } else { 'pass' }); seconds = 0; error = $(if ($leaks.Count) { "aparecen en: $(($leaks | Select-Object -Unique) -join ', ')" } else { $null }) })
if ($leaks.Count) { foreach ($f in ($leaks | Select-Object -Unique)) { Remove-Item (Join-Path $Evidence $f) -Force } }

$failed = @($results | Where-Object { $_.status -eq 'fail' })
$summary = [ordered]@{ environment = $env_info; finished_utc = (Get-Date).ToUniversalTime().ToString('o'); passed = $results.Count - $failed.Count; failed = $failed.Count; steps = $results;
                       note = 'Raíz AC = fixture de texto. Esto NO valida Assetto Corsa, CSP, la pista ni el auto. No se probó un reinicio real de Windows.' }
$summary | ConvertTo-Json -Depth 6 | Set-Content (Join-Path $Evidence 'e2e-summary.json') -Encoding UTF8
$md = @("# E2E Windows: $($summary.passed) OK, $($summary.failed) fallos", '', "Entorno: $($env_info.os); PowerShell $($env_info.powershell) $($env_info.edition); ZIP $($env_info.zip) (sha256 $($env_info.zip_sha256))", '', '| Paso | Resultado | s | Error |', '|---|---|---|---|')
foreach ($r in $results) { $md += "| $($r.step) | $($r.status) | $($r.seconds) | $(([string]$r.error) -replace '\|', '/') |" }
$md += ''; $md += $summary.note
$md -join "`n" | Set-Content (Join-Path $Evidence 'e2e-summary.md') -Encoding UTF8
Log "RESULTADO: $($summary.passed) OK, $($summary.failed) fallos"
if ($failed.Count) { exit 1 }
