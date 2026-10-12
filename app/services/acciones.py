"""Acciones acotadas sobre Ryu (A6): pasar_a_cuarentena y escalar_a_drop. NUNCA desbloquea.

ejecutar() es el ÚNICO lugar del código que llama a ryu.cuarentena / ryu.bloqueo_total
(tests/unit/test_unico_camino_desbloqueo.py), por decisión de la política o del operador. En los
incidentes del laboratorio no llama a Ryu: registra la acción como "simulada".
"""

import logging
from typing import Any, Literal

from app.db import repository_acciones, repository_operador
from app.integrations.ryu import RyuError
from app.services import auditoria, eventos, operador

logger = logging.getLogger(__name__)

ACCION_A_MODO = {"pasar_a_cuarentena": "cuarentena", "escalar_a_drop": "drop_total"}
Origen = Literal["politica", "operador"]


class AccionNoPermitida(ValueError):
    """Acción fuera de la lista cerrada, inválida para el modo del host o sin capacidad A6 (409)."""


def capacidad_a6() -> bool:
    """Si Ryu no responde, se asume que no hay capacidad: la política solo recomendará."""
    try:
        return operador.obtener_cliente_ryu().capacidades()["cuarentena"]
    except RyuError:
        return False


def modo_actual(ip: str) -> str:
    """Modo vigente del host en Ryu (solo lectura). Sin A6, sin dato o con Ryu caído → "drop_total"."""
    try:
        bloqueados = operador.obtener_cliente_ryu().bloqueados()
    except RyuError:
        return "drop_total"
    modo = next((h.get("modo") for h in bloqueados if h.get("ip") == ip), None)
    return modo if modo in ("drop_total", "cuarentena") else "drop_total"


def ejecutar(accion: str, incidente: dict[str, Any] | None, origen: Origen, host: str | None = None) -> dict[str, Any]:
    """{"estado": ejecutada | simulada | fallida, ...}. Nunca lanza por un fallo de Ryu: lo registra."""
    if accion not in ACCION_A_MODO:
        raise AccionNoPermitida(f"Acción fuera de la lista cerrada: {accion}")
    host = host or incidente["host_origen"]
    datos = {"incidente_id": incidente["id"] if incidente else None, "host": host, "accion": accion, "origen": origen}
    laboratorio = bool(incidente) and incidente.get("origen") == "prueba"

    if laboratorio:
        resultado = {"estado": "simulada", "modo": ACCION_A_MODO[accion]}
    else:
        cliente = operador.obtener_cliente_ryu()
        try:
            respuesta = cliente.cuarentena(host) if accion == "pasar_a_cuarentena" else cliente.bloqueo_total(host)
            resultado = {"estado": "ejecutada", "modo": respuesta.get("modo", ACCION_A_MODO[accion])}
        except RyuError as error:
            logger.warning("Falló %s sobre %s: %s", accion, host, error)
            resultado = {"estado": "fallida", "error": str(error)[:200]}

    evento = "accion_fallida" if resultado["estado"] == "fallida" else "accion_ejecutada"
    registro = {**datos, "resultado": resultado["estado"], **{k: v for k, v in resultado.items() if k != "estado"}}
    auditoria.registrar(evento, registro)
    eventos.emitir(evento, {**registro, **({"origen_incidente": "prueba"} if laboratorio else {})})
    if origen == "operador" and incidente and resultado["estado"] == "ejecutada":
        repository_acciones.marcar_accion_final(incidente["id"], accion)
    return resultado


def ejecutar_por_operador(ip: str, accion: str) -> dict[str, Any]:
    """Botones de "Hosts bloqueados": exige A6 y una acción válida para el modo actual del host."""
    if not capacidad_a6():
        raise AccionNoPermitida("Ryu no tiene la capacidad de cuarentena (A6)")
    actual = modo_actual(ip)
    if ACCION_A_MODO.get(accion) == actual or accion not in ACCION_A_MODO:
        raise AccionNoPermitida(f"El host ya está en modo {actual}")
    incidente_id = repository_operador.ultimo_incidente_abierto_por_ip([ip]).get(ip)
    incidente = {"id": incidente_id, "host_origen": ip, "origen": "real"} if incidente_id else None
    return {"ip": ip, "accion": accion, **ejecutar(accion, incidente, "operador", host=ip)}
