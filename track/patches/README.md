# Dummy.kn5 candidate patches (CANDIDATE, NOT VERIFIED IN GAME)

This directory versions only the **code** (generator and apply/revert script). The candidate
binaries are generated in `dist/track-calibration/patches/` (ignored by git) because they are derived
from the mod:

```
python -m track.tool dummy-patch --source <track folder or ZIP> --track FSAEJ_Endurance \
  --output dist/track-calibration/patches/FSAEJ_Endurance-dummy-start-after8
```

Each folder contains `Dummy.kn5.candidate`, `manifest.json` (SHA-256 of the expected original, positions
before/after and integrity checks), `SHA256SUMS.txt` and `Apply-DummyPatch.ps1`. The original
is not copied because it belongs to the mod: the script stores a verified backup in the installation itself.
See `docs/native-timing.md`.
