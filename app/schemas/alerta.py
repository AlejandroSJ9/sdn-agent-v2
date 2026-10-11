"""Contrato C1 (Ryu → agente) y respuestas de alertas e incidentes. Ver docs/contratos.md."""

import ipaddress
from datetime import datetime, timezone
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    ModelWrapValidatorHandler,
    NonNegativeInt,
    PrivateAttr,
    model_validator,
)

# Campo de Ryu que el agente ignora siempre (CLAUDE.md §1): no entra al modelo ni se guarda.
CAMPO_IGNORADO = "solicitud_al_agente"


def _a_utc(valor: datetime) -> datetime:
    """Sin zona horaria se asume UTC; con zona se convierte a UTC."""
    if valor.tzinfo is None:
        return valor.replace(tzinfo=timezone.utc)
    return valor.astimezone(timezone.utc)


def _validar_ipv4(valor: str) -> str:
    try:
        return str(ipaddress.IPv4Address(valor))
    except ValueError as error:
        raise ValueError("debe ser una dirección IPv4 válida") from error


FechaUTC = Annotated[datetime, AfterValidator(_a_utc)]
IPv4 = Annotated[str, AfterValidator(_validar_ipv4)]


class Metricas(BaseModel):
    model_config = ConfigDict(extra="ignore")

    M1_nuevos_flujos_por_30s: NonNegativeInt
    M2_destinos_unicos_por_30s: NonNegativeInt
    M3_smb_anomalo: bool
    M4_packet_in_por_min: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    M5_flujos_east_west: bool
    M5_count: NonNegativeInt


class AlertaEntrada(BaseModel):
    """Payload C1 validado. Los campos desconocidos y solicitud_al_agente quedan fuera del modelo."""

    model_config = ConfigDict(extra="ignore")

    timestamp: FechaUTC
    host_origen: IPv4
    metricas: Metricas
    alerta_nivel: str | None = None
    dpid_switch: str | None = None
    contexto_topologico: dict[str, Any] = Field(default_factory=dict)
    umbrales_superados: list[str] = Field(default_factory=list)
    ioc_activados: list[str] = Field(default_factory=list)
    accion_ryu_previa: str | None = None

    _crudo: dict[str, Any] = PrivateAttr(default_factory=dict)

    @model_validator(mode="wrap")
    @classmethod
    def _guardar_crudo(cls, datos: Any, handler: ModelWrapValidatorHandler["AlertaEntrada"]) -> "AlertaEntrada":
        alerta = handler(datos)
        if isinstance(datos, dict):
            alerta._crudo = {clave: valor for clave, valor in datos.items() if clave != CAMPO_IGNORADO}
        return alerta

    @property
    def crudo(self) -> dict[str, Any]:
        """Cuerpo tal como llegó (incluye campos desconocidos) sin solicitud_al_agente. Solo para guardar."""
        return self._crudo


class AlertaRespuesta(BaseModel):
    """Respuesta 202 a Ryu. Los Literal impiden devolver otra acción que no sea mantener el bloqueo."""

    estado: Literal["recibido"] = "recibido"
    incidente_id: int
    acciones_ryu: list[Literal["mantener_bloqueo"]] = ["mantener_bloqueo"]


class IncidenteSalida(BaseModel):
    """Incidente tal como lo ve el operador. contador_alertas = alertas agrupadas en él."""

    id: int
    host_origen: str
    dpid_switch: str | None
    alerta_nivel: str | None
    veredicto: str | None
    severidad: str | None
    confianza: float | None
    tecnica_mitre: str | None
    explicacion: str | None
    evidencias: list[str] | None
    recomendacion_operador: str | None
    fuente_explicacion: str | None
    t_ryu: FechaUTC | None
    t_recibido: FechaUTC
    t_explicado: FechaUTC | None
    t_notificado: FechaUTC | None
    t_desbloqueado: FechaUTC | None
    contador_alertas: int
    origen: str = "real"  # real | prueba (laboratorio)
    corrida_id: int | None = None
    caso_id: str | None = None
