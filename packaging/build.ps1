# Windows host: build server executable and three deployment ZIPs. No proprietary game assets.
param([string]$Version = '0.1.0')
$ErrorActionPreference = 'Stop'
if ($Version -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]*$') { throw 'Invalid version' }
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $root
foreach ($file in @('server/app.py','server/scoring.py','client/scoring.lua','client/detectors.lua','client/journal.lua','client/hud.lua','client/penalties.lua','client/delta.lua','client/native_laps.lua','client/diagnostics.lua','rules/scoring-v1.json','leaderboard/admin.html','leaderboard/admin.js','leaderboard/tv.html','leaderboard/tv.js','client/RiceTrials.lua','client/core.lua','client/manifest.ini','tools/validation.py','track/calibration/timing-landmarks.json')) {
    if (-not (Test-Path $file -PathType Leaf)) { throw "Missing required file: $file" }
}
if (-not (Get-Command py -ErrorAction SilentlyContinue)) { throw 'Python launcher required on Windows BUILD host' }
$python = Join-Path $root '.build-venv\Scripts\python.exe'
if (-not (Test-Path $python)) { & py -3 -m venv (Join-Path $root '.build-venv'); if ($LASTEXITCODE) { throw 'venv failed' } }
& $python -m pip install --disable-pip-version-check 'pyinstaller==6.16.0'
if ($LASTEXITCODE) { throw 'PyInstaller install failed' }
$work = Join-Path $root '.build-work'
New-Item -ItemType Directory -Force $work | Out-Null
# The entrypoint is server/app.py. Its ROOT assumes a source tree; this wrapper
# points ROOT at PyInstaller's resource directory for frozen leaderboard routes.
$wrapper = Join-Path $work 'server-entry.py'
@'
import sys
from pathlib import Path
from server import app
if getattr(sys, 'frozen', False):
    app.ROOT = Path(sys._MEIPASS)
app.main()
'@ | Set-Content -Encoding UTF8 $wrapper
& $python -m PyInstaller --noconfirm --clean --onedir --name RiceTrialsServer --paths $root --add-data "${root}\leaderboard;leaderboard" --distpath (Join-Path $root 'dist') --workpath $work --specpath $work $wrapper
if ($LASTEXITCODE) { throw 'PyInstaller failed' }
$server = Join-Path $root 'dist\RiceTrialsServer'
if (-not (Test-Path (Join-Path $server 'RiceTrialsServer.exe'))) { throw 'Missing executable' }
$stage = Join-Path $work 'stage'
if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
New-Item -ItemType Directory -Force $stage | Out-Null
$client = Join-Path $stage 'RiceTrials'
New-Item -ItemType Directory -Force $client | Out-Null
foreach ($file in @(Get-ChildItem -LiteralPath (Join-Path $root 'client') -File | Where-Object { $_.Name -ne 'station_config.lua' -and ($_.Name -eq 'manifest.ini' -or $_.Name -like '*.lua') })) { Copy-Item -LiteralPath $file.FullName -Destination $client }
$kit = Join-Path $stage 'tools'
New-Item -ItemType Directory -Force $kit | Out-Null
Copy-Item (Join-Path $root 'tools/validation.py') $kit
$calibration = Join-Path $stage 'track/calibration'
New-Item -ItemType Directory -Force $calibration | Out-Null
Copy-Item (Join-Path $root 'track/calibration/timing-landmarks.json') $calibration
$docs = Join-Path $stage 'docs'
New-Item -ItemType Directory -Force $docs | Out-Null
foreach ($name in @('deployment.md','windows-validation.md','track-tool.md')) { Copy-Item (Join-Path $root "docs/$name") $docs }
foreach ($name in @('install.ps1','uninstall.ps1','start-server.ps1','configure-secrets.ps1','server-admin.ps1')) { Copy-Item (Join-Path $PSScriptRoot $name) $stage }
$serverStage = Join-Path $stage 'RiceTrialsServer'
Copy-Item $server $serverStage -Recurse
foreach ($kind in @('client','server','complete')) {
    $zip = Join-Path $root "dist\RiceTrials-$Version-$kind-win64.zip"
    if (Test-Path $zip) { Remove-Item $zip -Force }
    $items = switch ($kind) {
        client { @($client, $kit, (Join-Path $stage 'track'), $docs, (Join-Path $stage 'install.ps1'), (Join-Path $stage 'uninstall.ps1')) }
        server { @($serverStage, (Join-Path $stage 'start-server.ps1'), (Join-Path $stage 'configure-secrets.ps1'), (Join-Path $stage 'server-admin.ps1'), $docs) }
        complete { @($client, $serverStage, $kit, (Join-Path $stage 'track'), $docs, (Join-Path $stage 'install.ps1'), (Join-Path $stage 'uninstall.ps1'), (Join-Path $stage 'start-server.ps1'), (Join-Path $stage 'configure-secrets.ps1'), (Join-Path $stage 'server-admin.ps1')) }
    }
    Compress-Archive -Path $items -DestinationPath $zip
    Get-FileHash $zip -Algorithm SHA256 | Format-List
}
Write-Host 'Built client/server/complete ZIPs; Windows rig validation still required.'
