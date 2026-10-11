"""Toda explicación por reglas tiene motivo_respaldo; solo la del LLM lo deja vacío. Se recorren todos los caminos."""

import json

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Incidente
from app.integrations.llm import LLMError, RespuestaLLM
from app.services import pipeline

SALIDA_VALIDA = {
    "veredicto": "confirmado", "severidad": "alta", "confianza": 0.8, "tecnica_mitre": "T1021.002",
    "explicacion": "LLM falso.", "evidencias": ["M3"], "recomendacion_operador": "Revisar.",
}


class LLMFalso:
    def __init__(self, respuesta):
        self.respuesta = respuesta

    def completar(self, system, usuario):
        if isinstance(self.respuesta, Exception):
            raise self.respuesta
        return RespuestaLLM(self.respuesta, 10, 10, 50, 1)


CAMINOS = [
    # (id, LLM_HABILITADO, OPENROUTER_API_KEY, LLM_CUPO_DIARIO, respuesta del LLM falso, fuente, motivo)
    ("llm_apagado", "false", "x", "1000", None, "reglas", "llm_deshabilitado"),
    ("sin_api_key", "true", "", "1000", None, "reglas", "sin_api_key"),
    ("sin_cupo", "true", "x", "0", None, "reglas", "sin_cupo"),
    ("timeout", "true", "x", "1000", LLMError("timeout", peticiones=1), "reglas", "timeout"),
    ("error_llm", "true", "x", "1000", LLMError("error_llm", "HTTP 500", 1), "reglas", "error_llm"),
    ("json_invalido", "true", "x", "1000", "esto no es JSON", "reglas", "json_invalido"),
    ("schema_invalido", "true", "x", "1000", json.dumps({**SALIDA_VALIDA, "confianza": 1.5}), "reglas", "schema_invalido"),
    ("error_interno", "true", "x", "1000", RuntimeError("fallo inesperado"), "reglas", "error_interno"),
    ("llm_valido", "true", "x", "1000", json.dumps(SALIDA_VALIDA), "llm", None),
]


@pytest.mark.parametrize(("habilitado", "clave", "cupo", "respuesta", "fuente", "motivo"),
                         [c[1:] for c in CAMINOS], ids=[c[0] for c in CAMINOS])
def test_cada_camino_del_pipeline(cliente, bd_temporal, cabecera_agente, payload_c1, monkeypatch,
                                  habilitado, clave, cupo, respuesta, fuente, motivo):
    for nombre, valor in {"LLM_HABILITADO": habilitado, "OPENROUTER_API_KEY": clave, "LLM_CUPO_DIARIO": cupo}.items():
        monkeypatch.setenv(nombre, valor)
    get_settings.cache_clear()
    if respuesta is not None:
        monkeypatch.setattr(pipeline, "obtener_cliente_llm", lambda: LLMFalso(respuesta))

    incidente_id = cliente.post("/api/alertas", json=payload_c1, headers=cabecera_agente).json()["incidente_id"]
    with Session(bd_temporal) as sesion:
        incidente = sesion.get(Incidente, incidente_id)
    assert (incidente.fuente_explicacion, incidente.motivo_respaldo) == (fuente, motivo)


def test_laboratorio_sin_llm(cliente, bd_temporal, cabecera_operador):
    corrida = cliente.post("/api/laboratorio/corridas", json={"conjunto": "todos"}, headers=cabecera_operador).json()
    resultados = cliente.get(f"/api/laboratorio/corridas/{corrida['corrida_id']}", headers=cabecera_operador).json()
    assert {(r["fuente"], r["motivo_respaldo"]) for r in resultados["resultados"]} == {("reglas", "laboratorio_sin_llm")}

    with Session(bd_temporal) as sesion:  # invariante sobre TODA la tabla
        por_reglas = sesion.scalars(select(Incidente).where(Incidente.fuente_explicacion == "reglas")).all()
    assert por_reglas and all(i.motivo_respaldo for i in por_reglas)
