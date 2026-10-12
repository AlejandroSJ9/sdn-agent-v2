"""Dictamen determinista de historial (Fase 6.8a): función pura, orden agravante → atenuante → ninguno."""

import ast
import inspect

import pytest

from app.services import dictamen_historial
from app.services.dictamen_historial import SIN_DICTAMEN, dictaminar

UMBRALES = ["M1", "M2", "M4"]


def _contexto(historial=(), actividad=(), confirmado_60=False, amenazas=0) -> dict:
    return {"historial_host": list(historial), "actividad_red": list(actividad),
            "resumen": {"total_previos": len(historial), "falsos_positivos_operador": 0,
                        "amenazas_contenidas_operador": amenazas, "confirmado_ultimos_60_min": confirmado_60}}


def _previo(clasificacion=None, umbrales=UMBRALES, veredicto="falso_positivo") -> dict:
    previo = {"minutos_antes": 10080, "veredicto_agente": veredicto, "estado_veredicto": "no_verificado",
              "umbrales_superados": list(umbrales)}
    return {**previo, "clasificacion_operador": clasificacion} if clasificacion else previo


def _otro(host, veredicto="confirmado") -> dict:
    return {"host": host, "minutos_antes": 5, "veredicto": veredicto, "umbrales_superados": ["M1", "M3", "M5"]}


def test_sin_contexto_no_hay_dictamen():
    assert dictaminar(None, UMBRALES) == SIN_DICTAMEN == {"tipo": None, "veredicto": None, "motivo": None}


@pytest.mark.parametrize(("contexto", "motivo"), [
    (_contexto(confirmado_60=True), "incidente confirmado en los últimos 60 min en el mismo host"),
    (_contexto(amenazas=1), "1 amenaza(s) contenida(s) por el operador en el host"),
    (_contexto(actividad=[_otro("10.0.10.2"), _otro("10.0.10.3")]), "2 hosts confirmados en la actividad de la red"),
])
def test_agravantes(contexto, motivo):
    assert dictaminar(contexto, UMBRALES) == {"tipo": "agravante", "veredicto": "confirmado", "motivo": motivo}


def test_campana_necesita_2_hosts_distintos_y_confirmados():
    assert dictaminar(_contexto(actividad=[_otro("10.0.10.2")] * 3), UMBRALES)["tipo"] is None
    assert dictaminar(_contexto(actividad=[_otro("10.0.10.2"), _otro("10.0.10.3", "indeterminado")]), UMBRALES)["tipo"] is None


def test_atenuante_con_2_fp_del_operador_y_mismos_umbrales_nunca_da_falso_positivo():
    dictamen = dictaminar(_contexto([_previo("falso_positivo")] * 2), UMBRALES)
    assert dictamen == {"tipo": "atenuante", "veredicto": "indeterminado",
                        "motivo": "2 clasificaciones falso_positivo del operador con los mismos umbrales (M1, M2, M4)"}
    for n in (2, 3, 10):
        assert dictaminar(_contexto([_previo("falso_positivo")] * n), UMBRALES)["veredicto"] != "falso_positivo"


@pytest.mark.parametrize("historial", [
    [_previo("falso_positivo", ["M1", "M2"])] * 3,       # umbrales distintos
    [_previo("falso_positivo", ["M2", "M1", "M4"])] * 3,  # mismo conjunto, distinto orden: no es "exactamente"
    [_previo(None)] * 3,                                  # FP del agente, no del operador
    [_previo("falso_positivo")],                          # uno solo
    [_previo("falso_positivo"), _previo("otro")],
])
def test_atenuante_no_aplica(historial):
    assert dictaminar(_contexto(historial), UMBRALES)["tipo"] is None


def test_el_agravante_gana_al_atenuante():
    contexto = _contexto([_previo("falso_positivo")] * 3, confirmado_60=True)
    assert dictaminar(contexto, UMBRALES)["tipo"] == "agravante"


def test_es_pura():
    arbol = ast.parse(inspect.getsource(dictamen_historial))
    modulos = {n.module for n in ast.walk(arbol) if isinstance(n, ast.ImportFrom)}
    modulos |= {a.name for n in ast.walk(arbol) if isinstance(n, ast.Import) for a in n.names}
    assert modulos <= {"typing"}, modulos
