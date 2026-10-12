"""Reglas con contexto (punto 7 de la Fase 6.7): una prueba por regla, el orden y el caso sin regla."""

import pytest

from app.schemas.alerta import AlertaEntrada
from app.services.explicacion_reglas import explicar_por_reglas


def _alerta(payload_c1, umbrales: list[str]) -> AlertaEntrada:
    return AlertaEntrada.model_validate({**payload_c1, "umbrales_superados": umbrales})


def _contexto(historial=(), actividad=(), confirmado_60=False, amenazas=0) -> dict:
    historial = list(historial)
    return {
        "historial_host": historial,
        "resumen": {"total_previos": len(historial),
                    "falsos_positivos_operador": sum(p.get("clasificacion_operador") == "falso_positivo" for p in historial),
                    "amenazas_contenidas_operador": amenazas, "confirmado_ultimos_60_min": confirmado_60},
        "actividad_red": list(actividad),
    }


def _fp(umbrales) -> dict:
    return {"minutos_antes": 10080, "veredicto_agente": "falso_positivo", "estado_veredicto": "no_verificado",
            "umbrales_superados": umbrales, "clasificacion_operador": "falso_positivo"}


def _otro(host, veredicto="confirmado") -> dict:
    return {"host": host, "minutos_antes": 5, "veredicto": veredicto, "umbrales_superados": ["M1", "M3", "M5"]}


def test_sin_contexto_es_igual_que_antes(payload_c1):
    alerta = _alerta(payload_c1, ["M1", "M2", "M4"])
    assert explicar_por_reglas(alerta, None) == explicar_por_reglas(alerta)


@pytest.mark.parametrize("contexto", [_contexto(confirmado_60=True), _contexto(amenazas=1)])
def test_regla_1_confirmado_reciente_o_amenaza_contenida(payload_c1, contexto):
    explicacion = explicar_por_reglas(_alerta(payload_c1, ["M4"]), contexto)  # sin contexto sería indeterminado
    assert explicacion.veredicto == "confirmado"
    assert explicacion.factores_ponderados[0].factor == "Dictamen agravante del historial"  # Fase 6.8a: vía dictaminar
    assert explicacion.accion_propuesta == "mantener"


def test_regla_2_dos_falsos_positivos_con_los_mismos_umbrales(payload_c1):
    explicacion = explicar_por_reglas(_alerta(payload_c1, ["M1", "M2", "M4"]), _contexto([_fp(["M1", "M2", "M4"])] * 3))
    assert explicacion.veredicto == "indeterminado" and explicacion.confianza == 0.6  # Fase 6.8: confianza fija
    assert explicacion.factores_ponderados[0].porque == "3 clasificaciones falso_positivo del operador con los mismos umbrales (M1, M2, M4)"
    assert explicacion.accion_propuesta == "pasar_a_cuarentena"


def test_regla_2_no_aplica_con_otros_umbrales_o_un_solo_falso_positivo(payload_c1):
    alerta = _alerta(payload_c1, ["M1", "M3", "M5"])
    assert explicar_por_reglas(alerta, _contexto([_fp(["M1", "M2", "M4"])] * 3)).veredicto == "confirmado"
    alerta = _alerta(payload_c1, ["M1", "M2", "M4"])
    assert explicar_por_reglas(alerta, _contexto([_fp(["M1", "M2", "M4"])])).veredicto == "confirmado"


def test_regla_3_dos_hosts_confirmados_en_la_ventana(payload_c1):
    alerta = _alerta(payload_c1, ["M4"])
    assert explicar_por_reglas(alerta, _contexto(actividad=[_otro("10.0.10.1"), _otro("10.0.10.2")])).veredicto == "confirmado"
    # Mismo host repetido o un solo host: no aplica
    assert explicar_por_reglas(alerta, _contexto(actividad=[_otro("10.0.10.1")] * 3)).veredicto == "indeterminado"
    assert explicar_por_reglas(alerta, _contexto(actividad=[_otro("10.0.10.1"), _otro("10.0.10.2", "indeterminado")])).veredicto == "indeterminado"


def test_orden_la_regla_1_gana_a_la_2(payload_c1):
    contexto = _contexto([_fp(["M1", "M2", "M4"])] * 2, confirmado_60=True)
    assert explicar_por_reglas(_alerta(payload_c1, ["M1", "M2", "M4"]), contexto).veredicto == "confirmado"


def test_ninguna_regla_aplica_resultado_igual_que_sin_contexto(payload_c1):
    alerta = _alerta(payload_c1, ["M1", "M3", "M5"])
    con, sin = explicar_por_reglas(alerta, _contexto()), explicar_por_reglas(alerta)
    # Fase 6.8b: solo cambia el texto de contexto_aplicado (sin contexto: "sin contexto disponible")
    assert con.model_dump(exclude={"contexto_aplicado"}) == sin.model_dump(exclude={"contexto_aplicado"})
    assert con.contexto_aplicado.startswith("Reglas: el historial") and sin.contexto_aplicado == "sin contexto disponible"


def test_el_factor_del_dictamen_sobrevive_al_recorte_a_4(payload_c1):
    alerta = _alerta(payload_c1, ["M1", "M2", "M3", "M4", "M5"])
    explicacion = explicar_por_reglas(alerta, _contexto(amenazas=1))
    assert len(explicacion.factores_ponderados) == 4
    assert explicacion.factores_ponderados[0].factor == "Dictamen agravante del historial"
