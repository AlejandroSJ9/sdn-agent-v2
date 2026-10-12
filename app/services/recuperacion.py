"""Recuperación al arrancar: lo que quedó a medias si el agente se detuvo.

- Corridas del laboratorio y ejecuciones de pytest que seguían "en_curso" → "interrumpida".
- Recomendaciones pendientes vencidas → "expirada" (Fase 6.8).
- Incidentes REALES sin explicar (t_explicado nulo) → se reprocesan en segundo plano con el pipeline
  normal, que respeta el cupo y LLM_HABILITADO y envía el correo pendiente. Los de prueba no se
  reprocesan: gastarían cupo de una corrida que ya no existe.
"""

import logging
import threading
from collections.abc import Callable
from typing import Any

from app.db import repository_laboratorio, repository_operador
from app.services import auditoria, pipeline, recomendaciones

logger = logging.getLogger(__name__)


def _en_hilo(funcion: Callable, *argumentos: Any) -> None:
    threading.Thread(target=funcion, args=argumentos, daemon=True, name="recuperacion").start()


def _reprocesar(incidentes: list[int]) -> None:
    for incidente_id in incidentes:  # uno por uno: no compite por el cupo consigo mismo
        pipeline.procesar(incidente_id)


def recuperar_al_arrancar(programar: Callable[..., Any] = _en_hilo) -> dict[str, list[int]]:
    """Nunca impide el arranque: si algo falla, queda en el log."""
    try:
        resultado = {
            "incidentes_reprocesados": repository_operador.incidentes_reales_sin_explicar(),
            "corridas_interrumpidas": repository_laboratorio.interrumpir_corridas_en_curso(),
            "pytest_interrumpidas": repository_laboratorio.interrumpir_pytest_en_curso(),
            "recomendaciones_expiradas": recomendaciones.expirar(),
        }
        if any(resultado.values()):
            auditoria.registrar("recuperacion_al_arrancar", resultado)
            logger.warning("Recuperación al arrancar: %s", resultado)
        if resultado["incidentes_reprocesados"]:
            programar(_reprocesar, resultado["incidentes_reprocesados"])
        return resultado
    except Exception:
        logger.exception("Falló la recuperación al arrancar")
        return {"incidentes_reprocesados": [], "corridas_interrumpidas": [], "pytest_interrumpidas": [],
                "recomendaciones_expiradas": []}
