"""Cliente de Ryu contra el mock en memoria (sin red)."""

import time

import httpx
import mock_ryu
import pytest
from conftest import TOKEN_AGENTE, transporte_asgi

from app.integrations import ryu as ryu_modulo
from app.integrations.ryu import (
    ApiNoDisponible, ClienteRyu, HostNoBloqueado, RyuError, RyuNoDisponible,
)


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

def _cliente_mock(url: str, cuarentena: bool, anterior_9b: bool = False, **capacidades) -> tuple[ClienteRyu, list[str]]:
    """Cliente contra el mock que además anota las rutas pedidas. anterior_9b: /ryu/estado sin "capacidades"."""
    interno = transporte_asgi(mock_ryu.crear_app(
        TOKEN_AGENTE, cuarentena=cuarentena, **{"limitar_tasa": False, "cuarentena_vlan": False, **capacidades}))
    rutas: list[str] = []

    def manejar(peticion: httpx.Request) -> httpx.Response:
        rutas.append(f"{peticion.method} {peticion.url.path}")
        respuesta = interno.handle_request(peticion)
        if anterior_9b and peticion.url.path == "/ryu/estado":
            cuerpo = {k: v for k, v in respuesta.json().items() if k != "capacidades"}
            return httpx.Response(respuesta.status_code, json=cuerpo)
        return respuesta

    return ClienteRyu(url, TOKEN_AGENTE, transporte=httpx.MockTransport(manejar)), rutas


NINGUNA = {"cuarentena": False, "limitar_tasa": False, "cuarentena_vlan": False}


@pytest.mark.parametrize("cuarentena", [True, False])
def test_capacidades_declaradas_en_estado_una_vez_y_en_cache(cuarentena):
    cliente, rutas = _cliente_mock("http://ryu-a.test", cuarentena, limitar_tasa=True)
    esperado = {"cuarentena": cuarentena, "limitar_tasa": True, "cuarentena_vlan": False}
    assert cliente.capacidades() == esperado
    assert cliente.capacidades() == esperado
    assert rutas == ["GET /ryu/estado"]  # la segunda vez sale de la caché
    cliente.capacidades(refrescar=True)
    assert len(rutas) == 2


@pytest.mark.parametrize("cuarentena", [True, False])
def test_ryu_anterior_a_9b_infiere_a6_por_el_modo(cuarentena):
    cliente, rutas = _cliente_mock("http://ryu-d.test", cuarentena, anterior_9b=True)
    assert cliente.capacidades() == {**NINGUNA, "cuarentena": cuarentena}
    assert rutas == ["GET /ryu/estado", "GET /ryu/hosts/bloqueados"]


def test_ryu_anterior_con_lista_vacia_es_false_sin_cache():
    cliente, rutas = _cliente_mock("http://ryu-b.test", cuarentena=True, anterior_9b=True)
    cliente.desbloquear("10.0.0.3")
    cliente.desbloquear("10.0.0.5")
    assert cliente.capacidades() == NINGUNA
    assert "http://ryu-b.test" not in ryu_modulo._capacidades
    cliente.capacidades()
    assert rutas.count("GET /ryu/hosts/bloqueados") == 2


def test_bloqueados_lee_modo_si_viene():
    cliente, _ = _cliente_mock("http://ryu-c.test", cuarentena=True)
    assert {host["modo"] for host in cliente.bloqueados()} == {"drop_total"}


def test_el_cliente_ya_no_puede_editar_umbrales():
    """Fase D2: solo lectura; los umbrales se editan en Ryu."""
    assert not hasattr(ClienteRyu, "actualizar_umbrales")


def test_desbloquear_sin_api_a5_no_es_host_no_bloqueado():
    """QA F1: si /bloqueados también da 404, la ruta no existe (falta A5), no "host no bloqueado"."""
    with pytest.raises(ApiNoDisponible) as error:
        _cliente_con(lambda peticion: httpx.Response(404)).desbloquear("10.0.0.3")
    assert error.value.codigo == 404 and str(error.value) == "api_no_disponible (falta A5)"


def test_desbloquear_404_con_bloqueados_en_error_propaga_ese_error():
    def manejar(peticion):
        return httpx.Response(404 if peticion.url.path.endswith("/desbloquear") else 500)

    with pytest.raises(RyuError) as error:
        _cliente_con(manejar).desbloquear("10.0.0.3")
    assert error.value.codigo == 500 and not isinstance(error.value, (HostNoBloqueado, ApiNoDisponible))
