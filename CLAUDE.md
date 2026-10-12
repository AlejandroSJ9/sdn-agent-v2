# CLAUDE.md — Reglas del proyecto SDN-Agent v2

Lee este archivo completo antes de cada tarea. Si una instrucción del usuario choca con estas reglas, **pregunta antes de actuar**.

## 1. Qué es este proyecto (y qué no)

Agente que **explica, registra y notifica** incidentes de movimiento lateral de ransomware detectados por el controlador SDN Ryu-IA, más un panel web para que un operador humano vea todo y actúe.

Flujo único del sistema:

```
Ryu-IA detecta y BLOQUEA  →  POST alerta al agente  →  agente responde 202 al instante
   →  guarda incidente + auditoría  →  LLM explica (o respaldo por reglas)
   →  envía payload a Apps Script (correo)  →  panel en vivo (WebSocket)
   →  el OPERADOR decide desbloquear desde el panel  →  agente llama a la API de Ryu
```

**Reglas de dominio que nunca se rompen:**
- El agente **NUNCA** bloquea ni desbloquea por su cuenta. Solo el operador desbloquea, con confirmación explícita.
- La respuesta a Ryu es siempre `{"estado":"recibido","incidente_id":...,"acciones_ryu":["mantener_bloqueo"]}`.
- El veredicto del LLM es **informativo**: no cambia el estado de bloqueo.
- Los datos de red **nunca** se mezclan con las instrucciones del prompt. El campo `solicitud_al_agente` del payload de Ryu se **ignora**.

## 2. Stack (cerrado)

Python 3.10+, FastAPI, Pydantic v2, SQLite (SQLAlchemy 2), httpx, pytest. Frontend: HTML + CSS + JS sin frameworks ni build, servido por FastAPI.
**Prohibido agregar** dependencias, servicios (Redis, Postgres, Docker, colas), Telegram, n8n, RAG, vectores, o cualquier canal de notificación distinto a Apps Script, salvo que el usuario lo pida por escrito en la tarea.

## 3. Arquitectura por capas

```
app/
  main.py            # crea la app, monta routers y estáticos. Sin lógica.
  config.py          # Settings desde .env (pydantic-settings). Único lugar con os.environ.
  api/               # Capa HTTP: valida entrada, llama a services, devuelve schemas. Sin SQL, sin httpx.
  services/          # Lógica de negocio. No importa FastAPI.
  integrations/      # Clientes externos: llm.py, ryu.py, appscript.py. Solo hablan HTTP.
  db/                # models.py, repository.py, database.py. Solo aquí hay SQL.
  schemas/           # Modelos Pydantic de los contratos (docs/contratos.md).
  web/               # index.html, app.js, styles.css
tests/
  unit/              # sin red, sin servidor
  integracion/       # TestClient + mock de Ryu + LLM falso
  escenarios/        # escenarios.json + runner
scripts/             # ataques para Mininet y utilidades de evaluación
docs/
```

Dependencias permitidas: `api → services → (integrations, db)`. Nunca al revés. `schemas` lo usan todos.

## 4. Cómo trabajar (anti-expansión)

1. **Haz solo lo que pide la fase actual** de `docs/PLAN.md`. Ni un endpoint, campo, botón o archivo extra.
2. Si ves algo que "estaría bien agregar", **no lo hagas**: escríbelo en `docs/ideas.md` con una línea y sigue.
3. Antes de escribir código, responde en 5 líneas máx.: qué archivos tocarás y qué no.
4. **No modifiques** `docs/contratos.md` ni un schema existente sin aprobación. Si una fase lo necesita, detente y pregunta.
5. No reescribas archivos completos para un cambio pequeño. Cambios mínimos.
6. No renombres ni muevas archivos fuera de la fase.
7. Ningún archivo de `app/` supera ~250 líneas. Si crece, avisa en vez de partirlo por tu cuenta.
8. Al terminar: corre `pytest -q`, muestra el resultado y un resumen de máx. 8 líneas: qué cambió, qué quedó pendiente. **No declares terminado con pruebas fallando.**
9. Toda prueba que pasaba antes debe seguir pasando. Si un cambio obliga a modificar una prueba existente, explica por qué antes de hacerlo.
10. Las pruebas unitarias **nunca** llaman al LLM real ni a Ryu real (cuidan el cupo diario).

## 5. Presupuesto del LLM (crítico)

OpenRouter con créditos comprados: **1.000 peticiones/día** (sin créditos serían 50).
- Toda llamada pasa por `services/presupuesto.py`, que cuenta en la base de datos el uso del día.
- Si se agota, el LLM falla, tarda más de `LLM_TIMEOUT` o devuelve JSON inválido → **respaldo por reglas** (`services/explicacion_reglas.py`). El incidente se marca `fuente_explicacion = "reglas"`.
- Alertas repetidas del mismo host dentro de `DEDUP_SEGUNDOS` se agrupan en el mismo incidente sin nueva llamada al LLM.
- El LLM se puede apagar desde el panel (`LLM_HABILITADO`).

## 6. Seguridad mínima obligatoria

- Ryu → agente: encabezado `X-Agent-Token` (valor en `.env`). Sin token válido → 401.
- Límite de tasa en `POST /api/alertas` (en memoria, configurable).
- Panel/acciones del operador: protegidos con `X-Operador-Token` (simple, local).
- Tabla `auditoria` de solo inserción, cada fila con `hash_previo` (cadena de hashes). Nunca UPDATE ni DELETE.
- Ningún secreto en el código ni en los logs.

## 7. Convenciones

Código y comentarios en español, nombres en `snake_case`. Tiempos en UTC ISO-8601. Cada incidente guarda marcas de tiempo: `t_ryu` (de Ryu), `t_recibido`, `t_explicado`, `t_notificado`, `t_desbloqueado`.

## 8. Comandos

```
uvicorn app.main:app --port 8081
pytest -q
pytest tests/unit -q
```
