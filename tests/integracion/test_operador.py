"""Acciones del operador sobre Ryu (mock en memoria), con su registro en BD, auditoría y notificación."""

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Auditoria, Incidente, Notificacion
from app.integrations.ryu import ClienteRyu
from app.services import operador

CUERPO = {"confirmacion": "10.0.0.3", "motivo": "Falso positivo verificado con el responsable", "clasificacion": "falso_positivo"}


def _alerta(cliente, cabecera, payload) -> int:
    respuesta = cliente.post("/api/alertas", json=payload, headers=cabecera)
    assert respuesta.status_code == 202
    return respuesta.json()["incidente_id"]


def _eventos(motor) -> list[str]:
    with Session(motor) as sesion:
        return list(sesion.scalars(select(Auditoria.evento).order_by(Auditoria.id)))


def test_desbloqueo_exitoso(cliente, bd_temporal, cabecera_agente, cabecera_operador, payload_c1, ryu_mock):
    incidente_id = _alerta(cliente, cabecera_agente, payload_c1)

    respuesta = cliente.post("/api/hosts/10.0.0.3/desbloquear", json=CUERPO, headers=cabecera_operador)

    assert respuesta.status_code == 200
    assert respuesta.json() == {"ip": "10.0.0.3", "reglas_borradas": 1, "incidentes": [incidente_id]}
    assert "10.0.0.3" not in ryu_mock.bloqueados
    ips = [h["ip"] for h in cliente.get("/api/ryu/bloqueados", headers=cabecera_operador).json()]
    assert ips == ["10.0.0.5"]
    with Session(bd_temporal) as sesion:
        assert sesion.get(Incidente, incidente_id).t_desbloqueado is not None
        auditado = sesion.scalars(select(Auditoria).where(Auditoria.evento == "host_desbloqueado")).one()
        notificaciones = sesion.scalars(select(Notificacion).where(Notificacion.tipo == "desbloqueo")).all()
    assert auditado.datos == {"ip": "10.0.0.3", "motivo": CUERPO["motivo"], "clasificacion": "falso_positivo",
                              "incidentes": [incidente_id]}
    assert [(n.incidente_id, n.payload["asunto"]) for n in notificaciones] == [
        (incidente_id, "[SDN] Host 10.0.0.3 desbloqueado por el operador")
    ]
    assert f"Motivo del operador: {CUERPO['motivo']}" in notificaciones[0].payload["mensaje"]


def test_desbloqueo_sin_incidente_abierto_notifica_uno_general(cliente, bd_temporal, cabecera_operador, ryu_mock):
    cuerpo = {"confirmacion": "10.0.0.5", "motivo": "Equipo de pruebas", "clasificacion": "otro"}
    respuesta = cliente.post("/api/hosts/10.0.0.5/desbloquear", json=cuerpo, headers=cabecera_operador)

    assert respuesta.status_code == 200 and respuesta.json()["incidentes"] == []
    with Session(bd_temporal) as sesion:
        notificacion = sesion.scalars(select(Notificacion).where(Notificacion.tipo == "desbloqueo")).one()
    assert notificacion.incidente_id is None and notificacion.payload["host"] == "10.0.0.5"


def test_bloqueados_enriquecidos_con_el_incidente_abierto(cliente, cabecera_agente, cabecera_operador, payload_c1, ryu_mock):
    incidente_id = _alerta(cliente, cabecera_agente, payload_c1)
    lista = cliente.get("/api/ryu/bloqueados", headers=cabecera_operador).json()
    assert {h["ip"]: h["incidente_id"] for h in lista} == {"10.0.0.3": incidente_id, "10.0.0.5": None}


def test_confirmacion_distinta_400_sin_llamar_a_ryu(cliente, cabecera_operador):
    # Sin ryu_mock: el Ryu instalado por conftest hace fallar la prueba si se llama.
    cuerpo = {**CUERPO, "confirmacion": "10.0.0.4"}
    assert cliente.post("/api/hosts/10.0.0.3/desbloquear", json=cuerpo, headers=cabecera_operador).status_code == 400


