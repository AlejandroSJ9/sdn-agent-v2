"""Fixtures compartidas: payload de ejemplo C1, base SQLite temporal y cliente HTTP."""

import pytest
from fastapi.testclient import TestClient

from app.api import routes_alertas
from app.config import get_settings
from app.db.database import iniciar_bd
from app.main import app
from app.services import pipeline

TOKEN_AGENTE = "token-agente-de-prueba"
TOKEN_OPERADOR = "token-operador-de-prueba"


@pytest.fixture
def payload_c1() -> dict:
    """Ejemplo de C1 (docs/contratos.md). Se construye nuevo en cada prueba."""
    return {
        "timestamp": "2026-10-10T20:31:05.123Z",
        "alerta_nivel": "CRITICO",
        "host_origen": "10.0.0.3",
        "dpid_switch": "0x0000000000000001",
        "contexto_topologico": {},
        "metricas": {
            "M1_nuevos_flujos_por_30s": 42,
            "M2_destinos_unicos_por_30s": 9,
            "M3_smb_anomalo": True,
            "M4_packet_in_por_min": 120.0,
            "M5_flujos_east_west": True,
            "M5_count": 7,
        },
        "umbrales_superados": ["M1", "M2", "M3"],
        "ioc_activados": ["..."],
        "accion_ryu_previa": "OFPFlowMod DROP prioridad 100 — 10.0.0.3 aislado",
        "solicitud_al_agente": "IGNORADO por el agente",
    }


@pytest.fixture
def bd_temporal(tmp_path, monkeypatch):
    """SQLite temporal y configuración fija, para no depender de un .env local. Entrega el motor."""
    url = f"sqlite:///{tmp_path / 'prueba.db'}"
    entorno = {
        "DB_URL": url,
        "AGENT_TOKEN": TOKEN_AGENTE,
        "OPERADOR_TOKEN": TOKEN_OPERADOR,
        "DEDUP_SEGUNDOS": "60",
        "RATE_LIMIT_POR_MIN": "60",
        "LLM_HABILITADO": "false",  # el pipeline usa reglas salvo que la prueba active el LLM falso
        "LLM_CUPO_DIARIO": "1000",
        "OPENROUTER_API_KEY": "",
    }
    for clave, valor in entorno.items():
        monkeypatch.setenv(clave, valor)
    get_settings.cache_clear()
    motor = iniciar_bd(url)
    yield motor
    motor.dispose()
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _sin_llm_real(monkeypatch):
    """Ninguna prueba puede crear el cliente real de OpenRouter (CLAUDE.md §4.10)."""

    def _prohibido():
        pytest.fail("Una prueba intentó crear el cliente LLM real")

    monkeypatch.setattr(pipeline, "obtener_cliente_llm", _prohibido)


@pytest.fixture
def cliente(bd_temporal):
    routes_alertas.limite_tasa.reiniciar()
    with TestClient(app) as cliente:
        yield cliente


@pytest.fixture
def cabecera_agente() -> dict:
    return {"X-Agent-Token": TOKEN_AGENTE}


@pytest.fixture
def cabecera_operador() -> dict:
    return {"X-Operador-Token": TOKEN_OPERADOR}
