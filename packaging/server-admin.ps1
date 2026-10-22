#Requires -Version 5.1
<#
.SYNOPSIS
  Operation of the portable RiceTrials server on Windows (no installed service).
.DESCRIPTION
  -Action NetworkCheck      local IPs, listening port, Test-NetConnection, network profile, firewall rules.
  -Action FirewallRule      creates (or with -Remove deletes) an inbound TCP rule on the Private profile only (requires admin).
  -Action RegisterTask      "At system startup" scheduled task that runs start-server.ps1 (asks for the account
                            interactively with Get-Credential; the password never goes on the command line).
  -Action UnregisterTask    deletes the task.
  -Action Stop              stops the task, the start-server.ps1 supervisor and the exe, and verifies the port is free
                            (killing only RiceTrialsServer.exe is not enough: the supervisor relaunches it).
  -Action Backup            hot backup via /api/admin/backup (admin token prompted with -AsSecureString).
  -Action Check             RiceTrialsServer.exe --db <db> --check (offline, SQLite integrity).
  -Action Restore           stops the task, runs RiceTrialsServer.exe --db <db> --restore <file>, resumes the task.
                            The server keeps a pre-restore copy in <db>\..\backups before replacing.
#>
param(
    [Parameter(Mandatory=$true)][ValidateSet('NetworkCheck','FirewallRule','RegisterTask','UnregisterTask','Stop','Backup','Check','Restore')][string]$Action,
    [string]$DatabasePath,
    [string]$SecretsPath,
    [string]$BackupFile,
    [string]$ServerExe,
    [string]$HostAddress = '127.0.0.1',
    [ValidateRange(1,65535)][int]$Port = 8080,
    [string]$TaskName = 'RiceTrialsServer',
    [string]$RemoteSubnet = 'LocalSubnet',
    [switch]$AllowLanBind,
    [switch]$EnablePenaltiesV2,
    [switch]$Remove
)
$ErrorActionPreference = 'Stop'
# Windows PowerShell 5.1 started with -File leaves $PSScriptRoot empty inside param() defaults: resolve here.
$scriptRoot = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
if (-not $ServerExe) { $ServerExe = Join-Path $scriptRoot 'RiceTrialsServer\RiceTrialsServer.exe' }
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
function Grant-BatchLogonRight([string]$userName) {
    # A task that runs "whether the user is logged on or not" logs on as a batch job. Local accounts created for the
    # server do not have SeBatchLogonRight by default, and Task Scheduler then fails with 0x80070569 at every start.
    $sid = (New-Object Security.Principal.NTAccount($userName)).Translate([Security.Principal.SecurityIdentifier])
    if (-not ('RiceTrials.Lsa' -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
namespace RiceTrials {
public static class Lsa {
    [StructLayout(LayoutKind.Sequential)] struct LSA_UNICODE_STRING { public ushort Length; public ushort MaximumLength; public IntPtr Buffer; }
    [StructLayout(LayoutKind.Sequential)] struct LSA_OBJECT_ATTRIBUTES { public int Length; public IntPtr RootDirectory; public IntPtr ObjectName; public uint Attributes; public IntPtr SecurityDescriptor; public IntPtr SecurityQualityOfService; }
    [DllImport("advapi32.dll")] static extern uint LsaOpenPolicy(IntPtr system, ref LSA_OBJECT_ATTRIBUTES attrs, uint access, out IntPtr handle);
    [DllImport("advapi32.dll")] static extern uint LsaAddAccountRights(IntPtr handle, byte[] sid, LSA_UNICODE_STRING[] rights, uint count);
    [DllImport("advapi32.dll")] static extern uint LsaClose(IntPtr handle);
    [DllImport("advapi32.dll")] static extern int LsaNtStatusToWinError(uint status);
    public static void AddRight(byte[] sid, string right) {
        var attrs = new LSA_OBJECT_ATTRIBUTES(); attrs.Length = Marshal.SizeOf(typeof(LSA_OBJECT_ATTRIBUTES));
        IntPtr policy;
        uint st = LsaOpenPolicy(IntPtr.Zero, ref attrs, 0x00000811 /* LOOKUP_NAMES | CREATE_ACCOUNT | VIEW_LOCAL_INFORMATION */, out policy);
        if (st != 0) throw new System.ComponentModel.Win32Exception(LsaNtStatusToWinError(st));
        IntPtr buf = Marshal.StringToHGlobalUni(right);
        try {
            var r = new LSA_UNICODE_STRING[1];
            r[0].Buffer = buf; r[0].Length = (ushort)(right.Length * 2); r[0].MaximumLength = (ushort)((right.Length + 1) * 2);
            st = LsaAddAccountRights(policy, sid, r, 1);
            if (st != 0) throw new System.ComponentModel.Win32Exception(LsaNtStatusToWinError(st));
        } finally { Marshal.FreeHGlobal(buf); LsaClose(policy); }
    }
}
}
'@
    }
    $bytes = New-Object byte[] ($sid.BinaryLength); $sid.GetBinaryForm($bytes, 0)
    [RiceTrials.Lsa]::AddRight($bytes, 'SeBatchLogonRight')
    Write-Host "Right 'Log on as a batch job' (SeBatchLogonRight) granted to $userName ($($sid.Value))."
}
function Stop-RiceServer {
    # Supervisor first (it would relaunch the exe), then the exe. Only processes of THIS release are touched.
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($task -and $task.State -eq 'Running') { Stop-ScheduledTask -TaskName $TaskName }
    $launcherScript = Join-Path $scriptRoot 'start-server.ps1'
    Get-CimInstance Win32_Process -Filter "Name='powershell.exe' OR Name='pwsh.exe'" |
        Where-Object { $_.ProcessId -ne $PID -and $_.CommandLine -and $_.CommandLine.IndexOf($launcherScript, [StringComparison]::OrdinalIgnoreCase) -ge 0 } |
        ForEach-Object { Write-Host "Stopping supervisor pid $($_.ProcessId)"; Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    $exeFull = [IO.Path]::GetFullPath($ServerExe)
    Get-Process RiceTrialsServer -ErrorAction SilentlyContinue | Where-Object { $_.Path -and ([IO.Path]::GetFullPath($_.Path) -ieq $exeFull) } |
        ForEach-Object { Write-Host "Stopping server pid $($_.Id)"; Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue }
    $deadline = (Get-Date).AddSeconds(20)
    while ((Get-Date) -lt $deadline -and @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue).Count -gt 0) { Start-Sleep -Milliseconds 500 }
    if (@(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue).Count -gt 0) { throw "Something still listens on $Port after stopping" }
}
function Quote([string]$value) { return '"' + $value + '"' }

switch ($Action) {
    'NetworkCheck' {
        Write-Host '== Local IPv4 addresses (use the event network one in the stations ServerUrl)'
        Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -ne '127.0.0.1' } |
            Format-Table InterfaceAlias, IPAddress, PrefixLength -AutoSize
        Write-Host '== Network profile (this kit firewall rule applies to Private only)'
        Get-NetConnectionProfile | Format-Table InterfaceAlias, Name, NetworkCategory -AutoSize
        Write-Host "== Process listening on TCP $Port"
        $listen = @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
        if ($listen.Count -eq 0) { Write-Warning "Nothing listens on $Port (server stopped?)" }
        else { $listen | ForEach-Object { "{0}:{1} pid={2} ({3})" -f $_.LocalAddress, $_.LocalPort, $_.OwningProcess, (Get-Process -Id $_.OwningProcess).ProcessName } }
        Write-Host "== Test-NetConnection $HostAddress : $Port"
        $test = Test-NetConnection -ComputerName $HostAddress -Port $Port -WarningAction SilentlyContinue
        "TcpTestSucceeded={0} RemoteAddress={1}" -f $test.TcpTestSucceeded, $test.RemoteAddress
        if ($test.TcpTestSucceeded) {
            try { $r = Invoke-WebRequest -Uri "http://${HostAddress}:$Port/tv" -UseBasicParsing -TimeoutSec 5; "GET /tv -> $($r.StatusCode)" }
            catch { Write-Warning "GET /tv failed: $_" }
        }
        Write-Host '== Firewall rules of this kit'
        Get-NetFirewallRule -DisplayName 'RiceTrials Server*' -ErrorAction SilentlyContinue | Format-Table DisplayName, Enabled, Profile, Direction, Action -AutoSize
        Write-Host 'From each station: Test-NetConnection <server-IP> -Port' $Port
    }
    'FirewallRule' {
        Assert-Administrator
        if ($Remove) { Remove-NetFirewallRule -DisplayName $ruleName -ErrorAction Stop; Write-Host "Rule removed: $ruleName"; return }
        if (Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue) { Write-Host "Already exists: $ruleName"; return }
        New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Action Allow -Protocol TCP -LocalPort $Port `
            -Profile Private -RemoteAddress $RemoteSubnet -Program $ServerExe | Out-Null
        Write-Host "Rule created: $ruleName (TCP $Port, Private profile, source $RemoteSubnet, program $ServerExe)"
        Write-Host 'If the event network shows as Public, change it to Private (Settings > Network) instead of opening the Public profile.'
    }
    'RegisterTask' {
        Assert-Administrator; Assert-DatabasePath
        if (-not $SecretsPath) { throw '-SecretsPath required' }
        $script = Join-Path $scriptRoot 'start-server.ps1'
        $arguments = '-NoProfile -ExecutionPolicy Bypass -File ' + (Quote $script) + ' -SecretsPath ' + (Quote $SecretsPath) +
                     ' -DatabasePath ' + (Quote $DatabasePath) + " -HostAddress $HostAddress -Port $Port"
        if ($AllowLanBind) { $arguments += ' -AllowLanBind' }
        if ($EnablePenaltiesV2) { $arguments += ' -EnablePenaltiesV2' }
        $taskAction = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arguments -WorkingDirectory $scriptRoot
        $trigger = New-ScheduledTaskTrigger -AtStartup
        $settings = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable
        # DPAPI secrets only decrypt under the account that ran configure-secrets.ps1: use that same account here.
        $credential = Get-Credential -Message 'Dedicated server account (the same one that ran configure-secrets.ps1)'
        Grant-BatchLogonRight $credential.UserName
        Register-ScheduledTask -TaskName $TaskName -Action $taskAction -Trigger $trigger -Settings $settings `
            -User $credential.UserName -Password $credential.GetNetworkCredential().Password -RunLevel Limited | Out-Null
        $credential = $null
        Write-Host "Task '$TaskName' registered (at system startup, restart on failure). Test: Start-ScheduledTask -TaskName $TaskName; reboot the machine and check the history."
    }
    'Stop' {
        Stop-RiceServer
        Write-Host "Server stopped; nothing listens on $Port. To start it again: Start-ScheduledTask -TaskName $TaskName"
    }
    'UnregisterTask' {
        Assert-Administrator
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "Task '$TaskName' removed."
    }
    'Backup' {
        $token = Read-Host 'Admin token' -AsSecureString
        $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($token)
        try {
            $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
            $response = Invoke-RestMethod -Method Post -Uri "http://${HostAddress}:$Port/api/admin/backup" -Headers @{ Authorization = "Bearer $plain" } -ContentType 'application/json' -Body '{}' -TimeoutSec 60
        } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr); $plain = $null; $token = $null }
        Write-Host "Backup created by the server: $($response.path)"
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
        $wasRunning = $task -and $task.State -eq 'Running'
        if ($wasRunning) { Stop-RiceServer }
        if (@(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue).Count -gt 0) { throw "Something still listens on $Port; stop the server before restoring" }
        $answer = Read-Host "Restore $BackupFile over $DatabasePath? Type RESTORE"
        if ($answer -cne 'RESTORE') { throw 'Cancelled' }
        Invoke-Server @('--db', (Quote $DatabasePath), '--restore', (Quote $BackupFile))
        Invoke-Server @('--db', (Quote $DatabasePath), '--check')
        if ($wasRunning) { Start-ScheduledTask -TaskName $TaskName; Write-Host "Task '$TaskName' resumed." }
    }
}
