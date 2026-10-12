"""Validación del payload C1 (AlertaEntrada), sin red ni base de datos."""

import pytest
from pydantic import ValidationError

from app.schemas.alerta import AlertaEntrada, describir_errores


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


# ---- Fase 6.6: compatibilidad con la alerta real de Ryu-IA ----

@pytest.mark.parametrize("contexto", ["VLAN OPS (ID=10), subred 10.0.10.0/24", {"vlan": 10}, "x" * 300])
def test_contexto_topologico_texto_u_objeto(payload_c1, contexto):
    payload_c1["contexto_topologico"] = contexto
    assert AlertaEntrada.model_validate(payload_c1).contexto_topologico == contexto


@pytest.mark.parametrize("contexto", ["x" * 301, 10, ["VLAN"]])
def test_contexto_topologico_invalido(payload_c1, contexto):
    payload_c1["contexto_topologico"] = contexto
    with pytest.raises(ValidationError):
        AlertaEntrada.model_validate(payload_c1)


def test_metricas_float_se_normalizan_sin_perder_precision(payload_c1):
    payload_c1["metricas"].update(
        {"M1_nuevos_flujos_por_30s": 24.0, "M2_destinos_unicos_por_30s": 9.0, "M5_count": 6.0, "M4_packet_in_por_min": 38.5}
    )
    metricas = AlertaEntrada.model_validate(payload_c1).metricas
    assert (metricas.M1_nuevos_flujos_por_30s, metricas.M2_destinos_unicos_por_30s, metricas.M5_count) == (24, 9, 6)
    assert type(metricas.M1_nuevos_flujos_por_30s) is int
    assert metricas.M4_packet_in_por_min == 38.5


@pytest.mark.parametrize(
    ("metrica", "valor"),
    [("M1_nuevos_flujos_por_30s", 24.5), ("M2_destinos_unicos_por_30s", float("nan")),
     ("M5_count", float("inf")), ("M1_nuevos_flujos_por_30s", -24.0)],
)
def test_metricas_float_invalidas(payload_c1, metrica, valor):
    payload_c1["metricas"][metrica] = valor
    with pytest.raises(ValidationError):
        AlertaEntrada.model_validate(payload_c1)


def test_campos_v2_solo_en_crudo(payload_c1):
    payload_c1["metricas"].update({"M3_duracion_seg": None, "M3_bytes": None})
    payload_c1.update({"puntaje_total": 85, "puntaje_maximo": 135, "pesos_metricas": {"M1": 25}})
    alerta = AlertaEntrada.model_validate(payload_c1)

    validado = alerta.model_dump()
    for clave in ("puntaje_total", "puntaje_maximo", "pesos_metricas"):
        assert clave not in validado and alerta.crudo[clave] == payload_c1[clave]
    assert "M3_bytes" not in validado["metricas"] and "M3_duracion_seg" not in validado["metricas"]
    assert alerta.crudo["metricas"]["M3_bytes"] is None


def test_describir_errores_en_espanol(payload_c1):
    del payload_c1["metricas"]["M1_nuevos_flujos_por_30s"]
    payload_c1["metricas"]["M5_count"] = float("nan")
    payload_c1["host_origen"] = "no-es-ip"
    with pytest.raises(ValidationError) as error:
        AlertaEntrada.model_validate(payload_c1)
    errores = {e["campo"]: e for e in describir_errores(error.value)}
    assert errores["metricas.M1_nuevos_flujos_por_30s"] == {
        "campo": "metricas.M1_nuevos_flujos_por_30s", "problema": "campo obligatorio ausente", "valor": None}
    assert errores["metricas.M5_count"]["problema"] == "no puede ser NaN ni infinito"
    assert errores["metricas.M5_count"]["valor"] == "nan"  # NaN no es JSON válido: se muestra como texto
    assert errores["host_origen"]["problema"] == "debe ser una dirección IPv4 válida"
