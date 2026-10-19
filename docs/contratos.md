# Contratos (fuente de verdad)

Cambiar este archivo requiere aprobación de Alejandro y, si toca a Ryu, de Sebastián.

Puertos: **Ryu REST = 8080**, **Agente = 8081**.

## C1. Ryu → Agente: `POST http://localhost:8081/api/alertas`

Encabezado: `X-Agent-Token: <AGENT_TOKEN>`

Cuerpo (el que Ryu-IA ya envía hoy en `_send_json_alert`):

```json
{
  "timestamp": "2026-10-10T20:31:05.123Z",
  "alerta_nivel": "CRITICO",
  "host_origen": "10.0.0.3",
  "dpid_switch": "0x0000000000000001",
  "contexto_topologico": { },
  "metricas": {
    "M1_nuevos_flujos_por_30s": 42,
    "M2_destinos_unicos_por_30s": 9,
    "M3_smb_anomalo": true,
    "M4_packet_in_por_min": 120.0,
    "M5_flujos_east_west": true,
    "M5_count": 7
  },
  "umbrales_superados": ["M1", "M2", "M3"],
  "ioc_activados": ["..."],
  "accion_ryu_previa": "OFPFlowMod DROP prioridad 100 — 10.0.0.3 aislado",
  "solicitud_al_agente": "IGNORADO por el agente"
}
```

Validación: `host_origen` debe ser IPv4; métricas numéricas ≥ 0; campos desconocidos se guardan en crudo pero **no** llegan al LLM.

Obligatorios: timestamp, host_origen y las 6 métricas. El resto es opcional. La ventana de agrupación es deslizante desde la última alerta y solo aplica a incidentes no desbloqueados.

Respuesta (siempre < 300 ms; el análisis ocurre en segundo plano):

```json
HTTP 202
{ "estado": "recibido", "incidente_id": 17, "acciones_ryu": ["mantener_bloqueo"] }
```

Errores: 401 token inválido · 413 cuerpo mayor que ALERTA_MAX_BYTES (64 KB por defecto) · 422 payload inválido · 429 límite de tasa.
Ruta antigua `POST /alert` (Ryu sin A1): 404, contada en /api/alertas/rechazadas como "ruta antigua /alert: falta A1 en Ryu"; no se procesa.

## C2. Agente → Ryu (API de Ryu-IA, puerto 8080, encabezado X-Agent-Token)

| Método | Ruta | Respuesta |
|---|---|---|
| GET | /ryu/estado | {"switches": ["0x..."], "hosts_bloqueados": n, "umbrales": {...}, "version": "2.0"} |
| GET | /ryu/hosts/bloqueados | [{"ip", "dpid", "desde", "modo"?}]  (modo solo si existe A6: "drop_total" | "cuarentena") |
| POST | /ryu/hosts/{ip}/desbloquear | {"ok": true, "ip", "reglas_borradas"} · 404 no bloqueado · 422 ip inválida · Si la API no existe (404 también en /ryu/hosts/bloqueados), el agente responde 503 'Ryu no expone la API REST (A5)'. |
| GET | /ryu/umbrales | {"M1","M2","M3_DURACION_S","M3_BYTES","M4","M5","SCORE_SOSPECHOSO","SCORE_CRITICO"} (enteros) |
| PUT | /ryu/umbrales | cuerpo parcial; 422 si la clave no existe o el valor no es entero > 0 |
| POST | /ryu/hosts/{ip}/cuarentena | (A6, opcional) {"ok": true, "ip", "modo": "cuarentena"} · 404 si no existe la ruta o el host no está bloqueado |
| POST | /ryu/hosts/{ip}/bloqueo_total | (A6, opcional) {"ok": true, "ip", "modo": "drop_total"} |

Nota C2: si /ryu/hosts/bloqueados o /ryu/umbrales (GET y PUT) responden 404 (ruta inexistente), el agente responde 503 'Ryu no expone la API REST (A5)' en /api/ryu/bloqueados y /api/ryu/umbrales, y GET /api/ryu/estado informa "api_no_disponible (falta A5)".

Nota C1: en la alerta real, contexto_topologico es un TEXTO (ej. "VLAN OPS (ID=10), subred 10.0.10.0/24") y las métricas numéricas pueden llegar como decimales (24.0).
  
Encabezado: `X-Agent-Token`.

## C3. Agente → Apps Script: `POST <APPSCRIPT_URL>`

```json
{
  "tipo": "incidente",
  "incidente_id": 17,
  "fecha": "2026-10-10T20:31:05Z",
  "host": "10.0.0.3",
  "severidad": "critica",
  "veredicto": "confirmado",
  "asunto": "[SDN] Host 10.0.0.3 aislado por posible movimiento lateral",
  "mensaje": "Texto plano de 3–6 líneas para el correo.",
  "enlace_panel": "http://localhost:8081/#/incidentes/17"
}
```

