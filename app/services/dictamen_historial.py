"""Dictamen determinista de historial (Fase 6.8a). Función PURA: sin BD, sin red, sin reloj.

Lo concluyente lo decide el CÓDIGO; el LLM lo recibe como dato y solo lo explica. Solo aplica con
contexto (variante con_contexto). Orden:
  a) AGRAVANTE (primero): confirmado en los últimos 60 min en el mismo host, o alguna 'amenaza_contenida'
     del operador en el historial del host, o 2 o más hosts confirmados en la actividad de la red → "confirmado".
     La clasificación del operador prevalece sobre el veredicto del agente: un "confirmado" que el operador
     clasificó como 'falso_positivo' no cuenta (ni en el host ni en la campaña). 'amenaza_contenida' cuenta siempre.
  b) ATENUANTE (solo sin agravante): 2 o más 'falso_positivo' del OPERADOR en el historial del host con
     exactamente los mismos umbrales_superados → "indeterminado" (NUNCA falso_positivo).
  c) Ninguno → tipo None.
"""

from typing import Any

SIN_DICTAMEN = {"tipo": None, "veredicto": None, "motivo": None}


def dictaminar(contexto: dict[str, Any] | None, umbrales_superados_actuales: list[str]) -> dict[str, Any]:
    if contexto is None:
        return dict(SIN_DICTAMEN)
    resumen = contexto["resumen"]
    agravantes = []
    if resumen["confirmado_ultimos_60_min"]:
        agravantes.append("incidente confirmado en los últimos 60 min en el mismo host")
    if resumen["amenazas_contenidas_operador"] > 0:
        agravantes.append(f"{resumen['amenazas_contenidas_operador']} amenaza(s) contenida(s) por el operador en el host")
    confirmados = sorted({
        a["host"] for a in contexto["actividad_red"]
        if a["veredicto"] == "confirmado" and a.get("clasificacion_operador") != "falso_positivo"
    })
    if len(confirmados) >= 2:
        agravantes.append(f"{len(confirmados)} hosts confirmados en la actividad de la red")
    if agravantes:
        return {"tipo": "agravante", "veredicto": "confirmado", "motivo": "; ".join(agravantes)}

    falsos = sum(
        1 for previo in contexto["historial_host"]
        if previo.get("clasificacion_operador") == "falso_positivo"
        and previo["umbrales_superados"] == list(umbrales_superados_actuales)
    )
    if falsos >= 2:
        lista = ", ".join(umbrales_superados_actuales) or "ninguno"
        return {"tipo": "atenuante", "veredicto": "indeterminado",
                "motivo": f"{falsos} clasificaciones falso_positivo del operador con los mismos umbrales ({lista})"}
    return dict(SIN_DICTAMEN)
