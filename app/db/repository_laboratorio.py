"""Acceso a datos del laboratorio (Fase 6): corridas y resultados por caso. Solo SQL.

Fase D2: la tabla ejecuciones_pytest (EjecucionPytest) queda sin uso; sus filas no se borran.
"""

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.db.database import SesionLocal
from app.db.models import Corrida, Incidente, ResultadoCaso


def _a_dict(fila) -> dict[str, Any]:
    return {columna.name: getattr(fila, columna.name) for columna in fila.__table__.columns}


def crear_corrida(
    casos: list[str], conjunto: str | None, usar_llm: bool, enviar_correos: bool, repeticiones: int, costo: int,
    variante: str = "sin_contexto", version_prompt: str = "v2.2-sin-contexto",
    modo_decision: str = "recomendar", umbral_confianza: float = 0.85,
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
            variante=variante,
            version_prompt=version_prompt,
            modo_decision=modo_decision,
            umbral_confianza=umbral_confianza,
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
        select(Corrida.usar_llm, Corrida.enviar_correos, Corrida.variante, Corrida.modo_decision, Corrida.umbral_confianza)
        .join(Incidente, Incidente.corrida_id == Corrida.id)
        .where(Incidente.id == incidente_id, Incidente.origen == "prueba")
    )
    with SesionLocal() as sesion:
        fila = sesion.execute(consulta).mappings().first()
    return dict(fila) if fila else None


def _interrumpir(modelo) -> list[int]:
    """Marca como "interrumpida" lo que siguiera "en_curso" (el agente se detuvo a mitad)."""
    with SesionLocal.begin() as sesion:
        filas = list(sesion.scalars(select(modelo).where(modelo.estado == "en_curso")))
        for fila in filas:
            fila.estado, fila.t_fin = "interrumpida", datetime.now(timezone.utc)
        return [fila.id for fila in filas]


def interrumpir_corridas_en_curso() -> list[int]:
    return _interrumpir(Corrida)
