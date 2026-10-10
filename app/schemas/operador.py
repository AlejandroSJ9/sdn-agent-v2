"""Cuerpos de las acciones del operador (desbloqueo y configuración en caliente)."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StringConstraints


class DesbloqueoEntrada(BaseModel):
    confirmacion: str  # debe ser exactamente la IP (se verifica en el servicio → 400)
    motivo: Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=200)]


class ConfigActualizar(BaseModel):
    """Solo los valores editables en caliente; cualquier otra clave → 422."""

    model_config = ConfigDict(extra="forbid")

    LLM_HABILITADO: StrictBool | None = None
    DEDUP_SEGUNDOS: Annotated[StrictInt, Field(ge=10, le=3600)] | None = None
