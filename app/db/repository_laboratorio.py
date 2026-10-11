"""Acceso a datos del laboratorio (Fase 6): corridas, resultados por caso y ejecuciones de pytest. Solo SQL."""

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.db.database import SesionLocal
from app.db.models import Corrida, EjecucionPytest, Incidente, ResultadoCaso


def _a_dict(fila) -> dict[str, Any]:
    return {columna.name: getattr(fila, columna.name) for columna in fila.__table__.columns}


def crear_corrida(
    casos: list[str], conjunto: str | None, usar_llm: bool, enviar_correos: bool, repeticiones: int, costo: int
) -> int:
    with SesionLocal.begin() as sesion:
        corrida = Corrida(
            estado="en_curso",
            casos=casos,
            conjunto=conjunto,
            usar_llm=usar_llm,
            enviar_correos=enviar_correos,
            repeticiones=repeticiones,
            costo_estimado=costo,
            t_inicio=datetime.now(timezone.utc),
        )
        sesion.add(corrida)
        sesion.flush()
        return corrida.id


def terminar_corrida(corrida_id: int, estado: str, resumen: dict | None, error: str | None = None) -> None:
    with SesionLocal.begin() as sesion:
        corrida = sesion.get(Corrida, corrida_id)
        corrida.estado, corrida.resumen, corrida.error = estado, resumen, error
        corrida.t_fin = datetime.now(timezone.utc)


def obtener_corrida(corrida_id: int) -> dict[str, Any] | None:
    with SesionLocal() as sesion:
        corrida = sesion.get(Corrida, corrida_id)
        return _a_dict(corrida) if corrida else None


def listar_corridas(limite: int) -> list[dict[str, Any]]:
    with SesionLocal() as sesion:
        filas = sesion.scalars(select(Corrida).order_by(Corrida.id.desc()).limit(limite))
        return [_a_dict(fila) for fila in filas]


def guardar_resultado(datos: dict[str, Any]) -> None:
    with SesionLocal.begin() as sesion:
        sesion.add(ResultadoCaso(**datos))


def resultados_de(corrida_id: int) -> list[dict[str, Any]]:
    consulta = select(ResultadoCaso).where(ResultadoCaso.corrida_id == corrida_id).order_by(ResultadoCaso.id)
    with SesionLocal() as sesion:
        return [_a_dict(fila) for fila in sesion.scalars(consulta)]


def opciones_corrida(incidente_id: int) -> dict[str, bool] | None:
    """Opciones de la corrida de un incidente de prueba; None si el incidente es real."""
    consulta = (
        select(Corrida.usar_llm, Corrida.enviar_correos)
        .join(Incidente, Incidente.corrida_id == Corrida.id)
        .where(Incidente.id == incidente_id, Incidente.origen == "prueba")
    )
    with SesionLocal() as sesion:
        fila = sesion.execute(consulta).mappings().first()
    return dict(fila) if fila else None


def crear_ejecucion_pytest(carpeta: str) -> int:
    with SesionLocal.begin() as sesion:
        ejecucion = EjecucionPytest(carpeta=carpeta, estado="en_curso", t_inicio=datetime.now(timezone.utc), salida="")
        sesion.add(ejecucion)
        sesion.flush()
        return ejecucion.id


def terminar_ejecucion_pytest(
    ejecucion_id: int, estado: str, aprobadas: int | None, fallidas: int | None, duracion_ms: int, salida: str
) -> None:
    with SesionLocal.begin() as sesion:
        ejecucion = sesion.get(EjecucionPytest, ejecucion_id)
        ejecucion.estado, ejecucion.aprobadas, ejecucion.fallidas = estado, aprobadas, fallidas
        ejecucion.duracion_ms, ejecucion.salida = duracion_ms, salida
        ejecucion.t_fin = datetime.now(timezone.utc)


def ultima_ejecucion_pytest() -> dict[str, Any] | None:
    with SesionLocal() as sesion:
        fila = sesion.scalar(select(EjecucionPytest).order_by(EjecucionPytest.id.desc()).limit(1))
        return _a_dict(fila) if fila else None
