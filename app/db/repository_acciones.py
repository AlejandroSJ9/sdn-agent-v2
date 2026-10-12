"""Acceso a datos de la decisión acotada (Fase 6.8): decisión de la política, recomendaciones y acciones. Solo SQL."""

from datetime import datetime
from typing import Any

from sqlalchemy import func, select, update

from app.db.database import SesionLocal
from app.db.models import Auditoria, Incidente, Recomendacion


def _a_dict(fila) -> dict[str, Any]:
    return {columna.name: getattr(fila, columna.name) for columna in fila.__table__.columns}


def guardar_decision(
    incidente_id: int, accion_propuesta: str, justificacion: str | None, decision: str, motivos: list[str],
    accion_final: str,
) -> None:
    """Propuesta, decisión de la política y acción final del incidente. No toca nada del bloqueo."""
    with SesionLocal.begin() as sesion:
        sesion.execute(
            update(Incidente).where(Incidente.id == incidente_id).values(
                accion_propuesta=accion_propuesta, justificacion_accion=justificacion, decision_politica=decision,
                motivos_politica=motivos, accion_final=accion_final,
            )
        )


def guardar_informe(incidente_id: int, informe: dict, resumen: str) -> None:
    """Informe auditable (Fase 6.8b) y su resumen generado por código, que reemplaza la explicación de texto."""
    with SesionLocal.begin() as sesion:
        sesion.execute(update(Incidente).where(Incidente.id == incidente_id).values(informe=informe, explicacion=resumen))


def marcar_accion_final(incidente_id: int, accion_final: str) -> None:
    with SesionLocal.begin() as sesion:
        sesion.execute(update(Incidente).where(Incidente.id == incidente_id).values(accion_final=accion_final))


def crear_recomendacion(
    incidente_id: int | None, host: str, accion: str, justificacion: str | None, confianza: float | None,
    motivos: list[str], t_creada: datetime, t_expira: datetime,
) -> dict[str, Any]:
    with SesionLocal.begin() as sesion:
        recomendacion = Recomendacion(
            incidente_id=incidente_id, host=host, accion=accion, justificacion=justificacion, confianza=confianza,
            motivos_politica=motivos, estado="pendiente", t_creada=t_creada, t_expira=t_expira,
        )
        sesion.add(recomendacion)
        sesion.flush()
        return _a_dict(recomendacion)


def obtener_recomendacion(recomendacion_id: int) -> dict[str, Any] | None:
    with SesionLocal() as sesion:
        fila = sesion.get(Recomendacion, recomendacion_id)
        return _a_dict(fila) if fila else None


def listar_recomendaciones(estado: str | None, limite: int) -> list[dict[str, Any]]:
    consulta = select(Recomendacion).order_by(Recomendacion.id.desc()).limit(limite)
    if estado is not None:
        consulta = consulta.where(Recomendacion.estado == estado)
    with SesionLocal() as sesion:
        return [_a_dict(fila) for fila in sesion.scalars(consulta)]


def resolver_recomendacion(
    recomendacion_id: int, estado: str, resuelta_por: str, motivo: str | None, ts: datetime, solo_si: str = "pendiente"
) -> bool:
    """Cambia el estado solo si sigue en `solo_si` (evita resolver dos veces). True si cambió."""
    with SesionLocal.begin() as sesion:
        resultado = sesion.execute(
            update(Recomendacion)
            .where(Recomendacion.id == recomendacion_id, Recomendacion.estado == solo_si)
            .values(estado=estado, resuelta_por=resuelta_por, motivo_resolucion=motivo, t_resuelta=ts)
        )
        return resultado.rowcount == 1


def expirar_vencidas(ahora: datetime) -> list[int]:
    """Pendientes cuyo t_expira ya pasó → "expirada" (resuelta por el sistema). Devuelve sus ids."""
    with SesionLocal.begin() as sesion:
        ids = list(sesion.scalars(
            select(Recomendacion.id).where(Recomendacion.estado == "pendiente", Recomendacion.t_expira <= ahora)
        ))
        if ids:
            sesion.execute(
                update(Recomendacion).where(Recomendacion.id.in_(ids), Recomendacion.estado == "pendiente")
                .values(estado="expirada", resuelta_por="sistema", t_resuelta=ahora)
            )
        return ids


def contar_pendientes() -> int:
    with SesionLocal() as sesion:
        return sesion.scalar(select(func.count(Recomendacion.id)).where(Recomendacion.estado == "pendiente"))


def acciones_reales_de_la_politica_desde(desde: datetime) -> int:
    """Acciones ejecutadas de verdad por la política (no simuladas ni del operador), para MAX_ACCIONES_POR_MIN."""
    consulta = select(func.count(Auditoria.id)).where(
        Auditoria.evento == "accion_ejecutada",
        Auditoria.ts >= desde,
        func.json_extract(Auditoria.datos, "$.origen") == "politica",
        func.json_extract(Auditoria.datos, "$.resultado") == "ejecutada",
    )
    with SesionLocal() as sesion:
        return sesion.scalar(consulta)


def acciones_de_incidente(incidente_id: int) -> list[dict[str, Any]]:
    """Acciones ejecutadas, simuladas o fallidas del incidente, leídas de la auditoría (solo inserción)."""
    consulta = (
        select(Auditoria.ts, Auditoria.evento, Auditoria.datos)
        .where(Auditoria.evento.in_(("accion_ejecutada", "accion_fallida")))
        .where(func.json_extract(Auditoria.datos, "$.incidente_id") == incidente_id)
        .order_by(Auditoria.id)
    )
    with SesionLocal() as sesion:
        return [{"ts": ts, "evento": evento, **datos} for ts, evento, datos in sesion.execute(consulta)]
