"""Confianza calculada y escalera de respuesta (C7, Fase 9a): función pura, recalculable a mano."""

import ast
import inspect

import pytest

from app.services import escalera
from app.services.escalera import MOTIVO_CONTRADICCION, calcular_confianza, decidir

DESCARTE = "No hay datos que indiquen actividad legítima."
SIN_DICTAMEN = {"tipo": None, "veredicto": None, "motivo": None}
AGRAVANTE = {"tipo": "agravante", "veredicto": "confirmado", "motivo": "2 hosts confirmados en la actividad de la red"}
ATENUANTE = {"tipo": "atenuante", "veredicto": "indeterminado", "motivo": "2 clasificaciones falso_positivo"}


def _confianza(veredicto, total=None, maximo=135, umbrales=(), verificacion="verificado", descarte=DESCARTE) -> dict:
    return calcular_confianza(veredicto, total, maximo if total is not None else None, list(umbrales), verificacion, descarte)


def _accion(veredicto, total=None, maximo=135, dictamen=SIN_DICTAMEN, hitl=0.70, vlan=0.95, **kw) -> dict:
    return decidir(_confianza(veredicto, total, maximo, **kw), dictamen, hitl, vlan)


# ---- Tabla de casos del plan (Paso 2) ----

@pytest.mark.parametrize(("veredicto", "total", "umbrales", "verificacion", "esperada", "accion"), [
    ("confirmado", 135, (), "verificado", 1.00, "cuarentena_vlan"),
    ("confirmado", 123, (), "verificado", 0.96, "cuarentena_vlan"),
    ("confirmado", 120, (), "verificado", 0.94, "cuarentena_puertos"),
    ("confirmado", None, ("M1", "M2", "M4"), "verificado", 0.72, "cuarentena_puertos"),
    ("confirmado", 60, (), "posible_alucinacion", 0.52, "mantener_drop"),
    ("falso_positivo", 60, (), "verificado", 0.78, "limitar_tasa"),
    ("falso_positivo", 135, (), "verificado", 0.50, "mantener_drop"),
])
def test_casos_de_la_tabla(veredicto, total, umbrales, verificacion, esperada, accion):
    confianza = _confianza(veredicto, total, umbrales=umbrales, verificacion=verificacion)
    assert confianza["total"] == esperada
    assert decidir(confianza, SIN_DICTAMEN, 0.70, 0.95)["accion_recomendada"] == accion


def test_el_desglose_se_recalcula_a_mano():
    c = _confianza("falso_positivo", 60)
    assert (c["s"], c["fuente_ryu"], c["termino_ryu"], c["termino_veredicto"], c["penalizacion"]) == (
        0.4444, "puntaje_ryu", 0.2778, 0.5, 0.0)
    assert round(c["termino_ryu"] + c["termino_veredicto"] - c["penalizacion"], 2) == c["total"] == 0.78
    assert c["formula"] == "falso_positivo: 0.5·(1 − 60/135) + 0.5 − 0.00 = 0.7778 → 0.78"


# ---- Bordes de la escalera ----

@pytest.mark.parametrize(("total", "maximo", "esperada", "accion"), [
    (38, 100, 0.69, "mantener_drop"),
    (39, 100, 0.70, "cuarentena_puertos"),  # 0.695 exacto → redondeo mitad hacia arriba → 0.70 (entra)
    (40, 100, 0.70, "cuarentena_puertos"),
    (90, 100, 0.95, "cuarentena_puertos"),  # 0.95 todavía es cuarentena_puertos (≤ VLAN)
    (122, 135, 0.95, "cuarentena_puertos"),
    (92, 100, 0.96, "cuarentena_vlan"),
])
def test_bordes_confirmado(total, maximo, esperada, accion):
    resultado = _accion("confirmado", total, maximo)
    assert (resultado["escalon_por_confianza"], _confianza("confirmado", total, maximo)["total"]) == (accion, esperada)


def test_bordes_falso_positivo_en_hitl():
    assert _confianza("falso_positivo", 61, 100)["total"] == 0.70  # 0.5·(1 − 0.61) + 0.5 = 0.695 → 0.70
    assert _accion("falso_positivo", 61, 100)["accion_recomendada"] == "limitar_tasa"
    assert _confianza("falso_positivo", 62, 100)["total"] == 0.69
    assert _accion("falso_positivo", 62, 100)["accion_recomendada"] == "mantener_drop"  # 0.69


def test_umbrales_configurables():
    assert _accion("confirmado", 135, hitl=0.70, vlan=1.0)["accion_recomendada"] == "cuarentena_puertos"
    assert _accion("falso_positivo", 60, hitl=0.80)["accion_recomendada"] == "mantener_drop"


def test_total_acotado_entre_0_y_1():
    assert _confianza("confirmado", 200, 135)["total"] == 1.0
    assert _confianza("falso_positivo", 300, 135, verificacion="posible_alucinacion", descarte="")["total"] == 0.0


