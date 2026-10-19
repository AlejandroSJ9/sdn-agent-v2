"""Cuerpos de las acciones del operador (desbloqueo y configuración en caliente)."""

from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StringConstraints,
)

from app.schemas.alerta import IPv4


def _sin_repetidos(hosts: list[str]) -> list[str]:
    return list(dict.fromkeys(hosts))


Motivo = Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=200)]


class DesbloqueoEntrada(BaseModel):
    confirmacion: str  # debe ser exactamente la IP (se verifica en el servicio → 400)
    motivo: Motivo
    # C6: lo que el operador concluyó; alimenta el contexto de incidentes futuros del host
    clasificacion: Literal["falso_positivo", "amenaza_contenida", "otro"]


class ConfigActualizar(BaseModel):
    """Solo los valores editables en caliente; cualquier otra clave → 422."""

    model_config = ConfigDict(extra="forbid")

    LLM_HABILITADO: StrictBool | None = None
    DEDUP_SEGUNDOS: Annotated[StrictInt, Field(ge=10, le=3600)] | None = None
    CONTEXTO_HABILITADO: StrictBool | None = None
    HISTORIAL_DIAS: Annotated[StrictInt, Field(ge=1, le=90)] | None = None
    CAMPANA_MINUTOS: Annotated[StrictInt, Field(ge=5, le=240)] | None = None
    # C7: motor de políticas
    MODO_DECISION: Literal["solo_explicar", "recomendar", "autonomo_acotado"] | None = None
    UMBRAL_CONFIANZA: Annotated[StrictFloat | StrictInt, Field(ge=0.5, le=1.0)] | None = None  # ejecución (9b)
    # C7 (Fase 9a): escalera; además HITL < VLAN (se valida contra los vigentes en el servicio → 422)
    ESCALON_HITL: Annotated[StrictFloat | StrictInt, Field(ge=0.5, le=1.0)] | None = None
    ESCALON_VLAN: Annotated[StrictFloat | StrictInt, Field(ge=0.5, le=1.0)] | None = None
    MAX_ACCIONES_POR_MIN: Annotated[StrictInt, Field(ge=1, le=20)] | None = None
    HOSTS_PROTEGIDOS: Annotated[list[IPv4], Field(max_length=100), AfterValidator(_sin_repetidos)] | None = None
    RECOMENDACION_TTL_MIN: Annotated[StrictInt, Field(ge=5, le=1440)] | None = None


class RechazoEntrada(BaseModel):
    """Rechazar una recomendación exige un motivo (5–200)."""

    model_config = ConfigDict(extra="forbid")

    motivo: Motivo
