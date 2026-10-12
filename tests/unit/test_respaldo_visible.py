"""QA F6: el respaldo por reglas se ve con su motivo legible (un solo mapeo) en la API, el informe y el correo."""

import re

import pytest

from app.schemas.alerta import MOTIVO_NO_REGISTRADO, MOTIVOS_RESPALDO, IncidenteSalida, motivo_legible
from app.services import informe
from app.services.notificacion import SUFIJO_RESPALDO, construir_payload
from test_informe import CASOS, TRAZA, _armar
from test_notificacion_payload import _incidente, _valida_c3

# Todos los motivos que puede guardar el pipeline (pipeline._intentar_llm, LLMError y error_interno).
MOTIVOS_DEL_PIPELINE = ["laboratorio_sin_llm", "llm_deshabilitado", "sin_api_key", "sin_cupo", "timeout",
                        "error_llm", "json_invalido", "schema_invalido", "error_interno"]


@pytest.mark.parametrize("motivo", MOTIVOS_DEL_PIPELINE)
def test_cada_motivo_tiene_texto_legible(motivo):
    assert motivo_legible(motivo) == MOTIVOS_RESPALDO[motivo] != MOTIVO_NO_REGISTRADO


def test_textos_del_mapeo():
    assert motivo_legible("timeout") == "el modelo no respondió a tiempo"
    assert motivo_legible("sin_cupo") == "se agotó el cupo diario del LLM"
    assert motivo_legible("llm_deshabilitado") == "el LLM está deshabilitado desde el panel"
    assert motivo_legible(None) == motivo_legible("otro_desconocido") == MOTIVO_NO_REGISTRADO


def test_incidente_salida_expone_el_motivo_legible_solo_en_respaldo():
    reglas = _incidente(motivo_respaldo="sin_cupo").model_dump(mode="json")
    assert (reglas["motivo_respaldo"], reglas["motivo_respaldo_legible"]) == ("sin_cupo", "se agotó el cupo diario del LLM")
    llm = _incidente(fuente_explicacion="llm").model_dump(mode="json")
    assert (llm["motivo_respaldo"], llm["motivo_respaldo_legible"]) == (None, None)
    sin_explicar = _incidente(fuente_explicacion=None).model_dump(mode="json")
    assert sin_explicar["motivo_respaldo_legible"] is None


def _con_informe(fuente: str, motivo: str | None) -> IncidenteSalida:
    armado = _armar(CASOS["LG-05"]["payload"])
    armado["trazabilidad_y_accion"]["fuente_explicacion"] = fuente
    return _incidente(fuente_explicacion=fuente, motivo_respaldo=motivo, informe=armado, severidad="media")


def test_correo_con_informe_en_respaldo_lleva_sufijo_y_primera_linea():
    payload = construir_payload(_con_informe("reglas", "timeout"), "incidente")
    _valida_c3(payload)
    assert payload.asunto == "[SDN] Host 10.0.0.3 aislado por posible movimiento lateral" + SUFIJO_RESPALDO
    lineas = payload.mensaje.splitlines()
    assert lineas[0] == "Explicación de respaldo por reglas: el modelo no respondió a tiempo. Revisar en el panel."
    assert [l.split(":")[0] for l in lineas[1:]] == ["Evidencia", "MITRE", "Razonamiento", "Acción", "Informe completo"]
    assert "modelo-de-prueba" not in payload.mensaje


def test_correo_con_llm_no_cambia():
    payload = construir_payload(_con_informe("llm", None), "incidente")
    assert payload.asunto == "[SDN] Host 10.0.0.3 aislado por posible movimiento lateral"
    assert [l.split(":")[0] for l in payload.mensaje.splitlines()] == [
        "Evidencia", "MITRE", "Razonamiento", "Acción", "Informe completo"]
    assert "respaldo" not in payload.mensaje


def test_correo_antiguo_en_respaldo_sigue_en_6_lineas():
    payload = construir_payload(_incidente(motivo_respaldo="json_invalido"), "incidente")
    _valida_c3(payload)
    assert payload.mensaje.splitlines()[0] == (
        "Explicación de respaldo por reglas: el modelo devolvió una respuesta que no es JSON. Revisar en el panel.")


def test_razonamiento_en_respaldo_no_atribuye_veredicto_al_llm():
    armado = _armar(CASOS["LG-05"]["payload"])
    armado["trazabilidad_y_accion"] = {**TRAZA, "fuente_explicacion": "reglas"}
    linea = informe.lineas(armado, "media")[2]
    assert linea == ("Razonamiento: veredicto final indeterminado (sin LLM: respaldo por reglas · dictamen atenuante) · "
                     "confianza declarada 0.70 · verificado")
    assert not re.search(r"LLM: (confirmado|falso_positivo|indeterminado|ya_contenido)", linea)
    assert TRAZA["modelo"] not in linea


def test_razonamiento_con_llm_queda_identico():
    linea = informe.lineas(_armar(CASOS["LG-05"]["payload"]), "media")[2]
    assert linea == ("Razonamiento: veredicto final indeterminado (LLM: indeterminado · dictamen atenuante) · "
                     "confianza declarada 0.70 · verificado")


# ---- QA F6 (2): la exactitud del LLM solo cuenta filas explicadas por el LLM ----

def test_metricas_xai_no_cuentan_respaldos_como_acierto_ni_fallo():
    from app.services.resultados_lab import metricas_xai

    casos = {"X": {"esperado": {"aceptables": ["confirmado"]}}}
    fila = {"caso_id": "X", "dictamen_historial": None, "verificacion": "verificado"}
    filas = [
        {**fila, "fuente": "llm", "veredicto_llm": "confirmado"},       # acierto del LLM
        {**fila, "fuente": "llm", "veredicto_llm": "falso_positivo"},   # fallo del LLM
        {**fila, "fuente": "reglas", "veredicto_llm": None},            # respaldo nuevo
        {**fila, "fuente": "reglas", "veredicto_llm": "confirmado"},    # respaldo antiguo (veredicto de reglas)
    ]
    xai = metricas_xai(filas, casos)
    assert xai["exactitud_veredicto_llm"] == {"aciertos": 1, "total": 2, "exactitud": 0.5}
    assert xai["sin_veredicto_llm"] == 2
    assert xai["cobertura_llm"] == {"llm": 2, "total": 4, "porcentaje": 50.0}
    assert metricas_xai([], casos)["cobertura_llm"] == {"llm": 0, "total": 0, "porcentaje": None}
