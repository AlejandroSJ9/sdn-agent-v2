"""Después de la explicación: evalúa la política (C7) sobre la acción de la escalera (Fase D1).

La propuesta llega del pipeline con el veredicto FINAL y la acción ya decidida por la escalera y el dictamen;
aquí no se reinterpreta nada.

El agente nunca ejecuta por su cuenta. recomendar → recomendación pendiente para el operador (en el laboratorio
solo se registra en el resultado del caso); aprobarla la ejecuta por acciones.py. descartar → nada.
"mantener_drop" y el modo solo_explicar siempre descartan.
"""

from typing import Any

from app.db import repository, repository_acciones
from app.integrations.ryu import CAPACIDADES
from app.services import acciones, auditoria, configuracion, politica, recomendaciones


def _contexto_sistema(laboratorio: dict | None) -> dict[str, Any]:
    if laboratorio is not None:  # simulado: asume A6, A7 y A8 y usa el modo de la corrida
        return {"modo_decision": laboratorio["modo_decision"], "capacidades": dict.fromkeys(CAPACIDADES, True)}
    modo = configuracion.politica()["modo_decision"]
    if modo == "solo_explicar":  # el interruptor no consulta nada más
        return {"modo_decision": modo, "capacidades": {}}
    return {"modo_decision": modo, "capacidades": acciones.capacidades()}


def decidir(incidente_id: int, propuesta: dict[str, Any], laboratorio: dict | None) -> dict[str, Any]:
    """propuesta: accion, confianza (calculada), veredicto (FINAL), veredicto_salida, justificacion, modo_actual."""
    accion = propuesta["accion"]
    incidente = repository.obtener_incidente(incidente_id)
    es_laboratorio = laboratorio is not None
    decision = politica.evaluar(
        {"accion": accion, "veredicto": propuesta["veredicto"], "veredicto_salida": propuesta.get("veredicto_salida")},
        {"modo_actual": propuesta.get("modo_actual", "drop_total")},
        _contexto_sistema(laboratorio),
    )
    motivos = list(decision.motivos)
    if politica.MOTIVO_INCOMPATIBLE in motivos:  # descartada sin pasar por la bandeja: queda en la auditoría
        auditoria.registrar("accion_descartada", {
            "incidente_id": incidente_id, "accion": accion, "veredicto": propuesta["veredicto"],
            "motivo": politica.MOTIVO_INCOMPATIBLE,
        })

    if decision.resultado == "descartar":
        final = "ninguna"
    else:
        if not es_laboratorio:  # el laboratorio no llena la bandeja del operador
            recomendaciones.crear(incidente_id, incidente["host_origen"], accion, propuesta["justificacion"],
                                  propuesta["confianza"], motivos)
        final = "recomendada_simulada" if es_laboratorio else "recomendada"

    repository_acciones.guardar_decision(
        incidente_id, accion, propuesta["justificacion"] or None, decision.resultado, motivos, final
    )
    return {"accion_propuesta": accion, "decision_politica": decision.resultado, "motivos": motivos,
            "accion_final": final}
