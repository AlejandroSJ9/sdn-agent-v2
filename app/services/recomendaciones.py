"""Recomendaciones (C7): propuestas que la política no ejecutó sola y que resuelve el operador.

Estados: pendiente → aprobada → ejecutada | fallida, pendiente → rechazada, pendiente → expirada
(tras RECOMENDACION_TTL_MIN; se revisa al listar, al resolver y al arrancar). Cambiar MODO_DECISION
a solo_explicar no borra las pendientes: el operador todavía puede aprobarlas.
Fase 9b: aprobar ejecuta la acción (las de la escalera y las heredadas de la 6.8) por acciones.ejecutar.
"""

from datetime import datetime, timedelta, timezone
from typing import Any

from app.db import repository, repository_acciones
from app.services import acciones, auditoria, configuracion, eventos


class RecomendacionNoEncontrada(LookupError):
    """404."""


class RecomendacionYaResuelta(ValueError):
    """La recomendación ya no está pendiente (409)."""


def _resuelta(recomendacion_id: int, estado: str, **extra: Any) -> None:
    datos = {"recomendacion_id": recomendacion_id, "estado": estado, **extra}
    auditoria.registrar("recomendacion_resuelta", datos)
    eventos.emitir("recomendacion_resuelta", datos)


def crear(incidente_id: int | None, host: str, accion: str, justificacion: str | None, confianza: float | None,
          motivos: list[str]) -> dict[str, Any]:
    ahora = datetime.now(timezone.utc)
    ttl = configuracion.politica()["recomendacion_ttl_min"]
    recomendacion = repository_acciones.crear_recomendacion(
        incidente_id, host, accion, justificacion, confianza, motivos, ahora, ahora + timedelta(minutes=ttl)
    )
    datos = {"recomendacion_id": recomendacion["id"], "incidente_id": incidente_id, "host": host, "accion": accion,
             "motivos": motivos}
    auditoria.registrar("recomendacion_creada", datos)
    eventos.emitir("recomendacion_creada", datos)
    return recomendacion


def expirar() -> list[int]:
    ids = repository_acciones.expirar_vencidas(datetime.now(timezone.utc))
    for recomendacion_id in ids:
        _resuelta(recomendacion_id, "expirada", resuelta_por="sistema")
    return ids


def listar(estado: str | None = None, limite: int = 100) -> list[dict[str, Any]]:
    expirar()
    return repository_acciones.listar_recomendaciones(estado, limite)


def _pendiente(recomendacion_id: int) -> dict[str, Any]:
    expirar()
    recomendacion = repository_acciones.obtener_recomendacion(recomendacion_id)
    if recomendacion is None:
        raise RecomendacionNoEncontrada(f"Recomendación {recomendacion_id} no encontrada")
    if recomendacion["estado"] != "pendiente":
        raise RecomendacionYaResuelta(f"La recomendación ya está {recomendacion['estado']}")
    return recomendacion


def aprobar(recomendacion_id: int) -> dict[str, Any]:
    """El operador aprueba: se ejecuta por acciones.ejecutar con origen "operador"."""
    recomendacion = _pendiente(recomendacion_id)
    ahora = datetime.now(timezone.utc)
    if not repository_acciones.resolver_recomendacion(recomendacion_id, "aprobada", "operador", None, ahora):
        raise RecomendacionYaResuelta("La recomendación ya fue resuelta")
    incidente = repository.obtener_incidente(recomendacion["incidente_id"]) if recomendacion["incidente_id"] else None
    resultado = acciones.ejecutar(recomendacion["accion"], incidente, "operador", host=recomendacion["host"])
    estado = "ejecutada" if resultado["estado"] in ("ejecutada", "simulada") else "fallida"
    repository_acciones.resolver_recomendacion(
        recomendacion_id, estado, "operador", resultado.get("error"), datetime.now(timezone.utc), solo_si="aprobada"
    )
    _resuelta(recomendacion_id, estado, resuelta_por="operador", error=resultado.get("error"))
    return {**repository_acciones.obtener_recomendacion(recomendacion_id), "resultado": resultado}


def rechazar(recomendacion_id: int, motivo: str) -> dict[str, Any]:
    _pendiente(recomendacion_id)
    if not repository_acciones.resolver_recomendacion(
        recomendacion_id, "rechazada", "operador", motivo, datetime.now(timezone.utc)
    ):
        raise RecomendacionYaResuelta("La recomendación ya fue resuelta")
    _resuelta(recomendacion_id, "rechazada", resuelta_por="operador", motivo=motivo)
    return repository_acciones.obtener_recomendacion(recomendacion_id)
