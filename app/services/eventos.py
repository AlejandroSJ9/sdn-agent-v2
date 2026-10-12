"""Eventos del agente (C5): se registran en el log y se publican a los clientes WebSocket conectados.

emitir() se llama desde hilos del pipeline (BackgroundTasks síncronas), no desde el event loop. Por eso
nunca toca el WebSocket directamente: entrega el mensaje al loop con call_soon_threadsafe y vuelve de
inmediato. Dentro del loop, cada cliente tiene una cola acotada; si un cliente es lento y su cola se
llena, se descarta su evento más viejo. Nada de esto puede bloquear ni romper el pipeline.
"""

import asyncio
import json
import logging
import threading
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

TIPOS_C5 = frozenset(
    {
        "alerta_recibida",
        "explicacion_lista",
        "notificacion_enviada",
        "notificacion_fallida",
        "host_desbloqueado",
        "config_cambiada",
        "prueba_progreso",
        "prueba_terminada",
        # C5 (extensión, Fase 6.8)
        "recomendacion_creada",
        "recomendacion_resuelta",
        "accion_ejecutada",
        "accion_fallida",
        "modo_decision_cambiado",
    }
)
TAMANO_COLA = 100

_candado = threading.Lock()
_loop: asyncio.AbstractEventLoop | None = None
_colas: set[asyncio.Queue] = set()


def registrar_cliente() -> asyncio.Queue:
    """Se llama desde el WebSocket (dentro del loop): guarda el loop y crea la cola del cliente."""
    global _loop
    cola: asyncio.Queue = asyncio.Queue(maxsize=TAMANO_COLA)
    with _candado:
        _loop = asyncio.get_running_loop()
        _colas.add(cola)
    return cola


def quitar_cliente(cola: asyncio.Queue) -> None:
    with _candado:
        _colas.discard(cola)


def _repartir(mensaje: dict, colas: list[asyncio.Queue]) -> None:
    """Corre DENTRO del loop. Nunca espera: si la cola está llena, descarta el evento más viejo."""
    for cola in colas:
        if cola.full():
            try:
                cola.get_nowait()
            except asyncio.QueueEmpty:
                pass
        cola.put_nowait(mensaje)


def emitir(evento: str, datos: dict) -> None:
    logger.info("evento %s %s", evento, datos)
    if evento not in TIPOS_C5:
        logger.warning("Evento %r fuera de C5: no se publica", evento)
        return
    try:
        mensaje = {
            "tipo": evento,
            "datos": json.loads(json.dumps(datos, default=str)),
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        }
        with _candado:
            colas, loop = list(_colas), _loop
        if colas and loop is not None:
            loop.call_soon_threadsafe(_repartir, mensaje, colas)
    except Exception:  # loop cerrado u otro fallo: solo se registra
        logger.exception("No se pudo publicar el evento %s", evento)
