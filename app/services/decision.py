"""Después de la explicación: evalúa la política (C7) sobre la propuesta ya resuelta y la aplica.

La propuesta llega del pipeline con el veredicto FINAL y la acción ya coherente con el dictamen y el modo
del host (Fase 6.8a); aquí no se reinterpreta nada.

ejecutar → acciones.ejecutar (simulada en el laboratorio); si falla, queda como recomendación con el error.
recomendar → recomendación pendiente (en el laboratorio solo se registra en el resultado del caso).
descartar → nada. "mantener" y el modo solo_explicar siempre descartan.
"""

from datetime import datetime, timedelta, timezone
from typing import Any

from app.db import repository, repository_acciones
from app.services import acciones, auditoria, configuracion, politica, recomendaciones

SIN_LIMITE = 10**6


def _contexto_sistema(laboratorio: dict | None) -> dict[str, Any]:
    if laboratorio is not None:  # simulado: asume A6 y usa los parámetros de la corrida
        return {"modo_decision": laboratorio["modo_decision"], "capacidad_a6": True,
                "umbral_confianza": laboratorio["umbral_confianza"], "hosts_protegidos": [],
                "acciones_ultimo_minuto": 0, "max_acciones_por_min": SIN_LIMITE}
    vigente = configuracion.politica()
    if vigente["modo_decision"] == "solo_explicar":  # el interruptor no consulta nada más
        return {**vigente, "capacidad_a6": False, "acciones_ultimo_minuto": 0}
    hace_un_minuto = datetime.now(timezone.utc) - timedelta(minutes=1)
    return {**vigente, "capacidad_a6": acciones.capacidad_a6(),
            "acciones_ultimo_minuto": repository_acciones.acciones_reales_de_la_politica_desde(hace_un_minuto)}


def decidir(incidente_id: int, propuesta: dict[str, Any], laboratorio: dict | None) -> dict[str, Any]:
    """propuesta: accion, confianza (de la acción), veredicto (FINAL), justificacion."""
    accion = propuesta["accion"]
    incidente = repository.obtener_incidente(incidente_id)
    es_laboratorio = laboratorio is not None
    decision = politica.evaluar(
        {"accion": accion, "confianza": propuesta["confianza"], "veredicto": propuesta["veredicto"]},
        {"host": incidente["host_origen"], "laboratorio": es_laboratorio},
        _contexto_sistema(laboratorio),
    )
    motivos = list(decision.motivos)
    if politica.MOTIVO_INCOMPATIBLE in motivos:  # descartada sin pasar por la bandeja: queda en la auditoría
        auditoria.registrar("accion_descartada", {
            "incidente_id": incidente_id, "accion": accion, "veredicto": propuesta["veredicto"],
            "motivo": politica.MOTIVO_INCOMPATIBLE,
        })
    sufijo = "_simulada" if es_laboratorio else ""

    if decision.resultado == "descartar":
        final = "ninguna"
    elif decision.resultado == "ejecutar":
        resultado = acciones.ejecutar(accion, incidente, "politica")
        if resultado["estado"] == "fallida":
            motivos.append(f"falló la ejecución: {resultado['error']}")
            recomendaciones.crear(incidente_id, incidente["host_origen"], accion, propuesta["justificacion"],
                                  propuesta["confianza"], motivos)
            final = "fallida"
        else:
            final = accion + sufijo
    else:
        if not es_laboratorio:  # el laboratorio no llena la bandeja del operador
            recomendaciones.crear(incidente_id, incidente["host_origen"], accion, propuesta["justificacion"],
                                  propuesta["confianza"], motivos)
        final = "recomendada" + sufijo

    repository_acciones.guardar_decision(
        incidente_id, accion, propuesta["justificacion"] or None, decision.resultado, motivos, final
    )
    return {"accion_propuesta": accion, "decision_politica": decision.resultado, "motivos": motivos,
            "accion_final": final}
