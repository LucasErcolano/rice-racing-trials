# Resumen técnico

**Implementado:** auditoría del material original y hashes; servidor SQLite/HTTP con asignación de pilotos por estación, tokens, heartbeat, duplicados idempotentes, clasificación por tiempo corregido para resultados `VALID`, backup y exportación CSV; interfaz web pública y administrativa básica; cliente Lua CSP de cruce provisional con HUD y fail-closed sin ruta completa; empaquetado de código fuente e instaladores preliminares Windows.

**Probado en Linux:** nueve pruebas del servidor y doce del cliente Lua aislado, más comprobación de integridad ZIP. Se probaron mensajes simulados al servidor, no vueltas desde Assetto Corsa.

**No implementado / pendiente:** detección verificable de conos derribados, off-course y slalom de la pista real; envío autenticado del cliente CSP a Package B; cola con durabilidad atómica comprobada; paridad integral Lua-servidor; servicio de arranque automático del servidor; reparación del Best Lap nativo. El cliente tiene una máquina de ruta/outlap provisional basada en gates que requieren calibración y una cola de revisiones en `ac.storage`, no certificación automática. Ninguna función está validada dentro de Assetto Corsa Windows. Los ZIPs con nombre solicitado contienen fuente solamente y no son paquetes operativos listos para trials.

**Activos recibidos después:** se descargaron y verificaron por SHA-256 los archivos FSAEJ Endurance, Skidpad y MFT02 desde el Drive compartido. La mera disponibilidad de los archivos no demuestra que los gates correspondan al modelo instalado ni que los conos expongan colisión física; esto requiere cotejo y pruebas en el simulador. Una calibración declarada por el cliente nunca reemplaza la validación del activo instalado por el servidor.

Ver `docs/limitations.md` y `docs/windows-validation.md` antes de cualquier demostración; no publicar resultados como oficiales.
