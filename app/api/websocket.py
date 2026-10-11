"""WebSocket /ws/eventos: eventos C5 en vivo para el panel del operador.

Los navegadores no permiten cabeceras en WebSocket, así que el PRIMER mensaje debe ser
{"token": "<OPERADOR_TOKEN>"} (nunca por la URL). Sin token válido en ESPERA_TOKEN s → cierre 1008.
Mensajes de control (clave "control", no "tipo"): el servidor envía {"control": "listo"} al autenticar
y responde {"control": "pong"} a cada {"control": "ping"} del panel (keepalive).
"""

import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from app.api.routes_alertas import _token_valido
from app.config import get_settings
from app.services import eventos

logger = logging.getLogger(__name__)
router = APIRouter()

ESPERA_TOKEN = 5.0


async def _autenticar(ws: WebSocket) -> bool:
    try:
        primero = json.loads(await asyncio.wait_for(ws.receive_text(), ESPERA_TOKEN))
        token = primero.get("token") if isinstance(primero, dict) else None
    except WebSocketDisconnect:
        return False
    except Exception:  # timeout, JSON inválido, mensaje binario
        token = None
    if isinstance(token, str) and _token_valido(token, get_settings().OPERADOR_TOKEN):
        return True
    await ws.close(code=status.WS_1008_POLICY_VIOLATION)
    return False


@router.websocket("/ws/eventos")
async def eventos_en_vivo(ws: WebSocket) -> None:
    await ws.accept()
    if not await _autenticar(ws):
        return
    cola = eventos.registrar_cliente()

    async def enviar() -> None:
        while True:
            await ws.send_json(await cola.get())

    tarea = asyncio.create_task(enviar())
    try:
        await ws.send_json({"control": "listo"})
        while True:
            texto = await ws.receive_text()
            try:
                mensaje = json.loads(texto)
            except ValueError:
                continue
            if isinstance(mensaje, dict) and mensaje.get("control") == "ping":
                await ws.send_json({"control": "pong"})
    except Exception:  # desconexión o error de envío: se limpia y termina
        pass
    finally:
        eventos.quitar_cliente(cola)
        tarea.cancel()
