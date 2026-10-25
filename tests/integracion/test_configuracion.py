"""Configuración en caliente (LLM_HABILITADO, DEDUP_SEGUNDOS), umbrales de Ryu y cupo del LLM."""

import json
from datetime import timedelta

import mock_ryu
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Alerta, Auditoria, Incidente
from app.integrations.llm import RespuestaLLM
from app.services import pipeline
from conftest import SALIDA_LLM_V3

SALIDA_VALIDA = {**SALIDA_LLM_V3, "recomendacion_operador": "Revisar antes de desbloquear."}  # Fase 6.8a+b: salida v3


class LLMFalso:
    def __init__(self) -> None:
        self.llamadas = 0

    def completar(self, system: str, usuario: str) -> RespuestaLLM:
        self.llamadas += 1
        return RespuestaLLM(json.dumps(SALIDA_VALIDA), 10, 10, 100, 1)


def _alerta(cliente, cabecera, payload) -> int:
    respuesta = cliente.post("/api/alertas", json=payload, headers=cabecera)
    assert respuesta.status_code == 202
    return respuesta.json()["incidente_id"]


def _fuente(motor, incidente_id: int) -> tuple[str, str | None]:
    with Session(motor) as sesion:
        incidente = sesion.get(Incidente, incidente_id)
        return incidente.fuente_explicacion, incidente.motivo_respaldo


def test_apagar_el_llm_por_api_aplica_sin_reiniciar(
    cliente, bd_temporal, cabecera_agente, cabecera_operador, payload_c1, monkeypatch
):
    monkeypatch.setenv("LLM_HABILITADO", "true")
    monkeypatch.setenv("OPENROUTER_API_KEY", "clave-ficticia")
    get_settings.cache_clear()
    falso = LLMFalso()
    monkeypatch.setattr(pipeline, "obtener_cliente_llm", lambda: falso)

    assert _fuente(bd_temporal, _alerta(cliente, cabecera_agente, payload_c1)) == ("llm", None)
    respuesta = cliente.put("/api/config", json={"LLM_HABILITADO": False}, headers=cabecera_operador)
    assert respuesta.json() == {"LLM_HABILITADO": False, "DEDUP_SEGUNDOS": 60, "CONTEXTO_HABILITADO": False,
                                "HISTORIAL_DIAS": 30, "CAMPANA_MINUTOS": 30,  # Fase 6.7: + contexto dinámico
                                "PROMPT_V4": True,  # Fase D3.1: prompt v4 por defecto
                                "MODO_DECISION": "recomendar", "RECOMENDACION_TTL_MIN": 60,  # Fase 6.8: + motor de políticas
                                # Fase D1: sin UMBRAL_CONFIANZA, MAX_ACCIONES_POR_MIN ni HOSTS_PROTEGIDOS (retiradas)
                                "ESCALON_HITL": 0.7, "ESCALON_VLAN": 0.95}  # Fase 9a: + escalera

    payload_c1["host_origen"] = "10.0.0.4"
    assert _fuente(bd_temporal, _alerta(cliente, cabecera_agente, payload_c1)) == ("reglas", "llm_deshabilitado")
    assert falso.llamadas == 1
    with Session(bd_temporal) as sesion:
        cambio = sesion.scalars(select(Auditoria).where(Auditoria.evento == "config_cambiada")).one()
    assert cambio.datos == {"clave": "LLM_HABILITADO", "anterior": True, "nuevo": False}


def test_cambiar_dedup_segundos_cambia_la_agrupacion(cliente, bd_temporal, cabecera_agente, cabecera_operador, payload_c1):
    assert cliente.get("/api/config", headers=cabecera_operador).json()["DEDUP_SEGUNDOS"] == 60
    primero = _alerta(cliente, cabecera_agente, payload_c1)
    with Session(bd_temporal) as sesion:  # la última alerta llegó hace 30 s
        for alerta in sesion.scalars(select(Alerta)):
            alerta.t_recibido -= timedelta(seconds=30)
        sesion.commit()

    cliente.put("/api/config", json={"DEDUP_SEGUNDOS": 10}, headers=cabecera_operador)
    assert _alerta(cliente, cabecera_agente, payload_c1) != primero  # con 60 s habría agrupado


def test_put_config_sin_cambios_no_audita(cliente, bd_temporal, cabecera_operador):
    cliente.put("/api/config", json={"DEDUP_SEGUNDOS": 60, "LLM_HABILITADO": False}, headers=cabecera_operador)
    with Session(bd_temporal) as sesion:
        assert sesion.scalars(select(Auditoria)).all() == []


def test_claves_retiradas_se_aceptan_sin_efecto(cliente, bd_temporal, cabecera_operador):
    """Fase D1: UMBRAL_CONFIANZA, MAX_ACCIONES_POR_MIN y HOSTS_PROTEGIDOS llegan de clientes viejos → 200, nada cambia."""
    antes = cliente.get("/api/config", headers=cabecera_operador).json()
    retiradas = {"UMBRAL_CONFIANZA": 0.6, "MAX_ACCIONES_POR_MIN": 1, "HOSTS_PROTEGIDOS": ["10.0.0.3"]}
    respuesta = cliente.put("/api/config", json=retiradas, headers=cabecera_operador)
    assert respuesta.status_code == 200 and respuesta.json() == antes
    assert not set(retiradas) & set(antes)
    with Session(bd_temporal) as sesion:
        assert sesion.scalars(select(Auditoria)).all() == []


@pytest.mark.parametrize(
    "cuerpo",
    [
        {"DEDUP_SEGUNDOS": 5},
        {"DEDUP_SEGUNDOS": 3601},
        {"DEDUP_SEGUNDOS": "60"},
        {"LLM_HABILITADO": "si"},
        {"LLM_HABILITADO": 1},
        {"RATE_LIMIT_POR_MIN": 10},
    ],
)
def test_config_invalida_422(cliente, cabecera_operador, cuerpo):
    assert cliente.put("/api/config", json=cuerpo, headers=cabecera_operador).status_code == 422


def test_umbrales_solo_lectura_y_rutas_retiradas(cliente, bd_temporal, cabecera_operador, ryu_mock):
    """Fase D2: GET de umbrales se conserva; PUT de umbrales y pytest desde el panel ya no existen."""
    assert cliente.get("/api/ryu/umbrales", headers=cabecera_operador).json() == mock_ryu.UMBRALES_INICIALES
    assert cliente.put("/api/ryu/umbrales", json={"M2": 7}, headers=cabecera_operador).status_code == 405
    assert ryu_mock.cliente.umbrales()["M2"] == mock_ryu.UMBRALES_INICIALES["M2"]  # Ryu no cambió
    assert cliente.post("/api/laboratorio/pytest", json={"carpeta": "unit"}, headers=cabecera_operador).status_code == 404
    assert cliente.get("/api/laboratorio/pytest/ultimo", headers=cabecera_operador).status_code == 404
    with Session(bd_temporal) as sesion:
        assert sesion.scalars(select(Auditoria)).all() == []


def test_cupo_del_llm(cliente, cabecera_operador):
    cupo = cliente.get("/api/llm/cupo", headers=cabecera_operador).json()
    assert set(cupo) == {"fecha", "usadas", "cupo", "restantes", "fallidas"}
    assert (cupo["usadas"], cupo["cupo"], cupo["restantes"], cupo["fallidas"]) == (0, 1000, 1000, 0)
