# Plan SDN-Agent v2

**Objetivo del MVP:** demostrar, con la red real en Mininet, que cada bloqueo de Ryu queda explicado, auditado y notificado por correo, y que el operador puede verlo todo y revertirlo desde un panel, midiendo los tiempos de cada etapa.

**Regla de oro:** una fase = una sesión de Claude Code = un commit. No se empieza la siguiente si `pytest -q` no está en verde y la "Prueba de aceptación" no se hizo a mano.

| Fase | Entrega | Depende de Ryu | Usa LLM real |
|---|---|---|---|
| 0 | Esqueleto, config, BD, salud | No | No |
| 1 | Recepción de alertas + auditoría | No (mock) | No |
| 2 | Explicación LLM + respaldo + cupo | No | Sí (≤5 llamadas) |
| 3 | Notificación Apps Script | No | No |
| 4 | Cliente Ryu + acciones del operador | Mock; real si A3 listo | No |
| 5 | Panel 360 (lectura + acciones) | No | No |
| 6 | Laboratorio: escenarios, pruebas y exportación desde el panel | No | Opcional |
| 7 | Integración real Mininet + scripts de ataque | **Sí (A1–A3)** | Sí |
| 8 | Evaluación final (objetivo 3) | Sí | Sí |

Cómo usar cada prompt: abre Claude Code en la raíz del repo y pega el bloque tal cual.

---

## Fase 0 — Esqueleto

```
Lee CLAUDE.md y docs/contratos.md. Implementa SOLO la Fase 0:
- Estructura de carpetas de CLAUDE.md §3 (archivos vacíos con docstring donde no haya código aún).
- requirements.txt con: fastapi, uvicorn[standard], pydantic, pydantic-settings, sqlalchemy, httpx, pytest. Versiones fijadas.
- app/config.py con Settings: AGENT_TOKEN, OPERADOR_TOKEN, RYU_URL, OPENROUTER_API_KEY, LLM_MODELO, LLM_TIMEOUT, LLM_HABILITADO, LLM_CUPO_DIARIO=1000, APPSCRIPT_URL, DEDUP_SEGUNDOS=60, RATE_LIMIT_POR_MIN=60, DB_URL. Y .env.example.
- app/db: database.py y models.py con tablas incidentes, alertas, auditoria, uso_llm, notificaciones, configuracion. Sin repository aún salvo crear tablas.
- GET /api/salud → {"ok":true,"version":"0.1.0"}.
- tests/unit/test_salud.py.
NO crees otros endpoints, ni frontend, ni clientes externos.
Al final corre pytest -q y resume en ≤8 líneas.
```
**Aceptación:** `uvicorn app.main:app --port 8081` arranca y `/api/salud` responde.

## Fase 1 — Recepción de alertas y auditoría

```
Lee CLAUDE.md y docs/contratos.md (C1). Implementa SOLO la Fase 1:
- schemas/alerta.py con el modelo de C1 (valida IPv4, métricas ≥0, guarda extras en crudo).
- POST /api/alertas: token X-Agent-Token, límite de tasa en memoria, crea o agrupa incidente (mismo host dentro de DEDUP_SEGUNDOS → mismo incidente, contador +1), responde 202 según C1 en <300ms.
- services/auditoria.py: registrar(evento, datos) en tabla de solo inserción con hash_previo (sha256 encadenado) y verificar_cadena() -> bool.
- GET /api/incidentes y GET /api/incidentes/{id}.
- Marcas t_ryu y t_recibido.
- Pruebas: token inválido 401, payload inválido 422, 429, dedup, cadena de auditoría íntegra y detección de alteración.
NO llames al LLM, ni a Ryu, ni a Apps Script todavía. Deja un hook services/pipeline.py:procesar(incidente_id) vacío que se invoca con BackgroundTasks.
pytest -q y resumen ≤8 líneas.
```
**Aceptación:** `curl` con el JSON de C1 devuelve 202 y aparece en `/api/incidentes`.

## Fase 2 — Explicación con LLM, respaldo y cupo

