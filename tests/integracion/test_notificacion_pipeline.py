"""Notificación al final del pipeline y endpoints de prueba/reenvío, con un Apps Script FALSO (sin red)."""

import json

import httpx
import pytest
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Incidente
from app.services import notificacion


class AppsScriptFalso:
    def __init__(self) -> None:
        self.payloads: list[dict] = []
        self.respuesta = httpx.Response(200, json={"ok": True})

    def manejar(self, peticion: httpx.Request) -> httpx.Response:
        self.payloads.append(json.loads(peticion.content))
        return self.respuesta


@pytest.fixture
def apps_script(bd_temporal, monkeypatch) -> AppsScriptFalso:
    monkeypatch.setenv("APPSCRIPT_URL", "https://script.google.com/macros/s/FALSO/exec")
    get_settings.cache_clear()
    falso = AppsScriptFalso()
    monkeypatch.setattr(notificacion, "transporte_appscript", lambda: httpx.MockTransport(falso.manejar))
    return falso


def _alerta(cliente, cabecera, payload) -> int:
    respuesta = cliente.post("/api/alertas", json=payload, headers=cabecera)
    assert respuesta.status_code == 202
    return respuesta.json()["incidente_id"]


def _incidente(motor, incidente_id: int) -> Incidente:
    with Session(motor) as sesion:
        return sesion.get(Incidente, incidente_id)


def test_alerta_nueva_genera_un_envio_y_la_agrupada_no(
    cliente, bd_temporal, cabecera_agente, cabecera_operador, payload_c1, apps_script
):
    incidente_id = _alerta(cliente, cabecera_agente, payload_c1)
    assert _alerta(cliente, cabecera_agente, payload_c1) == incidente_id  # agrupada

    assert len(apps_script.payloads) == 1
    enviado = apps_script.payloads[0]
    assert (enviado["tipo"], enviado["incidente_id"], enviado["host"]) == ("incidente", incidente_id, "10.0.0.3")
    assert enviado["asunto"] == "[SDN] Host 10.0.0.3 aislado por posible movimiento lateral"
    assert _incidente(bd_temporal, incidente_id).t_notificado is not None
    detalle = cliente.get(f"/api/incidentes/{incidente_id}", headers=cabecera_operador).json()
    assert detalle["ultima_notificacion"]["estado"] == "enviada"
    assert detalle["ultima_notificacion"]["intentos"] == 1


def test_notificacion_fallida_conserva_la_explicacion(
    cliente, bd_temporal, cabecera_agente, cabecera_operador, payload_c1, apps_script
):
    apps_script.respuesta = httpx.Response(500)
    incidente_id = _alerta(cliente, cabecera_agente, payload_c1)

    incidente = _incidente(bd_temporal, incidente_id)
    assert incidente.fuente_explicacion == "reglas" and incidente.explicacion
    assert incidente.t_explicado is not None
    assert incidente.t_notificado is None
    detalle = cliente.get(f"/api/incidentes/{incidente_id}", headers=cabecera_operador).json()
    assert (detalle["ultima_notificacion"]["estado"], detalle["ultima_notificacion"]["intentos"]) == ("fallida", 4)


def test_sin_url_queda_omitida(cliente, cabecera_agente, cabecera_operador, payload_c1):
    incidente_id = _alerta(cliente, cabecera_agente, payload_c1)
    detalle = cliente.get(f"/api/incidentes/{incidente_id}", headers=cabecera_operador).json()
    assert detalle["ultima_notificacion"]["estado"] == "omitida"
    assert detalle["t_notificado"] is None


def test_endpoint_de_prueba(cliente, cabecera_operador, apps_script):
    respuesta = cliente.post("/api/notificaciones/prueba", headers=cabecera_operador)

    assert respuesta.status_code == 200
    assert respuesta.json()["estado"] == "enviada"
    assert apps_script.payloads[0]["tipo"] == "prueba"
    assert apps_script.payloads[0]["asunto"] == "[SDN] Prueba de notificación"


def test_reenvio_no_altera_el_primer_t_notificado(
    cliente, bd_temporal, cabecera_agente, cabecera_operador, payload_c1, apps_script
):
    incidente_id = _alerta(cliente, cabecera_agente, payload_c1)
    primer_t_notificado = _incidente(bd_temporal, incidente_id).t_notificado

    respuesta = cliente.post(f"/api/incidentes/{incidente_id}/reenviar", headers=cabecera_operador)
    assert respuesta.status_code == 200
    assert respuesta.json()["estado"] == "enviada"
    assert len(apps_script.payloads) == 2
    assert _incidente(bd_temporal, incidente_id).t_notificado == primer_t_notificado
    assert cliente.post("/api/incidentes/999/reenviar", headers=cabecera_operador).status_code == 404


@pytest.mark.parametrize("ruta", ["/api/notificaciones/prueba", "/api/incidentes/1/reenviar"])
def test_endpoints_exigen_token_de_operador(cliente, ruta):
    assert cliente.post(ruta).status_code == 401
    assert cliente.post(ruta, headers={"X-Operador-Token": "incorrecto"}).status_code == 401


def test_auditoria_integra_con_notificaciones(cliente, cabecera_agente, cabecera_operador, payload_c1, apps_script):
    _alerta(cliente, cabecera_agente, payload_c1)
    apps_script.respuesta = httpx.Response(500)
    cliente.post("/api/notificaciones/prueba", headers=cabecera_operador)

    verificacion = cliente.get("/api/auditoria/verificar", headers=cabecera_operador).json()
    # alerta_recibida + explicacion_generada + notificacion_enviada + notificacion_fallida
    assert verificacion == {"integra": True, "filas": 4, "primera_rota": None}


def test_usa_appscript_timeout_configurado(cliente, cabecera_operador, apps_script, monkeypatch):
    capturado: dict = {}
    original = notificacion.appscript.enviar

    def espia(payload, url, tope_por_intento, **kwargs):
        capturado["tope"] = tope_por_intento
        return original(payload, url, tope_por_intento, **kwargs)

    monkeypatch.setenv("APPSCRIPT_TIMEOUT", "45")
    get_settings.cache_clear()
    monkeypatch.setattr(notificacion.appscript, "enviar", espia)

    assert cliente.post("/api/notificaciones/prueba", headers=cabecera_operador).json()["estado"] == "enviada"
    assert capturado["tope"] == 45
