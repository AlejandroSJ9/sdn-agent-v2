# SDN-Agent v2

Agente que explica, audita y notifica los bloqueos de Ryu-IA, con panel de control para el operador.

- Reglas para Claude Code: `CLAUDE.md`
- Plan por fases y prompts: `docs/PLAN.md`
- Contratos: `docs/contratos.md`
- Cambios pedidos a Ryu-IA: `docs/ajustes.md`

Empezar: abrir Claude Code aquí y pegar el prompt de la Fase 0.

## Instalación

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.lock   # versiones exactas, incluidas las transitivas
cp .env.example .env                          # completar AGENT_TOKEN, OPERADOR_TOKEN y AUDITORIA_CLAVE (obligatorios)
.venv/bin/uvicorn app.main:app --port 8081
```

`requirements.txt` lista solo las dependencias directas; `requirements.lock` (generado con `pip freeze`) es el que se instala.

