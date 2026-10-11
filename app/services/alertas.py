"""Recepción de alertas de Ryu: crea o agrupa incidentes, audita y dispara el pipeline."""

import threading
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from app.db import repository, repository_operador
from app.schemas.alerta import AlertaEntrada
from app.schemas.laboratorio import AmbitoPrueba
from app.schemas.notificacion import IncidenteDetalle
from app.services import auditoria, configuracion, eventos, pipeline

# Buscar-o-crear debe ser atómico: sin el lock, dos alertas simultáneas del mismo host abrirían
# dos incidentes (y dos llamadas al LLM). Basta porque el agente corre en un solo proceso.
_candado = threading.Lock()


def _marca_prueba(ambito: AmbitoPrueba | None) -> dict:
    """Datos extra de auditoría/evento para las alertas del laboratorio (las reales no cambian)."""
    return {} if ambito is None else {"origen": "prueba", "corrida_id": ambito.corrida_id, "caso_id": ambito.caso_id}


def _emitir_alerta(incidente_id: int, host: str, agrupada: bool, contador: int, ambito: AmbitoPrueba | None) -> None:
    eventos.emitir(
        "alerta_recibida",
        {
            "incidente_id": incidente_id,
            "host_origen": host,
            "agrupada": agrupada,
            "contador_alertas": contador,
            **_marca_prueba(ambito),
        },
    )


def recibir(alerta: AlertaEntrada, programar: Callable[..., Any], ambito: AmbitoPrueba | None = None) -> int:
    """Guarda la alerta en un incidente abierto del mismo host dentro de DEDUP_SEGUNDOS o en uno nuevo.

    Solo un incidente nuevo va al pipeline; `programar` lo agenda (BackgroundTasks.add_task en la API).
    El laboratorio usa esta MISMA función con `ambito`, que aísla la agrupación de sus incidentes.
    """
    t_recibido = datetime.now(timezone.utc)
    with _candado:
        incidente_id = repository.buscar_incidente_abierto_reciente(
            alerta.host_origen, configuracion.dedup_segundos(), ambito
        )
        if incidente_id is not None:
            contador = repository.agregar_alerta_a_incidente(incidente_id, alerta.crudo, alerta.timestamp, t_recibido)
            auditoria.registrar(
                "alerta_agrupada",
                {
                    "incidente_id": incidente_id,
                    "host_origen": alerta.host_origen,
                    "contador_alertas": contador,
                    **_marca_prueba(ambito),
                },
            )
            _emitir_alerta(incidente_id, alerta.host_origen, agrupada=True, contador=contador, ambito=ambito)
            return incidente_id
        incidente_id = repository.crear_incidente(
            host_origen=alerta.host_origen,
            dpid_switch=alerta.dpid_switch,
            alerta_nivel=alerta.alerta_nivel,
            t_ryu=alerta.timestamp,
            t_recibido=t_recibido,
            payload=alerta.crudo,
            ambito=ambito,
        )
        auditoria.registrar(
            "alerta_recibida",
            {"incidente_id": incidente_id, "host_origen": alerta.host_origen, **_marca_prueba(ambito)},
        )
        _emitir_alerta(incidente_id, alerta.host_origen, agrupada=False, contador=1, ambito=ambito)
    programar(pipeline.procesar, incidente_id)
    return incidente_id


def listar_incidentes(limite: int, offset: int, origen: str = "real") -> list[IncidenteDetalle]:
    """Página de incidentes con su última notificación (una sola consulta extra para toda la página)."""
    filas = repository.listar_incidentes(limite, offset, origen)
    notificaciones = repository_operador.ultimas_notificaciones([fila["id"] for fila in filas])
    return [IncidenteDetalle.model_validate({**f, "ultima_notificacion": notificaciones.get(f["id"])}) for f in filas]


def obtener_incidente(incidente_id: int) -> IncidenteDetalle | None:
    """Detalle con la última notificación y las métricas VALIDADAS de la alerta que originó el incidente."""
    fila = repository.obtener_incidente(incidente_id)
    if fila is None:
        return None
    datos = repository.obtener_datos_para_explicar(incidente_id)
    origen = AlertaEntrada.model_validate(datos[0]) if datos else None
    return IncidenteDetalle.model_validate(
        {
            **fila,
            "ultima_notificacion": repository.ultima_notificacion(incidente_id),
            "metricas": origen.metricas if origen else None,
            "umbrales_superados": origen.umbrales_superados if origen else None,
        }
    )


def resumen() -> dict:
    """Números de las tarjetas del panel. "Hoy" = día UTC actual."""
    hoy = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return repository_operador.resumen_panel(hoy)
