$g="C:\Users\riceadmin\Downloads\Assetto-Corsa\assettocorsa"
Set-Location $g
Start-Process "$g\steamclient_loader.exe" -WorkingDirectory $g
"started " + (Get-Date -f HH:mm:ss)
