# RR99 installer E2E on a SYNTHETIC MFT02 tree (no game assets). Run under Windows PowerShell 5.1 and pwsh.
# Usage: <powershell|pwsh> -File rr99-installer-e2e.ps1 -Installer <Install-RR99.ps1> -Engine <powershell|pwsh>
param([Parameter(Mandatory=$true)][string]$Installer, [Parameter(Mandatory=$true)][string]$Engine)
$ErrorActionPreference = 'Stop'
$ids = @('madformulateam_mft02','madformulateam_mft02_hy','madformulateam_mft02_evo')
$fail = 0; $n = 0
function Check([string]$name, [bool]$ok) {
    $script:n++
    if ($ok) { Write-Host "PASS $name" } else { Write-Host "FAIL $name"; $script:fail++ }
}
function Rand([string]$path, [int]$len) {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $path) | Out-Null
    $b = New-Object byte[] $len; (New-Object System.Random ($path.GetHashCode())).NextBytes($b)
    [System.IO.File]::WriteAllBytes($path, $b)
}
function Tree([string]$root) {
    $base = (Resolve-Path -LiteralPath $root).Path.TrimEnd('\')
    $r = @{}
    Get-ChildItem -LiteralPath $base -Recurse -File -Force | Where-Object { -not $_.FullName.StartsWith("$base\_RR99_backups") } | ForEach-Object {
        $r[$_.FullName.Substring($base.Length + 1)] = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash
    }
    $r
}
function Same($a, $b) {
    if ($a.Count -ne $b.Count) { return $false }
    foreach ($k in $a.Keys) { if (-not $b.ContainsKey($k) -or $a[$k] -ne $b[$k]) { return $false } }
    $true
}
function Run([string[]]$extra) {
    $exe = if ($Engine -eq 'pwsh') { 'pwsh' } else { 'powershell' }
    $out = & $exe -NoProfile -ExecutionPolicy Bypass -File $script:inst @extra 2>&1 | Out-String
    [pscustomobject]@{ Code = $LASTEXITCODE; Out = $out }
}
$tmp = Join-Path $env:RUNNER_TEMP ("rr99 test " + $Engine)
if (-not $env:RUNNER_TEMP) { $tmp = Join-Path ([IO.Path]::GetTempPath()) ("rr99 test " + $Engine) }
if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Recurse -Force }
$special = "Assetto [Corsa] n$([char]0xF1) & (1) 'q' `$x"
$ac = Join-Path $tmp $special
$patchDir = Join-Path $tmp 'patch dir'
New-Item -ItemType Directory -Force -Path $patchDir | Out-Null
Copy-Item -LiteralPath $Installer -Destination (Join-Path $patchDir 'Install-RR99.ps1')
$script:inst = Join-Path $patchDir 'Install-RR99.ps1'
foreach ($id in $ids) {
    $c = Join-Path $ac "content\cars\$id"
    Rand "$c\data.acd" 5000; Rand "$c\madformulateam_mft02.kn5" 9000; Rand "$c\collider.kn5" 3000
    Rand "$c\sfx\GUIDs.txt" 200; Rand "$c\ui\ui_car.json" 300; Rand "$c\ui\badge.png" 400
    Rand "$c\skins\2022_base\skin.dds" 2000; Rand "$c\skins\2022_base\ui_skin.json" 100; Rand "$c\skins\team_red\skin.dds" 2100
    $p = Join-Path $patchDir "content\cars\$id"
    Rand "$p\ui\ui_car.json" 310; Rand "$p\ui\badge.png" 410
    Rand "$p\skins\rr99\skin.dds" 2200; Rand "$p\skins\rr99\ui_skin.json" 110; Rand "$p\skins\rr99\preview.jpg" 700; Rand "$p\skins\rr99\livery.png" 800
}
$orig = Tree $ac
$origDirs = (Get-ChildItem -LiteralPath (Join-Path $ac 'content\cars') -Directory | ForEach-Object Name | Sort-Object) -join ','

# 1 preflight
$r = Run @('-AssettoRoot', $ac)
Check 'preflight exits 0' ($r.Code -eq 0)
Check 'preflight changes nothing and creates no backup dir' ((Same $orig (Tree $ac)) -and -not (Test-Path -LiteralPath (Join-Path $ac '_RR99_backups')))

