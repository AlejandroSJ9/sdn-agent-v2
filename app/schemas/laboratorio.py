"""Laboratorio: ámbito de aislamiento de las alertas de prueba y cuerpos de sus endpoints."""

from typing import Annotated, Literal, NamedTuple

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator


class AmbitoPrueba(NamedTuple):
    """Una alerta de prueba solo se agrupa con incidentes de la MISMA corrida, caso y repetición."""

    corrida_id: int
    caso_id: str
    repeticion: int


class CorridaEntrada(BaseModel):
    model_config = ConfigDict(extra="forbid")

    casos: Annotated[list[str], Field(min_length=1)] | None = None
    conjunto: Literal["desarrollo", "validacion", "todos"] | None = None
    usar_llm: StrictBool = False
    repeticiones: Annotated[StrictInt, Field(ge=1, le=5)] = 1
    enviar_correos: StrictBool = False

    @model_validator(mode="after")
    def _casos_o_conjunto(self) -> "CorridaEntrada":
        if (self.casos is None) == (self.conjunto is None):
            raise ValueError("Indica 'casos' o 'conjunto' (uno de los dos)")
        return self


class PytestEntrada(BaseModel):
    """Solo estas dos carpetas: nunca se arma un comando con texto del usuario."""

    model_config = ConfigDict(extra="forbid")

    carpeta: Literal["unit", "integracion"]
