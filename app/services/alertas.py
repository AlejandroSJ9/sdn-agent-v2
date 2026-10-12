"""Recepción de alertas de Ryu: crea o agrupa incidentes, audita y dispara el pipeline."""

import threading
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from app.config import get_settings
from app.db import repository, repository_acciones, repository_operador
from app.schemas.alerta import AlertaEntrada
from app.schemas.laboratorio import AmbitoPrueba
from app.schemas.notificacion import IncidenteDetalle
from app.services import auditoria, configuracion, eventos, pipeline, presupuesto

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
            "acciones": repository_acciones.acciones_de_incidente(incidente_id),
        }
    )


VEREDICTOS = ("confirmado", "indeterminado", "falso_positivo")


def agregados_tablero(filas: list[dict], hoy: datetime, inicio_24h: datetime) -> dict[str, Any]:
    """Veredictos y tiempo medio t_ryu→t_explicado de hoy, e incidentes por hora (24 franjas desde inicio_24h)."""
    de_hoy = [f for f in filas if f["t_recibido"] >= hoy]
    duraciones = [(f["t_explicado"] - f["t_ryu"]).total_seconds() * 1000
                  for f in de_hoy if f["t_ryu"] and f["t_explicado"]]
    por_hora = [0] * 24
    for f in filas:
        if f["t_recibido"] >= inicio_24h:
            por_hora[min(int((f["t_recibido"] - inicio_24h).total_seconds() // 3600), 23)] += 1
    return {
        "veredictos_hoy": {v: sum(f["veredicto"] == v for f in de_hoy) for v in VEREDICTOS},
        "tiempo_medio_explicacion_ms": round(sum(duraciones) / len(duraciones)) if duraciones else None,
        "incidentes_por_hora": [
            {"hora": (inicio_24h + timedelta(hours=i)).isoformat().replace("+00:00", "Z"), "cantidad": n}
            for i, n in enumerate(por_hora)
        ],
    }


def resumen() -> dict:
    """Números de las tarjetas del panel y agregados del tablero (solo lectura). "Hoy" = día UTC actual."""
    ahora = datetime.now(timezone.utc)
    hoy = ahora.replace(hour=0, minute=0, second=0, microsecond=0)
    inicio_24h = ahora.replace(minute=0, second=0, microsecond=0) - timedelta(hours=23)
    filas = repository_operador.tiempos_reales_desde(min(hoy, inicio_24h))
    cupo = presupuesto.estado_cupo()
    return {
        **repository_operador.resumen_panel(hoy),
        **agregados_tablero(filas, hoy, inicio_24h),
        "recomendaciones_pendientes": repository_acciones.contar_pendientes(),
        "llm": {"habilitado": configuracion.obtener()["LLM_HABILITADO"], "modelo": get_settings().LLM_MODELO,
                "usadas": cupo["usadas"], "cupo": cupo["cupo"]},
    }
