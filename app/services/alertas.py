"""Recepción de alertas de Ryu: crea o agrupa incidentes, audita y dispara el pipeline."""

import threading
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from app.config import get_settings
from app.db import repository
from app.schemas.alerta import AlertaEntrada, IncidenteSalida
from app.services import auditoria, pipeline

# Buscar-o-crear debe ser atómico: sin el lock, dos alertas simultáneas del mismo host abrirían
# dos incidentes (y dos llamadas al LLM). Basta porque el agente corre en un solo proceso.
_candado = threading.Lock()


def recibir(alerta: AlertaEntrada, programar: Callable[..., Any]) -> int:
    """Guarda la alerta en un incidente abierto del mismo host dentro de DEDUP_SEGUNDOS o en uno nuevo.

    Solo un incidente nuevo va al pipeline; `programar` lo agenda (BackgroundTasks.add_task en la API).
    """
    t_recibido = datetime.now(timezone.utc)
    with _candado:
        incidente_id = repository.buscar_incidente_abierto_reciente(alerta.host_origen, get_settings().DEDUP_SEGUNDOS)
        if incidente_id is not None:
            contador = repository.agregar_alerta_a_incidente(incidente_id, alerta.crudo, alerta.timestamp, t_recibido)
            auditoria.registrar(
                "alerta_agrupada",
                {"incidente_id": incidente_id, "host_origen": alerta.host_origen, "contador_alertas": contador},
            )
            return incidente_id
        incidente_id = repository.crear_incidente(
            host_origen=alerta.host_origen,
            dpid_switch=alerta.dpid_switch,
            alerta_nivel=alerta.alerta_nivel,
            t_ryu=alerta.timestamp,
            t_recibido=t_recibido,
            payload=alerta.crudo,
        )
        auditoria.registrar("alerta_recibida", {"incidente_id": incidente_id, "host_origen": alerta.host_origen})
    programar(pipeline.procesar, incidente_id)
    return incidente_id


def listar_incidentes(limite: int, offset: int) -> list[IncidenteSalida]:
    return [IncidenteSalida.model_validate(fila) for fila in repository.listar_incidentes(limite, offset)]


def obtener_incidente(incidente_id: int) -> IncidenteSalida | None:
    fila = repository.obtener_incidente(incidente_id)
    return IncidenteSalida.model_validate(fila) if fila else None
