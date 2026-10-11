"""La migración de escenarios v1 → C1 conserva las etiquetas y es coherente con las reglas."""

import json
from pathlib import Path

import pytest

from app.schemas.alerta import AlertaEntrada

RAIZ = Path(__file__).resolve().parents[2]
V2 = json.loads((RAIZ / "tests/escenarios/escenarios.json").read_text(encoding="utf-8"))
V1 = {c["id"]: c for c in json.loads((RAIZ / "docs/referencia/escenarios_v1.json").read_text(encoding="utf-8"))["casos"]}
CASOS = V2["casos"]


def _umbrales_segun_reglas(m: dict) -> list[str]:
    """Umbrales de la v1: M1 > 20, M2 > 15, M3 verdadero, M4 > 200, M5 >= 5."""
    pares = [
        ("M1", m["M1_nuevos_flujos_por_30s"] > 20),
        ("M2", m["M2_destinos_unicos_por_30s"] > 15),
        ("M3", m["M3_smb_anomalo"]),
        ("M4", m["M4_packet_in_por_min"] > 200),
        ("M5", m["M5_count"] >= 5),
    ]
    return [nombre for nombre, superado in pares if superado]


def test_estan_los_18_casos_de_la_v1():
    assert len(CASOS) == 18
    assert [c["id"] for c in CASOS] == list(V1)


@pytest.mark.parametrize("caso", CASOS, ids=[c["id"] for c in CASOS])
def test_payload_valida_contra_c1_y_conserva_la_v1(caso):
    original = V1[caso["id"]]
    alerta = AlertaEntrada.model_validate(caso["payload"])

    assert alerta.host_origen == original["alerta"]["host"]
    assert alerta.metricas.M1_nuevos_flujos_por_30s == original["alerta"]["M1"]
    assert alerta.metricas.M5_flujos_east_west is (original["alerta"]["M5_count"] >= 5)
    assert caso["payload"]["umbrales_superados"] == _umbrales_segun_reglas(caso["payload"]["metricas"])
    for clave in ("conjunto", "clase", "nombre", "justificacion"):
        assert caso[clave] == original[clave]
    assert caso["esperado"] == {"veredicto": original["esperado"]["veredicto"], "aceptables": original["esperado"]["aceptables"]}


@pytest.mark.parametrize("caso", CASOS, ids=[c["id"] for c in CASOS])
def test_decidibilidad(caso):
    if V1[caso["id"]]["contexto"]:
        assert caso["decidible_v2"] is False
    assert bool(caso["motivo_no_decidible"]) is (not caso["decidible_v2"])
    assert "contexto" not in caso["payload"]  # el contexto nunca viaja en la alerta


def test_du01_es_un_caso_de_agrupacion():
    (du,) = [c for c in CASOS if c["id"] == "DU-01"]
    assert du["alertas_a_enviar"] == 2 and du["decidible_v2"] is False
    assert all(c["alertas_a_enviar"] == 1 for c in CASOS if c["id"] != "DU-01")


def test_reparto_de_decidibles():
    decidibles = [c for c in CASOS if c["decidible_v2"]]
    assert len(decidibles) == 10
    assert sum(c["conjunto"] == "desarrollo" for c in decidibles) == 6
    assert sum(c["conjunto"] == "validacion" for c in decidibles) == 4
