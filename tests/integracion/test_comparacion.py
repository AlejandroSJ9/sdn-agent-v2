"""Comparar corridas: las 4 variantes sobre el mismo conjunto de casos, destacados y exportaciones."""

import csv
import io
import json

import pytest

from conftest import SALIDA_LLM_V3
from app.config import get_settings
from app.integrations.llm import RespuestaLLM
from app.services import comparacion, pipeline

# El LLM falso siempre dice "confirmado": falla en LG-05 (aceptables: falso_positivo / indeterminado).
SALIDA_LLM = json.dumps({
    **SALIDA_LLM_V3, "veredicto": "confirmado", "severidad": "alta", "confianza": 0.8,  # Fase 6.8a+b: salida v3
})
COMUN = ["AT-01", "AT-02", "AT-03", "AT-04", "AT-05", "AT-06", "AT-07", "AT-08", "AT-09", "AT-10", "LG-05",
         "AM-01", "AM-02", "DU-01"]


class LLMFalso:
    def completar(self, system: str, usuario: str) -> RespuestaLLM:
        return RespuestaLLM(SALIDA_LLM, tokens_entrada=1, tokens_salida=1, latencia_ms=1, peticiones=1)


@pytest.fixture
def con_llm(bd_temporal, monkeypatch):
    monkeypatch.setenv("LLM_HABILITADO", "true")
    monkeypatch.setenv("OPENROUTER_API_KEY", "clave-ficticia")
    get_settings.cache_clear()
    monkeypatch.setattr(pipeline, "obtener_cliente_llm", LLMFalso)


def _corrida(cliente, cabecera, **cuerpo) -> int:
    respuesta = cliente.post("/api/laboratorio/corridas", json=cuerpo, headers=cabecera)
    assert respuesta.status_code == 202, respuesta.text
    return respuesta.json()["corrida_id"]


@pytest.fixture
def cuatro(cliente, cabecera_operador, con_llm) -> list[int]:
    return [
        _corrida(cliente, cabecera_operador, conjunto="todos", variante=variante, usar_llm=usar_llm)
        for usar_llm in (False, True) for variante in ("sin_contexto", "con_contexto")
    ]


def test_cuatro_variantes_sobre_el_mismo_conjunto_comun(cliente, cabecera_operador, cuatro):
    r = cliente.get(f"/api/laboratorio/comparar?corridas={','.join(map(str, cuatro))}", headers=cabecera_operador).json()

    assert r["casos_comparados"] == COMUN
    assert r["casos_excluidos"] == ["LG-01", "LG-02", "LG-03", "LG-04"]
    assert [fila["caso_id"] for fila in r["tabla"]] == COMUN
    assert all(set(fila["celdas"]) == {str(i) for i in cuatro} for fila in r["tabla"])
    assert {r["resumen"][str(i)]["global"]["total"] for i in cuatro} == {14}

    reglas_sin, reglas_con, llm_sin, llm_con = (r["resumen"][str(i)]["global"]["aciertos"] for i in cuatro)
    # Reglas sin contexto fallan AM-02 y LG-05; con contexto LG-05 pasa a indeterminado (acierta).
    assert (reglas_sin, reglas_con) == (12, 13)
    # Fase 6.8a+b: el dictamen determinista también fija el veredicto final del LLM (LG-05 → indeterminado),
    # así que con contexto ninguno de los dos acierta donde el otro falla.
    assert r["destacados"] == {"llm_con_contexto_acierta_reglas_con_contexto_falla": [],
                               "reglas_con_contexto_acierta_llm_con_contexto_falla": []}
    assert set(r["resumen"][str(cuatro[0])]["por_clase"]) == {"ataque", "legitimo", "ambiguo", "duplicado"}
    assert set(r["resumen"][str(cuatro[0])]["por_conjunto"]) == {"desarrollo", "validacion"}
    assert llm_con == llm_sin + 1  # el LLM falso dice siempre "confirmado"; el dictamen atenuante corrige LG-05


def test_exportacion_json_incluye_las_hipotesis(cliente, cabecera_operador, cuatro):
    exportado = cliente.get(f"/api/export/comparar.json?corridas={cuatro[0]},{cuatro[1]}", headers=cabecera_operador).json()
    assert exportado["hipotesis"] == comparacion.HIPOTESIS
    assert exportado["hipotesis"]["H1"].startswith("Sin contexto, LLM y reglas obtienen la misma exactitud")
    assert "Fase 7" in exportado["hipotesis"]["limitacion"]
    assert exportado["destacados"] is None  # falta la corrida LLM con contexto
    sin_hipotesis = cliente.get(f"/api/laboratorio/comparar?corridas={cuatro[0]},{cuatro[1]}", headers=cabecera_operador).json()
    assert "hipotesis" not in sin_hipotesis


def test_exportacion_csv(cliente, cabecera_operador, cuatro):
    respuesta = cliente.get(f"/api/export/comparar.csv?corridas={cuatro[0]},{cuatro[3]}", headers=cabecera_operador)
    filas = list(csv.DictReader(io.StringIO(respuesta.text)))
    assert respuesta.headers["content-type"].startswith("text/csv")
    assert len(filas) == 14 * 2 and set(filas[0]) == set(comparacion.COLUMNAS_CSV)


def test_casos_en_comun_entre_corridas_distintas(cliente, cabecera_operador):
    a = _corrida(cliente, cabecera_operador, casos=["AT-01", "LG-05", "LG-01"])
    b = _corrida(cliente, cabecera_operador, casos=["AT-01", "AM-01"], variante="con_contexto")
    r = cliente.get(f"/api/laboratorio/comparar?corridas={a},{b}", headers=cabecera_operador).json()
    assert r["casos_comparados"] == ["AT-01"] and r["casos_excluidos"] == ["AM-01", "LG-01", "LG-05"]


@pytest.mark.parametrize("corridas", ["1", "1,2,3,4,5", "1,1", "1,x", "1,999"])
def test_corridas_invalidas_422(cliente, cabecera_operador, corridas):
    _corrida(cliente, cabecera_operador, casos=["AT-01"])
    respuesta = cliente.get(f"/api/laboratorio/comparar?corridas={corridas}", headers=cabecera_operador)
    assert respuesta.status_code == 422


def test_comparar_exige_token(cliente):
    assert cliente.get("/api/laboratorio/comparar?corridas=1,2").status_code == 401
    assert cliente.get("/api/export/comparar.json?corridas=1,2").status_code == 401
