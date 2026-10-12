"""Cliente de Ryu contra el mock en memoria (sin red)."""

import time

import httpx
import mock_ryu
import pytest
from conftest import TOKEN_AGENTE, transporte_asgi

from app.integrations import ryu as ryu_modulo
from app.integrations.ryu import ClienteRyu, HostNoBloqueado, RyuError, RyuNoDisponible, UmbralesNoValidos


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


# ---- Fase 6.6: capacidades y validación local de umbrales ----

def _cliente_mock(url: str, cuarentena: bool) -> tuple[ClienteRyu, list[str]]:
    """Cliente contra el mock que además anota las rutas pedidas."""
    interno = transporte_asgi(mock_ryu.crear_app(TOKEN_AGENTE, cuarentena=cuarentena))
    rutas: list[str] = []

    def manejar(peticion: httpx.Request) -> httpx.Response:
        rutas.append(f"{peticion.method} {peticion.url.path}")
        return interno.handle_request(peticion)

    return ClienteRyu(url, TOKEN_AGENTE, transporte=httpx.MockTransport(manejar)), rutas


@pytest.mark.parametrize("cuarentena", [True, False])
def test_capacidades_detecta_a6_una_vez_y_la_cachea(cuarentena):
    cliente, rutas = _cliente_mock("http://ryu-a.test", cuarentena)
    assert cliente.capacidades() == {"cuarentena": cuarentena}
    assert cliente.capacidades() == {"cuarentena": cuarentena}
    assert rutas == ["GET /ryu/hosts/bloqueados"]  # la segunda vez sale de la caché
    cliente.capacidades(refrescar=True)
    assert len(rutas) == 2


def test_capacidades_con_lista_vacia_es_false_sin_cache():
    cliente, rutas = _cliente_mock("http://ryu-b.test", cuarentena=True)
    cliente.desbloquear("10.0.0.3")
    cliente.desbloquear("10.0.0.5")
    assert cliente.capacidades() == {"cuarentena": False}
    assert "http://ryu-b.test" not in ryu_modulo._capacidades
    cliente.capacidades()
    assert rutas.count("GET /ryu/hosts/bloqueados") == 2


def test_bloqueados_lee_modo_si_viene():
    cliente, _ = _cliente_mock("http://ryu-c.test", cuarentena=True)
    assert {host["modo"] for host in cliente.bloqueados()} == {"drop_total"}


@pytest.mark.parametrize("cambios", [{}, {"CRITICAL_THRESHOLD": 2}, {"M2": 0}, {"M2": 2.5}, {"M2": True}])
def test_umbrales_invalidos_no_se_envian(cambios):
    cliente, rutas = _cliente_mock("http://ryu-d.test", cuarentena=False)
    with pytest.raises(UmbralesNoValidos):
        cliente.actualizar_umbrales(cambios)
    assert rutas == []
