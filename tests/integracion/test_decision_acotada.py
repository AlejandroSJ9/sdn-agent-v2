"""Decisión acotada (Fase 6.8): política → acción o recomendación, aprobación del operador y laboratorio simulado."""

import json
from datetime import datetime, timedelta, timezone

import httpx
import mock_ryu
import pytest
from conftest import SALIDA_LLM_V3, TOKEN_AGENTE, transporte_asgi
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import repository
from app.db.models import Auditoria, Incidente, Notificacion, Recomendacion
from app.integrations.llm import RespuestaLLM
from app.integrations.ryu import ClienteRyu
from app.services import acciones, operador, pipeline, politica


def _instalar_ryu(monkeypatch, cuarentena: bool) -> dict:
    """Mock de Ryu con o sin A6; devuelve su estado de hosts bloqueados (10.0.0.3 y 10.0.0.5)."""
    app_mock = mock_ryu.crear_app(TOKEN_AGENTE, cuarentena=cuarentena)
    cliente = ClienteRyu("http://ryu.test", TOKEN_AGENTE, transporte=transporte_asgi(app_mock))
    monkeypatch.setattr(operador, "obtener_cliente_ryu", lambda: cliente)
    return app_mock.state.bloqueados


@pytest.fixture
def ryu_a6(monkeypatch) -> dict:
    return _instalar_ryu(monkeypatch, True)


def _config(cliente, cabecera, **cambios) -> None:
    assert cliente.put("/api/config", json=cambios, headers=cabecera).status_code == 200, cambios


def _dos_falsos_positivos(host="10.0.0.3") -> None:
    """Historial real: 2 incidentes del host clasificados falso_positivo por el operador, mismos umbrales."""
    ahora = datetime.now(timezone.utc)
    for dias in (1, 2):
        t = ahora - timedelta(days=dias)
        incidente_id = repository.crear_incidente(host, None, None, t, t, {"umbrales_superados": ["M1", "M2", "M3"]})
        with repository.SesionLocal.begin() as sesion:
            sesion.execute(update(Incidente).where(Incidente.id == incidente_id).values(
                clasificacion_operador="falso_positivo", t_desbloqueado=t, t_explicado=t))


def _alerta(cliente, cabecera, payload, host="10.0.0.3") -> dict:
    respuesta = cliente.post("/api/alertas", json={**payload, "host_origen": host}, headers=cabecera)
    assert respuesta.status_code == 202
    return cliente.get(f"/api/incidentes/{respuesta.json()['incidente_id']}",
                       headers={"X-Operador-Token": get_settings().OPERADOR_TOKEN}).json()


def _recomendaciones(motor) -> list[Recomendacion]:
    with Session(motor) as sesion:
        return list(sesion.scalars(select(Recomendacion).order_by(Recomendacion.id)))


def _ultimo_correo(motor) -> str:
    with Session(motor) as sesion:
        return sesion.scalars(select(Notificacion).order_by(Notificacion.id.desc())).first().payload["mensaje"]


@pytest.fixture
def con_contexto(cliente, cabecera_operador):
    _config(cliente, cabecera_operador, CONTEXTO_HABILITADO=True)  # reglas: 2 FP verificados → pasar_a_cuarentena


# ---- Aceptación ----

def test_modo_recomendar_crea_recomendacion_y_al_aprobar_pasa_a_cuarentena(
    cliente, bd_temporal, cabecera_agente, cabecera_operador, payload_c1, ryu_a6, con_contexto
):
    _dos_falsos_positivos()
    incidente = _alerta(cliente, cabecera_agente, payload_c1)
    assert (incidente["veredicto"], incidente["accion_propuesta"], incidente["confianza"]) == ("indeterminado", "pasar_a_cuarentena", 0.6)
    assert (incidente["decision_politica"], incidente["accion_final"]) == ("recomendar", "recomendada")
    assert "Recomendación pendiente: pasar a cuarentena" in _ultimo_correo(bd_temporal)
    (recomendacion,) = _recomendaciones(bd_temporal)
    assert recomendacion.estado == "pendiente" and politica.MOTIVO_MODO in recomendacion.motivos_politica
    assert ryu_a6["10.0.0.3"]["modo"] == "drop_total"

    aprobada = cliente.post(f"/api/recomendaciones/{recomendacion.id}/aprobar", headers=cabecera_operador).json()
    assert aprobada["estado"] == "ejecutada" and aprobada["resuelta_por"] == "operador"
    assert ryu_a6["10.0.0.3"]["modo"] == "cuarentena"
    detalle = cliente.get(f"/api/incidentes/{incidente['id']}", headers=cabecera_operador).json()
    assert detalle["accion_final"] == "pasar_a_cuarentena"
    assert [(a["accion"], a["origen"], a["resultado"]) for a in detalle["acciones"]] == [("pasar_a_cuarentena", "operador", "ejecutada")]


