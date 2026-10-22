"""Motor de políticas (C7, Fase D1): función pura. El agente nunca ejecuta: recomendar o descartar, con el motivo
de capacidad por acción y la regla "confirmado nunca limitar_tasa"."""

import itertools

import pytest

from app.services import politica
from app.services.politica import Decision, evaluar

PROPUESTA = {"accion": "cuarentena_puertos", "veredicto": "confirmado", "veredicto_salida": "confirmado"}
INCIDENTE = {"modo_actual": "drop_total"}
TODAS = {"cuarentena": True, "limitar_tasa": True, "cuarentena_vlan": True}
SISTEMA = {"modo_decision": "recomendar", "capacidades": TODAS}


def _evaluar(propuesta=None, incidente=None, **sistema) -> Decision:
    return evaluar({**PROPUESTA, **(propuesta or {})}, {**INCIDENTE, **(incidente or {})}, {**SISTEMA, **sistema})


BENIGNO = {"accion": "limitar_tasa", "veredicto": "falso_positivo", "veredicto_salida": "falso_positivo"}


def test_cada_escalon_queda_como_recomendacion_sin_motivos():
    for propuesta in ({}, BENIGNO, {"accion": "cuarentena_vlan"}):
        assert _evaluar(propuesta) == Decision("recomendar", [])


def test_la_capacidad_es_por_accion_y_solo_agrega_un_motivo():
    solo_a6 = {"capacidades": {"cuarentena": True, "limitar_tasa": False, "cuarentena_vlan": False}}
    assert _evaluar(**solo_a6) == Decision("recomendar", [])
    assert _evaluar(BENIGNO, **solo_a6) == Decision("recomendar", [politica.MOTIVO_SIN_A7])
    assert _evaluar({"accion": "cuarentena_vlan"}, **solo_a6) == Decision("recomendar", [politica.MOTIVO_SIN_A8])
    assert _evaluar(capacidades={}) == Decision("recomendar", [politica.MOTIVO_SIN_A6])  # Ryu caído: sin ninguna


def test_nunca_ejecuta_en_ninguna_combinacion():
    acciones = ("mantener_drop", "limitar_tasa", "cuarentena_puertos", "cuarentena_vlan")
    modos = ("solo_explicar", "recomendar")
    for accion, modo, capacidades, modo_actual in itertools.product(acciones, modos, (TODAS, {}), ("drop_total", "cuarentena")):
        decision = _evaluar({"accion": accion}, {"modo_actual": modo_actual}, modo_decision=modo, capacidades=capacidades)
        assert decision.resultado in ("recomendar", "descartar")


def test_la_confianza_no_es_una_condicion():
    assert _evaluar({"confianza": 0.70, "umbral_confianza": 0.99}).resultado == "recomendar"


def test_host_que_ya_no_esta_en_drop_total_se_descarta():
    assert _evaluar(incidente={"modo_actual": "cuarentena"}) == Decision("descartar", [politica.MOTIVO_NO_DROP])


def test_confirmado_nunca_limitar_tasa_se_descarta_nunca_se_recomienda():
    for veredictos in ({"veredicto": "confirmado"}, {"veredicto_salida": "confirmado", "veredicto": "indeterminado"}):
        decision = _evaluar({"accion": "limitar_tasa", **veredictos})
        assert decision == Decision("descartar", [politica.MOTIVO_INCOMPATIBLE])
    assert _evaluar(BENIGNO).resultado == "recomendar"


def test_mantener_drop_nunca_genera_recomendacion():
    for modo in ("solo_explicar", "recomendar"):
        assert _evaluar({"accion": "mantener_drop"}, modo_decision=modo) == Decision("descartar", [])


def test_solo_explicar_ni_recomienda():
    for accion in ("cuarentena_puertos", "cuarentena_vlan"):
        assert _evaluar({"accion": accion}, modo_decision="solo_explicar").resultado == "descartar"


@pytest.mark.parametrize("accion", ["desbloquear", "liberar", "escalar_a_drop", "pasar_a_cuarentena", "mantener", "", None])
def test_accion_fuera_de_la_lista_cerrada_se_descarta(accion):
    assert _evaluar({"accion": accion}).resultado == "descartar"


def test_la_politica_es_pura():
    import ast
    import inspect

    arbol = ast.parse(inspect.getsource(politica))
    modulos = {n.module for n in ast.walk(arbol) if isinstance(n, ast.ImportFrom)}
    modulos |= {a.name for n in ast.walk(arbol) if isinstance(n, ast.Import) for a in n.names}
    assert modulos <= {"dataclasses", "typing"}, modulos  # sin BD, sin red, sin reloj
