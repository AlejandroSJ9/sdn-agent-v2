"""Informe auditable (Fase 6.8b): lo arma el código con los valores exactos de la alerta."""

import json
from pathlib import Path

import httpx
import pytest

from app.integrations.ryu import ClienteRyu
from app.schemas.alerta import AlertaEntrada
from app.schemas.explicacion import SalidaLLMv3
from app.schemas.informe import NO_DISPONIBLE
from app.services import evidencia, informe, operador

RAIZ = Path(__file__).resolve().parents[2]
CASOS = {c["id"]: c for c in json.loads((RAIZ / "tests/escenarios/escenarios.json").read_text(encoding="utf-8"))["casos"]}
CONTRATO = json.loads((RAIZ / "tests/contrato/alerta_ryu_real.json").read_text(encoding="utf-8"))
SALIDA = SalidaLLMv3.model_validate({
    "veredicto": "indeterminado", "severidad": "media", "confianza": 0.7, "tecnica_id": "T1046",
    "hipotesis_fase": "Descubrimiento.", "factores_ponderados": [{"factor": "a", "peso": "alto", "porque": "b"}] * 2,
    "contexto_aplicado": "sin contexto disponible", "descarte_benigno": "c", "limitaciones": ["d"],
    "accion_propuesta": "mantener", "justificacion_accion": "e", "recomendacion_operador": "f",
})
DICTAMEN = {"tipo": "atenuante", "veredicto": "indeterminado", "motivo": "3 clasificaciones falso_positivo del operador"}
VERIFICADO = {"resultado": "verificado", "numeros_no_respaldados": []}
TRAZA = {
    "incidente_id": 54, "hash_auditoria_explicacion": "a" * 64, "t_ryu": "2026-10-11T18:02:10Z", "t_explicado": "2026-10-11T18:02:13Z",
    "modelo": "modelo-de-prueba", "fuente_explicacion": "llm", "version_prompt": "v3.1-contexto-historial", "variante": "con_contexto",
    "veredicto_llm": "indeterminado", "veredicto_final": "indeterminado", "accion_propuesta": "pasar_a_cuarentena",
    "origen_accion": "dictamen", "confianza_accion": 0.6, "justificacion_accion": "2 o más falsos positivos",
    "decision_politica": {"resultado": "recomendar", "motivos": ["confianza por debajo del umbral"]}, "accion_final": "recomendada_simulada",
}


def _armar(payload, laboratorio=True, tecnica=("T1046", False)) -> dict:
    alerta = AlertaEntrada.model_validate(payload)
    umbrales, fuente = evidencia.umbrales_vigentes(laboratorio)
    return informe.ensamblar(alerta, evidencia.evaluar(alerta.metricas, umbrales), fuente, SALIDA, tecnica, DICTAMEN,
                             VERIFICADO, TRAZA)


def test_lg05_valores_exactos_desviaciones_y_solidez():
    armado = _armar(CASOS["LG-05"]["payload"])
    e = armado["evidencia_objetiva"]
    assert e["fuente_umbrales"] == "escenarios_v1 (laboratorio)" and e["host_origen"] == "10.0.10.1"
    assert e["metricas"] == {
        "M1_nuevos_flujos_por_30s": {"valor": 22, "umbral": 20, "comparacion": ">", "desviacion": 1.1, "supera": True},
        "M2_destinos_unicos_por_30s": {"valor": 17, "umbral": 15, "comparacion": ">", "desviacion": 1.13, "supera": True},
        "M4_packet_in_por_min": {"valor": 212.0, "umbral": 200, "comparacion": ">", "desviacion": 1.06, "supera": True},
        "M5_count": {"valor": 2, "umbral": 5, "comparacion": ">=", "desviacion": 0.4, "supera": False},
    }
    assert e["discrepancias"] == [] and e["umbrales_superados_ryu"] == ["M1", "M2", "M4"]
    assert armado["razonamiento"]["solidez_evidencia"] == {"valor": 0.6, "formula": "umbrales superados / 5 = 3/5 (la alerta no trae puntaje)"}
    assert armado["razonamiento"]["confianza_modelo"] == {"valor": 0.7, "rotulo": "declarada por el modelo"}
    assert armado["mitre"] == {"tecnica_id": "T1046", "nombre": "Network Service Discovery", "tactica": "Discovery (TA0007)",
                               "tecnica_corregida": False, "hipotesis_fase": "Descubrimiento."}
    assert armado["trazabilidad_y_accion"]["reversible"] is True and armado["razonamiento"]["dictamen_historial"] == DICTAMEN