def test_modo_autonomo_ejecuta_solo_y_confirmado_nunca_pasa_a_cuarentena(
    cliente, bd_temporal, cabecera_agente, cabecera_operador, payload_c1, ryu_a6, con_contexto
):
    _config(cliente, cabecera_operador, MODO_DECISION="autonomo_acotado", UMBRAL_CONFIANZA=0.6)
    _dos_falsos_positivos()
    incidente = _alerta(cliente, cabecera_agente, payload_c1)
    assert (incidente["decision_politica"], incidente["accion_final"]) == ("ejecutar", "pasar_a_cuarentena")
    assert ryu_a6["10.0.0.3"]["modo"] == "cuarentena" and _recomendaciones(bd_temporal) == []
    assert "Acción: pasado a cuarentena automáticamente por la política" in _ultimo_correo(bd_temporal)

    confirmado = _alerta(cliente, cabecera_agente, payload_c1, host="10.0.0.5")  # sin historial: confirmado
    assert (confirmado["veredicto"], confirmado["accion_propuesta"], confirmado["accion_final"]) == ("confirmado", "mantener", "ninguna")
    assert ryu_a6["10.0.0.5"]["modo"] == "drop_total"
    assert "Acción: se mantiene el bloqueo total" in _ultimo_correo(bd_temporal)


def test_autonomo_sin_a6_todo_queda_como_recomendacion(
    cliente, bd_temporal, cabecera_agente, cabecera_operador, payload_c1, monkeypatch, con_contexto
):
    bloqueados = _instalar_ryu(monkeypatch, False)
    _config(cliente, cabecera_operador, MODO_DECISION="autonomo_acotado", UMBRAL_CONFIANZA=0.6)
    _dos_falsos_positivos()
    incidente = _alerta(cliente, cabecera_agente, payload_c1)
    assert incidente["decision_politica"] == "recomendar" and incidente["motivos_politica"] == ["sin capacidad A6"]
    (recomendacion,) = _recomendaciones(bd_temporal)
    assert recomendacion.motivos_politica == ["sin capacidad A6"] and "modo" not in bloqueados["10.0.0.3"]


def test_solo_explicar_detiene_toda_accion_automatica(
    cliente, bd_temporal, cabecera_agente, cabecera_operador, payload_c1, ryu_a6, con_contexto
):
    _config(cliente, cabecera_operador, MODO_DECISION="autonomo_acotado", UMBRAL_CONFIANZA=0.6)
    _dos_falsos_positivos()
    _config(cliente, cabecera_operador, MODO_DECISION="solo_explicar")  # interruptor de emergencia
    incidente = _alerta(cliente, cabecera_agente, payload_c1)
    assert (incidente["decision_politica"], incidente["accion_final"]) == ("descartar", "ninguna")
    assert _recomendaciones(bd_temporal) == [] and ryu_a6["10.0.0.3"]["modo"] == "drop_total"
    with Session(bd_temporal) as sesion:
        assert sesion.scalars(select(Auditoria).where(Auditoria.evento == "accion_ejecutada")).all() == []


