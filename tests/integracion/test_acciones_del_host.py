"""Fase D5a: GET /api/hosts/{ip}/acciones, limitar tasa y VLAN manuales, coincidencia con una recomendación
pendiente y capacidades sin caché vieja. Todo contra el mock de Ryu en memoria (C2, C7)."""

from datetime import datetime, timedelta, timezone

import httpx
import mock_ryu
import pytest
from conftest import TOKEN_AGENTE, transporte_asgi
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db.models import Auditoria, Recomendacion
from app.integrations.ryu import ClienteRyu
from app.services import operador, recomendaciones

ORDEN = ["cuarentena_puertos", "limitar_tasa", "cuarentena_vlan", "bloqueo_total", "desbloquear"]
TODAS = {"cuarentena": True, "limitar_tasa": True, "cuarentena_vlan": True}


class Red:
    """Mock de Ryu instalado como el Ryu del agente, con las rutas pedidas anotadas."""

    def __init__(self, monkeypatch, **opciones) -> None:
        self.instalar(monkeypatch, **opciones)

    def instalar(self, monkeypatch, **opciones) -> None:
        """También sirve para "reiniciar" Ryu con otras capacidades sin reiniciar el agente (misma URL)."""
        app_mock = mock_ryu.crear_app(TOKEN_AGENTE, **{**TODAS, **opciones})
        interno = transporte_asgi(app_mock)
        self.bloqueados, self.rutas = app_mock.state.bloqueados, []

        def manejar(peticion: httpx.Request) -> httpx.Response:
            self.rutas.append(f"{peticion.method} {peticion.url.path}")
            return interno.handle_request(peticion)

        cliente = ClienteRyu("http://ryu.test", TOKEN_AGENTE, transporte=httpx.MockTransport(manejar))
        monkeypatch.setattr(operador, "obtener_cliente_ryu", lambda: cliente)

    def acciones_de_red(self) -> list[str]:
        return [r for r in self.rutas if r.startswith("POST")]


@pytest.fixture
def red(monkeypatch):
    return lambda **opciones: Red(monkeypatch, **opciones)


def _listar(cliente, cabecera, ip="10.0.0.3") -> dict:
    respuesta = cliente.get(f"/api/hosts/{ip}/acciones", headers=cabecera)
    assert respuesta.status_code == 200
    return respuesta.json()


def _disponibles(listado: dict) -> list[str]:
    return [a["accion"] for a in listado["acciones"] if a["disponible"]]


# ---- Listado ----

@pytest.mark.parametrize(("previa", "modo", "esperadas"), [
    (None, "drop_total", ["cuarentena_puertos", "limitar_tasa", "cuarentena_vlan", "desbloquear"]),
    ("cuarentena", "cuarentena", ["bloqueo_total", "desbloquear"]),
    ("limitar_tasa", "limitado", ["bloqueo_total", "desbloquear"]),
    ("cuarentena_vlan", "vlan_cuarentena", ["bloqueo_total", "desbloquear"]),
])
def test_listado_en_cada_modo(cliente, cabecera_operador, red, previa, modo, esperadas):
    red()
    if previa:
        assert cliente.post(f"/api/hosts/10.0.0.3/{previa}", headers=cabecera_operador).status_code == 200
    listado = _listar(cliente, cabecera_operador)
    assert (listado["ip"], listado["modo"], listado["capacidades"]) == ("10.0.0.3", modo, TODAS)
    assert [a["accion"] for a in listado["acciones"]] == ORDEN  # las 5, en el orden del contrato
    assert _disponibles(listado) == esperadas
    for accion in listado["acciones"]:
        assert set(accion) == {"accion", "etiqueta", "efecto", "disponible", "motivo", "recomendada",
                               "recomendacion_id", "confianza"}
        assert (accion["motivo"] is None) == accion["disponible"] and accion["etiqueta"] and accion["efecto"]
        assert (accion["recomendada"], accion["recomendacion_id"], accion["confianza"]) == (False, None, None)
    total = next(a for a in listado["acciones"] if a["accion"] == "bloqueo_total")
    assert total["disponible"] or total["motivo"] == "El host ya está en DROP total"


def test_listado_sin_capacidades_solo_desbloquear(cliente, cabecera_operador, red):
    red(cuarentena=False, limitar_tasa=False, cuarentena_vlan=False)
    listado = _listar(cliente, cabecera_operador)
    assert listado["modo"] == "drop_total" and _disponibles(listado) == ["desbloquear"]
    assert all(a["motivo"].startswith("Ryu no declara") for a in listado["acciones"] if a["accion"] != "desbloquear")


def test_host_no_bloqueado_modo_null_y_nada_disponible(cliente, cabecera_operador, red):
    red()
    listado = _listar(cliente, cabecera_operador, "10.0.0.99")
    assert listado["modo"] is None and _disponibles(listado) == []
    assert {a["motivo"] for a in listado["acciones"]} == {"El host no está bloqueado en Ryu"}


