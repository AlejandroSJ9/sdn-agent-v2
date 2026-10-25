"""Explicación simple (Fase D3): bloque 3 por código, plantillas de reglas, revisión de los bloques del LLM."""

import itertools

import pytest
from conftest import SALIDA_LLM_V3

from app.schemas.alerta import AlertaEntrada
from app.schemas.explicacion import SalidaLLMv4
from app.services import escalera, evidencia, explicacion_simple as es
from app.services.verificador import JERGA, verificar_bloques

UMBRALES = {"M1": 20, "M2": 8, "M4": 200, "M5": 5}
SIN = {"tipo": None, "veredicto": None, "motivo": None}
AGRAVANTE = {"tipo": "agravante", "veredicto": "confirmado",
             "motivo": "incidente confirmado en los últimos 60 min en el mismo host; 1 amenaza(s) contenida(s) por el "
                       "operador en el host; 3 hosts confirmados en la actividad de la red"}
ATENUANTE = {"tipo": "atenuante", "veredicto": "indeterminado",
             "motivo": "3 clasificaciones falso_positivo del operador con los mismos umbrales (M1, M2, M3)"}


def _bloque3(veredicto, puntaje, dictamen=SIN, umbrales=("M1", "M2", "M3")):
    confianza = escalera.calcular_confianza(veredicto, puntaje, 135 if puntaje is not None else None, list(umbrales),
                                            "verificado", "x" * 40)
    return es.bloque_recomendacion(escalera.decidir(confianza, dictamen, 0.70, 0.95), confianza, dictamen)


def _limpio(texto: str, maximo: int) -> None:
    assert not JERGA.search(texto), texto
    assert "lo normal" not in texto.lower() and len(texto) <= maximo, texto


@pytest.mark.parametrize(("total", "nivel"), [(None, None), (0.69, "baja"), (0.70, "media"), (0.95, "media"), (0.96, "alta")])
def test_confianza_en_palabras_ligada_a_los_escalones(total, nivel):
    assert es.nivel_confianza(total, 0.70, 0.95) == nivel


@pytest.mark.parametrize(("veredicto", "puntaje", "dictamen", "inicio", "cierre"), [
    ("indeterminado", 60, SIN, es.ACCION_INDETERMINADO, es.CIERRE_MANTENER),
    ("confirmado", 0, SIN, es.ACCION_BAJA.format(veredicto="Parece un ataque"), es.CIERRE_MANTENER),
    ("falso_positivo", 60, SIN, es.ACCION["limitar_tasa"].format(nivel="media"), es.CIERRE_RELAJAR),
    ("confirmado", None, SIN, es.ACCION["cuarentena_puertos"].format(nivel="media"), es.CIERRE_RELAJAR),
    ("confirmado", 135, SIN, es.ACCION["cuarentena_vlan"].format(nivel="alta"), es.CIERRE_RELAJAR),
    ("confirmado", None, ATENUANTE, es.CONTRADICCION, es.CIERRE_MANTENER),
    ("falso_positivo", 60, ATENUANTE, es.ACCION["limitar_tasa"].format(nivel="media") + es.ATENUANTE_EXTRA, es.CIERRE_RELAJAR),
])
def test_bloque3_por_accion_y_por_caso_del_dictamen(veredicto, puntaje, dictamen, inicio, cierre):
    assert _bloque3(veredicto, puntaje, dictamen) == inicio + cierre


def test_bloque3_agravante_cambia_la_accion_o_se_suma():
    cambio = _bloque3("confirmado", 135, AGRAVANTE)
    assert cambio.startswith(es.AISLADO + " La confianza permitía moverlo a una red de cuarentena separada")
    for motivo in ("ataque confirmado en la última hora", "ya contuvo un ataque", "otros equipos de la red"):
        assert motivo in cambio
    assert _bloque3("indeterminado", 60, AGRAVANTE).startswith(es.ACCION_INDETERMINADO + " Además, el historial")


