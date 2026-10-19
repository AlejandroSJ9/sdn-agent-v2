"""Alertas rechazadas: contador en memoria por motivo, sin tocar la BD, y modo/capacidades de Ryu en el panel."""

import json

import mock_ryu
from conftest import TOKEN_AGENTE, transporte_asgi
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Alerta, Auditoria
from app.integrations.ryu import ClienteRyu
from app.services import operador


def _rechazadas(cliente, cabecera_operador) -> dict:
    respuesta = cliente.get("/api/alertas/rechazadas", headers=cabecera_operador)
    assert respuesta.status_code == 200
    return respuesta.json()


def test_el_endpoint_exige_token_de_operador(cliente, cabecera_agente):
    assert cliente.get("/api/alertas/rechazadas").status_code == 401
    assert cliente.get("/api/alertas/rechazadas", headers=cabecera_agente).status_code == 401


def test_sin_rechazos_total_cero(cliente, cabecera_operador):
    assert _rechazadas(cliente, cabecera_operador) == {"total": 0, "motivos": []}


def test_cuenta_por_motivo_sin_escribir_en_la_bd(cliente, bd_temporal, cabecera_agente, cabecera_operador, payload_c1):
    cliente.post("/api/alertas", json=payload_c1, headers={"X-Agent-Token": "malo"})
    cliente.post("/api/alertas", json=payload_c1)
    sin_m1 = json.loads(json.dumps(payload_c1))
    del sin_m1["metricas"]["M1_nuevos_flujos_por_30s"]
    assert cliente.post("/api/alertas", json=sin_m1, headers=cabecera_agente).status_code == 422

    r = _rechazadas(cliente, cabecera_operador)
    motivos = {(m["codigo"], m["detalle"]): m for m in r["motivos"]}
    assert r["total"] == 3
    assert motivos[(401, "token de agente inválido")]["cantidad"] == 2
    assert motivos[(422, "metricas.M1_nuevos_flujos_por_30s: campo obligatorio ausente")]["cantidad"] == 1
    assert all(m["ultima"] for m in r["motivos"])
    with Session(bd_temporal) as sesion:
        assert sesion.scalar(select(func.count()).select_from(Alerta)) == 0
        assert sesion.scalar(select(func.count()).select_from(Auditoria)) == 0


def test_429_se_cuenta(cliente, cabecera_agente, cabecera_operador, payload_c1, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_POR_MIN", "1")
    from app.config import get_settings

    get_settings.cache_clear()
    cliente.post("/api/alertas", json=payload_c1, headers=cabecera_agente)
    assert cliente.post("/api/alertas", json=payload_c1, headers=cabecera_agente).status_code == 429
    assert _rechazadas(cliente, cabecera_operador)["motivos"][0]["codigo"] == 429


def test_nan_y_json_roto_son_422_explicados(cliente, cabecera_agente, cabecera_operador, payload_c1):
    texto = json.dumps(payload_c1).replace('"M5_count": 7', '"M5_count": NaN')
    respuesta = cliente.post("/api/alertas", content=texto, headers={**cabecera_agente, "Content-Type": "application/json"})
    assert respuesta.status_code == 422
    assert respuesta.json()["detail"][0] == {"campo": "metricas.M5_count", "problema": "no puede ser NaN ni infinito",
                                             "valor": "nan"}
    assert cliente.post("/api/alertas", content="{roto", headers=cabecera_agente).status_code == 422
    detalles = {m["detalle"] for m in _rechazadas(cliente, cabecera_operador)["motivos"]}
    assert detalles == {"metricas.M5_count: no puede ser NaN ni infinito", "(cuerpo): no es JSON válido"}


def _ryu_con_a6(monkeypatch, cuarentena: bool) -> None:
    app_mock = mock_ryu.crear_app(TOKEN_AGENTE, cuarentena=cuarentena, limitar_tasa=False, cuarentena_vlan=False)
    cliente_ryu = ClienteRyu("http://ryu.test", TOKEN_AGENTE, transporte=transporte_asgi(app_mock))
    monkeypatch.setattr(operador, "obtener_cliente_ryu", lambda: cliente_ryu)


def test_panel_ve_modo_y_capacidad_solo_con_a6(cliente, cabecera_operador, monkeypatch):
    _ryu_con_a6(monkeypatch, cuarentena=True)
    assert cliente.get("/api/ryu/estado", headers=cabecera_operador).json()["capacidades"] == {"cuarentena": True, "limitar_tasa": False, "cuarentena_vlan": False}
    assert all(h["modo"] == "drop_total" for h in cliente.get("/api/ryu/bloqueados", headers=cabecera_operador).json())


def test_panel_sin_a6_no_ve_modo(cliente, cabecera_operador, monkeypatch):
    _ryu_con_a6(monkeypatch, cuarentena=False)
    assert cliente.get("/api/ryu/estado", headers=cabecera_operador).json()["capacidades"] == {"cuarentena": False, "limitar_tasa": False, "cuarentena_vlan": False}
    assert all("modo" not in h for h in cliente.get("/api/ryu/bloqueados", headers=cabecera_operador).json())