def test_listado_no_toca_la_red(cliente, cabecera_operador, red):
    ryu = red()
    _listar(cliente, cabecera_operador)
    assert ryu.acciones_de_red() == [] and ryu.bloqueados["10.0.0.3"]["modo"] == "drop_total"


def test_recomendada_marcada_solo_si_esta_pendiente_y_vigente(cliente, bd_temporal, cabecera_operador, red):
    red()
    vigente = recomendaciones.crear(None, "10.0.0.3", "limitar_tasa", "Parece legítimo.", 0.82, [])
    vencida = recomendaciones.crear(None, "10.0.0.3", "cuarentena_vlan", "Ataque claro.", 0.97, [])
    recomendaciones.crear(None, "10.0.0.5", "cuarentena_puertos", "Otro host.", 0.75, [])
    with Session(bd_temporal) as sesion, sesion.begin():
        sesion.execute(update(Recomendacion).where(Recomendacion.id == vencida["id"])
                       .values(t_expira=datetime.now(timezone.utc) - timedelta(minutes=1)))
    marcadas = [(a["accion"], a["recomendacion_id"], a["confianza"])
                for a in _listar(cliente, cabecera_operador)["acciones"] if a["recomendada"]]
    assert marcadas == [("limitar_tasa", vigente["id"], 0.82)]


# ---- Ejecución manual ----

def test_limitar_tasa_y_cuarentena_vlan_manuales(cliente, cabecera_operador, red):
    ryu = red()
    limitado = cliente.post("/api/hosts/10.0.0.3/limitar_tasa", headers=cabecera_operador).json()
    assert (limitado["estado"], limitado["modo"], limitado["kbps"]) == ("ejecutada", "limitado", 1000)
    assert cliente.post("/api/hosts/10.0.0.3/cuarentena_vlan", headers=cabecera_operador).status_code == 409
    assert cliente.post("/api/hosts/10.0.0.3/bloqueo_total", headers=cabecera_operador).json()["modo"] == "drop_total"
    vlan = cliente.post("/api/hosts/10.0.0.3/cuarentena_vlan", headers=cabecera_operador).json()
    assert (vlan["estado"], vlan["modo"], vlan["vlan"]) == ("ejecutada", "vlan_cuarentena", 999)
    assert ryu.bloqueados["10.0.0.3"]["modo"] == "vlan_cuarentena"


@pytest.mark.parametrize(("ruta", "opciones"), [
    ("limitar_tasa", {"limitar_tasa": False}),
    ("cuarentena_vlan", {"cuarentena_vlan": False}),
])
def test_409_sin_la_capacidad(cliente, cabecera_operador, red, ruta, opciones):
    ryu = red(**opciones)
    respuesta = cliente.post(f"/api/hosts/10.0.0.3/{ruta}", headers=cabecera_operador)
    assert respuesta.status_code == 409 and respuesta.json()["detail"].startswith("Ryu no declara")
    assert ryu.acciones_de_red() == []


def test_409_host_no_bloqueado(cliente, cabecera_operador, red):
    ryu = red()
    assert cliente.post("/api/hosts/10.0.0.99/limitar_tasa", headers=cabecera_operador).status_code == 409
    assert ryu.acciones_de_red() == []


def test_502_si_ryu_no_la_aplica_y_revierte(cliente, bd_temporal, cabecera_operador, monkeypatch):
    ryu = Red(monkeypatch, fallas={"cuarentena_vlan": "aplica_y_error"})
    assert cliente.post("/api/hosts/10.0.0.3/cuarentena_vlan", headers=cabecera_operador).status_code == 502
    assert ryu.bloqueados["10.0.0.3"]["modo"] == "drop_total"
    with Session(bd_temporal) as sesion:
        assert sesion.scalars(select(Auditoria).where(Auditoria.evento == "accion_revertida")).first() is not None


def test_coincide_con_recomendacion_pendiente_se_ejecuta_como_aprobacion(cliente, bd_temporal, cabecera_operador, red):
    ryu = red()
    recomendacion = recomendaciones.crear(None, "10.0.0.3", "limitar_tasa", "Parece legítimo.", 0.82, [])
    resultado = cliente.post("/api/hosts/10.0.0.3/limitar_tasa", headers=cabecera_operador).json()
    assert (resultado["estado"], resultado["modo"]) == ("ejecutada", "limitado")
    assert (resultado["recomendacion_id"], resultado["recomendacion_estado"]) == (recomendacion["id"], "ejecutada")
    assert ryu.acciones_de_red() == ["POST /ryu/hosts/10.0.0.3/limitar_tasa"]  # una sola ejecución
    with Session(bd_temporal) as sesion:
        assert sesion.get(Recomendacion, recomendacion["id"]).estado == "ejecutada"
    assert cliente.post(f"/api/recomendaciones/{recomendacion['id']}/aprobar", headers=cabecera_operador).status_code == 409
    assert ryu.acciones_de_red() == ["POST /ryu/hosts/10.0.0.3/limitar_tasa"]


