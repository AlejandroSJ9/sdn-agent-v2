"""Fixtures compartidas: payload de ejemplo C1, base SQLite temporal y cliente HTTP."""

from types import SimpleNamespace

import httpx
import mock_ryu
import pytest
from fastapi.testclient import TestClient

from app.api import routes_alertas
from app.config import get_settings
from app.db.database import iniciar_bd
from app.integrations.ryu import ClienteRyu
from app.main import app
from app.services import notificacion, operador, pipeline

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
        "APPSCRIPT_URL": "",  # notificación "omitida" salvo que la prueba instale un Apps Script falso
        "PANEL_URL": "http://localhost:8081",
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


@pytest.fixture(autouse=True)
def _sin_appscript_real(monkeypatch):
    """Ninguna prueba puede llegar al Apps Script real ni dormir entre reintentos."""

    def _prohibido(peticion):
        pytest.fail("Una prueba intentó llamar al Apps Script real")

    monkeypatch.setattr(notificacion, "transporte_appscript", lambda: httpx.MockTransport(_prohibido))
    monkeypatch.setattr(notificacion, "esperar_reintento", lambda segundos: None)


def transporte_asgi(app_asgi) -> httpx.MockTransport:
    """Transporte SÍNCRONO hacia una app ASGI en memoria (httpx.ASGITransport solo sirve con AsyncClient)."""
    interno = TestClient(app_asgi)

    def manejar(peticion: httpx.Request) -> httpx.Response:
        respuesta = interno.request(peticion.method, str(peticion.url), headers=peticion.headers, content=peticion.content)
        return httpx.Response(respuesta.status_code, headers=respuesta.headers, content=respuesta.content)

    return httpx.MockTransport(manejar)


@pytest.fixture(autouse=True)
def _sin_ryu_real(monkeypatch):
    """Ninguna prueba puede llegar al Ryu real."""

    def _prohibido(peticion):
        pytest.fail("Una prueba intentó llamar al Ryu real")

    cliente_prohibido = ClienteRyu("http://ryu-real.invalido", "x", transporte=httpx.MockTransport(_prohibido))
    monkeypatch.setattr(operador, "obtener_cliente_ryu", lambda: cliente_prohibido)


@pytest.fixture
def ryu_mock(monkeypatch):
    """Mock de Ryu (tests/mock_ryu.py) en memoria, instalado como el Ryu de los servicios."""
    app_mock = mock_ryu.crear_app(TOKEN_AGENTE)
    cliente_ryu = ClienteRyu("http://ryu.test", TOKEN_AGENTE, transporte=transporte_asgi(app_mock))
    monkeypatch.setattr(operador, "obtener_cliente_ryu", lambda: cliente_ryu)
    return SimpleNamespace(app=app_mock, cliente=cliente_ryu, bloqueados=app_mock.state.bloqueados)


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
