"""Acciones acotadas sobre Ryu (C2, Fase 9b): las de la escalera y la vuelta a DROP total. NUNCA desbloquea.

_llamar() es el ÚNICO lugar del código que llama a ryu.cuarentena / limitar_tasa / cuarentena_vlan / bloqueo_total
(tests/unit/test_unico_camino_desbloqueo.py), por decisión de la política, aprobación del operador o reversión.
En los incidentes del laboratorio no llama a Ryu: registra la acción como "simulada".

Nunca se deja un host en un estado intermedio desconocido:
  1. antes de relajar, el host debe seguir en drop_total (se vuelve a leer en Ryu);
  2. después de cada acción se VERIFICA el modo con GET /ryu/hosts/bloqueados;
  3. ante error, timeout o modo distinto → reversión automática con bloqueo_total y verificación de drop_total
     (la única acción de red que el agente hace solo fuera de la escalera; no cuenta para el límite por minuto);
  4. si la reversión también falla → accion_estado_desconocido, sin reintentos en ciclo.
"""

import logging
from typing import Any, Literal

from app.config import get_settings
from app.db import repository_acciones, repository_operador
from app.integrations.ryu import SIN_CAPACIDADES, ClienteRyu, RyuError, RyuNoDisponible
from app.services import auditoria, eventos, operador

logger = logging.getLogger(__name__)

# acción → (modo que Ryu debe mostrar después, capacidad que la habilita)
ACCIONES = {
    "cuarentena_puertos": ("cuarentena", "cuarentena"),      # A6
    "limitar_tasa": ("limitado", "limitar_tasa"),            # A7
    "cuarentena_vlan": ("vlan_cuarentena", "cuarentena_vlan"),  # A8
    "bloqueo_total": ("drop_total", None),                   # con A6, A7 o A8; operador o reversión
}
ALIAS = {"pasar_a_cuarentena": "cuarentena_puertos", "escalar_a_drop": "bloqueo_total"}  # vocabulario de la 6.8
RELAJACIONES = ("cuarentena_puertos", "limitar_tasa", "cuarentena_vlan")
MODOS = ("drop_total", "cuarentena", "limitado", "vlan_cuarentena")
ESTADO_DESCONOCIDO = "Ryu no confirmó el DROP total: verifique el host"
Origen = Literal["politica", "operador"]


class AccionNoPermitida(ValueError):
    """Acción fuera de la lista cerrada, inválida para el modo del host o sin capacidad en Ryu (409)."""


def capacidades() -> dict[str, bool]:
    """Si Ryu no responde, se asume que no tiene ninguna: la política solo recomendará."""
    try:
        return operador.obtener_cliente_ryu().capacidades()
    except RyuError:
        return dict(SIN_CAPACIDADES)


def capacidad_a6() -> bool:
    return capacidades()["cuarentena"]


def _leer_modo(cliente: ClienteRyu, ip: str) -> str | None:
    """Modo del host en Ryu; None si no está bloqueado. Lanza RyuError si no se puede leer."""
    host = next((h for h in cliente.bloqueados() if h.get("ip") == ip), None)
    if host is None:
        return None
    return host.get("modo") if host.get("modo") in MODOS else "drop_total"


def modo_actual(ip: str) -> str:
    """Modo vigente del host en Ryu (solo lectura). Sin dato o con Ryu caído → "drop_total"."""
    try:
        return _leer_modo(operador.obtener_cliente_ryu(), ip) or "drop_total"
    except RyuError:
        return "drop_total"


def _llamar(cliente: ClienteRyu, accion: str, ip: str) -> dict:
    if accion == "cuarentena_puertos":
        return cliente.cuarentena(ip)
    if accion == "limitar_tasa":
        return cliente.limitar_tasa(ip, get_settings().LIMITE_TASA_KBPS)
    if accion == "cuarentena_vlan":
        return cliente.cuarentena_vlan(ip)
    return cliente.bloqueo_total(ip)


def _revertir(cliente: ClienteRyu, ip: str, forzar: bool) -> dict[str, Any]:
    """Vuelve a drop_total y lo verifica. Un solo intento (sin ciclo). forzar: tras un timeout no se confía en una
    lectura previa (Ryu pudo aplicar la acción tarde) y se pide bloqueo_total siempre."""
    try:
        if not forzar and _leer_modo(cliente, ip) == "drop_total":
            return {"reversion": "no_necesaria"}  # Ryu dejó el DROP total (C2: transaccional)
    except RyuError:
        pass  # no se pudo leer: se revierte igual
    try:
        _llamar(cliente, "bloqueo_total", ip)
        modo = _leer_modo(cliente, ip)
        if modo == "drop_total":
            return {"reversion": "revertida"}
        error = f"tras revertir, modo {modo}"
    except RyuError as falla:
        error = str(falla)[:200]
    return {"reversion": "fallida", "estado_desconocido": True, "error_reversion": error}


