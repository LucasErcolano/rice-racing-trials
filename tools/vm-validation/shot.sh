#!/bin/bash
# usage: shot.sh name  -> ~/rr-eval/validation/shots/name.png (captures interactive session 1 via scheduled task)
N=${1:-shot}; D=/home/lucas/vm/rice-win11; IP=192.168.122.52
O="-i $D/vm_ed25519 -o BatchMode=yes -o UserKnownHostsFile=$D/known_hosts"
scp $O -q /home/lucas/rr-eval/validation/tools/shot.ps1 riceadmin@$IP:C:/rr/shot.ps1
$D/vmssh "\$a=New-ScheduledTaskAction -Execute powershell.exe -Argument '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File C:\rr\shot.ps1 -Out C:\rr\shots\\$N.png'; \$p=New-ScheduledTaskPrincipal -UserId riceadmin -LogonType Interactive -RunLevel Limited; Unregister-ScheduledTask RiceShot -Confirm:\$false -ErrorAction SilentlyContinue; Register-ScheduledTask RiceShot -Action \$a -Principal \$p | Out-Null; Remove-Item C:\rr\shots\\$N.png -ErrorAction SilentlyContinue; Start-ScheduledTask RiceShot; for(\$i=0;\$i -lt 20 -and -not (Test-Path C:\rr\shots\\$N.png.txt);\$i++){Start-Sleep 1}; Get-Content C:\rr\shots\\$N.png.txt" 
scp $O -q riceadmin@$IP:C:/rr/shots/$N.png /home/lucas/rr-eval/validation/shots/$N.png && ls -la /home/lucas/rr-eval/validation/shots/$N.png
