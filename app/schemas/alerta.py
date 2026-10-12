"""Contrato C1 (Ryu → agente) y respuestas de alertas e incidentes. Ver docs/contratos.md."""

import ipaddress
import math
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
    ValidationError,
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


def _validar_contexto(valor: Any) -> str | dict[str, Any]:
    """Ryu-IA real lo envía como texto (ej. "VLAN OPS (ID=10), subred 10.0.10.0/24"); se acepta también objeto."""
    if isinstance(valor, dict) or (isinstance(valor, str) and len(valor) <= 300):
        return valor
    raise ValueError("debe ser texto (máx. 300 caracteres) u objeto")


FechaUTC = Annotated[datetime, AfterValidator(_a_utc)]
IPv4 = Annotated[str, AfterValidator(_validar_ipv4)]
ContextoTopologico = Annotated[Any, AfterValidator(_validar_contexto)]


class Metricas(BaseModel):
    """Los conteos aceptan 24.0 (Ryu-IA los envía como float) y se normalizan a 24; 24.5 es inválido.

    M3_duracion_seg y M3_bytes (RYU_IA v2) quedan fuera a propósito: se guardan en crudo y no llegan al LLM.
    """

    model_config = ConfigDict(extra="ignore")

    M1_nuevos_flujos_por_30s: NonNegativeInt
    M2_destinos_unicos_por_30s: NonNegativeInt
    M3_smb_anomalo: bool
    M4_packet_in_por_min: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    M5_flujos_east_west: bool
    M5_count: NonNegativeInt


class AlertaEntrada(BaseModel):
    """Payload C1 validado. Los campos desconocidos y solicitud_al_agente quedan fuera del modelo.

    puntaje_total, puntaje_maximo y pesos_metricas (RYU_IA v2) también quedan fuera: solo en crudo.
    """

    model_config = ConfigDict(extra="ignore")

    timestamp: FechaUTC
    host_origen: IPv4
    metricas: Metricas
    alerta_nivel: str | None = None
    dpid_switch: str | None = None
    contexto_topologico: ContextoTopologico = Field(default_factory=dict)
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


# Errores de Pydantic en español claro. Los usan el endpoint (422) y scripts/validar_payload_ryu.py.
_PROBLEMAS = {
    "missing": "campo obligatorio ausente",
    "greater_than_equal": "debe ser mayor o igual que 0",
    "int_from_float": "debe ser un número entero (sin parte decimal)",
    "int_parsing": "debe ser un número entero",
    "int_type": "debe ser un número entero",
    "float_parsing": "debe ser un número",
    "float_type": "debe ser un número",
    "finite_number": "no puede ser NaN ni infinito",
    "bool_parsing": "debe ser true o false",
    "bool_type": "debe ser true o false",
    "string_type": "debe ser texto",
    "list_type": "debe ser una lista",
    "model_type": "debe ser un objeto JSON",
    "model_attributes_type": "debe ser un objeto JSON",
    "datetime_parsing": "debe ser una fecha ISO-8601",
    "datetime_from_date_parsing": "debe ser una fecha ISO-8601",
    "datetime_type": "debe ser una fecha ISO-8601",
}


def _valor_mostrable(valor: Any) -> Any:
    """NaN/infinito no son JSON válido: se muestran como texto ("nan", "inf")."""
    if isinstance(valor, float) and not math.isfinite(valor):
        return str(valor)
    return valor


def describir_errores(error: ValidationError) -> list[dict[str, Any]]:
    """[{campo, problema, valor}] por cada error. `valor` es None si el campo no llegó."""
    salida = []
    for detalle in error.errors(include_url=False):
        tipo = detalle["type"]
        if tipo == "value_error":
            problema = str(detalle.get("ctx", {}).get("error", detalle["msg"]))
        else:
            problema = _PROBLEMAS.get(tipo, detalle["msg"])
        campo = ".".join(str(parte) for parte in detalle["loc"]) or "(cuerpo)"
        salida.append({"campo": campo, "problema": problema, "valor": None if tipo == "missing" else _valor_mostrable(detalle.get("input"))})
    return salida


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
    # Fase 6.7 (C6)
    clasificacion_operador: str | None = None
    motivo_operador: str | None = None
    variante: str | None = None
    version_prompt: str | None = None
    contexto_usado: dict[str, Any] | None = None
    sembrado: bool = False
    # Fase 6.8 (C4 extensión y C7)
    accion_propuesta: str | None = None
    justificacion_accion: str | None = None
    decision_politica: str | None = None
    motivos_politica: list[str] | None = None
    accion_final: str | None = None
    # Fase 6.8a+b ("veredicto" es el veredicto FINAL)
    informe: dict[str, Any] | None = None
    veredicto_llm: str | None = None
    dictamen_historial: str | None = None
