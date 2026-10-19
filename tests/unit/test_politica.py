"""Motor de políticas (C7, Fase 9b): función pura. Una prueba por condición, combinadas y la regla
"confirmado nunca limitar_tasa". UMBRAL_CONFIANZA está retirado: la confianza ya no es una condición."""

import pytest

from app.services import politica
from app.services.politica import Decision, evaluar

PROPUESTA = {"accion": "cuarentena_puertos", "veredicto": "confirmado", "veredicto_salida": "confirmado"}
INCIDENTE = {"host": "10.0.0.3", "laboratorio": False, "modo_actual": "drop_total"}
TODAS = {"cuarentena": True, "limitar_tasa": True, "cuarentena_vlan": True}
SISTEMA = {"modo_decision": "autonomo_acotado", "capacidades": TODAS,
           "hosts_protegidos": [], "acciones_ultimo_minuto": 0, "max_acciones_por_min": 3}


def _evaluar(propuesta=None, incidente=None, **sistema) -> Decision:
    return evaluar({**PROPUESTA, **(propuesta or {})}, {**INCIDENTE, **(incidente or {})}, {**SISTEMA, **sistema})


BENIGNO = {"accion": "limitar_tasa", "veredicto": "falso_positivo", "veredicto_salida": "falso_positivo"}


def test_todo_se_cumple_ejecuta_cada_escalon():
    for propuesta in ({}, BENIGNO, {"accion": "cuarentena_vlan"}):
        assert _evaluar(propuesta) == Decision("ejecutar", [])


@pytest.mark.parametrize(("cambio", "motivo"), [
    ({"modo_decision": "recomendar"}, politica.MOTIVO_MODO),                      # 1
    ({"capacidades": {**TODAS, "cuarentena": False}}, politica.MOTIVO_SIN_A6),    # 2
    ({"hosts_protegidos": ["10.0.0.3"]}, politica.MOTIVO_PROTEGIDO),              # 3
    ({"acciones_ultimo_minuto": 3}, politica.MOTIVO_LIMITE),                      # 4
])
def test_cada_condicion_del_sistema_por_separado(cambio, motivo):
    assert _evaluar(**cambio) == Decision("recomendar", [motivo])


def test_condicion_2_la_capacidad_es_por_accion():
    solo_a6 = {"capacidades": {"cuarentena": True, "limitar_tasa": False, "cuarentena_vlan": False}}
    assert _evaluar(**solo_a6).resultado == "ejecutar"
    assert _evaluar(BENIGNO, **solo_a6).motivos == [politica.MOTIVO_SIN_A7]
    assert _evaluar({"accion": "cuarentena_vlan"}, **solo_a6).motivos == [politica.MOTIVO_SIN_A8]
    assert _evaluar(capacidades={}).motivos == [politica.MOTIVO_SIN_A6]  # Ryu caído: sin ninguna


def test_la_confianza_ya_no_es_una_condicion():
    assert _evaluar({"confianza": 0.70, "umbral_confianza": 0.99}).resultado == "ejecutar"


def test_condicion_5_host_que_ya_no_esta_en_drop_total_se_descarta():
    assert _evaluar(incidente={"modo_actual": "cuarentena"}) == Decision("descartar", [politica.MOTIVO_NO_DROP])


def test_confirmado_nunca_limitar_tasa_se_descarta_nunca_se_recomienda():
    for modo in ("recomendar", "autonomo_acotado"):
        for veredictos in ({"veredicto": "confirmado"}, {"veredicto_salida": "confirmado", "veredicto": "indeterminado"}):
            decision = _evaluar({"accion": "limitar_tasa", **veredictos}, modo_decision=modo)
            assert decision == Decision("descartar", [politica.MOTIVO_INCOMPATIBLE])
    assert _evaluar(BENIGNO).resultado == "ejecutar"


def test_laboratorio_sin_limite_por_minuto():
    assert _evaluar(incidente={"laboratorio": True}, acciones_ultimo_minuto=99) == Decision("ejecutar", [])
    assert _evaluar(incidente={"laboratorio": True}, capacidades={}).motivos == [politica.MOTIVO_SIN_A6]


def test_condiciones_combinadas_listan_todos_los_motivos():
    decision = _evaluar(modo_decision="recomendar", capacidades={}, hosts_protegidos=["10.0.0.3"], acciones_ultimo_minuto=5)
    assert decision.resultado == "recomendar"
    assert decision.motivos == [politica.MOTIVO_MODO, politica.MOTIVO_SIN_A6, politica.MOTIVO_PROTEGIDO, politica.MOTIVO_LIMITE]


def test_mantener_drop_nunca_genera_recomendacion():
    for modo in ("solo_explicar", "recomendar", "autonomo_acotado"):
        assert _evaluar({"accion": "mantener_drop"}, modo_decision=modo) == Decision("descartar", [])


def test_solo_explicar_ni_ejecuta_ni_recomienda():
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