# ---- Reglas del diseño ----

def test_benigno_con_ryu_en_135_da_mantener_drop():
    assert _accion("falso_positivo", 135)["accion_recomendada"] == "mantener_drop"


def test_confirmado_con_ryu_en_60_da_cuarentena_puertos():
    assert _accion("confirmado", 60)["accion_recomendada"] == "cuarentena_puertos"


@pytest.mark.parametrize("total", range(0, 136, 5))
def test_indeterminado_siempre_da_mantener_drop(total):
    confianza = _confianza("indeterminado", total)
    assert confianza["total"] is None and confianza["termino_ryu"] is None
    assert decidir(confianza, SIN_DICTAMEN, 0.5, 1.0)["accion_recomendada"] == "mantener_drop"


def test_confirmado_nunca_da_limitar_tasa():
    for total in range(0, 136):
        for verificacion in ("verificado", "posible_alucinacion"):
            for dictamen in (SIN_DICTAMEN, AGRAVANTE, ATENUANTE):
                for hitl, vlan in ((0.5, 0.51), (0.70, 0.95), (0.99, 1.0)):
                    r = _accion("confirmado", total, dictamen=dictamen, hitl=hitl, vlan=vlan, verificacion=verificacion)
                    assert "limitar_tasa" not in (r["escalon_por_confianza"], r["accion_recomendada"])


def test_agravante_con_confianza_alta_da_mantener_drop():
    r = _accion("confirmado", 135, dictamen=AGRAVANTE)
    assert (r["escalon_por_confianza"], r["accion_recomendada"], r["modificada_por_dictamen"]) == (
        "cuarentena_vlan", "mantener_drop", True)
    assert r["motivo_modificacion"].startswith("dictamen agravante")
    assert _accion("falso_positivo", 60, dictamen=AGRAVANTE)["accion_recomendada"] == "mantener_drop"


def test_atenuante_con_confianza_alta_como_maximo_limitar_tasa():
    r = _accion("falso_positivo", 60, dictamen=ATENUANTE)
    assert (r["accion_recomendada"], r["modificada_por_dictamen"]) == ("limitar_tasa", False)


def test_atenuante_con_salida_confirmado_da_mantener_drop_decide_el_humano():
    for total in (60, 120, 135):  # cuarentena_puertos y cuarentena_vlan
        r = _accion("confirmado", total, dictamen=ATENUANTE)
        assert (r["accion_recomendada"], r["modificada_por_dictamen"], r["motivo_modificacion"]) == (
            "mantener_drop", True, MOTIVO_CONTRADICCION)


def test_respaldo_por_reglas_usa_el_veredicto_de_las_reglas():
    # Las reglas nunca dan falso_positivo: confirmado (≥ 2 métricas) o indeterminado → nunca limitar_tasa.
    assert _confianza("confirmado", umbrales=("M1", "M2", "M3"))["veredicto_usado"] == "confirmado"
    assert _accion("confirmado", umbrales=("M1", "M2", "M3"))["accion_recomendada"] == "cuarentena_puertos"  # 0.80


def test_alerta_sin_puntaje_usa_los_pesos_de_c1():
    c = _confianza("confirmado", umbrales=("M1", "M2", "M4"))
    assert (c["fuente_ryu"], c["s"], c["total"]) == ("pesos_c1", 0.4444, 0.72)
    assert c["formula"].startswith("confirmado: 0.5·(60/135)")
    assert _confianza("confirmado", umbrales=("M1", "M2", "M3", "M4", "M5"))["total"] == 1.0


def test_verificador_fallido_y_descarte_corto_penalizan_una_sola_vez():
    solo_alucinacion = _confianza("confirmado", 135, verificacion="posible_alucinacion")
    solo_descarte = _confianza("confirmado", 135, descarte="  N/A  ")
    ambos = _confianza("confirmado", 135, verificacion="posible_alucinacion", descarte="")
    assert solo_alucinacion["total"] == solo_descarte["total"] == ambos["total"] == 0.80
    assert len(ambos["motivos_penalizacion"]) == 2 and ambos["penalizacion"] == 0.2
    assert _confianza("confirmado", 135, descarte="x" * 20)["penalizacion"] == 0.0  # 20 caracteres alcanzan


def test_texto_de_la_accion():
    assert escalera.texto_accion("limitar_tasa", 0.78) == "Limitar tasa (confianza 0.78)"
    assert escalera.texto_accion("mantener_drop", None) == "Mantener DROP total (diagnóstico indeterminado)"


def test_la_escalera_es_pura():
    arbol = ast.parse(inspect.getsource(escalera))
    modulos = {n.module for n in ast.walk(arbol) if isinstance(n, ast.ImportFrom)}
    modulos |= {a.name for n in ast.walk(arbol) if isinstance(n, ast.Import) for a in n.names}
    assert modulos <= {"decimal", "typing"}, modulos
