#!/bin/bash
# ses.sh script.ps1 [args...] : copy script to VM, run it in interactive session 1 (task, Limited), wait, print C:\rr\ses.out
D=/home/lucas/vm/rice-win11; IP=192.168.122.52
O="-i $D/vm_ed25519 -o BatchMode=yes -o UserKnownHostsFile=$D/known_hosts"
S=$1; shift; B=$(basename "$S")
scp $O -q "$S" riceadmin@$IP:C:/rr/$B
cat > /tmp/ses_run.ps1 <<PS
\$args2 = '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -Command "& C:\rr\\$B $* *> C:\rr\ses.out; New-Item C:\rr\ses.done -Force | Out-Null"'
\$a = New-ScheduledTaskAction -Execute powershell.exe -Argument \$args2
\$p = New-ScheduledTaskPrincipal -UserId riceadmin -LogonType Interactive -RunLevel Limited
Unregister-ScheduledTask RiceSes -Confirm:\$false -EA SilentlyContinue
Register-ScheduledTask RiceSes -Action \$a -Principal \$p | Out-Null
Remove-Item C:\rr\ses.done,C:\rr\ses.out -EA SilentlyContinue
Start-ScheduledTask RiceSes
for (\$i=0; \$i -lt 60 -and -not (Test-Path C:\rr\ses.done); \$i++) { Start-Sleep -m 500 }
if (Test-Path C:\rr\ses.out) { Get-Content C:\rr\ses.out }
PS
$D/vmssh -f /tmp/ses_run.ps1
