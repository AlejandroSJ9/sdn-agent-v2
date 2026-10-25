"""Fase 9a en el pipeline: una alerta por escalón con LLM falso, desglose recalculable, dictamen y umbrales."""

import json
from datetime import datetime, timedelta, timezone

import pytest
from conftest import SALIDA_LLM_V3
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import repository
from app.db.models import Incidente, Notificacion, Recomendacion
from app.integrations.llm import RespuestaLLM
from app.services import pipeline
from app.services.escalera import MOTIVO_CONTRADICCION


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


def _alerta(cliente, cabecera, payload, host="10.0.0.3", puntaje=None) -> Incidente:
    cuerpo = {**payload, "host_origen": host}
    if puntaje is not None:
        cuerpo |= {"puntaje_total": puntaje, "puntaje_maximo": 135}
    incidente_id = cliente.post("/api/alertas", json=cuerpo, headers=cabecera).json()["incidente_id"]
    with repository.SesionLocal() as sesion:
        return sesion.get(Incidente, incidente_id)


def _recalcular(c: dict) -> float:
    """Lo que haría un humano con el desglose guardado."""
    termino = 0.5 * c["s"] if c["veredicto_usado"] == "confirmado" else 0.5 * (1 - c["s"])
    return round(min(max(termino + 0.5 - c["penalizacion"], 0), 1) + 1e-9, 2)


@pytest.mark.parametrize(("salida", "puntaje", "accion", "total"), [
    ({"veredicto": "indeterminado"}, 60, "mantener_drop", None),
    ({"veredicto": "falso_positivo", "accion_propuesta": "pasar_a_cuarentena"}, 60, "limitar_tasa", 0.78),
    ({"veredicto": "confirmado"}, None, "cuarentena_puertos", 0.80),  # sin puntaje: pesos C1 (M1+M2+M3 = 80/135)
    ({"veredicto": "confirmado"}, 135, "cuarentena_vlan", 1.00),
])
def test_una_alerta_por_escalon_con_desglose_recalculable(
    cliente, bd_temporal, cabecera_agente, payload_c1, llm, salida, puntaje, accion, total
):
    llm(**salida)
    i = _alerta(cliente, cabecera_agente, payload_c1, puntaje=puntaje)
    t = i.informe["trazabilidad_y_accion"]
    c = t["confianza_calculada"]
    assert (i.accion_propuesta, t["escalera"]["accion_recomendada"], c["total"], t["confianza_accion"]) == (accion, accion, total, total)
    assert c["fuente_ryu"] == ("puntaje_ryu" if puntaje else "pesos_c1") and i.informe["version_informe"] == "1.2"  # Fase D3: + explicacion_simple
    if total is not None:
        assert _recalcular(c) == total
    with Session(bd_temporal) as sesion:
        recomendaciones = sesion.scalars(select(Recomendacion)).all()
        correo = sesion.scalars(select(Notificacion)).one().payload["mensaje"].splitlines()
    if accion == "mantener_drop":
        assert recomendaciones == [] and i.decision_politica == "descartar"
    else:
        assert [(r.accion, r.confianza) for r in recomendaciones] == [(accion, total)]
    recomendamos = correo[correo.index("QUÉ RECOMENDAMOS") + 1]  # Fase D4: bloque corto, cierre según la acción
    assert recomendamos.endswith("hasta que un administrador decida." if accion == "mantener_drop"
                                 else "Solo se aplica si un administrador la aprueba en el panel.")
    assert t["reversible"] is True


def _historial(host, *, falsos_positivos=0, confirmados_red=0) -> None:
    ahora = datetime.now(timezone.utc)
    for n in range(falsos_positivos):
        t = ahora - timedelta(days=n + 1)
        incidente_id = repository.crear_incidente(host, None, None, t, t, {"umbrales_superados": ["M1", "M2", "M3"]})
        with repository.SesionLocal.begin() as sesion:
            sesion.execute(update(Incidente).where(Incidente.id == incidente_id).values(
                clasificacion_operador="falso_positivo", t_desbloqueado=t, t_explicado=t))
    for n in range(confirmados_red):
        t = ahora - timedelta(minutes=5 + n)
        incidente_id = repository.crear_incidente(f"10.0.0.{70 + n}", None, None, t, t, {"umbrales_superados": ["M1", "M2", "M3"]})
        with repository.SesionLocal.begin() as sesion:
            sesion.execute(update(Incidente).where(Incidente.id == incidente_id).values(t_explicado=t, veredicto="confirmado"))


