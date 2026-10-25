"""Acciones del operador sobre un host (C7, Fase D5): qué puede hacer ahora y, al pedirlo, ejecutarlo.

Solo lee y orquesta. La ejecución, la verificación y la reversión a DROP total son de services/acciones.py; el
desbloqueo, de services/operador.py (aquí solo se informa si está disponible). Nada se ejecuta sin una petición
del operador. Si la acción coincide con una recomendación pendiente y vigente del host, se ejecuta como su
aprobación (recomendaciones.aprobar): queda "ejecutada" o "fallida" y nunca se ejecuta dos veces.
"""

from typing import Any

from app.config import get_settings
from app.integrations.ryu import RyuError
from app.services import acciones, recomendaciones
from app.services.escalera import ETIQUETAS

__all__ = ["RyuError", "ejecutar", "listar"]

ORDEN = ("cuarentena_puertos", "limitar_tasa", "cuarentena_vlan", "bloqueo_total", "desbloquear")
TEXTOS = {
    "cuarentena_puertos": (ETIQUETAS["cuarentena_puertos"],
                           "Conserva lo básico de la red, sin caminos para saltar a otros equipos."),
    "limitar_tasa": (ETIQUETAS["limitar_tasa"], "Le devuelve la red a velocidad reducida ({kbps} kbps)."),
    "cuarentena_vlan": (ETIQUETAS["cuarentena_vlan"], "Mueve el equipo a una red de cuarentena separada."),
    "bloqueo_total": ("Volver a DROP total", "Corta otra vez todo el tráfico del equipo."),
    "desbloquear": ("Desbloquear", "Le devuelve la red completa; pide la IP, un motivo y la clasificación."),
}


def listar(ip: str) -> dict[str, Any]:
    """Las 5 acciones en el orden de C7, con Ryu recién leído. Lanza RyuError si Ryu no responde (503)."""
    disponibles, modo = acciones.leer_host(ip)
    vigentes = recomendaciones.pendientes_de_host(ip)
    kbps = get_settings().LIMITE_TASA_KBPS
    lista = []
    for accion in ORDEN:
        if accion == "desbloquear":  # A5 está: Ryu acaba de responder la lista de bloqueados
            motivo = None if modo else acciones.HOST_NO_BLOQUEADO
        else:
            motivo = acciones.motivo_no_disponible(accion, disponibles, modo)
        etiqueta, efecto = TEXTOS[accion]
        recomendacion = vigentes.get(accion)
        lista.append({
            "accion": accion, "etiqueta": etiqueta, "efecto": efecto.format(kbps=kbps), "disponible": motivo is None,
            "motivo": motivo, "recomendada": recomendacion is not None,
            "recomendacion_id": recomendacion["id"] if recomendacion else None,
            "confianza": recomendacion["confianza"] if recomendacion else None,
        })
    return {"ip": ip, "modo": modo, "capacidades": disponibles, "acciones": lista}


def ejecutar(ip: str, accion: str) -> dict[str, Any]:
    """Botón del operador. AccionNoPermitida o RecomendacionYaResuelta → 409; RyuError → 503; "fallida" → 502."""
    accion = acciones.ALIAS.get(accion, accion)
    recomendacion = recomendaciones.pendientes_de_host(ip).get(accion)
    if recomendacion is None:
        return acciones.ejecutar_por_operador(ip, accion)
    acciones.validar_por_operador(ip, accion)
    aprobada = recomendaciones.aprobar(recomendacion["id"])
    return {"ip": ip, "accion": accion, **aprobada["resultado"], "recomendacion_id": aprobada["id"],
            "recomendacion_estado": aprobada["estado"]}
