"""Motor de políticas (C7): función pura. Una prueba por condición, combinadas y la normalización de C4."""

import pytest

from app.services import politica
from app.services.politica import Decision, evaluar, normalizar_accion

PROPUESTA = {"accion": "pasar_a_cuarentena", "confianza": 0.9, "veredicto": "indeterminado"}
INCIDENTE = {"host": "10.0.0.3", "laboratorio": False}
SISTEMA = {"modo_decision": "autonomo_acotado", "capacidad_a6": True, "umbral_confianza": 0.85,
           "hosts_protegidos": [], "acciones_ultimo_minuto": 0, "max_acciones_por_min": 3}


def _evaluar(propuesta=None, incidente=None, **sistema) -> Decision:
    return evaluar({**PROPUESTA, **(propuesta or {})}, {**INCIDENTE, **(incidente or {})}, {**SISTEMA, **sistema})


def test_todo_se_cumple_ejecuta():
    assert _evaluar() == Decision("ejecutar", [])
    assert _evaluar({"accion": "escalar_a_drop", "veredicto": "confirmado"}) == Decision("ejecutar", [])


@pytest.mark.parametrize(("cambio", "motivo"), [
    ({"modo_decision": "recomendar"}, politica.MOTIVO_MODO),                      # 1
    ({"capacidad_a6": False}, politica.MOTIVO_SIN_A6),                            # 2
    ({"umbral_confianza": 0.95}, politica.MOTIVO_CONFIANZA),                      # 3
    ({"hosts_protegidos": ["10.0.0.3"]}, politica.MOTIVO_PROTEGIDO),              # 4
    ({"acciones_ultimo_minuto": 3}, politica.MOTIVO_LIMITE),                      # 5
])
def test_cada_condicion_del_sistema_por_separado(cambio, motivo):
    assert _evaluar(**cambio) == Decision("recomendar", [motivo])


def test_condicion_3_confianza_ausente_o_justo_en_el_umbral():
    assert _evaluar({"confianza": None}).motivos == [politica.MOTIVO_CONFIANZA]
    assert _evaluar({"confianza": 0.85}).resultado == "ejecutar"  # ≥ umbral


def test_condicion_6_confirmado_mas_cuarentena_se_descarta_nunca_se_recomienda():
    # Corrección Fase 6.8: antes quedaba como recomendación; ahora se descarta con su motivo.
    for modo in ("recomendar", "autonomo_acotado"):
        for sistema in ({}, {"umbral_confianza": 0.5}, {"capacidad_a6": False}, {"hosts_protegidos": ["10.0.0.3"]}):
            decision = _evaluar({"veredicto": "confirmado", "confianza": 1.0}, modo_decision=modo, **sistema)
            assert decision == Decision("descartar", [politica.MOTIVO_INCOMPATIBLE])
    assert _evaluar({"veredicto": "confirmado"}, incidente={"laboratorio": True}).resultado == "descartar"
    assert _evaluar({"veredicto": "falso_positivo"}).resultado == "ejecutar"
    assert _evaluar({"accion": "escalar_a_drop", "veredicto": "confirmado"}).resultado == "ejecutar"  # solo cuarentena


def test_condicion_7_laboratorio_ejecuta_simulado_sin_limite_por_minuto():
    assert _evaluar(incidente={"laboratorio": True}, acciones_ultimo_minuto=99) == Decision("ejecutar", [])
    assert _evaluar(incidente={"laboratorio": True}, capacidad_a6=False).motivos == [politica.MOTIVO_SIN_A6]


def test_condiciones_combinadas_listan_todos_los_motivos():
    decision = _evaluar({"veredicto": None, "confianza": 0.2}, modo_decision="recomendar", capacidad_a6=False,
                        hosts_protegidos=["10.0.0.3"], acciones_ultimo_minuto=5)
    assert decision.resultado == "recomendar"
    assert decision.motivos == [politica.MOTIVO_MODO, politica.MOTIVO_SIN_A6, politica.MOTIVO_CONFIANZA,
                                politica.MOTIVO_PROTEGIDO, politica.MOTIVO_LIMITE, politica.MOTIVO_CONFIRMADO]


def test_mantener_nunca_genera_recomendacion():
    for modo in ("solo_explicar", "recomendar", "autonomo_acotado"):
        assert _evaluar({"accion": "mantener"}, modo_decision=modo) == Decision("descartar", [])


def test_solo_explicar_ni_ejecuta_ni_recomienda():
    assert _evaluar(modo_decision="solo_explicar").resultado == "descartar"
    assert _evaluar({"veredicto": "confirmado"}, modo_decision="solo_explicar").resultado == "descartar"


@pytest.mark.parametrize("accion", ["desbloquear", "liberar", "", None])
def test_accion_fuera_de_la_lista_cerrada_se_descarta(accion):
    assert _evaluar({"accion": accion}).resultado == "descartar"


@pytest.mark.parametrize(("accion", "modo", "esperada", "corregida"), [
    ("pasar_a_cuarentena", "drop_total", "pasar_a_cuarentena", False),
    ("escalar_a_drop", "drop_total", "mantener", True),
    ("escalar_a_drop", "cuarentena", "escalar_a_drop", False),
    ("pasar_a_cuarentena", "cuarentena", "mantener", True),
    ("mantener", "cuarentena", "mantener", False),
    ("desbloquear", "drop_total", "mantener", True),
    (None, "drop_total", "mantener", True),
])
def test_accion_invalida_para_el_modo_pasa_a_mantener(accion, modo, esperada, corregida):
    assert normalizar_accion(accion, modo) == (esperada, corregida)


def test_la_politica_es_pura():
    import ast
    import inspect

    arbol = ast.parse(inspect.getsource(politica))
    modulos = {n.module for n in ast.walk(arbol) if isinstance(n, ast.ImportFrom)}
    modulos |= {a.name for n in ast.walk(arbol) if isinstance(n, ast.Import) for a in n.names}
    assert modulos <= {"dataclasses", "typing"}, modulos  # sin BD, sin red, sin reloj
