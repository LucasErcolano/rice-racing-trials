#!/bin/bash
# launch.sh : kill AC and relaunch acs.exe direct in interactive session 1
D=/home/lucas/vm/rice-win11
$D/vmssh '$g="C:\Users\riceadmin\Downloads\Assetto-Corsa\assettocorsa"; Get-Process acs,AssettoCorsa -EA SilentlyContinue | Stop-Process -Force; Start-Sleep 2; $a=New-ScheduledTaskAction -Execute "$g\acs.exe" -WorkingDirectory $g; $p=New-ScheduledTaskPrincipal -UserId riceadmin -LogonType Interactive -RunLevel Limited; Unregister-ScheduledTask RiceLaunchGame -Confirm:$false -EA SilentlyContinue; Register-ScheduledTask RiceLaunchGame -Action $a -Principal $p | Out-Null; Start-ScheduledTask RiceLaunchGame; "launched"'