# 2 apply
$r = Run @('-AssettoRoot', $ac, '-Apply')
Check 'apply exits 0' ($r.Code -eq 0)
$t1 = Tree $ac
$phys = $true
foreach ($id in $ids) { foreach ($f in @('data.acd','madformulateam_mft02.kn5','collider.kn5','sfx\GUIDs.txt')) { $k = "content\cars\$id\$f"; if ($t1[$k] -ne $orig[$k]) { $phys = $false } } }
Check 'physics/geometry/audio files byte-identical after apply' $phys
Check 'car folder names (internal IDs) unchanged' ((((Get-ChildItem -LiteralPath (Join-Path $ac 'content\cars') -Directory | ForEach-Object Name | Sort-Object) -join ',') -eq $origDirs))
$old = $false; $new = $true; $ui = $true
foreach ($id in $ids) {
    foreach ($k in $t1.Keys) { if ($k -like "content\cars\$id\skins\2022_base*" -or $k -like "content\cars\$id\skins\team_red*") { $old = $true } }
    foreach ($f in @('skin.dds','ui_skin.json','preview.jpg','livery.png')) {
        $a = (Get-FileHash -LiteralPath (Join-Path $patchDir "content\cars\$id\skins\rr99\$f")).Hash
        if ($t1["content\cars\$id\skins\rr99\$f"] -ne $a) { $new = $false }
    }
    foreach ($f in @('ui_car.json','badge.png')) {
        $a = (Get-FileHash -LiteralPath (Join-Path $patchDir "content\cars\$id\ui\$f")).Hash
        if ($t1["content\cars\$id\ui\$f"] -ne $a) { $ui = $false }
    }
}
Check 'previous skins removed from cars' (-not $old)
Check 'RR99 skins/previews byte-identical to patch' $new
Check 'RR99 ui_car.json and badge.png applied' $ui
$snaps = @(Get-ChildItem -LiteralPath (Join-Path $ac '_RR99_backups') -Directory)
Check 'one backup snapshot with marker' (($snaps.Count -eq 1) -and (Test-Path -LiteralPath (Join-Path $snaps[0].FullName 'RR99-backup.json')))
$snap1 = $snaps[0].FullName
$bk = $true
foreach ($id in $ids) { foreach ($k in $orig.Keys) { if ($k -like "content\cars\$id\skins\*" -or $k -eq "content\cars\$id\ui\ui_car.json" -or $k -eq "content\cars\$id\ui\badge.png") {
    $rel = $k.Substring("content\cars\$id\".Length); $bp = Join-Path (Join-Path $snap1 $id) $rel
    if (-not (Test-Path -LiteralPath $bp) -or (Get-FileHash -LiteralPath $bp).Hash -ne $orig[$k]) { $bk = $false } } } }
Check 'backup holds exact original skins and ui' $bk

# 3 idempotence
$r = Run @('-AssettoRoot', $ac, '-Apply')
Check 'second apply exits 0' ($r.Code -eq 0)
Check 'second apply leaves identical installed state' (Same $t1 (Tree $ac))

# 4 rollback to the original snapshot
$r = Run @('-AssettoRoot', $ac, '-RollbackFrom', $snap1)
Check 'rollback preview exits 0 and changes nothing' (($r.Code -eq 0) -and (Same $t1 (Tree $ac)))
$r = Run @('-AssettoRoot', $ac, '-RollbackFrom', $snap1, '-Apply')
Check 'rollback apply exits 0' ($r.Code -eq 0)
Check 'ROLLBACK RESTORES EXACT ORIGINAL (all files, hashes)' (Same $orig (Tree $ac))

# 5 safety: invalid rollback inputs
$empty = Join-Path $tmp 'not a snapshot'; New-Item -ItemType Directory -Force -Path $empty | Out-Null
$r = Run @('-AssettoRoot', $ac, '-RollbackFrom', $empty, '-Apply')
Check 'rollback without marker is refused' (($r.Code -ne 0) -and (Same $orig (Tree $ac)))
$r = Run @('-AssettoRoot', (Join-Path $tmp 'nope'), '-Apply')
Check 'nonexistent AssettoRoot is refused' ($r.Code -ne 0)
$bad = Join-Path $tmp 'badroot'; New-Item -ItemType Directory -Force -Path "$bad\content\cars" | Out-Null
$r = Run @('-AssettoRoot', $bad, '-Apply')
Check 'root without MFT02 cars is refused' ($r.Code -ne 0)

# 6 partial failure: lock a file inside the 2nd car's skins
$lockFile = Join-Path $ac 'content\cars\madformulateam_mft02_hy\skins\2022_base\skin.dds'
$fs = [System.IO.File]::Open($lockFile, 'Open', 'Read', 'None')
try { $r = Run @('-AssettoRoot', $ac, '-Apply') } finally { $fs.Dispose() }
Check 'locked file makes apply fail (exit != 0)' ($r.Code -ne 0)
Check 'after partial failure the tree equals the original' (Same $orig (Tree $ac))
$r = Run @('-AssettoRoot', $ac, '-Apply')
Check 'apply succeeds after the lock is released' ($r.Code -eq 0)
$snapsAll = @(Get-ChildItem -LiteralPath (Join-Path $ac '_RR99_backups') -Directory | Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName 'RR99-backup.json') } | Sort-Object LastWriteTime)
$r = Run @('-AssettoRoot', $ac, '-RollbackFrom', $snapsAll[-1].FullName, '-Apply')
Check 'final rollback restores exact original again' (($r.Code -eq 0) -and (Same $orig (Tree $ac)))

Write-Host "RESULT engine=$Engine psversion=$($PSVersionTable.PSVersion) checks=$n failed=$fail"
if ($fail -gt 0) { exit 1 }
exit 0
