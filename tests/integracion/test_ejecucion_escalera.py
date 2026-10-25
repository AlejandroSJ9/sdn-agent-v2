"""Fase 9b + D1: ejecución de la escalera contra el mock de Ryu (A6, A7, A8). El agente solo recomienda: cada
acción se ejecuta al APROBARLA, con verificación posterior, reversión automática a DROP total y estado desconocido.
También el motivo de capacidad, las acciones del operador y el laboratorio simulado."""

import json

import httpx
import mock_ryu
import pytest
from conftest import SALIDA_LLM_V3, TOKEN_AGENTE, transporte_asgi
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Auditoria, Notificacion, Recomendacion
from app.integrations.llm import RespuestaLLM
from app.integrations.ryu import ClienteRyu
from app.services import acciones, operador, pipeline, politica


class RyuDePrueba:
    """Mock de Ryu en memoria con las rutas pedidas anotadas."""

    def __init__(self, monkeypatch, tope: float = 3.0, **opciones) -> None:
        opciones = {"cuarentena": True, "limitar_tasa": True, "cuarentena_vlan": True, **opciones}
        app_mock = mock_ryu.crear_app(TOKEN_AGENTE, **opciones)
        interno = transporte_asgi(app_mock)
        self.bloqueados, self.rutas = app_mock.state.bloqueados, []

        def manejar(peticion: httpx.Request) -> httpx.Response:
            self.rutas.append(f"{peticion.method} {peticion.url.path}")
            return interno.handle_request(peticion)

        cliente = ClienteRyu("http://ryu.test", TOKEN_AGENTE, transporte=httpx.MockTransport(manejar), tope=tope)
        monkeypatch.setattr(operador, "obtener_cliente_ryu", lambda: cliente)

    def modo(self, ip="10.0.0.3") -> str:
        return self.bloqueados[ip]["modo"]

    def acciones_de_red(self) -> list[str]:
        return [r for r in self.rutas if r.startswith("POST")]


@pytest.fixture
def ryu(monkeypatch):
    return lambda **opciones: RyuDePrueba(monkeypatch, **opciones)


@pytest.fixture
def llm(bd_temporal, monkeypatch):
    monkeypatch.setenv("LLM_HABILITADO", "true")
    monkeypatch.setenv("OPENROUTER_API_KEY", "clave-ficticia")
    get_settings.cache_clear()

    def instalar(**cambios) -> None:
        texto = json.dumps({**SALIDA_LLM_V3, **cambios})

        class LLMFalso:
            def completar(self, system, usuario):
                return RespuestaLLM(texto, tokens_entrada=1, tokens_salida=1, latencia_ms=1, peticiones=1)

        monkeypatch.setattr(pipeline, "obtener_cliente_llm", LLMFalso)

    return instalar


def _alerta(cliente, cabecera, payload, host="10.0.0.3", puntaje=None) -> dict:
    cuerpo = {**payload, "host_origen": host}
    if puntaje is not None:
        cuerpo |= {"puntaje_total": puntaje, "puntaje_maximo": 135}
    incidente_id = cliente.post("/api/alertas", json=cuerpo, headers=cabecera).json()["incidente_id"]
    return cliente.get(f"/api/incidentes/{incidente_id}", headers={"X-Operador-Token": get_settings().OPERADOR_TOKEN}).json()


def _auditados(motor, evento) -> list[dict]:
    with Session(motor) as sesion:
        return [a.datos for a in sesion.scalars(select(Auditoria).where(Auditoria.evento == evento))]


def _recomendaciones(motor) -> list[Recomendacion]:
    with Session(motor) as sesion:
        return list(sesion.scalars(select(Recomendacion).order_by(Recomendacion.id)))


def _aprobar(cliente, motor, cabecera_operador) -> dict:
    """Aprueba la única recomendación pendiente (la de la última alerta)."""
    (pendiente,) = [r for r in _recomendaciones(motor) if r.estado == "pendiente"]
    return cliente.post(f"/api/recomendaciones/{pendiente.id}/aprobar", headers=cabecera_operador).json()


def _correo(motor) -> str:
    with Session(motor) as sesion:
        return sesion.scalars(select(Notificacion).order_by(Notificacion.id.desc())).first().payload["mensaje"]


# ---- Por escalón: queda como recomendación y aprobarla la ejecuta ----

