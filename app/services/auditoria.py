"""Auditoría de solo inserción con cadena de HMAC-SHA256 (clave AUDITORIA_CLAVE).

Sin la clave no se puede recalcular la cadena: alterar una fila y rehacer los hashes se detecta.
"""

import hashlib
import hmac
import json
import threading
from datetime import datetime, timezone
from typing import Any

from app.config import get_settings
from app.db import repository, repository_operador

HASH_INICIAL = "0" * 64

# Leer el último hash e insertar la fila nueva debe ser atómico: si dos hilos leyeran el mismo
# hash_previo, la cadena se bifurcaría. Basta un lock porque el agente corre en un solo proceso.
_candado = threading.Lock()


def _json_canonico(datos: Any) -> str:
    return json.dumps(datos, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _iso_utc(ts: datetime) -> str:
    """SQLite devuelve las fechas sin zona horaria; se normalizan a UTC para que el hash sea reproducible."""
    ts = ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts.astimezone(timezone.utc)
    return ts.isoformat(timespec="microseconds")


def _calcular_hash(hash_previo: str, evento: str, datos: Any, ts: datetime) -> str:
    contenido = hash_previo + evento + _json_canonico(datos) + _iso_utc(ts)
    clave = get_settings().AUDITORIA_CLAVE.encode("utf-8")
    return hmac.new(clave, contenido.encode("utf-8"), hashlib.sha256).hexdigest()


def registrar(evento: str, datos: dict[str, Any]) -> None:
    """Agrega un evento al final de la cadena de auditoría."""
    datos = json.loads(_json_canonico(datos))  # lo que se guarda es exactamente lo que se hashea
    with _candado:
        hash_previo = repository.ultimo_hash_auditoria() or HASH_INICIAL
        ts = datetime.now(timezone.utc)
        hash_ = _calcular_hash(hash_previo, evento, datos, ts)
        repository.insertar_auditoria(ts, evento, datos, hash_previo, hash_)


def verificar_cadena() -> dict[str, Any]:
    """Recalcula la cadena completa. primera_rota es el id de la primera fila alterada o desenlazada."""
    filas = repository.listar_auditoria()
    esperado = HASH_INICIAL
    for fila in filas:
        if fila.hash_previo != esperado or fila.hash != _calcular_hash(
            fila.hash_previo, fila.evento, fila.datos, fila.ts
        ):
            return {"integra": False, "filas": len(filas), "primera_rota": fila.id}
        esperado = fila.hash
    return {"integra": True, "filas": len(filas), "primera_rota": None}


def ultimos(limite: int) -> list[dict[str, Any]]:
    """Últimos eventos, del más reciente al más antiguo, con la hora en UTC."""
    return [
        {"id": fila.id, "ts": _iso_utc(fila.ts), "evento": fila.evento, "datos": fila.datos}
        for fila in repository_operador.ultimos_eventos_auditoria(limite)
    ]
