#Requires -Version 5.1
<#
.SYNOPSIS
  Aplica o revierte un Dummy.kn5 CANDIDATO NO VERIFICADO EN JUEGO.
.DESCRIPTION
  -Apply: verifica que el Dummy.kn5 instalado tenga el SHA-256 original del manifiesto,
  guarda una copia en <pista>\RiceTrials_backup\Dummy.kn5.<sha>.bak (si no existe) y copia el
  candidato tras verificar su SHA-256. -Revert: restaura el backup verificando su hash.
  -Status: informa qué versión está instalada. Nunca toca otros archivos del mod.
.EXAMPLE
  .\Apply-DummyPatch.ps1 -AcRoot 'C:\Program Files (x86)\Steam\steamapps\common\assettocorsa' -Apply
#>
[CmdletBinding(DefaultParameterSetName = 'Status')]
param(
    [Parameter(Mandatory = $true)][string]$AcRoot,
    [string]$Track = 'FSAEJ_Endurance',
    [string]$PatchDir = $PSScriptRoot,
    [Parameter(ParameterSetName = 'Apply')][switch]$Apply,
    [Parameter(ParameterSetName = 'Revert')][switch]$Revert,
    [Parameter(ParameterSetName = 'Status')][switch]$Status
)
$ErrorActionPreference = 'Stop'

function Get-Sha256([string]$Path) { (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() }

$manifestPath = Join-Path $PatchDir 'manifest.json'
$candidate = Join-Path $PatchDir 'Dummy.kn5.candidate'
if (-not (Test-Path -LiteralPath $manifestPath) -or -not (Test-Path -LiteralPath $candidate)) {
    throw "Falta manifest.json o Dummy.kn5.candidate en $PatchDir"
}
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
if ($manifest.track -ne $Track) { throw "El manifiesto es para $($manifest.track), no para $Track" }
$trackDir = Join-Path $AcRoot "content\tracks\$Track"
$target = Join-Path $trackDir 'Dummy.kn5'
if (-not (Test-Path -LiteralPath $target)) { throw "No existe $target" }
$backupDir = Join-Path $trackDir 'RiceTrials_backup'
$backup = Join-Path $backupDir "Dummy.kn5.$($manifest.original_sha256).bak"
$installed = Get-Sha256 $target
$state = switch ($installed) {
    $manifest.original_sha256 { 'original' }
    $manifest.candidate_sha256 { 'candidato' }
    default { 'desconocido' }
}
Write-Host "Etiqueta: $($manifest.label)"
Write-Host "Instalado: $installed ($state)"

if ($Apply) {
    if ($state -eq 'candidato') { Write-Host 'El candidato ya está instalado.'; exit 0 }
    if ($state -ne 'original') { throw 'El Dummy.kn5 instalado no coincide con el original del manifiesto: no se aplica.' }
    if ((Get-Sha256 $candidate) -ne $manifest.candidate_sha256) { throw 'El candidato no coincide con su SHA-256: archivo dañado.' }
    New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
    if (-not (Test-Path -LiteralPath $backup)) { Copy-Item -LiteralPath $target -Destination $backup }
    if ((Get-Sha256 $backup) -ne $manifest.original_sha256) { throw 'El backup no coincide con el original: abortado.' }
    Copy-Item -LiteralPath $candidate -Destination $target -Force
    if ((Get-Sha256 $target) -ne $manifest.candidate_sha256) {
        Copy-Item -LiteralPath $backup -Destination $target -Force
        throw 'La copia del candidato falló la verificación: se restauró el original.'
    }
    Write-Host "Candidato aplicado. Backup: $backup"
    Write-Host 'Si Content Manager o un servidor online validan checksums, esta pista modificada puede ser rechazada.'
}
elseif ($Revert) {
    if ($state -eq 'original') { Write-Host 'Ya está el original.'; exit 0 }
    if (-not (Test-Path -LiteralPath $backup)) { throw "No hay backup en $backup" }
    if ((Get-Sha256 $backup) -ne $manifest.original_sha256) { throw 'El backup no coincide con el SHA-256 original.' }
    Copy-Item -LiteralPath $backup -Destination $target -Force
    if ((Get-Sha256 $target) -ne $manifest.original_sha256) { throw 'La restauración no verificó.' }
    Write-Host 'Original restaurado y verificado.'
}