# Fase D4: "texto" es el bloque QUÉ RECOMENDAMOS del correo en lenguaje simple (versión corta, armada por el código)
@pytest.mark.parametrize(("salida", "puntaje", "accion", "modo", "texto"), [
    ({"veredicto": "indeterminado"}, 60, "mantener_drop", "drop_total",
     "Mantener el equipo aislado: no está claro si es un ataque. El equipo sigue aislado hasta que un administrador decida."),
    ({"veredicto": "falso_positivo"}, 60, "limitar_tasa", "limitado",
     "Devolverle la red a velocidad reducida: parece una falsa alarma. Confianza media. Solo se aplica si un administrador"),
    ({"veredicto": "confirmado"}, None, "cuarentena_puertos", "cuarentena", "Cuarentena de puertos: conserva lo básico de la red, sin caminos para saltar a otros equipos. Confianza media."),
    ({"veredicto": "confirmado"}, 135, "cuarentena_vlan", "vlan_cuarentena",
     "Mover el equipo a una red de cuarentena separada. Confianza alta."),
])
def test_cada_escalon_se_recomienda_y_al_aprobarlo_se_ejecuta_y_verifica(
    cliente, bd_temporal, cabecera_agente, cabecera_operador, payload_c1, llm, ryu, salida, puntaje, accion, modo, texto
):
    red = ryu()
    llm(**salida)
    i = _alerta(cliente, cabecera_agente, payload_c1, puntaje=puntaje)
    assert (i["accion_propuesta"], red.modo()) == (accion, "drop_total")
    assert red.acciones_de_red() == [] and texto in _correo(bd_temporal)  # el agente no ejecutó nada
    if accion == "mantener_drop":
        assert i["decision_politica"] == "descartar" and _recomendaciones(bd_temporal) == []
        return
    assert (i["decision_politica"], i["accion_final"], i["motivos_politica"]) == ("recomendar", "recomendada", [])
    aprobada = _aprobar(cliente, bd_temporal, cabecera_operador)
    assert (aprobada["estado"], red.modo()) == ("ejecutada", modo)
    assert red.acciones_de_red() == [f"POST /ryu/hosts/10.0.0.3/{ {'cuarentena_puertos': 'cuarentena'}.get(accion, accion)}"]
    (ejecutada,) = _auditados(bd_temporal, "accion_ejecutada")
    assert (ejecutada["accion"], ejecutada["origen"], ejecutada["modo"]) == (accion, "operador", modo)
    assert cliente.get(f"/api/incidentes/{i['id']}", headers=cabecera_operador).json()["accion_final"] == accion
    if accion == "limitar_tasa":
        assert red.bloqueados["10.0.0.3"]["kbps"] == 1000  # LIMITE_TASA_KBPS por defecto


# ---- Fallos de Ryu al aprobar: el host nunca queda en un estado intermedio desconocido ----

def test_fallo_transaccional_de_ryu_deja_drop_total(
    cliente, bd_temporal, cabecera_agente, cabecera_operador, payload_c1, ryu
):
    red = ryu(fallas={"cuarentena": "error"})
    _alerta(cliente, cabecera_agente, payload_c1)
    aprobada = _aprobar(cliente, bd_temporal, cabecera_operador)
    assert (aprobada["estado"], aprobada["resultado"]["error"], red.modo()) == ("fallida", "http_500", "drop_total")
    assert "POST /ryu/hosts/10.0.0.3/bloqueo_total" not in red.rutas  # verificado: no hacía falta revertir
    assert _auditados(bd_temporal, "accion_fallida")[0]["reversion"] == "no_necesaria"


@pytest.mark.parametrize(("falla", "tope", "error"), [
    ("aplica_y_error", 3.0, "http_500"),          # Ryu aplicó y aun así respondió error
    ("no_aplica", 3.0, "Ryu no aplicó"),          # 200, pero la verificación posterior no ve el modo
    ("lento", 0.3, "timeout"),                    # timeout ambiguo: se revierte sin fiarse de la lectura
])
def test_error_modo_distinto_o_timeout_revierten_a_drop_total(
    cliente, bd_temporal, cabecera_agente, cabecera_operador, payload_c1, ryu, falla, tope, error
):
    red = ryu(tope=tope, fallas={"cuarentena": falla})
    _alerta(cliente, cabecera_agente, payload_c1)
    aprobada = _aprobar(cliente, bd_temporal, cabecera_operador)
    assert (aprobada["estado"], red.modo()) == ("fallida", "drop_total")
    assert aprobada["resultado"]["error"].startswith(error)
    (fallida,) = _auditados(bd_temporal, "accion_fallida")
    assert fallida["reversion"] in ("revertida", "no_necesaria") and not fallida.get("estado_desconocido")
    if falla != "no_aplica":
        assert "POST /ryu/hosts/10.0.0.3/bloqueo_total" in red.rutas
        (revertida,) = _auditados(bd_temporal, "accion_revertida")
        assert (revertida["origen"], revertida["accion_fallida"]) == ("reversion_automatica", "cuarentena_puertos")


def test_si_la_reversion_tambien_falla_queda_estado_desconocido_sin_reintentos(
    cliente, bd_temporal, cabecera_agente, cabecera_operador, payload_c1, ryu
):
    red = ryu(fallas={"cuarentena": "aplica_y_error", "bloqueo_total": "error"})
    _alerta(cliente, cabecera_agente, payload_c1)
    aprobada = _aprobar(cliente, bd_temporal, cabecera_operador)
    assert red.modo() == "cuarentena" and red.rutas.count("POST /ryu/hosts/10.0.0.3/bloqueo_total") == 1  # un intento
    assert aprobada["estado"] == "fallida" and aprobada["resultado"]["estado_desconocido"] is True
    (desconocido,) = _auditados(bd_temporal, "accion_estado_desconocido")
    assert desconocido["accion"] == "cuarentena_puertos" and desconocido["error_reversion"] == "http_500"


