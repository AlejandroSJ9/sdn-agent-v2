# Ajustes solicitados a Ryu-IA (para Sebastián)

Cambios pequeños en `ryu_controller.py`. Ninguno toca la lógica de detección M1–M5.

## A1. Puerto y token del agente — obligatorio
```python
AGENT_URL     = 'http://localhost:8081/api/alertas'   # antes: 8080/alert
AGENT_ENABLED = True
AGENT_TOKEN   = os.environ.get('AGENT_TOKEN', '')
```
En `_post_to_agent` agregar el encabezado `'X-Agent-Token': AGENT_TOKEN`.
Motivo: 8080 lo usa la API REST de Ryu.

## A2. Quitar el desbloqueo automático — obligatorio
En `_ejecutar_respuesta_agente` eliminar la rama `accion == 'desbloquear'`. Solo se acepta `mantener_bloqueo` (registrar en log). Cualquier otra acción → `logger.warning` y no hacer nada.
Motivo: decisión de diseño. Ryu decide el bloqueo; solo un humano desbloquea.

## A3. API REST mínima (WSGI de Ryu, puerto 8080) — obligatorio
Usar `ryu.app.wsgi` (`ControllerBase`, `WSGIApplication`, `route`). Todas las rutas validan `X-Agent-Token`.

| Método | Ruta | Respuesta |
|---|---|---|
| GET | `/ryu/estado` | `{ "switches": ["0x..1"], "hosts_bloqueados": 1, "umbrales": {...}, "version": "x" }` |
| GET | `/ryu/hosts/bloqueados` | `[{ "ip": "10.0.0.3", "dpid": "0x..1", "desde": "ISO-8601" }]` |
| POST | `/ryu/hosts/{ip}/desbloquear` | `{ "ok": true, "ip": "...", "reglas_borradas": 1 }` · 404 si no está bloqueado |
| GET | `/ryu/umbrales` | `{ "M1": 20, "M2": 5, "M4": 60, "M5": 3, "CRITICAL_THRESHOLD": 2 }` (nombres reales del código) |
| PUT | `/ryu/umbrales` | mismo cuerpo, parcial; valida enteros > 0; devuelve los vigentes |

**Importante en desbloquear:** hoy solo se limpia la memoria y la regla DROP queda en el switch. Debe enviar `OFPFlowMod` con `command=OFPFC_DELETE_STRICT`, `priority=100` y `match(eth_type=0x0800, ipv4_src=ip)` a cada datapath donde se instaló, y luego limpiar `blocked_hosts`, `host_alert_state`, `metric_snapshot` y `host_flow_log` de esa IP.

Para que esto funcione, `_block_host` debe recordar en qué `datapath` y cuándo bloqueó (por ejemplo `self.blocked_hosts[ip] = {'dpid':..., 'desde':...}`).

## A4. Nomenclatura (documento de tesis) — recomendado
El código usa M1–M5 y la tesis KPI-01–KPI-05 con definiciones distintas (p. ej. KPI-05 regularidad de interarribo no existe en el código). Elegir una sola y alinear el capítulo 1 con lo que el controlador realmente mide.

## Cómo probarlo
```bash
curl -H "X-Agent-Token: $AGENT_TOKEN" localhost:8080/ryu/hosts/bloqueados
curl -X POST -H "X-Agent-Token: $AGENT_TOKEN" localhost:8080/ryu/hosts/10.0.0.3/desbloquear
# en Mininet: h3 ping h1 debe volver a responder
```
