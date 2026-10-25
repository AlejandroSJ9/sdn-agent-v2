"""Fase D5a: condiciones de C7 para las acciones manuales del operador (función pura, sin red ni BD)."""

import pytest

from app.services.acciones import HOST_NO_BLOQUEADO, motivo_no_disponible

TODAS = {"cuarentena": True, "limitar_tasa": True, "cuarentena_vlan": True}
NINGUNA = dict.fromkeys(TODAS, False)
RELAJACIONES = ("cuarentena_puertos", "limitar_tasa", "cuarentena_vlan")


@pytest.mark.parametrize("accion", [*RELAJACIONES, "bloqueo_total"])
def test_host_no_bloqueado_nada_disponible(accion):
    assert motivo_no_disponible(accion, TODAS, None) == HOST_NO_BLOQUEADO


@pytest.mark.parametrize("accion", RELAJACIONES)
def test_relajaciones_solo_desde_drop_total(accion):
    assert motivo_no_disponible(accion, TODAS, "drop_total") is None
    for modo in ("cuarentena", "limitado", "vlan_cuarentena"):
        assert motivo_no_disponible(accion, TODAS, modo) == f"Solo se aplica desde DROP total (modo actual: {modo})"


@pytest.mark.parametrize(("accion", "capacidad", "sigla"), [
    ("cuarentena_puertos", "cuarentena", "A6"), ("limitar_tasa", "limitar_tasa", "A7"),
    ("cuarentena_vlan", "cuarentena_vlan", "A8"),
])
def test_cada_relajacion_exige_su_capacidad(accion, capacidad, sigla):
    sin_esa = {**TODAS, capacidad: False}
    assert motivo_no_disponible(accion, sin_esa, "drop_total").startswith(f"Ryu no declara {sigla}")
    solo_esa = {**NINGUNA, capacidad: True}
    assert motivo_no_disponible(accion, solo_esa, "drop_total") is None


def test_bloqueo_total_con_cualquier_capacidad_y_fuera_de_drop_total():
    assert motivo_no_disponible("bloqueo_total", TODAS, "drop_total") == "El host ya está en DROP total"
    assert motivo_no_disponible("bloqueo_total", {**NINGUNA, "cuarentena_vlan": True}, "vlan_cuarentena") is None
    assert motivo_no_disponible("bloqueo_total", NINGUNA, "drop_total").startswith("Ryu no declara A6, A7 ni A8")