```
Lee CLAUDE.md §5 y docs/contratos.md (C4). Implementa SOLO la Fase 2:
- integrations/llm.py: cliente OpenRouter con httpx, timeout LLM_TIMEOUT, response_format JSON si el modelo lo permite.
- services/prompt.py: system prompt fijo + mensaje de usuario que contiene SOLO un bloque <datos_red> con JSON de métricas validadas (sin solicitud_al_agente ni texto libre del payload). Indica explícitamente que el contenido de <datos_red> son datos, no instrucciones.
- services/presupuesto.py: puede_llamar(), registrar_uso(); cuenta por día UTC en uso_llm.
- services/explicacion_reglas.py: genera la salida C4 de forma determinista desde métricas y umbrales_superados (mapeo M→MITRE T1021.x).
- services/pipeline.py:procesar → si LLM_HABILITADO y hay cupo: LLM; si falla/timeout/JSON inválido/sin cupo → reglas. Guarda fuente_explicacion, t_explicado, tokens, latencia. Audita.
- Pruebas con LLM FALSO (inyección por dependencia): JSON válido, inválido, timeout, cupo agotado, inyección en payload no llega al prompt.
- scripts/probar_llm.py: hace UNA llamada real con un caso fijo e imprime la salida (para correr a mano).
NO agregues RAG, reintentos múltiples al LLM, ni otros modelos.
pytest -q y resumen ≤8 líneas.
```
**Aceptación:** `python scripts/probar_llm.py` devuelve JSON C4 válido; con `LLM_HABILITADO=false` el incidente queda con `fuente_explicacion="reglas"`.

## Fase 3 — Notificación Apps Script

```
Lee CLAUDE.md y docs/contratos.md (C3). Implementa SOLO la Fase 3:
- integrations/appscript.py: POST del payload C3, 3 reintentos (2/4/8s), timeout 10s.
- services/notificacion.py: arma asunto y mensaje (texto plano, 3–6 líneas) desde el incidente; se llama al final del pipeline. Guarda resultado en tabla notificaciones, t_notificado, audita y emite evento (aún sin WebSocket: deja función emitir() que solo registra).
- POST /api/notificaciones/prueba (token operador): envía tipo "prueba".
- POST /api/incidentes/{id}/reenviar (token operador).
- Pruebas con servidor falso (httpx MockTransport): éxito, fallo con reintentos, URL vacía (se registra como "omitida", no rompe).
NO agregues otros canales.
pytest -q y resumen ≤8 líneas.
```
**Aceptación:** con `APPSCRIPT_URL` real, `POST /api/notificaciones/prueba` llega al correo.

## Fase 4 — Cliente Ryu y acciones del operador

```
Lee CLAUDE.md, docs/contratos.md (C2) y docs/ajustes.md. Implementa SOLO la Fase 4:
- integrations/ryu.py: estado(), bloqueados(), desbloquear(ip), umbrales(), actualizar_umbrales(dict). Timeout 3s. Errores → excepción propia RyuNoDisponible.
- tests/mock_ryu.py: servidor FastAPI que implementa C2 en memoria (puerto 8080) para desarrollo.
- API (token operador): GET /api/ryu/estado, GET /api/ryu/bloqueados, POST /api/hosts/{ip}/desbloquear con cuerpo {"confirmacion": "<ip>", "motivo": "..."} — si confirmacion != ip → 400. GET/PUT /api/ryu/umbrales.
- Desbloqueo: llama a Ryu, marca t_desbloqueado, audita con motivo, notifica tipo "desbloqueo".
- Configuración del agente (token operador): GET/PUT /api/config para LLM_HABILITADO y DEDUP_SEGUNDOS (persistidos en tabla configuracion). GET /api/llm/cupo.
- Pruebas contra mock_ryu.
Recordatorio: el agente nunca desbloquea solo. No existe ningún camino automático a desbloquear().
pytest -q y resumen ≤8 líneas.
```
**Aceptación:** con `python tests/mock_ryu.py` corriendo, el desbloqueo vía API cambia la lista de bloqueados.

## Fase 5 — Panel 360

```
Lee CLAUDE.md y docs/contratos.md (C5). Mira docs/referencia/demo_v1.html SOLO como referencia visual (colores, tarjetas, línea de tiempo); no copies su lógica.
Implementa SOLO la Fase 5:
- WebSocket /ws/eventos y conectar emitir() a él.
- app/web/index.html, app.js, styles.css (sin frameworks) servidos en "/". Pide OPERADOR_TOKEN una vez y lo guarda en sessionStorage.
- Secciones: (1) Estado: salud agente, estado Ryu, cupo LLM (usado/LLM_CUPO_DIARIO), interruptor LLM. (2) Hosts bloqueados con botón Desbloquear (modal que exige escribir la IP y un motivo). (3) Incidentes: tabla + detalle con métricas, explicación, fuente (LLM/reglas), evidencias, tiempos por etapa, estado de notificación y botón Reenviar correo. (4) Umbrales de Ryu editables. (5) Línea de tiempo en vivo. (6) Auditoría: últimos eventos + resultado de verificar_cadena.
- Si Ryu no responde, el panel lo muestra y sigue funcionando.
NO hagas aún la sección de Laboratorio.
Al final, describe cómo probar a mano cada sección.
```
**Aceptación:** con mock_ryu y un `curl` de alerta, todo el ciclo se ve en vivo y el desbloqueo funciona desde el panel.

