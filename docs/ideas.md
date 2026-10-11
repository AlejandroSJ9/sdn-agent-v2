# Ideas fuera de alcance

Anotar aquí (una línea) lo que no pide la fase actual.

## Implementadas en la Fase 6.5 (endurecimiento)

- ✅ **[F0] Dependencias transitivas fijadas:** `requirements.lock` (pip freeze); el README indica instalar desde él.
- ✅ **[F0] SQLite con `PRAGMA foreign_keys=ON` y `journal_mode=WAL`:** listener `connect` en `database.py`.
- ✅ **[F0] Auditoría protegida en la base:** triggers `BEFORE UPDATE/DELETE` con `RAISE(ABORT)`.
- ✅ **[F0] Secretos obligatorios al arrancar:** sin `AGENT_TOKEN`, `OPERADOR_TOKEN` o `AUDITORIA_CLAVE` el agente no arranca (`Settings.verificar_secretos`).
- ✅ **[F0] Fechas siempre UTC "aware":** `TypeDecorator` `FechaHoraUTC` en las 13 columnas de fecha.
- ✅ **[F1] Cadena de auditoría con clave:** HMAC-SHA256 con `AUDITORIA_CLAVE` (opción a). Sin la clave, rehacer la cadena se detecta.
- ✅ **[F1] 429 sin rastro:** ahora queda una advertencia en el log con la IP (sin escribir en la BD). El reintento desde Ryu quedó descartado (abajo).
- ✅ **[F2] Incidentes sin explicar tras un reinicio:** se reprocesan al arrancar (solo los reales; respeta cupo y `LLM_HABILITADO`).
- ✅ **[F6] Corridas y pytest "en_curso" tras un reinicio:** se marcan "interrumpida" al arrancar y se audita `recuperacion_al_arrancar`.

## Descartadas para el MVP (trabajo futuro)

- **[F0] Migraciones con Alembic:** dependencia nueva; en el MVP basta con borrar la `sdn_agent.db` local tras un cambio de esquema.
- **[F1] Anclar el último hash fuera del agente (p. ej. en cada correo):** el HMAC ya impide rehacer la cadena; truncar las últimas filas sigue sin detectarse y queda como mejora futura.
- **[F1] Locks válidos entre procesos (`BEGIN IMMEDIATE`):** el agente se arranca con un solo worker; se dejará escrito en la guía de demo (Fase 7).
- **[F1] Reintento de Ryu ante un 429:** requiere cambiar Ryu-IA (Sebastián) y el límite actual cubre el tráfico esperado.
- **[F1] Límite de tamaño del cuerpo antes de validar el token:** el agente solo escucha en localhost durante el MVP.
- **[F1] Alerta y auditoría en la misma transacción:** la ventana de fallo es mínima y exigiría cambiar la firma de `registrar`.
- **[F2] Umbrales vigentes de Ryu dentro de `<datos_red>`:** implica cambiar el prompt del LLM, fuera del alcance del MVP.
- **[F2] `scripts/probar_llm.py` no descuenta cupo:** es deliberado (no toca la BD de producción); el script es de uso manual y puntual.
- **[F3] Endpoints de prueba/reenvío síncronos:** son acciones manuales del operador; la espera solo ocurre si Apps Script falla.
- **[F4] Host desbloqueado en Ryu sin registro si la BD falla justo después:** caso muy improbable con SQLite local; el error queda en el log.
- **[F5] Evento `notificacion_omitida`:** requiere cambiar el contrato C5; el panel lo ve al refrescar el incidente.
- **[F5] `favicon.ico`:** puramente cosmético.
- **[F6] Contar peticiones HTTP (no incidentes) por corrida:** la aproximación basta para la evaluación y exigiría una columna nueva.