def test_campos_sin_telemetria_no_disponibles():
    e = _armar(CASOS["AT-01"]["payload"])["evidencia_objetiva"]
    for campo in ("destinos", "puertos", "protocolo", "duracion_sesiones", "varianza_intervalos", "linea_base"):
        assert e[campo] == NO_DISPONIBLE == "no disponible en la telemetría actual"
    assert (e["puntaje_total"], e["puntaje_maximo"]) == (None, None)


def test_solidez_con_puntaje_de_ryu():
    armado = _armar(CONTRATO, laboratorio=False)  # Ryu no disponible en las pruebas → umbrales de ajustes.md
    assert armado["evidencia_objetiva"]["fuente_umbrales"] == "ajustes_por_defecto"
    assert (armado["evidencia_objetiva"]["puntaje_total"], armado["evidencia_objetiva"]["puntaje_maximo"]) == (85, 135)
    assert armado["razonamiento"]["solidez_evidencia"] == {"valor": 0.63, "formula": "puntaje_total / puntaje_maximo = 85/135"}


def test_umbrales_de_ryu_si_responde(monkeypatch):
    umbrales = {"M1": 30, "M2": 8, "M3_DURACION_S": 5, "M3_BYTES": 512000, "M4": 200, "M5": 5, "SCORE_SOSPECHOSO": 30, "SCORE_CRITICO": 60}
    cliente = ClienteRyu("http://ryu.test", "t", transporte=httpx.MockTransport(lambda p: httpx.Response(200, json=umbrales)))
    monkeypatch.setattr(operador, "obtener_cliente_ryu", lambda: cliente)
    armado = _armar(CONTRATO, laboratorio=False)
    assert armado["evidencia_objetiva"]["fuente_umbrales"] == "ryu"
    m1 = armado["evidencia_objetiva"]["metricas"]["M1_nuevos_flujos_por_30s"]
    assert (m1["umbral"], m1["desviacion"], m1["supera"]) == (30, 0.8, False)
    assert armado["evidencia_objetiva"]["discrepancias"] == ["M1_nuevos_flujos_por_30s"]  # Ryu lo marcó como superado


@pytest.mark.parametrize(("propuesta", "esperada"), [
    ("T1021.002", ("T1021.002", False)), ("T1570", ("T1570", False)), ("T9999", ("T1021", True)),
    ("T1021.999", ("T1021", True)), (None, ("T1021", True)),
])
def test_tecnica_fuera_de_la_lista_pasa_a_t1021(propuesta, esperada):
    assert informe.normalizar_tecnica(propuesta) == esperada
    armado = _armar(CASOS["AT-01"]["payload"], tecnica=esperada)
    assert armado["mitre"]["nombre"] == informe.CATALOGO_MITRE[esperada[0]][0]


def test_resumen_y_lineas_del_correo():
    armado = _armar(CASOS["LG-05"]["payload"])
    resumen = informe.resumen(armado, "media")
    assert len(resumen) <= 600 and resumen.startswith("Indeterminado (media) en 10.0.10.1: supera M1 22/20 (×1.10)")
    assert "Dictamen atenuante: 3 clasificaciones falso_positivo del operador." in resumen
    lineas = informe.lineas(armado, "media")
    assert len(lineas) == 4 and [l.split(":")[0] for l in lineas] == ["Evidencia", "MITRE", "Razonamiento", "Acción"]
    assert "recomendación pendiente: pasar a cuarentena (simulado)" in lineas[3]