@pytest.fixture
def con_contexto(cliente, cabecera_operador):
    assert cliente.put("/api/config", json={"CONTEXTO_HABILITADO": True}, headers=cabecera_operador).status_code == 200


def test_agravante_con_confianza_alta_da_mantener_drop(cliente, bd_temporal, cabecera_agente, payload_c1, llm, con_contexto):
    _historial("10.0.0.3", confirmados_red=2)
    llm(veredicto="confirmado")
    i = _alerta(cliente, cabecera_agente, payload_c1, puntaje=135)
    e = i.informe["trazabilidad_y_accion"]["escalera"]
    assert (i.dictamen_historial, e["escalon_por_confianza"], e["accion_recomendada"], e["modificada_por_dictamen"]) == (
        "agravante", "cuarentena_vlan", "mantener_drop", True)
    assert i.informe["trazabilidad_y_accion"]["origen_accion"] == "dictamen" and i.decision_politica == "descartar"


def test_atenuante_con_benigno_alto_queda_en_limitar_tasa(cliente, bd_temporal, cabecera_agente, payload_c1, llm, con_contexto):
    _historial("10.0.0.3", falsos_positivos=2)
    llm(veredicto="falso_positivo")
    i = _alerta(cliente, cabecera_agente, payload_c1, puntaje=60)
    e = i.informe["trazabilidad_y_accion"]["escalera"]
    assert (i.dictamen_historial, e["accion_recomendada"], e["modificada_por_dictamen"]) == ("atenuante", "limitar_tasa", False)


def test_atenuante_con_salida_confirmado_da_mantener_drop_y_decide_el_humano(
    cliente, bd_temporal, cabecera_agente, payload_c1, llm, con_contexto
):
    _historial("10.0.0.3", falsos_positivos=2)
    llm(veredicto="confirmado")
    i = _alerta(cliente, cabecera_agente, payload_c1, puntaje=120)
    e = i.informe["trazabilidad_y_accion"]["escalera"]
    assert (i.veredicto_llm, i.veredicto, i.dictamen_historial) == ("confirmado", "indeterminado", "atenuante")
    assert (e["escalon_por_confianza"], e["accion_recomendada"], e["modificada_por_dictamen"], e["motivo_modificacion"]) == (
        "cuarentena_puertos", "mantener_drop", True, MOTIVO_CONTRADICCION)


def test_verificador_fallido_penaliza_y_baja_de_vlan_a_puertos(cliente, bd_temporal, cabecera_agente, payload_c1, llm):
    llm(veredicto="confirmado", hipotesis_fase="Conexiones al puerto 445 de 10.0.0.99.")
    i = _alerta(cliente, cabecera_agente, payload_c1, puntaje=135)
    c = i.informe["trazabilidad_y_accion"]["confianza_calculada"]
    assert i.informe["razonamiento"]["verificacion"]["resultado"] == "posible_alucinacion"
    assert (c["penalizacion"], c["total"], i.accion_propuesta) == (0.2, 0.8, "cuarentena_puertos")


def test_respaldo_por_reglas_nunca_llega_a_limitar_tasa(cliente, bd_temporal, cabecera_agente, payload_c1):
    i = _alerta(cliente, cabecera_agente, payload_c1, puntaje=60)  # LLM apagado: reglas
    c = i.informe["trazabilidad_y_accion"]["confianza_calculada"]
    assert (i.fuente_explicacion, c["veredicto_usado"], c["total"], i.accion_propuesta) == (
        "reglas", "confirmado", 0.72, "cuarentena_puertos")


def test_umbrales_de_la_escalera_en_caliente_y_validados(cliente, bd_temporal, cabecera_agente, cabecera_operador, payload_c1, llm):
    for cuerpo in ({"ESCALON_HITL": 0.95}, {"ESCALON_HITL": 0.4}, {"ESCALON_VLAN": 1.1},
                   {"ESCALON_HITL": 0.9, "ESCALON_VLAN": 0.8}, {"ESCALON_VLAN": 0.70}):
        assert cliente.put("/api/config", json=cuerpo, headers=cabecera_operador).status_code == 422, cuerpo
    assert cliente.put("/api/config", json={"ESCALON_HITL": 0.80}, headers=cabecera_operador).json()["ESCALON_HITL"] == 0.8
    llm(veredicto="falso_positivo")
    i = _alerta(cliente, cabecera_agente, payload_c1, puntaje=60)  # 0.78 < 0.80 → mantener_drop
    assert i.accion_propuesta == "mantener_drop"
    assert i.informe["trazabilidad_y_accion"]["escalera"]["umbrales"] == {"ESCALON_HITL": 0.8, "ESCALON_VLAN": 0.95}