def test_solo_explicar_no_borra_pendientes_y_el_operador_puede_aprobarlas(
    cliente, bd_temporal, cabecera_agente, cabecera_operador, payload_c1, ryu_a6, con_contexto
):
    _dos_falsos_positivos()
    _alerta(cliente, cabecera_agente, payload_c1)
    _config(cliente, cabecera_operador, MODO_DECISION="solo_explicar")
    (recomendacion,) = _recomendaciones(bd_temporal)
    assert recomendacion.estado == "pendiente"
    assert cliente.post(f"/api/recomendaciones/{recomendacion.id}/aprobar", headers=cabecera_operador).json()["estado"] == "ejecutada"


# ---- Política: límite por minuto y fallos de ejecución ----

def test_limite_por_minuto_y_fallo_de_ryu_quedan_como_recomendacion(
    cliente, bd_temporal, cabecera_agente, cabecera_operador, payload_c1, ryu_a6, con_contexto
):
    _config(cliente, cabecera_operador, MODO_DECISION="autonomo_acotado", UMBRAL_CONFIANZA=0.6, MAX_ACCIONES_POR_MIN=1)
    _dos_falsos_positivos("10.0.0.3")
    _dos_falsos_positivos("10.0.0.5")
    _dos_falsos_positivos("10.0.0.9")  # no está bloqueado en el mock: Ryu responde 404
    assert _alerta(cliente, cabecera_agente, payload_c1, "10.0.0.3")["accion_final"] == "pasar_a_cuarentena"
    segundo = _alerta(cliente, cabecera_agente, payload_c1, "10.0.0.5")
    assert segundo["motivos_politica"] == [politica.MOTIVO_LIMITE]
    _config(cliente, cabecera_operador, MAX_ACCIONES_POR_MIN=20)
    tercero = _alerta(cliente, cabecera_agente, payload_c1, "10.0.0.9")
    assert tercero["accion_final"] == "fallida" and tercero["motivos_politica"][0].startswith("falló la ejecución")
    assert [r.estado for r in _recomendaciones(bd_temporal)] == ["pendiente", "pendiente"]


def test_accion_invalida_del_llm_se_corrige_a_mantener_y_se_audita(
    cliente, bd_temporal, cabecera_agente, payload_c1, ryu_a6, monkeypatch
):
    salida = {**SALIDA_LLM_V3, "veredicto": "indeterminado", "severidad": "media", "confianza": 0.95,
              "accion_propuesta": "escalar_a_drop", "justificacion_accion": "inválida en drop_total"}

    class LLMFalso:
        def completar(self, system, usuario):
            assert json.loads(usuario.split("\n")[1])["modo_actual"] == "drop_total"
            return RespuestaLLM(json.dumps(salida), tokens_entrada=1, tokens_salida=1, latencia_ms=1, peticiones=1)

    monkeypatch.setenv("LLM_HABILITADO", "true")
    monkeypatch.setenv("OPENROUTER_API_KEY", "clave-ficticia")
    get_settings.cache_clear()
    monkeypatch.setattr(pipeline, "obtener_cliente_llm", LLMFalso)
    incidente = _alerta(cliente, cabecera_agente, payload_c1)
    assert (incidente["accion_propuesta"], incidente["decision_politica"]) == ("mantener", "descartar")
    with Session(bd_temporal) as sesion:
        corregida = sesion.scalars(select(Auditoria).where(Auditoria.evento == "accion_propuesta_corregida")).one()
    assert corregida.datos["propuesta"] == "escalar_a_drop" and corregida.datos["corregida_a"] == "mantener"


# ---- acciones.ejecutar ----

@pytest.mark.parametrize(("cuarentena", "estado"), [(True, "ejecutada"), (False, "fallida")])
def test_acciones_ejecutar_contra_el_mock(bd_temporal, monkeypatch, cuarentena, estado):
    bloqueados = _instalar_ryu(monkeypatch, cuarentena)
    incidente = {"id": None, "host_origen": "10.0.0.3", "origen": "real"}
    assert acciones.ejecutar("pasar_a_cuarentena", incidente, "politica")["estado"] == estado
    if cuarentena:
        assert bloqueados["10.0.0.3"]["modo"] == "cuarentena"
        assert acciones.ejecutar("escalar_a_drop", incidente, "operador")["estado"] == "ejecutada"
        assert bloqueados["10.0.0.3"]["modo"] == "drop_total"
    with pytest.raises(acciones.AccionNoPermitida):
        acciones.ejecutar("desbloquear", incidente, "operador")


