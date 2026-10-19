# Límites y gates de aceptación

1. El archivo original incluye definiciones medidas de FSAEJ Endurance, **no** el KN5 instalable. Posteriormente se descargaron los archivos FSAEJ Endurance, FSAEJ Skidpad y MFT02 desde el Drive autorizado. No se incluyen en los paquetes distribuidos; todavía hace falta comprobarlos dentro del juego. Los circuitos Rice del archivo original son alternativos y no reemplazan al seleccionado.
2. No se ejecutó Assetto Corsa ni CSP en Windows en este entorno. Pruebas Lua aisladas verifican algoritmos puros, no API ni autoload real.
3. No hay evidencia física suficientemente fiable de derribo de conos: proximidad, por sí sola, no permite penalizar. Si la API física no expone cambio de estado verificable, cada contacto requiere revisión humana.
4. El off-course y slalom exigen geometría y gates versionados de la instalación real. Las definiciones históricas deben cotejarse con el KN5 descargado y validarse dentro del juego. Sin calibración, estado `PENDING_REVIEW` o `DETECTOR_ERROR`, nunca `VALID`.
5. El primer cruce del timing gate desde el spawn es outlap; no se declara corregido el Best Lap nativo. Un HUD propio no altera tiempos internos del juego.
6. HTTP simple con token en LAN no proporciona confidencialidad ante otros usuarios de la red. Aislar la LAN o añadir terminación TLS y controles operativos.
7. El pipeline Windows no equivale a un ejecutable Windows realmente compilado y probado. Comprobar instalación, autoload, continuidad sin HUD, persistencia tras apagado y reversión.
8. Las pruebas de entrega Linux no bastan para habilitar las pruebas de pilotos: hacer dry-run humano y firmar la checklist de `windows-validation.md`.

Los estados se distinguen como **implementado**, **probado en Linux** y **validado en Assetto Corsa/Windows**. No se confunden entre sí.