def test_bloque3_defensa_confirmado_nunca_limitar_tasa():
    resultado = {"accion_recomendada": "mantener_drop", "escalon_por_confianza": "limitar_tasa",
                 "modificada_por_dictamen": True, "motivo_modificacion": "confirmado nunca limitar_tasa",
                 "umbrales": {"ESCALON_HITL": 0.7, "ESCALON_VLAN": 0.95}}
    texto = es.bloque_recomendacion(resultado, {"total": 0.9, "veredicto_usado": "confirmado"}, SIN)
    assert texto == es.DEFENSA + es.CIERRE_MANTENER


def test_bloque3_nunca_lleva_cifras_ni_jerga_en_ninguna_combinacion():
    for veredicto, puntaje, dictamen in itertools.product(("confirmado", "falso_positivo", "indeterminado"),
                                                         (None, 0, 60, 100, 135), (SIN, AGRAVANTE, ATENUANTE)):
        texto = _bloque3(veredicto, puntaje, dictamen)
        _limpio(texto, 500)  # peor caso: los 3 motivos del agravante (456)
        assert not any(c.isdigit() for c in texto), texto
        assert texto.endswith((es.CIERRE_MANTENER, es.CIERRE_RELAJAR))


def _alerta(payload, **metricas) -> tuple[AlertaEntrada, dict]:
    alerta = AlertaEntrada.model_validate({**payload, "metricas": {**payload["metricas"], **metricas}})
    return alerta, evidencia.evaluar(alerta.metricas, UMBRALES)


def test_por_que_una_frase_por_senal_con_valor_y_lo_esperado(payload_c1):
    alerta, evaluadas = _alerta(payload_c1)  # M1=42, M2=9, M3, M5_count=7 superan; M4=120 no
    assert es.plantilla_por_que(alerta, evaluadas, SIN) == [
        es.SENAL_M3, "Hizo 7 conexiones directas a otros equipos internos; lo esperado es menos de 5.",
        "Abrió 42 conexiones nuevas en 30 segundos; lo esperado es 20 o menos."]
    frases = es.plantilla_por_que(alerta, evaluadas, ATENUANTE)
    assert frases[0] == es.SENAL_ATENUANTE and len(frases) == 3


def test_por_que_con_una_o_ninguna_senal_sigue_teniendo_dos_frases(payload_c1):
    una, evaluadas = _alerta({**payload_c1, "umbrales_superados": ["M2"]}, M1_nuevos_flujos_por_30s=3, M3_smb_anomalo=False,
                             M5_count=0)
    assert es.plantilla_por_que(una, evaluadas, SIN) == [
        "Intentó conectarse a 9 equipos distintos en 30 segundos; lo esperado es 8 o menos.", es.UNA_SENAL]
    ninguna, evaluadas = _alerta({**payload_c1, "umbrales_superados": []}, M1_nuevos_flujos_por_30s=3,
                                 M2_destinos_unicos_por_30s=1, M3_smb_anomalo=False, M5_count=0)
    assert es.plantilla_por_que(ninguna, evaluadas, SIN) == list(es.SIN_SENALES)


def test_plantillas_de_reglas_sin_jerga_y_con_cifras_respaldadas(payload_c1):
    alerta, evaluadas = _alerta(payload_c1, M4_packet_in_por_min=250.0)  # las 5 señales superan
    permitidos, ips = es.respaldados(alerta, evaluadas, 1)
    for veredicto, dictamen in itertools.product(("confirmado", "indeterminado", "falso_positivo"), (SIN, AGRAVANTE, ATENUANTE)):
        que_paso = es.plantilla_que_paso(veredicto, alerta.host_origen, dictamen)
        por_que = es.plantilla_por_que(alerta, evaluadas, dictamen)
        podria = es.plantilla_podria_equivocarnos(veredicto)
        _limpio(que_paso, 250), _limpio(podria, 300)
        assert 2 <= len(por_que) <= 3 and all(_limpio(f, 220) is None for f in por_que)
        assert verificar_bloques({"que_paso": [que_paso], "por_que": por_que, "podria": [podria]}, permitidos, ips) == {}
    assert es.plantilla_que_paso("indeterminado", "10.0.0.3", ATENUANTE).endswith(es.QUE_PASO_ATENUANTE)


