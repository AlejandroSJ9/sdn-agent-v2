"""Cliente del web app de Apps Script (correo). Solo habla HTTP.

Apps Script responde al POST con un 302 a script.googleusercontent.com: se sigue la redirección y
solo cuenta como éxito un 200 final cuyo cuerpo sea JSON con "ok": true.
"""

import json
import time
from collections.abc import Callable

import httpx

from app.integrations._tope import TopeSuperado, ejecutar_con_tope, post_hasta
from app.schemas.notificacion import ResultadoNotificacion

ESPERAS_SEGUNDOS = (2, 4, 8)  # 3 reintentos después del primer intento


def _intento(
    payload: dict, url: str, transporte: httpx.BaseTransport | None, tope: float
) -> tuple[int, bytes]:
    """Un intento con tope TOTAL de `tope` segundos, redirección incluida."""
    limite = time.monotonic() + tope

    def hacer() -> tuple[int, bytes]:
        with httpx.Client(transport=transporte, follow_redirects=True) as http:
            return post_hasta(http, url, payload, {}, limite)

    return ejecutar_con_tope(hacer, tope, "appscript")


def _evaluar(codigo: int, contenido: bytes) -> tuple[str | None, bool]:
    """Devuelve (error, reintentable). error None = éxito."""
    if codigo == 200:
        try:
            ok = json.loads(contenido).get("ok") is True
        except (ValueError, AttributeError):
            ok = False
        return (None, False) if ok else ("respuesta_sin_ok", True)
    if codigo == 429 or codigo >= 500:
        return f"http_{codigo}", True
    return f"http_{codigo}", False  # otro 4xx: error de configuración, no se reintenta


def enviar(
    payload: dict,
    url: str,
    tope_por_intento: float,
    transporte: httpx.BaseTransport | None = None,
    esperar: Callable[[float], None] = time.sleep,
) -> ResultadoNotificacion:
    """Envía el payload C3. Nunca incluye la URL en el error (contiene el id del script)."""
    if not url:
        return ResultadoNotificacion(estado="omitida", intentos=0)
    inicio = time.perf_counter()
    codigo: int | None = None
    error: str | None = None
    intento = 0
    for intento in range(1, len(ESPERAS_SEGUNDOS) + 2):
        try:
            codigo, contenido = _intento(payload, url, transporte, tope_por_intento)
            error, reintentable = _evaluar(codigo, contenido)
        except (TopeSuperado, httpx.TimeoutException):
            codigo, error, reintentable = None, "timeout", True
        except httpx.HTTPError as excepcion:
            codigo, error, reintentable = None, f"red_{type(excepcion).__name__}", True
        if error is None or not reintentable or intento > len(ESPERAS_SEGUNDOS):
            break
        esperar(ESPERAS_SEGUNDOS[intento - 1])
    return ResultadoNotificacion(
        estado="enviada" if error is None else "fallida",
        intentos=intento,
        codigo_http=codigo,
        error=error,
        latencia_ms=int((time.perf_counter() - inicio) * 1000),
    )
