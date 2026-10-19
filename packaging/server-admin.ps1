#Requires -Version 5.1
<#
.SYNOPSIS
  Operación del servidor portable RiceTrials en Windows (sin servicio instalado).
.DESCRIPTION
  -Action NetworkCheck      IP locales, puerto en escucha, Test-NetConnection, perfil de red, reglas de firewall.
  -Action FirewallRule      crea (o con -Remove borra) una regla entrante TCP sólo en perfil Private (requiere admin).
  -Action RegisterTask      tarea programada "Al iniciar el sistema" que ejecuta start-server.ps1 (pide la cuenta de forma
                            interactiva con Get-Credential; la contraseña nunca va en la línea de comandos).
  -Action UnregisterTask    borra la tarea.
  -Action Backup            backup en caliente vía /api/admin/backup (token admin pedido con -AsSecureString).
  -Action Check             RiceTrialsServer.exe --db <db> --check (offline, integridad SQLite).
  -Action Restore           detiene la tarea, ejecuta RiceTrialsServer.exe --db <db> --restore <archivo>, reanuda la tarea.
                            El servidor guarda una copia pre-restore en <db>\..\backups antes de reemplazar.
#>
param(
    [Parameter(Mandatory=$true)][ValidateSet('NetworkCheck','FirewallRule','RegisterTask','UnregisterTask','Backup','Check','Restore')][string]$Action,
    [string]$DatabasePath,
    [string]$SecretsPath,
    [string]$BackupFile,
    [string]$ServerExe = (Join-Path $PSScriptRoot 'RiceTrialsServer\RiceTrialsServer.exe'),
    [string]$HostAddress = '127.0.0.1',
    [ValidateRange(1,65535)][int]$Port = 8080,
    [string]$TaskName = 'RiceTrialsServer',
    [string]$RemoteSubnet = 'LocalSubnet',
    [switch]$AllowLanBind,
    [switch]$Remove
)
$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') { throw 'Windows required' }
$ruleName = "RiceTrials Server TCP $Port"
function Assert-Administrator {
    $principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw "$Action requires an elevated PowerShell" }
}
function Assert-DatabasePath {
    if (-not $DatabasePath -or -not [IO.Path]::IsPathRooted($DatabasePath)) { throw '-DatabasePath (absolute) required' }
}
function Invoke-Server([string[]]$arguments) {
    if (-not (Test-Path -LiteralPath $ServerExe -PathType Leaf)) { throw "Server executable missing: $ServerExe" }
    $out = [IO.Path]::GetTempFileName(); $err = [IO.Path]::GetTempFileName()
    try {
        $proc = Start-Process -FilePath $ServerExe -ArgumentList $arguments -NoNewWindow -Wait -PassThru -RedirectStandardOutput $out -RedirectStandardError $err
        Get-Content -LiteralPath $out
        Get-Content -LiteralPath $err | ForEach-Object { Write-Warning $_ }
        if ($proc.ExitCode -ne 0) { throw "RiceTrialsServer.exe exited with $($proc.ExitCode)" }
    } finally { Remove-Item -LiteralPath $out, $err -Force -ErrorAction SilentlyContinue }
}
function Quote([string]$value) { return '"' + $value + '"' }

