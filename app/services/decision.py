"""Después de la explicación: evalúa la política (C7) sobre la acción de la escalera y la aplica (Fase 9b).

La propuesta llega del pipeline con el veredicto FINAL y la acción ya decidida por la escalera y el dictamen;
aquí no se reinterpreta nada.

ejecutar → acciones.ejecutar (simulada en el laboratorio); si falla, el host vuelve a DROP total (acciones.py) y la
acción queda como recomendación con el error. recomendar → recomendación pendiente (en el laboratorio solo se
registra en el resultado del caso). descartar → nada. "mantener_drop" y el modo solo_explicar siempre descartan.
"""

from datetime import datetime, timedelta, timezone
from typing import Any

from app.db import repository, repository_acciones
from app.integrations.ryu import CAPACIDADES
from app.services import acciones, auditoria, configuracion, politica, recomendaciones

SIN_LIMITE = 10**6


def _contexto_sistema(laboratorio: dict | None) -> dict[str, Any]:
    if laboratorio is not None:  # simulado: asume A6, A7 y A8 y usa el modo de la corrida
        return {"modo_decision": laboratorio["modo_decision"], "capacidades": dict.fromkeys(CAPACIDADES, True),
                "hosts_protegidos": [], "acciones_ultimo_minuto": 0, "max_acciones_por_min": SIN_LIMITE}
    vigente = configuracion.politica()
    if vigente["modo_decision"] == "solo_explicar":  # el interruptor no consulta nada más
        return {**vigente, "capacidades": {}, "acciones_ultimo_minuto": 0}
    hace_un_minuto = datetime.now(timezone.utc) - timedelta(minutes=1)
    return {**vigente, "capacidades": acciones.capacidades(),
            "acciones_ultimo_minuto": repository_acciones.acciones_reales_de_la_politica_desde(hace_un_minuto)}


def _motivo_fallo(resultado: dict[str, Any]) -> str:
    if resultado.get("estado_desconocido"):
        return f"falló la ejecución: {resultado['error']}; {acciones.ESTADO_DESCONOCIDO}"
    return f"falló la ejecución: {resultado['error']}; el host sigue en DROP total"


def decidir(incidente_id: int, propuesta: dict[str, Any], laboratorio: dict | None) -> dict[str, Any]:
    """propuesta: accion, confianza (calculada), veredicto (FINAL), veredicto_salida, justificacion, modo_actual."""
    accion = propuesta["accion"]
    incidente = repository.obtener_incidente(incidente_id)
    es_laboratorio = laboratorio is not None
    decision = politica.evaluar(
        {"accion": accion, "veredicto": propuesta["veredicto"], "veredicto_salida": propuesta.get("veredicto_salida")},
        {"host": incidente["host_origen"], "laboratorio": es_laboratorio,
         "modo_actual": propuesta.get("modo_actual", "drop_total")},
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
            motivos.append(_motivo_fallo(resultado))
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
