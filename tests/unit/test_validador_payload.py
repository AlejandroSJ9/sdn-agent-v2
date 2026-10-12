"""scripts/validar_payload_ryu.py: mismo esquema que el agente, funciona sin el agente y sin .env."""

import json
import os
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
SCRIPT = RAIZ / "scripts" / "validar_payload_ryu.py"
CONTRATO = RAIZ / "tests" / "contrato" / "alerta_ryu_real.json"


def _ejecutar(tmp_path, *argumentos, entrada: str | None = None) -> subprocess.CompletedProcess:
    """Desde una carpeta vacía y sin los secretos del agente: no puede depender del .env."""
    entorno = {k: v for k, v in os.environ.items() if k not in ("AGENT_TOKEN", "OPERADOR_TOKEN", "AUDITORIA_CLAVE")}
    return subprocess.run([sys.executable, str(SCRIPT), *argumentos], input=entrada, capture_output=True, text=True,
                          cwd=tmp_path, env=entorno, timeout=30)


def test_la_alerta_de_contrato_es_compatible(tmp_path):
    resultado = _ejecutar(tmp_path, str(CONTRATO))
    assert resultado.returncode == 0 and resultado.stdout.strip() == "COMPATIBLE", resultado.stderr


def test_lee_de_stdin(tmp_path):
    resultado = _ejecutar(tmp_path, entrada=CONTRATO.read_text(encoding="utf-8"))
    assert resultado.stdout.strip() == "COMPATIBLE"


def test_campo_renombrado_explicado_con_claridad(tmp_path):
    datos = json.loads(CONTRATO.read_text(encoding="utf-8"))
    datos["metricas"]["KPI_01"] = datos["metricas"].pop("M1_nuevos_flujos_por_30s")
    resultado = _ejecutar(tmp_path, entrada=json.dumps(datos))
    assert resultado.returncode == 1
    assert "campo metricas.M1_nuevos_flujos_por_30s: campo obligatorio ausente" in resultado.stdout
    assert "campo metricas.KPI_01: el agente no lo reconoce (¿nombre cambiado?)" in resultado.stdout


def test_valor_invalido_muestra_lo_recibido(tmp_path):
    datos = json.loads(CONTRATO.read_text(encoding="utf-8"))
    datos["metricas"]["M2_destinos_unicos_por_30s"] = 9.5
    resultado = _ejecutar(tmp_path, entrada=json.dumps(datos))
    assert "campo metricas.M2_destinos_unicos_por_30s: debe ser un número entero (sin parte decimal) (recibido: 9.5)" \
        in resultado.stdout


def test_json_roto(tmp_path):
    resultado = _ejecutar(tmp_path, entrada="{roto")
    assert resultado.returncode == 1 and "no es JSON válido" in resultado.stdout
