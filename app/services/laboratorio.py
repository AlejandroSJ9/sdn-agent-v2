"""Laboratorio: ejecuta los escenarios migrados (tests/escenarios/escenarios.json) contra el agente real.

Las alertas de prueba entran por alertas.recibir (el mismo servicio que las reales) con un AmbitoPrueba
que aísla su agrupación. El pipeline lee las opciones de la corrida: sin LLM usa reglas sin tocar el cupo
ni el interruptor global, y con los correos apagados la notificación queda "omitida" (laboratorio).
"""

import json
import logging
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.db import repository, repository_laboratorio
from app.schemas.alerta import AlertaEntrada
from app.schemas.laboratorio import AmbitoPrueba
from app.services import alertas, configuracion, eventos, presupuesto, resultados_lab, siembra
from app.services.prompt import VERSIONES

logger = logging.getLogger(__name__)

RUTA_ESCENARIOS = Path(__file__).resolve().parents[2] / "tests" / "escenarios" / "escenarios.json"
_candado = threading.Lock()  # una sola corrida a la vez


class CorridaEnCurso(Exception):
    """Ya hay una corrida ejecutándose (409)."""


class CorridaRechazada(Exception):
    """La corrida no se puede ejecutar: cupo insuficiente o LLM global apagado (400)."""


class CasoDesconocido(ValueError):
    """Se pidió un id de escenario que no existe (422)."""


@lru_cache
def cargar_escenarios() -> tuple[dict, ...]:
    return tuple(json.loads(RUTA_ESCENARIOS.read_text(encoding="utf-8"))["casos"])


def _por_id() -> dict[str, dict]:
    return {caso["id"]: caso for caso in cargar_escenarios()}


def listar_escenarios() -> list[dict[str, Any]]:
    claves = ("id", "conjunto", "clase", "nombre", "decidible_v2", "decidible_con_contexto", "motivo_no_decidible",
              "alertas_a_enviar")
    return [{**{k: caso[k] for k in claves}, "costo_llm": 1} for caso in cargar_escenarios()]


def _seleccionar(casos: list[str] | None, conjunto: str | None) -> list[str]:
    if conjunto is not None:
        return [c["id"] for c in cargar_escenarios() if conjunto == "todos" or c["conjunto"] == conjunto]
    desconocidos = [i for i in casos if i not in _por_id()]
    if desconocidos:
        raise CasoDesconocido(f"Escenarios desconocidos: {desconocidos}")
    return list(dict.fromkeys(casos))


def iniciar_corrida(
    casos: list[str] | None,
    conjunto: str | None,
    usar_llm: bool,
    repeticiones: int,
    enviar_correos: bool,
    programar: Callable[..., Any],
    variante: str = "sin_contexto",
    modo_decision: str = "autonomo_acotado",
    umbral_confianza: float = 0.85,
) -> dict[str, Any]:
    """Valida, reserva la corrida y la agenda en segundo plano con `programar`."""
    seleccion = _seleccionar(casos, conjunto)
    total = len(seleccion) * repeticiones
    costo = total if usar_llm else 0
    if usar_llm:
        if not configuracion.llm_habilitado():
            raise CorridaRechazada("El LLM está apagado en la configuración global: enciéndelo o ejecuta sin LLM.")
        restantes = presupuesto.estado_cupo()["restantes"]
        if costo > restantes:
            raise CorridaRechazada(f"La corrida costaría {costo} peticiones al LLM y quedan {restantes} hoy.")
    if not _candado.acquire(blocking=False):
        raise CorridaEnCurso("Ya hay una corrida del laboratorio en curso.")
    try:
        corrida_id = repository_laboratorio.crear_corrida(
            seleccion, conjunto, usar_llm, enviar_correos, repeticiones, costo, variante, VERSIONES[variante],
            modo_decision, float(umbral_confianza),
        )
        programar(ejecutar_corrida, corrida_id)
    except Exception:
        _candado.release()
        raise
    return {"corrida_id": corrida_id, "total": total, "costo_llm": costo}


def _ms(desde: datetime | None, hasta: datetime | None) -> int | None:
    return int((hasta - desde).total_seconds() * 1000) if desde and hasta else None


