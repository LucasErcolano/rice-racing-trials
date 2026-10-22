# Rice Racing Driver Trials Core + Package B

**Estado: desarrollo y comisionamiento, no apto para trials oficiales.** El ZIP inicial no contenía el KN5 de FSAEJ Endurance ni el vehículo MFT02; posteriormente se obtuvieron ambos archivos autorizados desde la carpeta Drive compartida. Se conservan en `assets/source/` para auditoría local y **no se redistribuyen**. La app CSP y el servidor no han sido ejecutados juntos dentro de Assetto Corsa en Windows. No hay validación física de conos. Ningún resultado provisional debe utilizarse para seleccionar pilotos.

## V2 de campo (pre-release opt-in)

Guía de instalación y pruebas: [`docs/v2-field-testing.md`](docs/v2-field-testing.md). La V2 agrega captura nativa y penalizada con journals/reintentos separados, reconciliación por identidad de vuelta y monitor propio en `/v2/field`. No depende del leaderboard de Velocity. V1 sigue siendo el default.

Las nativas solas se muestran como `PENALTIES_PENDING`, no como vueltas limpias ni resultados oficiales. La corregida requiere evidencia penalizada; las inconsistencias pasan a revisión. El contacto geométrico suficiente de conos mantiene el criterio V1, sin exigir derribo físico. `Dummy.kn5` está validado en juego por confirmación del usuario.

Velocity confirmó CSP 2.11, instalación de nuestra app Lua, LAN y uso de nuestro servidor/TV. Las calibraciones pendientes se completan durante las pruebas y no bloquean instalar la pre-release. La V2 completa aún necesita validación en juego.

## Componentes

- `client/`: app CSP Lua, HUD y pruebas aisladas. La detección espacial opera solamente con calibración verificable; no certifica derribos mediante proximidad.
- `server/`: recepción HTTP, almacenamiento SQLite, asignación de piloto, clasificaciones y exportación.
- `leaderboard/`: pantalla pública y consola administrativa.
- `packaging/`: scripts de empaquetado e instalación; los binarios Windows deben compilarse y probarse en Windows.
- `docs/`: auditoría del archivo original, limitaciones, contrato, despliegue y checklist presencial.

## Fuente inalterada

`Rice_Driver_Trials_v1.0_Hardware_Human_Handoff.zip`: SHA-256 `46b33cf4085d7e4e8f003010fc3e75903596601531e7d047f20ec026c27f7574`. No se redistribuyen circuitos, auto ni Assetto Corsa en nuestros paquetes.

## Pruebas Linux

Requieren un venv con `lupa` (y `playwright` para las pruebas de navegador):

```sh
V=<venv>/bin/python
$V -m unittest discover -s tests -p '*test*.py'
(cd client/tests && $V -m unittest discover -p 'test_*.py')
```

Estado completo y pendientes: `docs/final-status.md`. Resultados: `docs/test-report.md`.

Ver `docs/zip-audit.md`, `docs/limitations.md` y `docs/windows-validation.md` antes de cualquier despliegue. El criterio de habilitación oficial es una prueba real de pista, vehículo, conos, outlap, geometría y red en cada estación, no solamente que pasen tests Linux.
