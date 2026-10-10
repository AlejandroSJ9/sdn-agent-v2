"""Contrato C3 (agente → Apps Script), resultado de un envío y detalle de incidente con su notificación."""

from typing import Literal

from pydantic import BaseModel

from app.schemas.alerta import FechaUTC, IncidenteSalida


class PayloadC3(BaseModel):
    tipo: Literal["incidente", "desbloqueo", "prueba"]
    incidente_id: int | None = None  # null solo en "prueba"
    fecha: FechaUTC
    host: str
    severidad: str | None = None  # null en "prueba" o si el incidente no llegó a explicarse
    veredicto: str | None = None
    asunto: str
    mensaje: str
    enlace_panel: str


class ResultadoNotificacion(BaseModel):
    estado: Literal["enviada", "fallida", "omitida"]
    intentos: int
    codigo_http: int | None = None
    error: str | None = None
    latencia_ms: int = 0


class NotificacionResumen(BaseModel):
    estado: str
    intentos: int
    ts: FechaUTC


class IncidenteDetalle(IncidenteSalida):
    """Detalle de un incidente: IncidenteSalida + su última notificación."""

    ultima_notificacion: NotificacionResumen | None = None
