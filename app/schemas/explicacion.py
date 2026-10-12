"""Salida del LLM v3 (Fase 6.8a+b): SOLO interpretación. Los hechos, umbrales, nombres MITRE, dictámenes y
datos de auditoría los pone el código (services/informe.py). También es la forma del respaldo por reglas.

Textos largos se recortan; valores fuera de rango invalidan la salida (→ respaldo por reglas).
tecnica_id y accion_propuesta se aceptan como texto y el código los normaliza a sus listas cerradas.
"""

from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field


def _recortar(maximo: int):
    return AfterValidator(lambda valor: valor[:maximo])


def _texto_o_nada(valor: Any) -> str | None:
    return valor if isinstance(valor, str) else None


class FactorPonderado(BaseModel):
    model_config = ConfigDict(extra="ignore")

    factor: Annotated[str, _recortar(100)]
    peso: Literal["alto", "medio", "bajo"]
    porque: Annotated[str, _recortar(200)]


class SalidaLLMv3(BaseModel):
    model_config = ConfigDict(extra="ignore")

    veredicto: Literal["confirmado", "falso_positivo", "indeterminado"]
    severidad: Literal["critica", "alta", "media"]
    confianza: Annotated[float, Field(ge=0.0, le=1.0, allow_inf_nan=False)]
    tecnica_id: Annotated[str | None, BeforeValidator(_texto_o_nada)] = None
    hipotesis_fase: Annotated[str, _recortar(300)]
    # 2 a 4 factores: menos de 2 invalida la salida; más de 4 se recortan
    factores_ponderados: Annotated[list[FactorPonderado], Field(min_length=2), _recortar(4)]
    contexto_aplicado: Annotated[str, _recortar(300)] = "sin contexto disponible"
    descarte_benigno: Annotated[str, _recortar(300)]
    limitaciones: Annotated[list[Annotated[str, _recortar(200)]], _recortar(10)] = []
    accion_propuesta: Annotated[str | None, BeforeValidator(_texto_o_nada)] = None
    justificacion_accion: Annotated[str, BeforeValidator(lambda v: v if isinstance(v, str) else ""), _recortar(300)] = ""
    recomendacion_operador: Annotated[str, _recortar(300)]
