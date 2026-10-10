"""Acciones del operador sobre Ryu: estado, hosts bloqueados, DESBLOQUEO y umbrales.

El agente nunca bloquea ni desbloquea por su cuenta: desbloquear_host es el ÚNICO camino del código que
llama a Ryu para desbloquear, y solo se invoca desde la API del operador (tests/unit/test_unico_camino_desbloqueo.py).
"""

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from app.config import get_settings
from app.db import repository_operador
from app.integrations.ryu import ClienteRyu, HostNoBloqueado, RyuError, RyuNoDisponible
from app.services import auditoria, eventos, notificacion

__all__ = ["ConfirmacionInvalida", "UmbralesInvalidos", "HostNoBloqueado", "RyuError", "RyuNoDisponible"]


class ConfirmacionInvalida(Exception):
    """La confirmación escrita por el operador no coincide con la IP."""


class UmbralesInvalidos(Exception):
    """Cambio de umbrales con claves que Ryu no expone o valores que no son enteros > 0."""


def obtener_cliente_ryu() -> ClienteRyu:
    """Punto de inyección: las pruebas lo reemplazan por el mock en memoria."""
    ajustes = get_settings()
    return ClienteRyu(ajustes.RYU_URL, ajustes.AGENT_TOKEN)


def estado_ryu() -> dict[str, Any]:
    """Nunca falla: si Ryu no responde, lo informa para que el panel siga funcionando."""
    try:
        return {"disponible": True, **obtener_cliente_ryu().estado()}
    except RyuError as error:
        return {"disponible": False, "error": str(error)}


def bloqueados_enriquecidos() -> list[dict[str, Any]]:
    lista = obtener_cliente_ryu().bloqueados()
    abiertos = repository_operador.ultimo_incidente_abierto_por_ip([host["ip"] for host in lista])
    return [{**host, "incidente_id": abiertos.get(host["ip"])} for host in lista]


def desbloquear_host(ip: str, confirmacion: str, motivo: str, programar: Callable[..., Any]) -> dict[str, Any]:
    """Desbloquea en Ryu y, solo si Ryu lo confirma, lo registra. `programar` agenda los correos."""
    if confirmacion != ip:
        raise ConfirmacionInvalida("La confirmación debe ser exactamente la IP del host")
    motivo = motivo.strip()
    if not 5 <= len(motivo) <= 200:
        raise ValueError("El motivo debe tener entre 5 y 200 caracteres")

    respuesta = obtener_cliente_ryu().desbloquear(ip)  # si Ryu falla, se propaga y la BD no se toca

    ts = datetime.now(timezone.utc)
    incidentes = repository_operador.incidentes_abiertos_de(ip)
    repository_operador.marcar_desbloqueados(incidentes, ts)
    auditoria.registrar("host_desbloqueado", {"ip": ip, "motivo": motivo, "incidentes": incidentes})
    for incidente_id in incidentes:
        programar(notificacion.notificar, incidente_id, "desbloqueo", motivo=motivo)
    if not incidentes:
        programar(notificacion.notificar, None, "desbloqueo", host=ip, motivo=motivo)
    eventos.emitir("host_desbloqueado", {"ip": ip, "incidentes": incidentes})
    return {"ip": ip, "reglas_borradas": respuesta.get("reglas_borradas"), "incidentes": incidentes}


def obtener_umbrales() -> dict[str, int]:
    return obtener_cliente_ryu().umbrales()


def actualizar_umbrales(cambios: dict[str, Any]) -> dict[str, int]:
    """Solo claves que Ryu ya devuelve y valores enteros > 0 (un bool no cuenta como entero)."""
    cliente = obtener_cliente_ryu()
    actuales = cliente.umbrales()
    desconocidas = sorted(set(cambios) - set(actuales))
    invalidos = sorted(k for k, v in cambios.items() if type(v) is not int or v <= 0)
    if not cambios or desconocidas or invalidos:
        raise UmbralesInvalidos(f"claves desconocidas: {desconocidas}; valores inválidos: {invalidos}")
    nuevos = cliente.actualizar_umbrales(cambios)
    datos = {"anterior": {k: actuales[k] for k in cambios}, "nuevo": {k: nuevos.get(k) for k in cambios}}
    auditoria.registrar("umbrales_cambiados", datos)
    eventos.emitir("config_cambiada", {"tipo": "umbrales", **datos})
    return nuevos
