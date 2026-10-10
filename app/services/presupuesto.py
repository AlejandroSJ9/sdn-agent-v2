"""Cupo diario del LLM, contado por día UTC en la tabla uso_llm contra LLM_CUPO_DIARIO."""

import threading
from datetime import datetime, timezone

from app.config import get_settings
from app.db import repository, repository_operador

# Mismo criterio que en la Fase 1: un lock basta porque el agente corre en un solo proceso.
_candado = threading.Lock()


def _hoy_utc() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def puede_llamar() -> bool:
    """Comprueba y RESERVA una petición del cupo del día en un solo paso atómico.

    Si solo comprobara, dos pipelines simultáneos con el cupo en 999/1000 pasarían ambos.
    """
    with _candado:
        return repository.reservar_peticion_llm(_hoy_utc(), get_settings().LLM_CUPO_DIARIO)


def registrar_uso(tokens_in: int, tokens_out: int, latencia_ms: int, exito: bool, peticiones: int = 1) -> None:
    """Completa el registro de una llamada ya reservada con puede_llamar().

    `peticiones` > 1 cuando el cliente repitió sin response_format: la petición extra también cuenta.
    `latencia_ms` no se guarda en uso_llm; queda por incidente (latencia_llm_ms).
    """
    with _candado:
        repository.registrar_uso_llm(_hoy_utc(), max(peticiones - 1, 0), tokens_in, tokens_out, fallida=not exito)


def estado_cupo() -> dict:
    fecha = _hoy_utc()
    uso = repository_operador.uso_llm_del_dia(fecha) or {"peticiones": 0, "fallidas": 0}
    cupo = get_settings().LLM_CUPO_DIARIO
    return {
        "fecha": fecha,
        "usadas": uso["peticiones"],
        "cupo": cupo,
        "restantes": max(cupo - uso["peticiones"], 0),
        "fallidas": uso["fallidas"],
    }
