"""Cliente de Apps Script con httpx.MockTransport (sin red y sin dormir)."""

import time

import httpx
import pytest

from app.integrations import appscript

URL = "https://script.google.com/macros/s/FALSO/exec"
PAYLOAD = {"tipo": "prueba", "asunto": "x"}


class Servidor:
    """Responde en orden la lista programada (la última se repite) y registra peticiones y esperas."""

    def __init__(self, *respuestas) -> None:
        self.respuestas = list(respuestas)
        self.peticiones: list[httpx.Request] = []
        self.esperas: list[float] = []

    def manejar(self, peticion: httpx.Request) -> httpx.Response:
        self.peticiones.append(peticion)
        respuesta = self.respuestas.pop(0) if len(self.respuestas) > 1 else self.respuestas[0]
        if isinstance(respuesta, Exception):
            raise respuesta
        return respuesta

    def enviar(self, url: str = URL, tope: float = 30):
        transporte = httpx.MockTransport(self.manejar)
        return appscript.enviar(PAYLOAD, url, tope, transporte=transporte, esperar=self.esperas.append)


def _ok(valor=True) -> httpx.Response:
    return httpx.Response(200, json={"ok": valor})


def test_exito_siguiendo_la_redireccion_302():
    redireccion = httpx.Response(302, headers={"Location": "https://script.googleusercontent.com/macros/echo?u=1"})
    servidor = Servidor(redireccion, _ok())
    resultado = servidor.enviar()

    assert (resultado.estado, resultado.intentos, resultado.codigo_http, resultado.error) == ("enviada", 1, 200, None)
    assert [p.url.host for p in servidor.peticiones] == ["script.google.com", "script.googleusercontent.com"]
    assert servidor.esperas == []


def test_200_con_ok_false_cuenta_como_fallo():
    servidor = Servidor(_ok(False))
    resultado = servidor.enviar()

    assert (resultado.estado, resultado.intentos, resultado.error) == ("fallida", 4, "respuesta_sin_ok")
    assert servidor.esperas == [2, 4, 8]


@pytest.mark.parametrize(
    "fallo", [httpx.Response(500), httpx.Response(429), httpx.Response(200, text="<html>no es JSON</html>")]
)
def test_dos_fallos_reintentables_y_luego_exito(fallo):
    servidor = Servidor(fallo, fallo, _ok())
    resultado = servidor.enviar()

    assert (resultado.estado, resultado.intentos, resultado.codigo_http) == ("enviada", 3, 200)
    assert servidor.esperas == [2, 4]


def test_fallo_en_los_4_intentos():
    servidor = Servidor(httpx.Response(500))
    resultado = servidor.enviar()

    assert (resultado.estado, resultado.intentos, resultado.codigo_http, resultado.error) == ("fallida", 4, 500, "http_500")
    assert len(servidor.peticiones) == 4
    assert servidor.esperas == [2, 4, 8]


def test_timeout_es_fallida():
    servidor = Servidor(httpx.ReadTimeout("lento"))
    resultado = servidor.enviar()

    assert (resultado.estado, resultado.intentos, resultado.codigo_http, resultado.error) == ("fallida", 4, None, "timeout")
    assert servidor.esperas == [2, 4, 8]


def test_403_termina_sin_reintentos():
    servidor = Servidor(httpx.Response(403))
    resultado = servidor.enviar()

    assert (resultado.estado, resultado.intentos, resultado.codigo_http, resultado.error) == ("fallida", 1, 403, "http_403")
    assert servidor.esperas == []


def test_url_vacia_omitida_sin_red():
    servidor = Servidor(AssertionError("no debía usar la red"))
    resultado = servidor.enviar(url="")

    assert (resultado.estado, resultado.intentos, resultado.error) == ("omitida", 0, None)
    assert servidor.peticiones == []


def test_respeta_el_tope_por_intento_recibido():
    """Una respuesta que tarda más que el tope corta en el tope (no en un valor fijo) y cuenta como timeout."""

    def lento(peticion: httpx.Request) -> httpx.Response:
        time.sleep(0.5)
        return _ok()

    servidor = Servidor(None)
    servidor.manejar = lento
    inicio = time.perf_counter()
    resultado = servidor.enviar(tope=0.1)
    duracion = time.perf_counter() - inicio

    assert (resultado.estado, resultado.intentos, resultado.error) == ("fallida", 4, "timeout")
    assert duracion < 1.0  # 4 intentos × 0.1 s; con un tope fijo de 10 s tardaría mucho más
