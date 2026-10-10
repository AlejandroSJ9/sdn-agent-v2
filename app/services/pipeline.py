"""Pipeline de un incidente nuevo. Corre en segundo plano (BackgroundTasks): explica con LLM o por reglas.

El veredicto es informativo: aquí nada toca el bloqueo ni el estado del incidente.
"""

import logging
from datetime import datetime, timezone

from pydantic import ValidationError

from app.config import get_settings
from app.db import repository
from app.integrations.llm import ClienteLLM, ClienteOpenRouter, LLMError, RespuestaLLM
from app.schemas.alerta import AlertaEntrada
from app.schemas.explicacion import ExplicacionC4
from app.services import auditoria, configuracion, notificacion, presupuesto
from app.services.explicacion_reglas import explicar_por_reglas
from app.services.prompt import SYSTEM_PROMPT, construir_mensaje_usuario

logger = logging.getLogger(__name__)

Intento = tuple[ExplicacionC4 | None, str | None, RespuestaLLM | None]  # (explicación, motivo_respaldo, respuesta)


def obtener_cliente_llm() -> ClienteLLM:
    """Punto de inyección: las pruebas lo reemplazan por un cliente falso."""
    ajustes = get_settings()
    return ClienteOpenRouter(ajustes.OPENROUTER_API_KEY, ajustes.LLM_MODELO, ajustes.LLM_TIMEOUT)


def _intentar_llm(alerta: AlertaEntrada, alertas_agrupadas: int, cliente: ClienteLLM | None) -> Intento:
    ajustes = get_settings()
    if not configuracion.llm_habilitado():
        return None, "llm_deshabilitado", None
    if not ajustes.OPENROUTER_API_KEY:
        return None, "sin_api_key", None
    if not presupuesto.puede_llamar():
        return None, "sin_cupo", None

    cliente = cliente or obtener_cliente_llm()
    try:
        respuesta = cliente.completar(SYSTEM_PROMPT, construir_mensaje_usuario(alerta, alertas_agrupadas))
    except LLMError as error:
        presupuesto.registrar_uso(0, 0, 0, exito=False, peticiones=max(error.peticiones, 1))
        return None, error.motivo, None

    try:
        explicacion = ExplicacionC4.model_validate_json(respuesta.texto)
        motivo = None
    except ValidationError as error:
        explicacion = None
        motivo = "json_invalido" if any(e["type"] == "json_invalid" for e in error.errors()) else "schema_invalido"
    presupuesto.registrar_uso(
        respuesta.tokens_entrada, respuesta.tokens_salida, respuesta.latencia_ms, motivo is None, respuesta.peticiones
    )
    return explicacion, motivo, respuesta


def procesar(incidente_id: int, cliente: ClienteLLM | None = None) -> None:
    """Explica el incidente y lo audita. Nunca lanza excepción: cualquier error del LLM termina en reglas."""
    try:
        datos = repository.obtener_datos_para_explicar(incidente_id)
        if datos is None:
            logger.warning("Incidente %s sin alertas: no hay nada que explicar", incidente_id)
            return
        payload, alertas_agrupadas = datos
        alerta = AlertaEntrada.model_validate(payload)  # al prompt solo llegan campos validados

        try:
            explicacion, motivo, respuesta = _intentar_llm(alerta, alertas_agrupadas, cliente)
        except Exception:
            logger.exception("Error inesperado al usar el LLM en el incidente %s", incidente_id)
            explicacion, motivo, respuesta = None, "error_interno", None
        if explicacion is None:
            explicacion = explicar_por_reglas(alerta)
        fuente = "llm" if motivo is None else "reglas"

        repository.guardar_explicacion(
            incidente_id,
            explicacion.model_dump(),
            fuente,
            motivo,
            datetime.now(timezone.utc),
            respuesta.tokens_entrada if respuesta else None,
            respuesta.tokens_salida if respuesta else None,
            respuesta.latencia_ms if respuesta else None,
        )
        auditoria.registrar(
            "explicacion_generada", {"incidente_id": incidente_id, "fuente": fuente, "motivo_respaldo": motivo}
        )
        notificacion.notificar(incidente_id, "incidente")  # nunca lanza; la explicación ya quedó guardada
    except Exception:
        logger.exception("Falló el pipeline del incidente %s", incidente_id)
