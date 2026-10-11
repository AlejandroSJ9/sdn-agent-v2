"""Notificación por correo (Apps Script): arma el payload C3, envía, guarda, audita y emite el evento."""

import logging
import time
from datetime import datetime, timezone

from app.config import get_settings
from app.db import repository
from app.integrations import appscript
from app.schemas.alerta import IncidenteSalida
from app.schemas.notificacion import PayloadC3, ResultadoNotificacion
from app.services import auditoria, eventos

logger = logging.getLogger(__name__)

ASUNTO_PRUEBA = "[SDN] Prueba de notificación"
CIERRE_INCIDENTE = "Ryu-IA mantiene el host bloqueado; solo el operador puede desbloquearlo desde el panel."
MAX_EXPLICACION = 400


def transporte_appscript():
    """Punto de inyección: None en producción (red real); las pruebas lo reemplazan."""
    return None


def esperar_reintento(segundos: float) -> None:
    """Punto de inyección: las pruebas lo reemplazan para no dormir."""
    time.sleep(segundos)


def _hora(fecha: datetime | None) -> str:
    return fecha.strftime("%Y-%m-%dT%H:%M:%SZ") if fecha else "—"


def _linea_veredicto(incidente: IncidenteSalida) -> str:
    return (
        f"Veredicto: {incidente.veredicto or '—'} | Severidad: {incidente.severidad or '—'} | "
        f"Técnica MITRE: {incidente.tecnica_mitre or '—'}"
    )


def construir_payload(
    incidente: IncidenteSalida | None, tipo: str, host: str | None = None, motivo: str | None = None
) -> PayloadC3:
    """Payload C3 en texto plano (3 a 6 líneas). En "prueba" el incidente se ignora.

    Un "desbloqueo" sin incidente abierto usa `host`; `motivo` es el que escribió el operador.
    """
    panel = get_settings().PANEL_URL.rstrip("/")
    if tipo == "prueba":
        ahora = datetime.now(timezone.utc).replace(microsecond=0)
        lineas = [
            "Mensaje de prueba del agente SDN: si lo recibes, la integración con Apps Script funciona.",
            f"Hora (UTC): {_hora(ahora)}",
            f"Panel: {panel}/",
        ]
        return PayloadC3(
            tipo="prueba", fecha=ahora, host="prueba", asunto=ASUNTO_PRUEBA,
            mensaje="\n".join(lineas), enlace_panel=f"{panel}/",
        )

    if tipo == "desbloqueo":
        ip = incidente.host_origen if incidente else host
        fecha = (incidente.t_desbloqueado if incidente else None) or datetime.now(timezone.utc)
        asunto = f"[SDN] Host {ip} desbloqueado por el operador"
        if incidente:
            lineas = [f"El operador desbloqueó el host {ip} (incidente {incidente.id}).", _linea_veredicto(incidente)]
        else:
            lineas = [f"El operador desbloqueó el host {ip} (sin incidente abierto en el agente)."]
        lineas += [
            f"Motivo del operador: {' '.join((motivo or '—').split())}",
            f"Hora del desbloqueo (UTC): {_hora(fecha)}",
        ]
        if incidente is None:
            return PayloadC3(
                tipo="desbloqueo", fecha=fecha.replace(microsecond=0), host=ip, asunto=asunto,
                mensaje="\n".join(lineas), enlace_panel=f"{panel}/",
            )
    else:
        fecha = incidente.t_ryu or incidente.t_recibido
        asunto = f"[SDN] Host {incidente.host_origen} aislado por posible movimiento lateral"
        explicacion = " ".join((incidente.explicacion or "—").split())[:MAX_EXPLICACION]
        fuente = {"llm": "LLM", "reglas": "reglas"}.get(incidente.fuente_explicacion or "", "—")
        lineas = [
            _linea_veredicto(incidente),
            f"Explicación: {explicacion}",
            f"Alertas agrupadas: {incidente.contador_alertas} | Fuente de la explicación: {fuente}",
            f"Hora de detección (UTC): {_hora(fecha)}",
            CIERRE_INCIDENTE,
        ]
    return PayloadC3(
        tipo=tipo,
        incidente_id=incidente.id,
        fecha=fecha.replace(microsecond=0),
        host=incidente.host_origen,
        severidad=incidente.severidad,
        veredicto=incidente.veredicto,
        asunto=asunto,
        mensaje="\n".join(lineas),
        enlace_panel=f"{panel}/#/incidentes/{incidente.id}",
    )


def notificar(
    incidente_id: int | None,
    tipo: str,
    host: str | None = None,
    motivo: str | None = None,
    omitir: str | None = None,
) -> ResultadoNotificacion | None:
    """Envía y registra. Devuelve None si el incidente no existe. Nunca lanza excepción.

    Con `omitir` (p. ej. "laboratorio") no se usa la red: queda "omitida" con ese motivo en `error`.
    """
    payload: dict = {}
    try:
        incidente = None
        if tipo != "prueba" and incidente_id is not None:
            fila = repository.obtener_incidente(incidente_id)
            if fila is None:
                return None
            incidente = IncidenteSalida.model_validate(fila)
        payload = construir_payload(incidente, tipo, host=host, motivo=motivo).model_dump(mode="json")
        ajustes = get_settings()
        if omitir:
            resultado = ResultadoNotificacion(estado="omitida", intentos=0, error=omitir)
        else:
            resultado = appscript.enviar(
                payload,
                ajustes.APPSCRIPT_URL,
                ajustes.APPSCRIPT_TIMEOUT,
                transporte=transporte_appscript(),
                esperar=esperar_reintento,
            )
    except Exception:
        logger.exception("No se pudo enviar la notificación %s del incidente %s", tipo, incidente_id)
        resultado = ResultadoNotificacion(estado="fallida", intentos=0, error="error_interno")

    try:
        ts = datetime.now(timezone.utc)
        incidente_ref = None if tipo == "prueba" else incidente_id
        repository.guardar_notificacion(incidente_ref, tipo, payload, resultado.model_dump(), ts)
        if resultado.estado == "enviada" and tipo == "incidente":
            repository.marcar_notificado(incidente_id, ts)
        evento = f"notificacion_{resultado.estado}"
        datos = {"incidente_id": incidente_ref, "tipo": tipo, **resultado.model_dump(exclude={"latencia_ms"})}
        auditoria.registrar(evento, datos)
        if resultado.estado != "omitida":  # C5 solo define notificacion_enviada / notificacion_fallida
            eventos.emitir(evento, datos)
    except Exception:
        logger.exception("No se pudo registrar la notificación %s del incidente %s", tipo, incidente_id)
    return resultado
