# Parches candidatos de Dummy.kn5 (CANDIDATO NO VERIFICADO EN JUEGO)

Este directorio versiona sólo el **código** (generador y script de aplicar/revertir). Los binarios
candidatos se generan en `dist/track-calibration/patches/` (ignorado por git) porque son derivados
del mod:

```
python -m track.tool dummy-patch --source <carpeta o ZIP de la pista> --track FSAEJ_Endurance \
  --output dist/track-calibration/patches/FSAEJ_Endurance-dummy-start-after8
```

Cada carpeta contiene `Dummy.kn5.candidate`, `manifest.json` (SHA-256 del original esperado, posiciones
antes/después y comprobaciones de integridad), `SHA256SUMS.txt` y `Apply-DummyPatch.ps1`. El original
no se copia porque es del mod: el script guarda un backup verificado en la propia instalación.
Ver `docs/native-timing.md`.
