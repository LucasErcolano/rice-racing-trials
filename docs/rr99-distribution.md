# Distribución conjunta: RiceTrials V2 + overlays + RR99

RR99 es un rebrand visual autorizado del MFT02. El material binario queda fuera del historial Git y se adjunta como assets independientes de la pre-release pública `v0.3.0-rc.2`, conforme al permiso temporal del creador para los trials. El repositorio contiene la herramienta de auditoría, tests y el instalador de la variante para actualizar instalaciones MFT02 existentes.

Base de software: el commit final probado de la pre-release `v0.3.0-rc.2` (SHA indicado en sus assets/notas), que integra la recuperación offline y los overlays nativos. Los binarios RR99 no se guardan en Git; se adjuntan como assets versionados de la release.

## Qué hace y qué no hace el parche

- Reemplaza las skins de los tres autos y los metadatos de `ui/` (`ui_car.json`, `badge.png`). Agrega `skins/rr99/`.
- No toca `data.acd`, `collider.kn5`, el KN5 del auto, `.ini`, audio ni animaciones.
- Los tres IDs internos no cambian: `madformulateam_mft02`, `madformulateam_mft02_hy`, `madformulateam_mft02_evo`. RiceTrials solo usa `madformulateam_mft02` (preflight de `install.ps1`).
- No contiene ningún archivo de RiceTrials. Cliente y servidor no se modifican.

Auditoría reproducible contra el MFT02 original y el mod completo (carpetas extraídas por el operador):

```powershell
python tools\rr99_audit.py --orig <MFT02_extraido> --full <RR99_standalone_extraido> --patch <RR99_patch_extraido> `
  --client-zip RiceTrials-0.3.0-rc.2-client-win64.zip --server-zip RiceTrials-0.3.0-rc.2-server-win64.zip --output audit.json
```

## Orden de instalación en el puesto (Windows)

Cerrar Assetto Corsa y Content Manager antes de empezar.

1. Verificar hashes del paquete con `SHA256SUMS.txt`:
   `Get-FileHash -Algorithm SHA256 <archivo>` y comparar a mano.
2. Instalar RiceTrials primero, siguiendo `docs/v2-field-testing.md` (`install.ps1 -Preflight` y luego sin `-Preflight`, con `-BackupDirectory`). Guardar la carpeta de backup que imprime.
3. Extraer `RR99_Trials_Rebrand_PATCH.zip` en una carpeta nueva (no ejecutar desde el ZIP).
4. Vista previa, sin cambios en disco:
   `.\Install-RR99.ps1 -AssettoRoot 'C:\...\assettocorsa'`
5. Aplicar:
   `.\Install-RR99.ps1 -AssettoRoot 'C:\...\assettocorsa' -Apply`
   Crea un snapshot en `<AssettoRoot>\_RR99_backups\RR99-<fecha>-<guid>` con `RR99-backup.json`. **Esto elimina las skins anteriores** de los tres autos (quedan en el snapshot).
6. Reabrir Content Manager y comprobar que el auto aparece como RR99 con la skin `rr99`.
7. Repetir `install.ps1 -Preflight` de RiceTrials: debe seguir en PASS para `MFT02 installed ID`.

Si se instala con arrastrar y soltar en Content Manager en vez del script, no hay backup ni se eliminan las skins anteriores. No lo probé.

## Rollback (cada componente es independiente)

- **RR99**: `.\Install-RR99.ps1 -AssettoRoot '...' -RollbackFrom '<AssettoRoot>\_RR99_backups\RR99-...'` (vista previa) y luego con `-Apply`. Restaura skins y `ui/` originales y guarda antes el estado RR99 en otro snapshot. Rechaza carpetas sin `RR99-backup.json`.
- **RiceTrials**: `.\install.ps1 -AssettoRoot '...' -Rollback '<carpeta de backup de RiceTrials>'`, o `uninstall.ps1`.
- Revertir uno no afecta al otro.

## Resultados de verificación

Ver el informe de entrega. En resumen: auditoría 11/11 gates OK; instalación, idempotencia, rollback exacto (542/542 archivos idénticos) y falla inyectada probados con PowerShell 7.6.7 sobre Linux en un directorio de prueba; **no probado en Windows ni en Content Manager**.

## Créditos y vigencia temporal

- Vehículo base MFT02: MAD Formula Team / O21 Modelling. No reclamamos autoría del modelo, la física ni el audio.
- Rebrand visual RR99 adaptado con permiso expreso del creador para los trials. Gracias a Mad Racing por la autorización y colaboración; el equipo recibirá también el agradecimiento en el evento presencial.
- El permiso de publicación de RR99 es temporal, limitado a los trials. Al terminar: retirar los assets RR99 de esta release y, si corresponde, eliminar la pre-release o restringir el repositorio; publicar una actualización sin RR99 solo si se desea conservar el software. La eliminación del asset/release no borra copias ya descargadas, mirrors ni cachés.
- La distribución no convierte esta pre-release en software oficial de competencia. Revisar la autorización antes de cualquier redistribución posterior a los trials.
