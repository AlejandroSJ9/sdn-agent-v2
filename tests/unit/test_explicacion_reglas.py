"""Respaldo por reglas y schema C4, sin LLM ni base de datos."""

import pytest
from pydantic import ValidationError

from app.schemas.alerta import AlertaEntrada
from app.schemas.explicacion import ExplicacionC4
from app.services.explicacion_reglas import explicar_por_reglas


@pytest.mark.parametrize(
    ("umbrales", "severidad", "veredicto", "confianza", "mitre"),
    [
        ([], "media", "indeterminado", 0.4, "T1046"),
        (["M1"], "media", "indeterminado", 0.4, "T1046"),
        (["M5"], "media", "indeterminado", 0.4, "T1021"),
        (["M1", "M1", "X9"], "media", "indeterminado", 0.4, "T1046"),  # cuenta umbrales distintos y válidos
        (["M1", "M5"], "alta", "confirmado", 0.6, "T1021"),
        (["M1", "M2", "M3"], "critica", "confirmado", 0.6, "T1021.002"),
        (["M5", "M4", "M3", "M2", "M1"], "critica", "confirmado", 0.6, "T1021.002"),
    ],
)
def test_reglas_por_numero_de_umbrales(payload_c1, umbrales, severidad, veredicto, confianza, mitre):
    payload_c1["umbrales_superados"] = umbrales
    salida = explicar_por_reglas(AlertaEntrada.model_validate(payload_c1))

    assert (salida.severidad, salida.veredicto, salida.confianza, salida.tecnica_mitre) == (
        severidad,
        veredicto,
        confianza,
        mitre,
    )
    assert ExplicacionC4.model_validate(salida.model_dump()) == salida  # siempre valida contra C4


def test_evidencias_con_valores(payload_c1):
    salida = explicar_por_reglas(AlertaEntrada.model_validate(payload_c1))
    assert salida.evidencias == [
        "M1=42 flujos nuevos en 30 s",
        "M2=9 destinos únicos en 30 s",
        "M3=tráfico SMB anómalo detectado",
    ]
    assert "10.0.0.3" in salida.explicacion


def test_schema_c4_recorta_textos_y_rechaza_fuera_de_rango():
    base = {
        "veredicto": "confirmado",
        "severidad": "alta",
        "confianza": 0.9,
        "tecnica_mitre": "T1021.002",
        "explicacion": "x" * 700,
        "evidencias": [f"e{i}" for i in range(8)],
        "recomendacion_operador": "y" * 400,
        "accion": "desbloquear",  # clave extra: se ignora
    }
    salida = ExplicacionC4.model_validate(base)
    assert len(salida.explicacion) == 600 and len(salida.recomendacion_operador) == 300
    assert len(salida.evidencias) == 5
    assert "accion" not in salida.model_dump()

    for campo, valor in [("confianza", 1.5), ("veredicto", "bloquear"), ("severidad", "baja"), ("tecnica_mitre", "xx")]:
        with pytest.raises(ValidationError):
            ExplicacionC4.model_validate({**base, campo: valor})
