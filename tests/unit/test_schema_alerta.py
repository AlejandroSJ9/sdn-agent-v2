"""Validación del payload C1 (AlertaEntrada), sin red ni base de datos."""

import pytest
from pydantic import ValidationError

from app.schemas.alerta import AlertaEntrada


@pytest.mark.parametrize("ip", ["10.0.0.300", "no-es-ip", "::1", "10.0.0", "", 167772163])
def test_ip_invalida(payload_c1, ip):
    payload_c1["host_origen"] = ip
    with pytest.raises(ValidationError):
        AlertaEntrada.model_validate(payload_c1)


@pytest.mark.parametrize(
    ("metrica", "valor"),
    [
        ("M1_nuevos_flujos_por_30s", -1),
        ("M2_destinos_unicos_por_30s", -1),
        ("M4_packet_in_por_min", -0.5),
        ("M5_count", -1),
        ("M4_packet_in_por_min", float("inf")),
        ("M4_packet_in_por_min", float("nan")),
    ],
)
def test_metrica_invalida(payload_c1, metrica, valor):
    payload_c1["metricas"][metrica] = valor
    with pytest.raises(ValidationError):
        AlertaEntrada.model_validate(payload_c1)


def test_campos_extra_se_guardan_en_crudo_fuera_del_modelo(payload_c1):
    payload_c1["campo_nuevo_ryu"] = {"x": 1}
    payload_c1["metricas"]["M6_futura"] = 3
    alerta = AlertaEntrada.model_validate(payload_c1)

    validado = alerta.model_dump()
    assert "campo_nuevo_ryu" not in validado
    assert "M6_futura" not in validado["metricas"]
    assert alerta.crudo["campo_nuevo_ryu"] == {"x": 1}
    assert alerta.crudo["metricas"]["M6_futura"] == 3
    assert alerta.host_origen == "10.0.0.3"
    assert alerta.timestamp.utcoffset().total_seconds() == 0


def test_solicitud_al_agente_descartada(payload_c1):
    alerta = AlertaEntrada.model_validate(payload_c1)
    assert "solicitud_al_agente" not in alerta.model_dump()
    assert "solicitud_al_agente" not in alerta.crudo