def _salida(**bloques) -> SalidaLLMv4:
    return SalidaLLMv4.model_validate({**SALIDA_LLM_V3, **bloques})


BUENOS = {"que_paso": "Este equipo intentó saltar a otros equipos de la red y el sistema lo aisló.",
          "por_que": ["Intentó conectarse a 9 equipos distintos en 30 segundos; lo esperado es 8 o menos.",
                      "Abrió 42 conexiones nuevas en 30 segundos; lo esperado es 20 o menos."],
          "podria_equivocarnos": "Podría ser una copia de seguridad que se conecta a muchos equipos."}


def test_revisar_acepta_bloques_limpios_y_los_de_reglas_salen_de_plantillas():
    permitidos = [9, 8, 42, 20]
    assert es.revisar(_salida(**BUENOS), "llm", "confirmado", permitidos, ["10.0.0.3"]) == {
        "aceptados": {"que_paso": BUENOS["que_paso"], "por_que": BUENOS["por_que"], "podria_equivocarnos": BUENOS["podria_equivocarnos"]},
        "reemplazos": [], "cifras": []}
    assert es.revisar(_salida(**BUENOS), "reglas", "confirmado", permitidos, [])["aceptados"] == dict.fromkeys(es.BLOQUES_LLM)
    assert es.revisar(_salida(), "llm", "confirmado", permitidos, [])["aceptados"] == dict.fromkeys(es.BLOQUES_LLM)


def test_revisar_jerga_reemplaza_sin_penalizar_y_cifra_reemplaza_y_penaliza():
    revision = es.revisar(_salida(**{**BUENOS, "que_paso": "Superó el umbral M2 con flujos east-west.",
                                     "podria_equivocarnos": "Hubo 445 conexiones legítimas."}),
                          "llm", "confirmado", [9, 8, 42, 20], [])
    assert revision["aceptados"]["que_paso"] is None and revision["aceptados"]["podria_equivocarnos"] is None
    assert revision["reemplazos"] == [{"bloque": "que_paso", "motivo": "jerga: umbral, M2, flujos, east-west"},
                                      {"bloque": "podria_equivocarnos", "motivo": "cifra no respaldada: 445"}]
    assert revision["cifras"] == ["445"]
    verificacion = es.penalizar({"resultado": "verificado", "numeros_no_respaldados": []}, revision)
    assert verificacion == {"resultado": "posible_alucinacion", "numeros_no_respaldados": ["445"]}
    sin_cifras = {**revision, "cifras": []}
    assert es.penalizar({"resultado": "verificado", "numeros_no_respaldados": []}, sin_cifras)["resultado"] == "verificado"


def test_que_paso_se_reemplaza_si_el_dictamen_cambio_el_veredicto():
    revision = es.revisar(_salida(**BUENOS), "llm", "indeterminado", [9, 8, 42, 20], [])
    assert revision["reemplazos"] == [{"bloque": "que_paso", "motivo": "el dictamen cambió el veredicto"}]
    assert revision["aceptados"]["por_que"] == BUENOS["por_que"] and revision["cifras"] == []


def test_armar_combina_llm_y_plantillas_con_sus_fuentes(payload_c1):
    alerta, evaluadas = _alerta(payload_c1)
    revision = es.revisar(_salida(que_paso=BUENOS["que_paso"]), "llm", "confirmado", [], [])
    confianza = escalera.calcular_confianza("confirmado", None, None, ["M1", "M2", "M3"], "verificado", "x" * 40)
    resultado = escalera.decidir(confianza, SIN, 0.70, 0.95)
    simple = es.armar(revision, alerta, evaluadas, SIN, "confirmado", confianza, resultado)
    assert simple["que_paso"] == BUENOS["que_paso"]
    assert simple["fuentes"] == {"que_paso": "llm", "por_que": "reglas", "podria_equivocarnos": "reglas"}
    assert simple["por_que"] == es.plantilla_por_que(alerta, evaluadas, SIN)
    assert simple["que_recomendamos"].startswith(es.ACCION["cuarentena_puertos"].format(nivel="media"))