def test_aprobar_sobre_un_host_que_ryu_no_tiene_bloqueado_falla_sin_tocar_la_red(
    cliente, bd_temporal, cabecera_agente, cabecera_operador, payload_c1, ryu
):
    red = ryu()
    _alerta(cliente, cabecera_agente, payload_c1, "10.0.0.9")  # no está bloqueado en el mock
    aprobada = _aprobar(cliente, bd_temporal, cabecera_operador)
    assert aprobada["estado"] == "fallida" and aprobada["resultado"]["error"] == "host no bloqueado en Ryu"
    assert red.acciones_de_red() == []


# ---- Capacidad (C7): queda como recomendación con el motivo ----

def test_sin_capacidad_a7_queda_como_recomendacion_con_el_motivo(cliente, bd_temporal, cabecera_agente, payload_c1, llm, ryu):
    red = ryu(limitar_tasa=False)
    llm(veredicto="falso_positivo")
    i = _alerta(cliente, cabecera_agente, payload_c1, puntaje=60)
    assert (i["accion_propuesta"], i["decision_politica"], i["motivos_politica"]) == (
        "limitar_tasa", "recomendar", [politica.MOTIVO_SIN_A7])
    assert red.acciones_de_red() == [] and red.modo() == "drop_total"


def test_host_que_ya_no_esta_en_drop_total_no_se_toca(bd_temporal, ryu):
    red = ryu()
    red.bloqueados["10.0.0.3"]["modo"] = "limitado"
    resultado = acciones.ejecutar("cuarentena_vlan", {"id": None, "host_origen": "10.0.0.3", "origen": "real"}, "politica")
    assert resultado == {"estado": "fallida", "error": "el host ya no está en DROP total (modo limitado)"}
    assert red.acciones_de_red() == []


# ---- Operador: aprobar y volver a DROP total ----

def test_aprobar_en_modo_recomendar_ejecuta_limitar_tasa(cliente, bd_temporal, cabecera_agente, cabecera_operador, payload_c1, llm, ryu):
    red = ryu()
    llm(veredicto="falso_positivo")
    i = _alerta(cliente, cabecera_agente, payload_c1, puntaje=60)
    (recomendacion,) = _recomendaciones(bd_temporal)
    assert (recomendacion.accion, red.modo()) == ("limitar_tasa", "drop_total")
    aprobada = cliente.post(f"/api/recomendaciones/{recomendacion.id}/aprobar", headers=cabecera_operador).json()
    assert aprobada["estado"] == "ejecutada" and red.modo() == "limitado"
    assert cliente.get(f"/api/incidentes/{i['id']}", headers=cabecera_operador).json()["accion_final"] == "limitar_tasa"


@pytest.mark.parametrize("accion", ["cuarentena_puertos", "limitar_tasa", "cuarentena_vlan"])
def test_operador_vuelve_a_drop_total_desde_cada_modo(cliente, bd_temporal, cabecera_operador, ryu, accion):
    red = ryu()
    acciones.ejecutar(accion, None, "operador", host="10.0.0.3")
    assert red.modo() != "drop_total"
    assert cliente.post("/api/hosts/10.0.0.3/bloqueo_total", headers=cabecera_operador).json()["modo"] == "drop_total"
    assert red.modo() == "drop_total" and "kbps" not in red.bloqueados["10.0.0.3"]
    assert cliente.post("/api/hosts/10.0.0.3/bloqueo_total", headers=cabecera_operador).status_code == 409  # ya lo está
    assert cliente.post("/api/hosts/10.0.0.3/limitar_tasa", headers=cabecera_operador).json()["modo"] == "limitado"  # D5a


def test_cuarentena_manual_del_operador_solo_desde_drop_total(cliente, cabecera_operador, ryu):
    red = ryu()
    acciones.ejecutar("limitar_tasa", None, "operador", host="10.0.0.3")
    assert cliente.post("/api/hosts/10.0.0.3/cuarentena", headers=cabecera_operador).status_code == 409
    assert cliente.post("/api/hosts/10.0.0.5/cuarentena", headers=cabecera_operador).json()["modo"] == "cuarentena"
    assert red.modo("10.0.0.5") == "cuarentena"


# ---- Laboratorio: siempre simulado ----

def test_laboratorio_simula_las_tres_acciones_sin_llamar_a_ryu(bd_temporal, ryu):
    red = ryu()
    incidente = {"id": None, "host_origen": "10.0.10.1", "origen": "prueba"}
    for accion, modo in (("cuarentena_puertos", "cuarentena"), ("limitar_tasa", "limitado"), ("cuarentena_vlan", "vlan_cuarentena")):
        assert acciones.ejecutar(accion, incidente, "politica") == {"estado": "simulada", "modo": modo}
    assert red.rutas == []
