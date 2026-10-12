"""Contrato con Ryu-IA: toda alerta de esta carpeta (construida o capturada del log de Ryu) se acepta con 202
y lo que llega al LLM no incluye los campos que el agente nunca le pasa."""

import json
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from conftest import SALIDA_LLM_V3
from app.config import get_settings
from app.db.models import Alerta
from app.integrations.llm import RespuestaLLM
from app.services import pipeline

CARPETA = Path(__file__).parent
ALERTAS = sorted(CARPETA.glob("*.json"))
# Claves que nunca llegan al LLM: datos de red no validados, la petición de Ryu y los campos de RYU_IA v2.
CLAVES_FUERA_DEL_PROMPT = ("contexto_topologico", "ioc_activados", "solicitud_al_agente", "puntaje_total",
                           "puntaje_maximo", "pesos_metricas", "M3_duracion_seg", "M3_bytes")
RESPUESTA_LLM = json.dumps({
    **SALIDA_LLM_V3, "veredicto": "confirmado", "severidad": "critica", "confianza": 0.9,  # Fase 6.8a+b: salida v3
})


class LLMQueGuarda:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def completar(self, system: str, usuario: str) -> RespuestaLLM:
        self.prompts.append(system + "\n" + usuario)
        return RespuestaLLM(RESPUESTA_LLM, tokens_entrada=1, tokens_salida=1, latencia_ms=1, peticiones=1)


@pytest.fixture
def llm_que_guarda(bd_temporal, monkeypatch) -> LLMQueGuarda:
    monkeypatch.setenv("LLM_HABILITADO", "true")
    monkeypatch.setenv("OPENROUTER_API_KEY", "clave-ficticia")
    get_settings.cache_clear()
    falso = LLMQueGuarda()
    monkeypatch.setattr(pipeline, "obtener_cliente_llm", lambda: falso)
    return falso


def test_existe_la_alerta_de_contrato():
    assert CARPETA / "alerta_ryu_real.json" in ALERTAS


@pytest.mark.parametrize("archivo", ALERTAS, ids=lambda archivo: archivo.name)
def test_alerta_de_ryu_aceptada_y_fuera_del_prompt(archivo, cliente, bd_temporal, cabecera_agente, llm_que_guarda):
    payload = json.loads(archivo.read_text(encoding="utf-8"))
    respuesta = cliente.post("/api/alertas", json=payload, headers=cabecera_agente)

    assert respuesta.status_code == 202
    assert respuesta.json()["acciones_ryu"] == ["mantener_bloqueo"]
    assert len(llm_que_guarda.prompts) == 1
    prompt = llm_que_guarda.prompts[0]
    for clave in CLAVES_FUERA_DEL_PROMPT:
        assert clave not in prompt, clave
    textos = [payload.get("solicitud_al_agente"), *payload.get("ioc_activados", [])]
    if isinstance(payload.get("contexto_topologico"), str):
        textos.append(payload["contexto_topologico"])
    for texto in filter(None, textos):
        assert texto not in prompt, texto

    with Session(bd_temporal) as sesion:  # en crudo sí se guardan (salvo solicitud_al_agente)
        guardado = sesion.scalars(select(Alerta)).one().payload
    assert "solicitud_al_agente" not in guardado
    for clave in ("puntaje_total", "puntaje_maximo", "pesos_metricas", "contexto_topologico"):
        if clave in payload:
            assert guardado[clave] == payload[clave]
