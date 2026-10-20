param([string]$Close="")
Get-Process | ? {$_.MainWindowHandle -ne 0} | select Id,ProcessName,SessionId,MainWindowTitle | ft -auto | Out-String -Width 200
if($Close){ Get-Process -Name $Close -EA SilentlyContinue | % { $_.CloseMainWindow() | Out-Null; "closed $($_.Id)" } }
