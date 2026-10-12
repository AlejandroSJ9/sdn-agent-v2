"""Acceso a datos del contexto dinámico (C6) y de los incidentes sembrados del laboratorio. Solo SQL.

El contexto solo LEE: estas consultas nunca bloquean, desbloquean ni cambian el estado de un incidente.
La única escritura es crear_sembrado, que usa exclusivamente el laboratorio.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import func, select

from app.db.database import SesionLocal
from app.db.models import Alerta, Incidente
from app.db.repository import _mismo_ambito
from app.schemas.laboratorio import AmbitoPrueba


def referencia(incidente_id: int) -> dict[str, Any] | None:
    """Host, hora de recepción y ámbito del incidente para el que se arma el contexto."""
    columnas = (Incidente.host_origen, Incidente.t_recibido, Incidente.origen, Incidente.corrida_id,
                Incidente.caso_id, Incidente.repeticion)
    with SesionLocal() as sesion:
        fila = sesion.execute(select(*columnas).where(Incidente.id == incidente_id)).mappings().first()
    if fila is None:
        return None
    ambito = None if fila["origen"] == "real" else AmbitoPrueba(fila["corrida_id"], fila["caso_id"], fila["repeticion"])
    return {"host": fila["host_origen"], "t_recibido": fila["t_recibido"], "ambito": ambito}


def _umbrales_por_incidente(sesion, ids: list[int]) -> dict[int, list]:
    """umbrales_superados de la PRIMERA alerta de cada incidente (la que lo originó)."""
    if not ids:
        return {}
    primeras = select(func.min(Alerta.id)).where(Alerta.incidente_id.in_(ids)).group_by(Alerta.incidente_id)
    filas = sesion.execute(select(Alerta.incidente_id, Alerta.payload).where(Alerta.id.in_(primeras)))
    return {incidente_id: (payload or {}).get("umbrales_superados") or [] for incidente_id, payload in filas}


def incidentes_en_ventana(
    ambito: AmbitoPrueba | None, desde: datetime, hasta: datetime, excluir_id: int,
    host: str | None = None, otros_que: str | None = None, limite: int | None = None,
) -> list[dict[str, Any]]:
    """Incidentes del mismo ámbito recibidos en [desde, hasta], del más reciente al más antiguo.

    host = solo ese host; otros_que = todos menos ese host. Incluye los sembrados (son el historial).
    """
    consulta = (
        select(Incidente.id, Incidente.host_origen, Incidente.t_recibido, Incidente.veredicto,
               Incidente.clasificacion_operador, Incidente.motivo_operador)
        .where(*_mismo_ambito(ambito), Incidente.id != excluir_id)
        .where(Incidente.t_recibido >= desde, Incidente.t_recibido <= hasta)
        .order_by(Incidente.t_recibido.desc(), Incidente.id.desc())
    )
    if host is not None:
        consulta = consulta.where(Incidente.host_origen == host)
    if otros_que is not None:
        consulta = consulta.where(Incidente.host_origen != otros_que)
    if limite is not None:
        consulta = consulta.limit(limite)
    with SesionLocal() as sesion:
        filas = [dict(f) for f in sesion.execute(consulta).mappings()]
        umbrales = _umbrales_por_incidente(sesion, [f["id"] for f in filas])
    for fila in filas:
        fila["umbrales_superados"] = umbrales.get(fila["id"], [])
    return filas


def crear_sembrado(
    ambito: AmbitoPrueba, host: str, t_recibido: datetime, veredicto: str | None, confianza: float | None,
    clasificacion: str | None, umbrales: list[str], fuente: str,
) -> int:
    """Incidente histórico del laboratorio (sembrado=true) con una alerta mínima: sin métricas inventadas.

    Los que traen clasificación del operador quedan desbloqueados en el mismo instante (t_desbloqueado = t_recibido).
    """
    with SesionLocal.begin() as sesion:
        incidente = Incidente(
            host_origen=host, t_ryu=t_recibido, t_recibido=t_recibido, veredicto=veredicto, confianza=confianza,
            clasificacion_operador=clasificacion, t_desbloqueado=t_recibido if clasificacion else None,
            origen="prueba", sembrado=True, **ambito._asdict(),
        )
        sesion.add(incidente)
        sesion.flush()
        payload = {"host_origen": host, "umbrales_superados": umbrales, "sembrado": True, "fuente": fuente}
        sesion.add(Alerta(incidente_id=incidente.id, payload=payload, t_ryu=t_recibido, t_recibido=t_recibido))
        return incidente.id