switch ($Action) {
    'NetworkCheck' {
        Write-Host '== IPv4 locales (usar la de la red del evento en ServerUrl de las estaciones)'
        Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -ne '127.0.0.1' } |
            Format-Table InterfaceAlias, IPAddress, PrefixLength -AutoSize
        Write-Host '== Perfil de red (la regla de firewall de este kit sólo aplica a Private)'
        Get-NetConnectionProfile | Format-Table InterfaceAlias, Name, NetworkCategory -AutoSize
        Write-Host "== Proceso escuchando en TCP $Port"
        $listen = @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
        if ($listen.Count -eq 0) { Write-Warning "Nadie escucha en $Port (¿servidor detenido?)" }
        else { $listen | ForEach-Object { "{0}:{1} pid={2} ({3})" -f $_.LocalAddress, $_.LocalPort, $_.OwningProcess, (Get-Process -Id $_.OwningProcess).ProcessName } }
        Write-Host "== Test-NetConnection $HostAddress : $Port"
        $test = Test-NetConnection -ComputerName $HostAddress -Port $Port -WarningAction SilentlyContinue
        "TcpTestSucceeded={0} RemoteAddress={1}" -f $test.TcpTestSucceeded, $test.RemoteAddress
        if ($test.TcpTestSucceeded) {
            try { $r = Invoke-WebRequest -Uri "http://${HostAddress}:$Port/tv" -UseBasicParsing -TimeoutSec 5; "GET /tv -> $($r.StatusCode)" }
            catch { Write-Warning "GET /tv falló: $_" }
        }
        Write-Host '== Reglas de firewall de este kit'
        Get-NetFirewallRule -DisplayName 'RiceTrials Server*' -ErrorAction SilentlyContinue | Format-Table DisplayName, Enabled, Profile, Direction, Action -AutoSize
        Write-Host 'Desde cada estación: Test-NetConnection <IP-servidor> -Port' $Port
    }
    'FirewallRule' {
        Assert-Administrator
        if ($Remove) { Remove-NetFirewallRule -DisplayName $ruleName -ErrorAction Stop; Write-Host "Regla eliminada: $ruleName"; return }
        if (Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue) { Write-Host "Ya existe: $ruleName"; return }
        New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Action Allow -Protocol TCP -LocalPort $Port `
            -Profile Private -RemoteAddress $RemoteSubnet -Program $ServerExe | Out-Null
        Write-Host "Regla creada: $ruleName (TCP $Port, perfil Private, origen $RemoteSubnet, programa $ServerExe)"
        Write-Host 'Si la red del evento figura como Public, cambiarla a Private (Configuración > Red) en vez de abrir el perfil Public.'
    }
    'RegisterTask' {
        Assert-Administrator; Assert-DatabasePath
        if (-not $SecretsPath) { throw '-SecretsPath required' }
        $script = Join-Path $PSScriptRoot 'start-server.ps1'
        $arguments = '-NoProfile -ExecutionPolicy Bypass -File ' + (Quote $script) + ' -SecretsPath ' + (Quote $SecretsPath) +
                     ' -DatabasePath ' + (Quote $DatabasePath) + " -HostAddress $HostAddress -Port $Port"
        if ($AllowLanBind) { $arguments += ' -AllowLanBind' }
        $taskAction = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arguments -WorkingDirectory $PSScriptRoot
        $trigger = New-ScheduledTaskTrigger -AtStartup
        $settings = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable
        # DPAPI secrets only decrypt under the account that ran configure-secrets.ps1: use that same account here.
        $credential = Get-Credential -Message 'Cuenta dedicada del servidor (la misma que ejecutó configure-secrets.ps1)'
        Register-ScheduledTask -TaskName $TaskName -Action $taskAction -Trigger $trigger -Settings $settings `
            -User $credential.UserName -Password $credential.GetNetworkCredential().Password -RunLevel Limited | Out-Null
        $credential = $null
        Write-Host "Tarea '$TaskName' registrada (al iniciar el sistema, reinicio ante fallo). Probar: Start-ScheduledTask -TaskName $TaskName; reiniciar el equipo y revisar el historial."
    }
    'UnregisterTask' {
        Assert-Administrator
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "Tarea '$TaskName' eliminada."
    }
    'Backup' {
        $token = Read-Host 'Admin token' -AsSecureString
        $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($token)
        try {
            $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
            $response = Invoke-RestMethod -Method Post -Uri "http://${HostAddress}:$Port/api/admin/backup" -Headers @{ Authorization = "Bearer $plain" } -TimeoutSec 60
        } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr); $plain = $null; $token = $null }
        Write-Host "Backup creado por el servidor: $($response.path)"
        if (Test-Path -LiteralPath $response.path -PathType Leaf) { Get-FileHash -LiteralPath $response.path -Algorithm SHA256 | Format-List }
    }
    'Check' {
        Assert-DatabasePath
        Invoke-Server @('--db', (Quote $DatabasePath), '--check')
    }
    'Restore' {
        Assert-DatabasePath
        if (-not $BackupFile -or -not (Test-Path -LiteralPath $BackupFile -PathType Leaf)) { throw '-BackupFile must be an existing .sqlite backup' }
        $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        if ($task -and $task.State -eq 'Running') { Stop-ScheduledTask -TaskName $TaskName; Start-Sleep -Seconds 2 }
        if (@(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue).Count -gt 0) { throw "Something still listens on $Port; stop the server before restoring" }
        $answer = Read-Host "Restaurar $BackupFile sobre $DatabasePath? Escriba RESTORE"
        if ($answer -cne 'RESTORE') { throw 'Cancelled' }
        Invoke-Server @('--db', (Quote $DatabasePath), '--restore', (Quote $BackupFile))
        Invoke-Server @('--db', (Quote $DatabasePath), '--check')
        if ($task) { Start-ScheduledTask -TaskName $TaskName; Write-Host "Tarea '$TaskName' reanudada." }
    }
}
