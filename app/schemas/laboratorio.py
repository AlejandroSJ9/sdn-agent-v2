"""Laboratorio: ámbito de aislamiento de las alertas de prueba y cuerpos de sus endpoints."""

from typing import Annotated, Literal, NamedTuple

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, model_validator


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
    variante: Literal["sin_contexto", "con_contexto"] = "sin_contexto"  # no cambia la configuración global
    # C7 en el laboratorio: SIEMPRE simulado (asume A6, A7 y A8) y nunca llama a Ryu.
    # autonomo_acotado se retiró en la Fase D1 (el servicio responde 422); umbral_confianza se acepta sin efecto.
    modo_decision: Literal["solo_explicar", "recomendar", "autonomo_acotado"] = "recomendar"
    umbral_confianza: Annotated[StrictFloat | StrictInt, Field(ge=0.5, le=1.0)] = 0.85

    @model_validator(mode="after")
    def _casos_o_conjunto(self) -> "CorridaEntrada":
        if (self.casos is None) == (self.conjunto is None):
            raise ValueError("Indica 'casos' o 'conjunto' (uno de los dos)")
        return self


class PytestEntrada(BaseModel):
    """Solo estas dos carpetas: nunca se arma un comando con texto del usuario."""

    model_config = ConfigDict(extra="forbid")

    carpeta: Literal["unit", "integracion"]