@pytest.mark.parametrize("motivo", ["", "   ", "abc", "x" * 201])
def test_motivo_invalido_422(cliente, cabecera_operador, motivo):
    cuerpo = {**CUERPO, "motivo": motivo}
    assert cliente.post("/api/hosts/10.0.0.3/desbloquear", json=cuerpo, headers=cabecera_operador).status_code == 422


def test_ip_invalida_422(cliente, cabecera_operador):
    cuerpo = {**CUERPO, "confirmacion": "no-es-ip"}
    assert cliente.post("/api/hosts/no-es-ip/desbloquear", json=cuerpo, headers=cabecera_operador).status_code == 422


def test_ryu_caido_502_y_la_bd_no_cambia(cliente, bd_temporal, cabecera_agente, cabecera_operador, payload_c1, monkeypatch):
    incidente_id = _alerta(cliente, cabecera_agente, payload_c1)
    eventos_antes = _eventos(bd_temporal)

    def caido(peticion):
        raise httpx.ConnectError("conexión rechazada")

    caido_ryu = ClienteRyu("http://ryu.test", "x", transporte=httpx.MockTransport(caido))
    monkeypatch.setattr(operador, "obtener_cliente_ryu", lambda: caido_ryu)

    respuesta = cliente.post("/api/hosts/10.0.0.3/desbloquear", json=CUERPO, headers=cabecera_operador)
    assert respuesta.status_code == 502
    with Session(bd_temporal) as sesion:
        assert sesion.get(Incidente, incidente_id).t_desbloqueado is None
        assert sesion.scalars(select(Notificacion).where(Notificacion.tipo == "desbloqueo")).all() == []
    assert _eventos(bd_temporal) == eventos_antes + ["desbloqueo_fallido"]  # QA F1: el intento fallido se audita
    estado = cliente.get("/api/ryu/estado", headers=cabecera_operador)
    assert estado.status_code == 200 and estado.json()["disponible"] is False


def test_host_no_bloqueado_404(cliente, cabecera_operador, ryu_mock):
    cuerpo = {**CUERPO, "confirmacion": "10.0.0.9"}
    assert cliente.post("/api/hosts/10.0.0.9/desbloquear", json=cuerpo, headers=cabecera_operador).status_code == 404


def test_tras_desbloquear_una_alerta_nueva_abre_incidente_nuevo(
    cliente, cabecera_agente, cabecera_operador, payload_c1, ryu_mock
):
    primero = _alerta(cliente, cabecera_agente, payload_c1)
    assert cliente.post("/api/hosts/10.0.0.3/desbloquear", json=CUERPO, headers=cabecera_operador).status_code == 200

    assert _alerta(cliente, cabecera_agente, payload_c1) != primero


def test_estado_de_ryu_disponible(cliente, cabecera_operador, ryu_mock):
    estado = cliente.get("/api/ryu/estado", headers=cabecera_operador).json()
    assert estado["disponible"] is True and estado["hosts_bloqueados"] == 2


@pytest.mark.parametrize(
    ("metodo", "ruta"),
    [
        ("get", "/api/ryu/estado"),
        ("get", "/api/ryu/bloqueados"),
        ("post", "/api/hosts/10.0.0.3/desbloquear"),
        ("get", "/api/ryu/umbrales"),
        ("get", "/api/config"),
        ("put", "/api/config"),
        ("get", "/api/llm/cupo"),
    ],
)
def test_401_sin_token_de_operador(cliente, metodo, ruta):
    assert cliente.request(metodo, ruta, json={}).status_code == 401


def test_auditoria_integra_tras_el_desbloqueo(cliente, cabecera_agente, cabecera_operador, payload_c1, ryu_mock):
    _alerta(cliente, cabecera_agente, payload_c1)
    cliente.post("/api/hosts/10.0.0.3/desbloquear", json=CUERPO, headers=cabecera_operador)

    verificacion = cliente.get("/api/auditoria/verificar", headers=cabecera_operador).json()
    assert verificacion["integra"] is True