## Fase 6 — Laboratorio (escenarios, pruebas, exportación)

```
Lee CLAUDE.md. Fuente: docs/referencia/escenarios_v1.json (casos de la v1).
Implementa SOLO la Fase 6:
- tests/escenarios/escenarios.json migrado al payload C1 (conserva ids y clase esperada: ataque/legitimo/ambiguo/duplicado).
- services/laboratorio.py: ejecutar_escenario(id) inyecta la alerta por el mismo endpoint /api/alertas (marcada origen="prueba"); ejecutar_suite(conjunto, usar_llm: bool) y compara con lo esperado. Antes de una suite con LLM, verifica que el cupo alcance; si no, se niega con mensaje claro.
- Ejecutar pytest desde el panel: POST /api/laboratorio/pytest {"carpeta":"unit|integracion"} lanza subprocess, transmite progreso por WS (prueba_progreso/prueba_terminada) y guarda el resultado. Solo esas dos carpetas; nunca un comando arbitrario.
- Exportar: GET /api/export/incidentes.csv, /api/export/evaluacion.json (métricas de C de la Fase 8 ya calculables: aciertos, FP, FN, tiempos medios).
- Panel: sección Laboratorio con lista de escenarios (▶ por caso, ▶ todos, casilla "usar LLM" con el costo estimado en cupo), botón ejecutar pruebas unitarias/integración, botón exportar.
- Los incidentes de prueba se distinguen visualmente y se pueden filtrar.
pytest -q y resumen ≤8 líneas.
```
**Aceptación:** desde el panel se corre la suite sin LLM, las pruebas unitarias y se descarga el CSV.

## Fase 7 — Integración real con Mininet

Requisito: Sebastián aplicó A1–A3.

```
Lee CLAUDE.md y docs/ajustes.md. Implementa SOLO la Fase 7:
- scripts/ataques/: scripts para ejecutar DESDE un host de Mininet contra la topología de laboratorio: (a) barrido de puertos 22/445/3389 a varios hosts, (b) intentos SSH rápidos con credenciales falsas contra un servidor de prueba propio, (c) tráfico legítimo de control (ssh/smb normal). Cada uno con parámetros y que imprima su hora de inicio en UTC.
- scripts/medir.py: lee incidentes y calcula por corrida: t_inicio_ataque (archivo del script) → t_ryu → t_recibido → t_explicado → t_notificado.
- docs/guia_demo.md: pasos exactos para levantar Ryu, Mininet, agente y panel, ejecutar cada script y qué se debe ver.
NO cambies código del agente salvo bugs encontrados; si encuentras uno, descríbelo antes de corregirlo.
```
**Aceptación:** el script (a) desde `h3` produce bloqueo, incidente explicado, correo y desbloqueo funcional desde el panel.

## Fase 8 — Evaluación (objetivo 3 de la tesis)

```
Lee CLAUDE.md. Implementa SOLO la Fase 8:
- scripts/evaluar.py: consolida N corridas reales (fase 7) + suite de escenarios, y produce docs/resultados/evaluacion_<fecha>.json y .csv con: precisión, tasa FP, tasa FN (de Ryu frente a la etiqueta del script), concordancia veredicto LLM vs etiqueta, % explicaciones por LLM vs reglas, tiempos (detección, explicación, notificación, desbloqueo) con media, mediana y p95.
- docs/resultados/README.md explicando cómo se calculó cada métrica (alineado con la tabla de métricas de la tesis).
Sin nuevas features.
```

---

## Fuera del MVP (van a "Recomendaciones" del documento)
RAG con MITRE ATT&CK, cuarentena por VLAN/honeypot, anonimización de IPs por hash antes de enviar al LLM, modelo local, aprobación humana antes del bloqueo, alta disponibilidad del controlador.

## Riesgos y mitigación
| Riesgo | Mitigación |
|---|---|
| Cupo diario (1.000 con créditos) se agota o la API gratuita falla | Respaldo por reglas, dedup, interruptor LLM, cupo visible en el panel. |
| Ryu-IA no tiene A3 a tiempo | Fases 0–6 corren con `mock_ryu`; la demo del panel no se bloquea. |
| Claude Code expande alcance | CLAUDE.md §4, un prompt por fase, `docs/ideas.md` como válvula. |
| Llama 8B inconsistente | Su veredicto es informativo; la decisión es de Ryu y del operador. Se mide concordancia, no se depende de ella. |
