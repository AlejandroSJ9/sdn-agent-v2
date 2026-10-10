"""Cliente de Ryu contra el mock en memoria (sin red)."""

import time

import httpx
import mock_ryu
import pytest
from conftest import TOKEN_AGENTE, transporte_asgi

from app.integrations.ryu import ClienteRyu, HostNoBloqueado, RyuError, RyuNoDisponible


@pytest.fixture
def ryu():
    app_mock = mock_ryu.crear_app(TOKEN_AGENTE)
    return ClienteRyu("http://ryu.test", TOKEN_AGENTE, transporte=transporte_asgi(app_mock))


def _cliente_con(manejar, tope: float = 3.0) -> ClienteRyu:
    return ClienteRyu("http://ryu.test/", TOKEN_AGENTE, transporte=httpx.MockTransport(manejar), tope=tope)


def test_estado_bloqueados_y_umbrales(ryu):
    estado = ryu.estado()
    assert estado["hosts_bloqueados"] == 2 and estado["switches"]
    assert [h["ip"] for h in ryu.bloqueados()] == ["10.0.0.3", "10.0.0.5"]
    assert ryu.umbrales() == mock_ryu.UMBRALES_INICIALES


def test_desbloquear_y_404_si_ya_no_esta(ryu):
    assert ryu.desbloquear("10.0.0.3") == {"ok": True, "ip": "10.0.0.3", "reglas_borradas": 1}
    assert [h["ip"] for h in ryu.bloqueados()] == ["10.0.0.5"]
    with pytest.raises(HostNoBloqueado):
        ryu.desbloquear("10.0.0.3")


def test_actualizar_umbrales(ryu):
    assert ryu.actualizar_umbrales({"M2": 7})["M2"] == 7
    assert ryu.umbrales()["M2"] == 7


def test_el_token_viaja_en_la_cabecera():
    vistos: list[str | None] = []

    def manejar(peticion: httpx.Request) -> httpx.Response:
        vistos.append(peticion.headers.get("X-Agent-Token"))
        return httpx.Response(200, json={"ok": True})

    _cliente_con(manejar).estado()
    assert vistos == [TOKEN_AGENTE]


def test_token_incorrecto_es_ryu_error_401():
    ryu = ClienteRyu("http://ryu.test", "otro", transporte=transporte_asgi(mock_ryu.crear_app(TOKEN_AGENTE)))
    with pytest.raises(RyuError) as error:
        ryu.estado()
    assert error.value.codigo == 401 and not isinstance(error.value, RyuNoDisponible)


def test_timeout_es_ryu_no_disponible():
    def lento(peticion: httpx.Request) -> httpx.Response:
        time.sleep(0.5)
        return httpx.Response(200, json={})

    inicio = time.perf_counter()
    with pytest.raises(RyuNoDisponible):
        _cliente_con(lento, tope=0.1).bloqueados()
    assert time.perf_counter() - inicio < 0.4


def test_error_de_red_es_ryu_no_disponible():
    def caido(peticion: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("conexión rechazada")

    with pytest.raises(RyuNoDisponible):
        _cliente_con(caido).estado()


def test_otro_codigo_es_ryu_error_con_el_codigo():
    with pytest.raises(RyuError) as error:
        _cliente_con(lambda p: httpx.Response(500)).desbloquear("10.0.0.3")
    assert error.value.codigo == 500 and not isinstance(error.value, HostNoBloqueado)