def _ejecutar_en_ryu(accion: str, ip: str) -> dict[str, Any]:
    cliente = operador.obtener_cliente_ryu()
    esperado = ACCIONES[accion][0]
    if accion in RELAJACIONES:  # verificación previa: solo se relaja un DROP total
        try:
            previo = _leer_modo(cliente, ip)
        except RyuError as falla:
            return {"estado": "fallida", "error": f"no se pudo leer el modo del host: {falla}"[:200]}
        if previo != "drop_total":
            return {"estado": "fallida", "error": "host no bloqueado en Ryu" if previo is None
                    else f"el host ya no está en DROP total (modo {previo})"}
    forzar = False
    try:
        respuesta = _llamar(cliente, accion, ip)
        modo = _leer_modo(cliente, ip)  # verificación posterior
        if modo == esperado:
            extra = {k: respuesta[k] for k in ("kbps", "vlan") if k in respuesta}
            return {"estado": "ejecutada", "modo": modo, **extra}
        error = f"Ryu no aplicó {accion} (modo {modo})"
    except RyuError as falla:
        error = str(falla)[:200]
        forzar = isinstance(falla, RyuNoDisponible)  # timeout o red: resultado ambiguo
        if falla.codigo == 404:  # quizá Ryu perdió la capacidad: se vuelve a detectar
            try:
                cliente.capacidades(refrescar=True)
            except RyuError:
                pass
    if accion == "bloqueo_total":  # la propia vuelta a DROP total falló: el host queda como estaba
        return {"estado": "fallida", "error": error}
    return {"estado": "fallida", "error": error, **_revertir(cliente, ip, forzar)}


def ejecutar(accion: str, incidente: dict[str, Any] | None, origen: Origen, host: str | None = None) -> dict[str, Any]:
    """{"estado": ejecutada | simulada | fallida, ...}. Nunca lanza por un fallo de Ryu: lo registra."""
    accion = ALIAS.get(accion, accion)
    if accion not in ACCIONES:
        raise AccionNoPermitida(f"Acción fuera de la lista cerrada: {accion}")
    host = host or incidente["host_origen"]
    datos = {"incidente_id": incidente["id"] if incidente else None, "host": host, "accion": accion, "origen": origen}
    laboratorio = bool(incidente) and incidente.get("origen") == "prueba"

    if laboratorio:
        resultado = {"estado": "simulada", "modo": ACCIONES[accion][0]}
    else:
        resultado = _ejecutar_en_ryu(accion, host)
        if resultado["estado"] == "fallida":
            logger.warning("Falló %s sobre %s: %s", accion, host, resultado["error"])

    evento = "accion_fallida" if resultado["estado"] == "fallida" else "accion_ejecutada"
    registro = {**datos, "resultado": resultado["estado"], **{k: v for k, v in resultado.items() if k != "estado"}}
    auditoria.registrar(evento, registro)
    if resultado.get("reversion") == "revertida":
        auditoria.registrar("accion_revertida", {**datos, "origen": "reversion_automatica", "accion_fallida": accion})
    if resultado.get("estado_desconocido"):
        auditoria.registrar("accion_estado_desconocido", {**datos, "error": resultado["error"],
                                                          "error_reversion": resultado["error_reversion"]})
    eventos.emitir(evento, {**registro, **({"origen_incidente": "prueba"} if laboratorio else {})})
    if origen == "operador" and incidente and resultado["estado"] == "ejecutada":
        repository_acciones.marcar_accion_final(incidente["id"], accion)
    return resultado


def ejecutar_por_operador(ip: str, accion: str) -> dict[str, Any]:
    """Botones de "Hosts bloqueados": cuarentena de puertos (desde DROP total, exige A6) o volver a DROP total."""
    accion = ALIAS.get(accion, accion)
    if accion not in ("cuarentena_puertos", "bloqueo_total"):
        raise AccionNoPermitida(f"Acción no disponible para el operador: {accion}")
    disponibles = capacidades()
    if accion == "cuarentena_puertos" and not disponibles["cuarentena"]:
        raise AccionNoPermitida("Ryu no tiene la capacidad de cuarentena (A6)")
    if accion == "bloqueo_total" and not any(disponibles.values()):
        raise AccionNoPermitida("Ryu no tiene A6, A7 ni A8: el host solo puede estar en DROP total")
    actual = modo_actual(ip)
    if (accion == "bloqueo_total") == (actual == "drop_total"):
        raise AccionNoPermitida(f"No aplica: el host está en modo {actual}")
    incidente_id = repository_operador.ultimo_incidente_abierto_por_ip([ip]).get(ip)
    incidente = {"id": incidente_id, "host_origen": ip, "origen": "real"} if incidente_id else None
    return {"ip": ip, "accion": accion, **ejecutar(accion, incidente, "operador", host=ip)}
