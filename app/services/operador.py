"""Acciones del operador sobre Ryu: estado, hosts bloqueados, DESBLOQUEO y lectura de umbrales.

El agente nunca bloquea ni desbloquea por su cuenta: desbloquear_host es el ÚNICO camino del código que
llama a Ryu para desbloquear, y solo se invoca desde la API del operador (tests/unit/test_unico_camino_desbloqueo.py).
"""

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from app.config import get_settings
from app.db import repository_operador
from app.integrations.ryu import API_NO_DISPONIBLE, ClienteRyu, HostNoBloqueado, RyuError, RyuNoDisponible
from app.services import auditoria, eventos, notificacion

__all__ = ["ConfirmacionInvalida", "HostNoBloqueado", "RyuError", "RyuNoDisponible"]


class ConfirmacionInvalida(Exception):
    """La confirmación escrita por el operador no coincide con la IP."""


def obtener_cliente_ryu() -> ClienteRyu:
    """Punto de inyección: las pruebas lo reemplazan por el mock en memoria."""
    ajustes = get_settings()
    return ClienteRyu(ajustes.RYU_URL, ajustes.AGENT_TOKEN)


def estado_ryu() -> dict[str, Any]:
    """Nunca falla: si Ryu no responde, lo informa para que el panel siga funcionando."""
    try:
        cliente = obtener_cliente_ryu()
        return {"disponible": True, **cliente.estado(), "capacidades": cliente.capacidades()}
    except RyuError as error:
        return {"disponible": False, "error": API_NO_DISPONIBLE if error.codigo == 404 else str(error)}


def bloqueados_enriquecidos() -> list[dict[str, Any]]:
    lista = obtener_cliente_ryu().bloqueados()
    abiertos = repository_operador.ultimo_incidente_abierto_por_ip([host["ip"] for host in lista])
    return [{**host, "incidente_id": abiertos.get(host["ip"])} for host in lista]


def desbloquear_host(
    ip: str, confirmacion: str, motivo: str, programar: Callable[..., Any], clasificacion: str
) -> dict[str, Any]:
    """Desbloquea en Ryu y, solo si Ryu lo confirma, lo registra con la clasificación del operador (C6).

    `programar` agenda los correos. La clasificación solo se guarda: no cambia nada en Ryu.
    """
    if confirmacion != ip:
        raise ConfirmacionInvalida("La confirmación debe ser exactamente la IP del host")
    motivo = motivo.strip()
    if not 5 <= len(motivo) <= 200:
        raise ValueError("El motivo debe tener entre 5 y 200 caracteres")

    try:
        respuesta = obtener_cliente_ryu().desbloquear(ip)
    except RyuError as error:  # se audita el intento y se propaga: los incidentes no se tocan
        datos = {"ip": ip, "motivo": motivo, "clasificacion": clasificacion, "codigo": error.codigo, "error": str(error)}
        auditoria.registrar("desbloqueo_fallido", datos)
        raise

    ts = datetime.now(timezone.utc)
    incidentes = repository_operador.incidentes_abiertos_de(ip)
    repository_operador.marcar_desbloqueados(incidentes, ts, clasificacion, motivo)
    auditoria.registrar(
        "host_desbloqueado", {"ip": ip, "motivo": motivo, "clasificacion": clasificacion, "incidentes": incidentes}
    )
    for incidente_id in incidentes:
        programar(notificacion.notificar, incidente_id, "desbloqueo", motivo=motivo)
    if not incidentes:
        programar(notificacion.notificar, None, "desbloqueo", host=ip, motivo=motivo)
    eventos.emitir("host_desbloqueado", {"ip": ip, "incidentes": incidentes})
    return {"ip": ip, "reglas_borradas": respuesta.get("reglas_borradas"), "incidentes": incidentes}


def obtener_umbrales() -> dict[str, int]:
    return obtener_cliente_ryu().umbrales()

