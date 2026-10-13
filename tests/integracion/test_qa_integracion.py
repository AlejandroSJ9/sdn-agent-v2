"""QA previa a Ryu real: Ryu sin A5 (F1, F3 y rutas de Ryu), ruta antigua /alert (F2) y tope del cuerpo (F5)."""

import json

import pytest
from conftest import TOKEN_AGENTE, TOKEN_OPERADOR, transporte_asgi
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api import routes_alertas
from app.config import get_settings
from app.db.models import Alerta, Auditoria, Incidente
from app.integrations.ryu import ClienteRyu
from app.services import operador

CUERPO = {"confirmacion": "10.0.0.3", "motivo": "Falso positivo verificado", "clasificacion": "falso_positivo"}
SIN_A5 = "Ryu no expone la API REST (A5)"


@pytest.fixture
def ryu_sin_a5(monkeypatch):
    """Ryu que responde, pero sin ninguna ruta /ryu/* (todo 404)."""
    cliente_ryu = ClienteRyu("http://ryu.test", TOKEN_AGENTE, transporte=transporte_asgi(FastAPI()))
    monkeypatch.setattr(operador, "obtener_cliente_ryu", lambda: cliente_ryu)


def _fallidos(motor) -> list[dict]:
    with Session(motor) as sesion:
        return list(sesion.scalars(select(Auditoria.datos).where(Auditoria.evento == "desbloqueo_fallido")))


def _rechazadas(cliente, cabecera_operador) -> dict:
    motivos = cliente.get("/api/alertas/rechazadas", headers=cabecera_operador).json()["motivos"]
    return {(m["codigo"], m["detalle"]): m["cantidad"] for m in motivos}


def _alertas_en_bd(motor) -> int:
    with Session(motor) as sesion:
        return sesion.scalar(select(func.count()).select_from(Alerta))


# ---- F1: desbloqueo con Ryu sin A5 ----

def test_desbloqueo_sin_a5_503_incidente_abierto_y_auditado(
    cliente, bd_temporal, cabecera_agente, cabecera_operador, payload_c1, ryu_sin_a5
):
    incidente_id = cliente.post("/api/alertas", json=payload_c1, headers=cabecera_agente).json()["incidente_id"]
    respuesta = cliente.post("/api/hosts/10.0.0.3/desbloquear", json=CUERPO, headers=cabecera_operador)
    assert (respuesta.status_code, respuesta.json()["detail"]) == (503, SIN_A5)
    with Session(bd_temporal) as sesion:
        assert sesion.get(Incidente, incidente_id).t_desbloqueado is None
    (fallido,) = _fallidos(bd_temporal)
    assert fallido == {"ip": "10.0.0.3", "motivo": CUERPO["motivo"], "clasificacion": "falso_positivo",
                       "codigo": 404, "error": "api_no_disponible (falta A5)"}
    assert TOKEN_AGENTE not in json.dumps(fallido) and TOKEN_OPERADOR not in json.dumps(fallido)


def test_host_realmente_no_bloqueado_sigue_404_y_se_audita(cliente, bd_temporal, cabecera_operador, ryu_mock):
    cuerpo = {**CUERPO, "confirmacion": "10.0.0.9"}
    respuesta = cliente.post("/api/hosts/10.0.0.9/desbloquear", json=cuerpo, headers=cabecera_operador)
    assert (respuesta.status_code, respuesta.json()["detail"]) == (404, "El host no está bloqueado en Ryu")
    assert [(f["ip"], f["error"]) for f in _fallidos(bd_temporal)] == [("10.0.0.9", "host_no_bloqueado")]


# ---- F3 y ampliación: rutas de Ryu sin A5 ----

def test_estado_sin_a5_explica_la_causa(cliente, cabecera_operador, ryu_sin_a5):
    estado = cliente.get("/api/ryu/estado", headers=cabecera_operador).json()
    assert estado == {"disponible": False, "error": "api_no_disponible (falta A5)"}