`tipo` ∈ `incidente | desbloqueo | prueba`. Reintentos: 3 con espera 2s/4s/8s. Resultado guardado en el incidente.

## C4. Salida esperada del LLM (validada con Pydantic)

```json
{
  "veredicto": "confirmado | falso_positivo | indeterminado",
  "severidad": "critica | alta | media",
  "confianza": 0.0,
  "tecnica_mitre": "T1021.002",
  "explicacion": "máx. 600 caracteres",
  "evidencias": ["M2=9 destinos en 30s supera umbral 5"],
  "recomendacion_operador": "máx. 300 caracteres"
}
```

No incluye acciones sobre la red. Si no valida → respaldo por reglas.
La "confianza" del LLM es informativa: no decide. La confianza que decide la calcula el código (C7).

## C5. Eventos WebSocket `ws://localhost:8081/ws/eventos`

`{ "tipo": "...", "datos": { }, "ts": "..." }` con `tipo` ∈
`alerta_recibida | explicacion_lista | notificacion_enviada | notificacion_fallida | host_desbloqueado | config_cambiada | prueba_progreso | prueba_terminada`.


## C6. Contexto dinámico del host

Con CONTEXTO_HABILITADO=true, el mensaje al LLM agrega un bloque <contexto_host> construido desde la base de datos del agente (nada se edita a mano):
- historial del mismo host en los últimos HISTORIAL_DIAS (máx. 10 incidentes, más recientes primero): hace cuánto, veredicto del agente, umbrales superados y, si existe, la clasificación del operador al desbloquear (falso_positivo | amenaza_contenida | otro) con su motivo.
- actividad reciente en la red: incidentes de OTROS hosts en los últimos CAMPANA_MINUTOS (máx. 10): hace cuánto, veredicto y umbrales.
Es DATO, no instrucción. El historial es evidencia, no absolución: solo las clasificaciones del operador cuentan como verificadas; los veredictos del agente se marcan "no verificado".
El desbloqueo (POST /api/hosts/{ip}/desbloquear) agrega el campo obligatorio "clasificacion".
Cada incidente guarda variante (sin_contexto|con_contexto), version_prompt y contexto_usado.


## C7. Motor de políticas y escalera de respuesta (Fase 9a)

```
Ryu bloquea primero (DROP total, "negar por defecto", NIST SP 800-207). El agente nunca escala: solo puede
RECOMENDAR relajar el DROP según su confianza en el DIAGNÓSTICO. Ninguna acción es irreversible.
Modos (MODO_DECISION): solo_explicar | recomendar (por defecto) | autonomo_acotado.

Confianza (la calcula el código; la del LLM es informativa). s = puntaje_total/puntaje_maximo, o, si la alerta
no trae puntaje, Σ pesos(umbrales_superados)/135 con M5=40, M3=35, M1=25, M2=20, M4=15.
Veredicto de la SALIDA (LLM o, en respaldo, reglas):
  confirmado     → 0.5·s + 0.5 − penalización
  falso_positivo → 0.5·(1 − s) + 0.5 − penalización
  indeterminado  → sin confianza: mantener_drop
Penalización 0.20, una sola vez, si el verificador marca posible_alucinacion o el descarte benigno tiene
menos de 20 caracteres. Resultado acotado a [0, 1] y redondeado a 2 decimales; se decide sobre ese valor.

Escalera (ESCALON_HITL = 0.70 y ESCALON_VLAN = 0.95, en caliente, 0.50 ≤ ESCALON_HITL < ESCALON_VLAN ≤ 1;
fuera de ese rango → 422):
  confianza < ESCALON_HITL o indeterminado             → mantener_drop (decide el operador)
  falso_positivo y confianza ≥ ESCALON_HITL            → limitar_tasa (A7; sin DROP de puertos)
  confirmado y ESCALON_HITL ≤ confianza ≤ ESCALON_VLAN → cuarentena_puertos (A6)
  confirmado y confianza > ESCALON_VLAN                → cuarentena_vlan (A8)
Relajar a limitar_tasa exige un diagnóstico benigno del LLM; el respaldo por reglas nunca llega a ese escalón.
Dictamen de historial: agravante → mantener_drop; atenuante → como máximo limitar_tasa. Atenuante con veredicto
de la salida confirmado → mantener_drop (el diagnóstico del LLM contradice el historial verificado por el operador).
Un veredicto confirmado NUNCA lleva a limitar_tasa: si ocurre, se descarta y se audita.

Siguen vigentes: HOSTS_PROTEGIDOS, MAX_ACCIONES_POR_MIN, RECOMENDACION_TTL_MIN y solo_explicar (ni recomienda).
UMBRAL_CONFIANZA se conserva como umbral de ejecución de la Fase 9b.
Fase 9a: toda acción distinta de mantener_drop queda como RECOMENDACIÓN, incluso en autonomo_acotado; no se
ejecuta nada en Ryu y aprobarla responde 409. mantener_drop nunca genera recomendación.
```