def test_laboratorio_simula_sin_tocar_la_red(bd_temporal, monkeypatch):
    llamadas = []
    cliente = ClienteRyu("http://ryu.test", TOKEN_AGENTE, transporte=httpx.MockTransport(lambda p: llamadas.append(p)))
    monkeypatch.setattr(operador, "obtener_cliente_ryu", lambda: cliente)
    resultado = acciones.ejecutar("pasar_a_cuarentena", {"id": None, "host_origen": "10.0.10.1", "origen": "prueba"}, "politica")
    assert resultado == {"estado": "simulada", "modo": "cuarentena"} and llamadas == []


# ---- Acciones manuales del operador (Hosts bloqueados) ----

def test_operador_pasa_a_cuarentena_y_vuelve_a_bloqueo_total(cliente, cabecera_operador, ryu_a6):
    assert cliente.post("/api/hosts/10.0.0.3/cuarentena", headers=cabecera_operador).json()["modo"] == "cuarentena"
    assert cliente.post("/api/hosts/10.0.0.3/cuarentena", headers=cabecera_operador).status_code == 409  # ya lo está
    assert cliente.post("/api/hosts/10.0.0.3/bloqueo_total", headers=cabecera_operador).json()["modo"] == "drop_total"
    assert ryu_a6["10.0.0.3"]["modo"] == "drop_total"
    assert cliente.post("/api/hosts/10.0.0.0%2F24/cuarentena", headers=cabecera_operador).status_code in (404, 422)  # nunca subredes
    assert cliente.post("/api/hosts/10.0.0.3/cuarentena").status_code == 401


def test_operador_sin_a6_409(cliente, cabecera_operador, monkeypatch):
    _instalar_ryu(monkeypatch, False)
    assert cliente.post("/api/hosts/10.0.0.3/cuarentena", headers=cabecera_operador).status_code == 409


# ---- Recomendaciones: rechazar, expirar, errores ----

def test_rechazar_exige_motivo_y_no_se_resuelve_dos_veces(
    cliente, bd_temporal, cabecera_agente, cabecera_operador, payload_c1, ryu_a6, con_contexto
):
    _dos_falsos_positivos()
    _alerta(cliente, cabecera_agente, payload_c1)
    (recomendacion,) = _recomendaciones(bd_temporal)
    ruta = f"/api/recomendaciones/{recomendacion.id}"
    for cuerpo in ({}, {"motivo": "no"}, {"motivo": "x" * 201}):
        assert cliente.post(f"{ruta}/rechazar", json=cuerpo, headers=cabecera_operador).status_code == 422
    rechazada = cliente.post(f"{ruta}/rechazar", json={"motivo": "Prefiero revisarlo en sitio"}, headers=cabecera_operador).json()
    assert (rechazada["estado"], rechazada["motivo_resolucion"]) == ("rechazada", "Prefiero revisarlo en sitio")
    assert cliente.post(f"{ruta}/aprobar", headers=cabecera_operador).status_code == 409
    assert ryu_a6["10.0.0.3"]["modo"] == "drop_total"
    assert cliente.post("/api/recomendaciones/999/aprobar", headers=cabecera_operador).status_code == 404


def test_recomendaciones_expiran_al_listar(cliente, bd_temporal, cabecera_agente, cabecera_operador, payload_c1, ryu_a6, con_contexto):
    _dos_falsos_positivos()
    _alerta(cliente, cabecera_agente, payload_c1)
    with Session(bd_temporal) as sesion, sesion.begin():
        sesion.execute(update(Recomendacion).values(t_expira=datetime.now(timezone.utc) - timedelta(seconds=1)))
    assert cliente.get("/api/recomendaciones?estado=pendiente", headers=cabecera_operador).json() == []
    (expirada,) = cliente.get("/api/recomendaciones", headers=cabecera_operador).json()
    assert (expirada["estado"], expirada["resuelta_por"]) == ("expirada", "sistema")


