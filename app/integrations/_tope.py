"""Tope de tiempo TOTAL para llamadas HTTP bloqueantes.

Los timeouts de httpx son por operación (y el de lectura se fija una vez por respuesta), así que una
respuesta que gotea bytes o una redirección pueden superar el tiempo pedido. Aquí la llamada corre en
un hilo y se espera como máximo el tope; además, post_hasta revisa el mismo límite tras cada fragmento,
de modo que un hilo abandonado termina solo.
"""

import threading
import time
from collections.abc import Callable
from typing import TypeVar

import httpx

T = TypeVar("T")


class TopeSuperado(Exception):
    """La operación superó su tope total de tiempo."""


def ejecutar_con_tope(funcion: Callable[[], T], segundos: float, nombre: str) -> T:
    """Ejecuta `funcion` en un hilo y espera como máximo `segundos`. Re-lanza sus excepciones."""
    resultado: dict = {}

    def trabajar() -> None:
        try:
            resultado["ok"] = funcion()
        except BaseException as error:  # se re-lanza en el hilo que llamó
            resultado["error"] = error

    hilo = threading.Thread(target=trabajar, daemon=True, name=nombre)
    hilo.start()
    hilo.join(segundos)
    if hilo.is_alive():
        raise TopeSuperado(f"tope total de {segundos} s superado")
    if "error" in resultado:
        raise resultado["error"]
    return resultado["ok"]


def solicitar_hasta(
    http: httpx.Client, metodo: str, url: str, limite: float, cuerpo: dict | None = None, cabeceras: dict | None = None
) -> tuple[int, bytes]:
    """Petición leyendo el cuerpo por partes; lanza TopeSuperado en cuanto se pasa `limite` (time.monotonic)."""
    restante = limite - time.monotonic()
    if restante <= 0:
        raise TopeSuperado("tope total superado")
    with http.stream(metodo, url, json=cuerpo, headers=cabeceras, timeout=restante) as respuesta:
        partes = []
        for parte in respuesta.iter_bytes():
            if time.monotonic() > limite:
                raise TopeSuperado("tope total superado")
            partes.append(parte)
        return respuesta.status_code, b"".join(partes)


def post_hasta(http: httpx.Client, url: str, cuerpo: dict, cabeceras: dict, limite: float) -> tuple[int, bytes]:
    return solicitar_hasta(http, "POST", url, limite, cuerpo, cabeceras)
