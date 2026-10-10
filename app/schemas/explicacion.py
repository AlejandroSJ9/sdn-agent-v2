"""Contrato C4: salida esperada del LLM (y del respaldo por reglas). Ver docs/contratos.md."""

from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field


def _recortar(maximo: int):
    return AfterValidator(lambda valor: valor[:maximo])


class ExplicacionC4(BaseModel):
    """Textos largos se recortan; valores fuera de rango invalidan la salida. Claves extra se ignoran."""

    model_config = ConfigDict(extra="ignore")

    veredicto: Literal["confirmado", "falso_positivo", "indeterminado"]
    severidad: Literal["critica", "alta", "media"]
    confianza: Annotated[float, Field(ge=0.0, le=1.0, allow_inf_nan=False)]
    tecnica_mitre: Annotated[str, Field(pattern=r"^T\d{4}(\.\d{3})?$")]
    explicacion: Annotated[str, _recortar(600)]
    evidencias: Annotated[list[Annotated[str, _recortar(200)]], _recortar(5)]
    recomendacion_operador: Annotated[str, _recortar(300)]