def _ejecutar_caso(corrida_id: int, caso: dict, repeticion: int, variante: str = "sin_contexto") -> dict[str, Any]:
    ambito = AmbitoPrueba(corrida_id, caso["id"], repeticion)
    con_contexto = variante == "con_contexto"
    if con_contexto:
        siembra.sembrar(ambito, caso)  # historial literal de la v1, antes de la alerta del caso
    explicaciones: list[int] = []

    def en_linea(funcion: Callable, incidente_id: int, *args, **kwargs) -> None:
        explicaciones.append(incidente_id)  # el pipeline corre aquí mismo para poder leer su resultado
        funcion(incidente_id, *args, **kwargs)

    incidentes = set()
    for _ in range(caso["alertas_a_enviar"]):
        payload = {**caso["payload"], "timestamp": datetime.now(timezone.utc).isoformat()}
        incidentes.add(alertas.recibir(AlertaEntrada.model_validate(payload), en_linea, ambito=ambito))
    incidente_id = min(incidentes)
    incidente = repository.obtener_incidente(incidente_id)
    obtenido = incidente["veredicto"]

    detalle = None
    if caso["alertas_a_enviar"] > 1:
        detalle = {
            "incidentes": len(incidentes),
            "alertas": incidente["contador_alertas"],
            "explicaciones": len(explicaciones),
        }
        detalle["agrupacion_ok"] = detalle == {
            "incidentes": 1, "alertas": caso["alertas_a_enviar"], "explicaciones": 1,
        }
    return {
        "corrida_id": corrida_id,
        "caso_id": caso["id"],
        "repeticion": repeticion,
        "incidente_id": incidente_id,
        "decidible": caso["decidible_v2"] or (con_contexto and caso["decidible_con_contexto"]),
        "veredicto_esperado": caso["esperado"]["veredicto"],
        "veredicto_obtenido": obtenido,
        "acierto": obtenido in caso["esperado"]["aceptables"],
        "fuente": incidente["fuente_explicacion"],
        "motivo_respaldo": incidente["motivo_respaldo"],
        "latencia_explicacion_ms": _ms(incidente["t_recibido"], incidente["t_explicado"]),
        "detalle": detalle,
        # C7 simulado: nunca se llama a Ryu (los motivos quedan en el incidente del caso)
        "accion_propuesta": incidente["accion_propuesta"],
        "decision_politica": incidente["decision_politica"],
        "accion_final": incidente["accion_final"],
        # Fase 6.8a+b: el acierto es sobre el veredicto FINAL; además se guarda lo que dijo el LLM
        "veredicto_llm": incidente["veredicto_llm"],
        "dictamen_historial": incidente["dictamen_historial"],
        "verificacion": ((incidente["informe"] or {}).get("razonamiento") or {}).get("verificacion", {}).get("resultado"),
    }


def ejecutar_corrida(corrida_id: int) -> None:
    """Corre en segundo plano. Siempre libera la corrida, aunque algo falle."""
    try:
        corrida = repository_laboratorio.obtener_corrida(corrida_id)
        casos = _por_id()
        total = len(corrida["casos"]) * corrida["repeticiones"]
        actual = 0
        for caso_id in corrida["casos"]:
            for repeticion in range(1, corrida["repeticiones"] + 1):
                repository_laboratorio.guardar_resultado(
                    _ejecutar_caso(corrida_id, casos[caso_id], repeticion, corrida["variante"])
                )
                actual += 1
                eventos.emitir(
                    "prueba_progreso",
                    {"tipo": "escenario", "corrida_id": corrida_id, "actual": actual, "total": total,
                     "caso_id": caso_id, "repeticion": repeticion},
                )
        resumen = resultados_lab.resumen_corrida(corrida_id)
        repository_laboratorio.terminar_corrida(corrida_id, "terminada", resumen)
        eventos.emitir(
            "prueba_terminada",
            {"tipo": "escenario", "corrida_id": corrida_id, "estado": "terminada",
             "exactitud": resumen["exactitud_decidibles"]["global"]},
        )
    except Exception as error:
        logger.exception("Falló la corrida %s del laboratorio", corrida_id)
        repository_laboratorio.terminar_corrida(corrida_id, "fallida", None, str(error)[:200])
        eventos.emitir("prueba_terminada", {"tipo": "escenario", "corrida_id": corrida_id, "estado": "fallida"})
    finally:
        if _candado.locked():
            _candado.release()


def listar_corridas() -> list[dict[str, Any]]:
    return repository_laboratorio.listar_corridas(20)


def obtener_corrida(corrida_id: int) -> dict[str, Any] | None:
    corrida = repository_laboratorio.obtener_corrida(corrida_id)
    if corrida is None:
        return None
    return {**corrida, "resultados": repository_laboratorio.resultados_de(corrida_id)}