def test_recomendaciones_exigen_token(cliente):
    assert cliente.get("/api/recomendaciones").status_code == 401
    assert cliente.post("/api/recomendaciones/1/aprobar").status_code == 401
    assert cliente.post("/api/recomendaciones/1/rechazar", json={"motivo": "motivo largo"}).status_code == 401


# ---- Laboratorio: siempre simulado ----

def test_laboratorio_guarda_la_decision_simulada_sin_llamar_a_ryu(cliente, bd_temporal, cabecera_operador, monkeypatch):
    llamadas = []
    cliente_ryu = ClienteRyu("http://ryu.test", TOKEN_AGENTE, transporte=httpx.MockTransport(lambda p: llamadas.append(p)))
    monkeypatch.setattr(operador, "obtener_cliente_ryu", lambda: cliente_ryu)

    def correr(**opciones) -> dict:
        respuesta = cliente.post("/api/laboratorio/corridas", headers=cabecera_operador,
                                 json={"casos": ["LG-05", "AT-01"], "variante": "con_contexto", **opciones})
        corrida = cliente.get(f"/api/laboratorio/corridas/{respuesta.json()['corrida_id']}", headers=cabecera_operador).json()
        return {r["caso_id"]: (r["accion_propuesta"], r["decision_politica"], r["accion_final"]) for r in corrida["resultados"]}

    assert correr() == {"LG-05": ("pasar_a_cuarentena", "recomendar", "recomendada_simulada"),
                        "AT-01": ("mantener", "descartar", "ninguna")}
    assert correr(umbral_confianza=0.6)["LG-05"] == ("pasar_a_cuarentena", "ejecutar", "pasar_a_cuarentena_simulada")
    assert correr(modo_decision="solo_explicar")["LG-05"] == ("pasar_a_cuarentena", "descartar", "ninguna")
    assert llamadas == [] and _recomendaciones(bd_temporal) == []  # el laboratorio no llena la bandeja


# ---- Corrección: confirmado + pasar_a_cuarentena → descartar, auditado, nunca a la bandeja ----

@pytest.mark.parametrize("modo", ["recomendar", "autonomo_acotado"])
def test_confirmado_con_cuarentena_se_descarta_y_audita(
    cliente, bd_temporal, cabecera_agente, cabecera_operador, payload_c1, ryu_a6, monkeypatch, modo
):
    salida = {**SALIDA_LLM_V3, "veredicto": "confirmado", "severidad": "critica", "confianza": 0.99,
              "accion_propuesta": "pasar_a_cuarentena", "justificacion_accion": "contradictoria"}

    class LLMFalso:
        def completar(self, system, usuario):
            return RespuestaLLM(json.dumps(salida), tokens_entrada=1, tokens_salida=1, latencia_ms=1, peticiones=1)

    monkeypatch.setenv("LLM_HABILITADO", "true")
    monkeypatch.setenv("OPENROUTER_API_KEY", "clave-ficticia")
    get_settings.cache_clear()
    monkeypatch.setattr(pipeline, "obtener_cliente_llm", LLMFalso)
    _config(cliente, cabecera_operador, MODO_DECISION=modo, UMBRAL_CONFIANZA=0.5)

    incidente = _alerta(cliente, cabecera_agente, payload_c1)
    assert (incidente["accion_propuesta"], incidente["decision_politica"], incidente["accion_final"]) == (
        "pasar_a_cuarentena", "descartar", "ninguna")
    assert incidente["motivos_politica"] == ["cuarentena_incompatible_con_confirmado"]
    assert _recomendaciones(bd_temporal) == [] and ryu_a6["10.0.0.3"]["modo"] == "drop_total"
    with Session(bd_temporal) as sesion:
        (auditado,) = sesion.scalars(select(Auditoria).where(Auditoria.evento == "accion_descartada")).all()
    assert auditado.datos == {"incidente_id": incidente["id"], "accion": "pasar_a_cuarentena", "veredicto": "confirmado",
                              "motivo": "cuarentena_incompatible_con_confirmado"}
