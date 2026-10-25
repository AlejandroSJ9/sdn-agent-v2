"""Prompts v4.0 / v4.1 (Fase D3): v3 intacto + explicación simple, glosario, cifras y selección por PROMPT_V4."""

import pytest

from app.services import prompt
from app.services.prompt import SYSTEM_PROMPT_V30, SYSTEM_PROMPT_V31
from app.services.prompt_v4 import CAMPOS_FIN_V3, SYSTEM_PROMPT_V40, SYSTEM_PROMPT_V41

PARES = [(SYSTEM_PROMPT_V30, SYSTEM_PROMPT_V40), (SYSTEM_PROMPT_V31, SYSTEM_PROMPT_V41)]
GLOSARIO = ("conexiones nuevas que abrió el equipo en 30 segundos", "equipos distintos a los que intentó conectarse",
            "servicio de carpetas compartidas de Windows", "obligó a la red a decidir qué hacer con una conexión desconocida",
            "conexiones directas entre equipos internos", "intentar saltar a otros equipos de la red", "falsa alarma")


@pytest.mark.parametrize(("v3", "v4"), PARES)
def test_v4_conserva_cada_linea_de_v3(v3, v4):
    """El informe técnico no cambia: solo la línea de recomendacion_operador gana una coma."""
    faltan = [linea for linea in v3.splitlines() if linea not in v4.splitlines()]
    assert faltan == [CAMPOS_FIN_V3]


@pytest.mark.parametrize(("v3", "v4"), PARES)
def test_v4_pide_los_tres_bloques_con_glosario_y_reglas_de_cifras(v3, v4):
    salida = v4.split("Responde SOLO")[1]
    for campo in ("que_paso", "por_que", "podria_equivocarnos"):
        assert f'"{campo}"' in salida and f'"{campo}"' not in v3
    assert "máximo 250 caracteres" in salida and "cada una de máximo 180 caracteres" in salida
    for termino in GLOSARIO:
        assert termino in v4, termino
    assert '"lo esperado es N o menos"' in v4 and '"lo esperado es menos de N"' in v4
    assert "lo normal" not in v4
    assert "No digas qué hacer: la recomendación la arma el sistema." in v4
    reglas = v4.index("- Explicación simple:")
    assert reglas < v4.index('- Basa tu análisis únicamente en esos datos.')  # antes de la última regla de v3


def test_solo_v41_trae_el_glosario_del_historial():
    assert "un administrador ya revisó un caso igual" in SYSTEM_PROMPT_V41
    assert "un administrador ya revisó un caso igual" not in SYSTEM_PROMPT_V40


def test_seleccion_de_version_y_prompt():
    assert prompt.system_prompt(False) is prompt.SYSTEM_PROMPT and prompt.system_prompt(True) is prompt.SYSTEM_PROMPT_CONTEXTO
    assert prompt.system_prompt(False, v4=True) == SYSTEM_PROMPT_V40
    assert prompt.system_prompt(True, v4=True) == SYSTEM_PROMPT_V41
    assert [prompt.version(v, f) for v in ("sin_contexto", "con_contexto") for f in (False, True)] == [
        "v3.0-sin-contexto", "v4.0-sin-contexto", "v3.1-contexto-historial", "v4.1-contexto-historial"]
    assert prompt.es_v4("v4.1-contexto-historial") and not prompt.es_v4("v3.1-contexto-historial")
    assert not prompt.es_v4(None) and not prompt.es_v4("v2.2-sin-contexto")
