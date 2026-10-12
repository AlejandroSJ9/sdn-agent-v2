"""Respaldo por reglas y schema de salida v3 (SalidaLLMv3), sin LLM ni base de datos.

Fase 6.8a+b: la salida cambió de estructura (sin explicación ni evidencias libres; factores ponderados,
tecnica_id de lista cerrada, etc.). Estas pruebas reemplazan a las del schema C4 v2.
"""

import pytest
from pydantic import ValidationError

from app.schemas.alerta import AlertaEntrada
from app.schemas.explicacion import SalidaLLMv3
from app.services.explicacion_reglas import explicar_por_reglas


@pytest.mark.parametrize(
    ("umbrales", "severidad", "veredicto", "confianza", "mitre"),
    [
        ([], "media", "indeterminado", 0.6, "T1046"),
        (["M1"], "media", "indeterminado", 0.6, "T1046"),
        (["M5"], "media", "indeterminado", 0.6, "T1021"),
        (["M1", "M1", "X9"], "media", "indeterminado", 0.6, "T1046"),  # cuenta umbrales distintos y válidos
        (["M1", "M5"], "alta", "confirmado", 0.6, "T1021"),
        (["M1", "M2", "M3"], "critica", "confirmado", 0.6, "T1021.002"),
        (["M5", "M4", "M3", "M2", "M1"], "critica", "confirmado", 0.6, "T1021.002"),
    ],
)
def test_reglas_por_numero_de_umbrales(payload_c1, umbrales, severidad, veredicto, confianza, mitre):
    payload_c1["umbrales_superados"] = umbrales
    salida = explicar_por_reglas(AlertaEntrada.model_validate(payload_c1))

    assert (salida.severidad, salida.veredicto, salida.confianza, salida.tecnica_id) == (severidad, veredicto, confianza, mitre)
    assert SalidaLLMv3.model_validate(salida.model_dump()) == salida  # siempre valida contra v3
    assert 2 <= len(salida.factores_ponderados) <= 4
    assert salida.contexto_aplicado == "sin contexto disponible" and salida.accion_propuesta == "mantener"


def test_factores_con_valores_de_la_alerta(payload_c1):
    salida = explicar_por_reglas(AlertaEntrada.model_validate(payload_c1))
    assert [f.porque for f in salida.factores_ponderados] == [
        "M1=42 flujos nuevos en 30 s",
        "M2=9 destinos únicos en 30 s",
        "M3: tráfico SMB anómalo detectado",
    ]
    assert any("no disponible" in limitacion for limitacion in salida.limitaciones)


BASE = {
    "veredicto": "confirmado", "severidad": "alta", "confianza": 0.9, "tecnica_id": "T1021.002",
    "hipotesis_fase": "h" * 400, "factores_ponderados": [{"factor": "f", "peso": "alto", "porque": "p" * 300}] * 6,
    "contexto_aplicado": "c" * 400, "descarte_benigno": "d" * 400, "limitaciones": ["l"],
    "accion_propuesta": "mantener", "justificacion_accion": "j" * 400, "recomendacion_operador": "y" * 400,
    "explicacion": "clave v2: se ignora",
}


def test_schema_v3_recorta_textos_y_rechaza_fuera_de_rango():
    salida = SalidaLLMv3.model_validate(BASE)
    assert len(salida.hipotesis_fase) == 300 and len(salida.recomendacion_operador) == 300
    assert len(salida.contexto_aplicado) == 300 and len(salida.descarte_benigno) == 300
    assert len(salida.factores_ponderados) == 4 and len(salida.factores_ponderados[0].porque) == 200
    assert "explicacion" not in salida.model_dump()

    for campo, valor in [("confianza", 1.5), ("veredicto", "bloquear"), ("severidad", "baja"),
                         ("factores_ponderados", [{"factor": "f", "peso": "alto", "porque": "p"}]),
                         ("factores_ponderados", [{"factor": "f", "peso": "enorme", "porque": "p"}] * 2)]:
        with pytest.raises(ValidationError):
            SalidaLLMv3.model_validate({**BASE, campo: valor})


def test_tecnica_y_accion_se_aceptan_como_texto_para_normalizar_despues():
    salida = SalidaLLMv3.model_validate({**BASE, "tecnica_id": "T9999", "accion_propuesta": 5})
    assert salida.tecnica_id == "T9999" and salida.accion_propuesta is None
