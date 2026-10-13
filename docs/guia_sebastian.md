# Guía rápida: validar la alerta de Ryu-IA contra el agente

1. Copia el JSON que Ryu imprime en el log (`[CAPA 3] Payload JSON para ...`) a un archivo, por ejemplo `alerta.json`.
2. Desde la raíz de sdn-agent-v2 (con el `.venv` activo): `python scripts/validar_payload_ryu.py alerta.json`.
3. También puedes pegarlo directo: `python scripts/validar_payload_ryu.py`, pega el JSON y termina con Ctrl+D.
4. No necesitas el agente corriendo ni el `.env`: usa el mismo esquema que el agente (C1 de `docs/contratos.md`).
5. Si responde `COMPATIBLE`, el agente aceptará la alerta con 202.
6. Si no, lista cada problema como `campo <nombre>: <problema> (recibido: <valor>)`.
7. "campo obligatorio ausente" junto a "el agente no lo reconoce (¿nombre cambiado?)" suele indicar un campo renombrado.
8. Obligatorios: `timestamp`, `host_origen` (IPv4) y las 6 métricas de `metricas`; los conteos pueden ir como 24.0, no como 24.5.
9. Ejemplo de referencia aceptado: `tests/contrato/alerta_ryu_real.json`. Si guardas ahí una alerta real, las pruebas la usarán.
10. Código de salida: 0 compatible, 1 no compatible (útil para automatizarlo).

## Antes de conectar

Con **A0** aplicado, en el log de Ryu (`[CAPA 3] Payload JSON para ...`) `M1_nuevos_flujos_por_30s`, `M2_destinos_unicos_por_30s`, `M4_packet_in_por_min` y `M5_count` deben tener **valores reales**, no 1, 2, 4 y 5. Si ves 1/2/4/5, A0 no está aplicado: el informe del agente mostrará que esas métricas "no superan" el umbral mientras Ryu dice que sí (el agente no corrige los datos de Ryu, solo los compara).

Síntomas y causa probable:

| Síntoma | Causa |
|---|---|
| 401 en las alertas | `AGENT_TOKEN` distinto en Ryu y en el `.env` del agente |
| 404 en "Alertas rechazadas" ("ruta antigua /alert") | falta A1: Ryu sigue enviando a `/alert` |
| `api_no_disponible (falta A5)` en el estado de Ryu | falta A5: Ryu no expone la API REST `/ryu/*` |
| 503 "Ryu no expone la API REST (A5)" al desbloquear o leer bloqueados/umbrales | falta A5 |
| 413 | payload mayor que `ALERTA_MAX_BYTES` (64 KB por defecto) |
| 422 | campo inválido o ausente: valida con `python scripts/validar_payload_ryu.py alerta.json` |
