"""Acceso a datos de la Fase 4: desbloqueo, configuración en caliente y cupo. Solo SQL."""

from datetime import datetime
from typing import Any

from sqlalchemy import func, select, update

from app.db.database import SesionLocal
from app.db.models import Auditoria, Configuracion, Incidente, Notificacion, UsoLlm


def incidentes_abiertos_de(ip: str) -> list[int]:
    """Ids de los incidentes de esa IP que aún no se desbloquearon."""
    consulta = select(Incidente.id).where(Incidente.host_origen == ip, Incidente.t_desbloqueado.is_(None))
    with SesionLocal() as sesion:
        return list(sesion.scalars(consulta.order_by(Incidente.id)))


def marcar_desbloqueados(ids: list[int], ts: datetime) -> None:
    if not ids:
        return
    with SesionLocal.begin() as sesion:
        sesion.execute(
            update(Incidente)
            .where(Incidente.id.in_(ids), Incidente.t_desbloqueado.is_(None))
            .values(t_desbloqueado=ts)
        )


def ultimo_incidente_abierto_por_ip(ips: list[str]) -> dict[str, int]:
    """Para cada IP, el id del incidente abierto más reciente (si existe)."""
    consulta = (
        select(Incidente.host_origen, func.max(Incidente.id))
        .where(Incidente.host_origen.in_(ips), Incidente.t_desbloqueado.is_(None))
        .group_by(Incidente.host_origen)
    )
    with SesionLocal() as sesion:
        return dict(sesion.execute(consulta).tuples().all())


def leer_configuracion() -> dict[str, str]:
    with SesionLocal() as sesion:
        return dict(sesion.execute(select(Configuracion.clave, Configuracion.valor)).tuples().all())


def guardar_configuracion(clave: str, valor: str) -> None:
    with SesionLocal.begin() as sesion:
        sesion.merge(Configuracion(clave=clave, valor=valor))


def uso_llm_del_dia(fecha: str) -> dict[str, Any] | None:
    consulta = select(UsoLlm.peticiones, UsoLlm.fallidas).where(UsoLlm.fecha == fecha)
    with SesionLocal() as sesion:
        fila = sesion.execute(consulta).mappings().first()
    return dict(fila) if fila else None


def ultimas_notificaciones(ids: list[int]) -> dict[int, dict[str, Any]]:
    """Para cada incidente, su notificación más reciente (estado, intentos, ts), en una sola consulta."""
    if not ids:
        return {}
    consulta = (
        select(Notificacion.incidente_id, Notificacion.estado, Notificacion.intentos, Notificacion.ts)
        .where(Notificacion.incidente_id.in_(ids))
        .order_by(Notificacion.id)
    )
    with SesionLocal() as sesion:
        filas = sesion.execute(consulta).mappings().all()
    return {f["incidente_id"]: {"estado": f["estado"], "intentos": f["intentos"], "ts": f["ts"]} for f in filas}


def ultimos_eventos_auditoria(limite: int) -> list[Auditoria]:
    with SesionLocal() as sesion:
        return list(sesion.scalars(select(Auditoria).order_by(Auditoria.id.desc()).limit(limite)))


def resumen_panel(desde: datetime) -> dict[str, Any]:
    with SesionLocal() as sesion:
        contar = lambda consulta: sesion.scalar(select(func.count()).select_from(consulta.subquery()))  # noqa: E731
        return {
            "incidentes_abiertos": contar(select(Incidente.id).where(Incidente.t_desbloqueado.is_(None))),
            "incidentes_hoy": contar(select(Incidente.id).where(Incidente.t_recibido >= desde)),
            "notificaciones_fallidas_hoy": contar(
                select(Notificacion.id).where(Notificacion.estado == "fallida", Notificacion.ts >= desde)
            ),
            "ultimo_incidente_id": sesion.scalar(select(func.max(Incidente.id))),
        }
