"""WebSocket /ws/eventos: autenticación por primer mensaje, eventos C5 en vivo y robustez del pipeline."""

import httpx
import pytest
from sqlalchemy.orm import Session
from starlette.websockets import WebSocketDisconnect

from app.api import websocket
from app.config import get_settings
from app.db.models import Incidente
from app.services import eventos, notificacion
from conftest import TOKEN_OPERADOR


@pytest.fixture(autouse=True)
def espera_corta(monkeypatch):
    monkeypatch.setattr(websocket, "ESPERA_TOKEN", 0.2)


@pytest.fixture
def apps_script_ok(bd_temporal, monkeypatch):
    """Con la URL vacía la notificación queda "omitida" (sin evento); aquí se envía de verdad a un falso."""
    monkeypatch.setenv("APPSCRIPT_URL", "https://script.google.com/macros/s/FALSO/exec")
    get_settings.cache_clear()
    transporte = httpx.MockTransport(lambda peticion: httpx.Response(200, json={"ok": True}))
    monkeypatch.setattr(notificacion, "transporte_appscript", lambda: transporte)


def _autenticar(ws) -> None:
    ws.send_json({"token": TOKEN_OPERADOR})
    assert ws.receive_json() == {"control": "listo"}


def _alerta(cliente, cabecera, payload) -> int:
    respuesta = cliente.post("/api/alertas", json=payload, headers=cabecera)
    assert respuesta.status_code == 202
    return respuesta.json()["incidente_id"]


@pytest.mark.parametrize("primer_mensaje", [None, {"token": "incorrecto"}, {"otra": 1}, "no es json"])
def test_sin_token_valido_cierra_con_1008(cliente, primer_mensaje):
    with cliente.websocket_connect("/ws/eventos") as ws:
        if isinstance(primer_mensaje, dict):
            ws.send_json(primer_mensaje)
        elif isinstance(primer_mensaje, str):
            ws.send_text(primer_mensaje)
        with pytest.raises(WebSocketDisconnect) as cierre:
            ws.receive_json()
    assert cierre.value.code == 1008


def test_token_por_url_no_sirve(cliente):
    with cliente.websocket_connect(f"/ws/eventos?token={TOKEN_OPERADOR}") as ws:
        with pytest.raises(WebSocketDisconnect) as cierre:
            ws.receive_json()
    assert cierre.value.code == 1008


def test_ping_pong(cliente):
    with cliente.websocket_connect("/ws/eventos") as ws:
        _autenticar(ws)
        ws.send_json({"control": "ping"})
        assert ws.receive_json() == {"control": "pong"}


def test_alerta_produce_eventos_en_orden(cliente, cabecera_agente, payload_c1, apps_script_ok):
    with cliente.websocket_connect("/ws/eventos") as ws:
        _autenticar(ws)
        incidente_id = _alerta(cliente, cabecera_agente, payload_c1)
        recibidos = [ws.receive_json() for _ in range(3)]

    assert [e["tipo"] for e in recibidos] == ["alerta_recibida", "explicacion_lista", "notificacion_enviada"]
    assert recibidos[0]["datos"] == {
        "incidente_id": incidente_id, "host_origen": "10.0.0.3", "agrupada": False, "contador_alertas": 1,
    }
    assert recibidos[1]["datos"]["fuente"] == "reglas" and recibidos[1]["datos"]["veredicto"] == "confirmado"
    assert all(set(e) == {"tipo", "datos", "ts"} and e["ts"].endswith("Z") for e in recibidos)


def test_alerta_agrupada_emite_agrupada_true(cliente, cabecera_agente, payload_c1):
    _alerta(cliente, cabecera_agente, payload_c1)
    with cliente.websocket_connect("/ws/eventos") as ws:
        _autenticar(ws)
        _alerta(cliente, cabecera_agente, payload_c1)
        evento = ws.receive_json()
    assert evento["tipo"] == "alerta_recibida"
    assert (evento["datos"]["agrupada"], evento["datos"]["contador_alertas"]) == (True, 2)


def test_dos_clientes_reciben_el_mismo_evento(cliente, cabecera_agente, payload_c1):
    with cliente.websocket_connect("/ws/eventos") as uno, cliente.websocket_connect("/ws/eventos") as dos:
        _autenticar(uno)
        _autenticar(dos)
        _alerta(cliente, cabecera_agente, payload_c1)
        assert uno.receive_json() == dos.receive_json()


def test_cliente_desconectado_no_rompe_el_pipeline(cliente, bd_temporal, cabecera_agente, payload_c1):
    with cliente.websocket_connect("/ws/eventos") as ws:
        _autenticar(ws)
    with cliente.websocket_connect("/ws/eventos") as vivo:
        _autenticar(vivo)
        incidente_id = _alerta(cliente, cabecera_agente, payload_c1)
        assert [vivo.receive_json()["tipo"] for _ in range(2)] == ["alerta_recibida", "explicacion_lista"]
    with Session(bd_temporal) as sesion:
        assert sesion.get(Incidente, incidente_id).fuente_explicacion == "reglas"


def test_solo_se_publican_tipos_c5(cliente):
    with cliente.websocket_connect("/ws/eventos") as ws:
        _autenticar(ws)
        eventos.emitir("inventado", {"x": 1})
        eventos.emitir("config_cambiada", {"clave": "DEDUP_SEGUNDOS", "anterior": 60, "nuevo": 30})
        assert ws.receive_json()["tipo"] == "config_cambiada"


def test_sin_clientes_emitir_no_hace_nada():
    eventos.emitir("config_cambiada", {"clave": "x"})  # no debe fallar ni bloquear