def test_coincidencia_que_no_aplica_deja_la_recomendacion_pendiente(cliente, bd_temporal, cabecera_operador, red):
    red(limitar_tasa=False)
    recomendacion = recomendaciones.crear(None, "10.0.0.3", "limitar_tasa", "Parece legítimo.", 0.82, [])
    assert cliente.post("/api/hosts/10.0.0.3/limitar_tasa", headers=cabecera_operador).status_code == 409
    with Session(bd_temporal) as sesion:
        assert sesion.get(Recomendacion, recomendacion["id"]).estado == "pendiente"


def test_accion_distinta_de_la_recomendada_no_la_resuelve(cliente, bd_temporal, cabecera_operador, red):
    red()
    recomendacion = recomendaciones.crear(None, "10.0.0.3", "limitar_tasa", "Parece legítimo.", 0.82, [])
    resultado = cliente.post("/api/hosts/10.0.0.3/cuarentena", headers=cabecera_operador).json()
    assert resultado["modo"] == "cuarentena" and "recomendacion_id" not in resultado
    with Session(bd_temporal) as sesion:
        assert sesion.get(Recomendacion, recomendacion["id"]).estado == "pendiente"


# ---- Errores y caché ----

def test_422_401_y_503(cliente, cabecera_operador):
    assert cliente.get("/api/hosts/abc/acciones", headers=cabecera_operador).status_code == 422
    assert cliente.get("/api/hosts/10.0.0.3/acciones").status_code == 401
    assert cliente.post("/api/hosts/abc/limitar_tasa", headers=cabecera_operador).status_code == 422
    assert cliente.post("/api/hosts/10.0.0.3/cuarentena_vlan").status_code == 401
    caido = cliente.get("/api/hosts/10.0.0.3/acciones", headers=cabecera_operador)  # Ryu por defecto: caído
    assert caido.status_code == 503 and caido.json()["detail"].startswith("Ryu no responde")


@pytest.mark.parametrize("ruta", ["cuarentena", "bloqueo_total", "limitar_tasa", "cuarentena_vlan"])
def test_post_con_ryu_caido_503_sin_ejecutar_nada(cliente, bd_temporal, cabecera_operador, ruta):
    respuesta = cliente.post(f"/api/hosts/10.0.0.3/{ruta}", headers=cabecera_operador)  # Ryu por defecto: caído
    assert respuesta.status_code == 503 and respuesta.json()["detail"].startswith("Ryu no responde")
    with Session(bd_temporal) as sesion:
        eventos = set(sesion.scalars(select(Auditoria.evento)))
    assert not eventos & {"accion_ejecutada", "accion_fallida", "accion_revertida"}


def test_post_con_ryu_caido_no_resuelve_la_recomendacion(cliente, bd_temporal, cabecera_operador):
    recomendacion = recomendaciones.crear(None, "10.0.0.3", "limitar_tasa", "Parece legítimo.", 0.82, [])
    assert cliente.post("/api/hosts/10.0.0.3/limitar_tasa", headers=cabecera_operador).status_code == 503
    with Session(bd_temporal) as sesion:
        assert sesion.get(Recomendacion, recomendacion["id"]).estado == "pendiente"


def test_ryu_sin_a5_503(cliente, cabecera_operador, monkeypatch):
    sin_api = ClienteRyu("http://ryu.test", TOKEN_AGENTE, transporte=httpx.MockTransport(lambda p: httpx.Response(404)))
    monkeypatch.setattr(operador, "obtener_cliente_ryu", lambda: sin_api)
    assert cliente.get("/api/hosts/10.0.0.3/acciones", headers=cabecera_operador).status_code == 503


def test_capacidades_sin_cache_vieja_si_ryu_se_reinicia(cliente, cabecera_operador, monkeypatch):
    ryu = Red(monkeypatch)
    assert cliente.get("/api/ryu/estado", headers=cabecera_operador).json()["capacidades"] == TODAS
    assert len(_disponibles(_listar(cliente, cabecera_operador))) == 4
    ryu.instalar(monkeypatch, cuarentena=False, limitar_tasa=False, cuarentena_vlan=False)  # misma URL, sin A6-A8
    listado = _listar(cliente, cabecera_operador)
    assert listado["capacidades"] == dict.fromkeys(TODAS, False) and _disponibles(listado) == ["desbloquear"]
    assert cliente.post("/api/hosts/10.0.0.3/cuarentena", headers=cabecera_operador).status_code == 409
    ryu.instalar(monkeypatch)
    assert cliente.get("/api/ryu/estado", headers=cabecera_operador).json()["capacidades"] == TODAS