def test_bloqueados_sin_a5_503(cliente, cabecera_operador, ryu_sin_a5):
    respuesta = cliente.get("/api/ryu/bloqueados", headers=cabecera_operador)
    assert (respuesta.status_code, respuesta.json()["detail"]) == (503, SIN_A5)


def test_umbrales_get_sin_a5_503(cliente, cabecera_operador, ryu_sin_a5):
    respuesta = cliente.get("/api/ryu/umbrales", headers=cabecera_operador)
    assert (respuesta.status_code, respuesta.json()["detail"]) == (503, SIN_A5)


def test_umbrales_put_sin_a5_503(cliente, cabecera_operador, ryu_sin_a5):
    respuesta = cliente.put("/api/ryu/umbrales", json={"M2": 7}, headers=cabecera_operador)
    assert (respuesta.status_code, respuesta.json()["detail"]) == (503, SIN_A5)


# ---- F2: ruta antigua /alert ----

def test_ruta_antigua_alert_404_contada_sin_token_ni_cupo(cliente, bd_temporal, cabecera_operador, payload_c1):
    respuesta = cliente.post("/alert", json=payload_c1)
    assert (respuesta.status_code, respuesta.json()["detail"]) == (404, "Ruta antigua /alert: falta A1 en Ryu")
    assert _rechazadas(cliente, cabecera_operador) == {(404, "ruta antigua /alert: falta A1 en Ryu"): 1}
    assert _alertas_en_bd(bd_temporal) == 0
    assert not routes_alertas.limite_tasa._marcas  # no consume cupo de tasa


# ---- F5: tope del cuerpo (ALERTA_MAX_BYTES) ----

def _cuerpo_de(payload: dict, tamano: int) -> bytes:
    """JSON válido de exactamente `tamano` bytes, rellenando un campo desconocido."""
    base = json.dumps({**payload, "relleno": ""}).encode()
    return json.dumps({**payload, "relleno": "x" * (tamano - len(base))}).encode()


def test_cuerpo_en_el_limite_exacto_202(cliente, cabecera_agente, payload_c1):
    maximo = get_settings().ALERTA_MAX_BYTES
    cuerpo = _cuerpo_de(payload_c1, maximo)
    assert len(cuerpo) == maximo == 65536
    assert cliente.post("/api/alertas", content=cuerpo, headers=cabecera_agente).status_code == 202


def test_cuerpo_en_limite_mas_uno_413_contado_sin_bd(cliente, bd_temporal, cabecera_agente, cabecera_operador, payload_c1):
    cuerpo = _cuerpo_de(payload_c1, get_settings().ALERTA_MAX_BYTES + 1)
    respuesta = cliente.post("/api/alertas", content=cuerpo, headers=cabecera_agente)
    assert (respuesta.status_code, respuesta.json()["detail"]) == (413, "Cuerpo de la alerta mayor que 65536 bytes")
    assert _rechazadas(cliente, cabecera_operador) == {(413, "cuerpo mayor que 65536 bytes"): 1}
    assert _alertas_en_bd(bd_temporal) == 0


def test_orden_401_429_413_422(cliente, cabecera_agente, payload_c1, monkeypatch):
    grande = _cuerpo_de(payload_c1, 70000)
    assert cliente.post("/api/alertas", content=grande, headers={"X-Agent-Token": "malo"}).status_code == 401
    assert cliente.post("/api/alertas", content=b"{no es json", headers=cabecera_agente).status_code == 422
    assert cliente.post("/api/alertas", content=grande, headers=cabecera_agente).status_code == 413
    monkeypatch.setenv("RATE_LIMIT_POR_MIN", "2")  # 422 y 413 ya usaron 2 del cupo
    get_settings.cache_clear()
    assert cliente.post("/api/alertas", content=grande, headers=cabecera_agente).status_code == 429
