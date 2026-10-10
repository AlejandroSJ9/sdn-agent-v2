"""Eventos del agente (C5). Por ahora solo se registran en el log; la Fase 5 los enviará por WebSocket."""

import logging

logger = logging.getLogger(__name__)


def emitir(evento: str, datos: dict) -> None:
    logger.info("evento %s %s", evento, datos)
