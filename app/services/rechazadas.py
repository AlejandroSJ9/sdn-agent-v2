"""Alertas rechazadas (401, 422, 429) contadas en memoria por motivo. Nunca se escriben en la BD.

Así un emisor mal configurado o un ataque de saturación no llena la base de datos; el panel solo
necesita saber cuántas se rechazaron, por qué y cuándo fue la última.
"""

import threading
from datetime import datetime, timezone
from typing import Any

MAX_MOTIVOS = 50  # tope de motivos distintos (los 422 varían según el campo); el resto va a "otros"

_candado = threading.Lock()
_motivos: dict[tuple[int, str], dict[str, Any]] = {}


def registrar(codigo: int, detalle: str) -> None:
    clave = (codigo, detalle[:200])
    ahora = datetime.now(timezone.utc).isoformat()
    with _candado:
        if clave not in _motivos and len(_motivos) >= MAX_MOTIVOS:
            clave = (codigo, "otros")
        motivo = _motivos.setdefault(clave, {"codigo": codigo, "detalle": clave[1], "cantidad": 0, "ultima": None})
        motivo["cantidad"] += 1
        motivo["ultima"] = ahora


def resumen() -> dict[str, Any]:
    """{"total": N, "motivos": [{codigo, detalle, cantidad, ultima}]}, el más reciente primero."""
    with _candado:
        motivos = [dict(motivo) for motivo in _motivos.values()]
    motivos.sort(key=lambda motivo: motivo["ultima"], reverse=True)
    return {"total": sum(motivo["cantidad"] for motivo in motivos), "motivos": motivos}


def reiniciar() -> None:
    with _candado:
        _motivos.clear()
