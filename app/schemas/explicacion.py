"""Salida del LLM v3 (Fase 6.8a+b): SOLO interpretación. Los hechos, umbrales, nombres MITRE, dictámenes y
datos de auditoría los pone el código (services/informe.py). También es la forma del respaldo por reglas.

Textos largos se recortan; valores fuera de rango invalidan la salida (→ respaldo por reglas).
tecnica_id y accion_propuesta se aceptan como texto y el código los normaliza a sus listas cerradas.
Fase D3: SalidaLLMv4 agrega los 3 bloques de la explicación simple, OPCIONALES: si faltan, salen de las plantillas
(services/explicacion_simple.py) sin descartar el diagnóstico del LLM.
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


def _texto_simple(maximo: int):
    """Texto no vacío recortado a `maximo`; vacío, en blanco o de otro tipo → None (sale de la plantilla)."""
    return BeforeValidator(lambda v: v.strip()[:maximo] or None if isinstance(v, str) else None)


def _lista_de_textos(valor: Any) -> list[str] | None:
    if isinstance(valor, str):
        valor = [valor]
    if not isinstance(valor, list):
        return None
    return [t.strip()[:180] for t in valor if isinstance(t, str) and t.strip()][:3] or None


class SalidaLLMv4(SalidaLLMv3):
    que_paso: Annotated[str | None, _texto_simple(250)] = None
    por_que: Annotated[list[str] | None, BeforeValidator(_lista_de_textos)] = None  # 2 o 3 frases de ≤ 180
    podria_equivocarnos: Annotated[str | None, _texto_simple(300)] = None
