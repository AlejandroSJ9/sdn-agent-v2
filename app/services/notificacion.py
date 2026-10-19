"""Notificación por correo (Apps Script): arma el payload C3, envía, guarda, audita y emite el evento."""

import logging
import time
from datetime import datetime, timezone

from app.config import get_settings
from app.db import repository
from app.integrations import appscript
from app.schemas.alerta import IncidenteSalida
from app.schemas.notificacion import PayloadC3, ResultadoNotificacion
from app.services import auditoria, eventos, informe as informe_svc

logger = logging.getLogger(__name__)

ASUNTO_PRUEBA = "[SDN] Prueba de notificación"
CIERRE_INCIDENTE = "Ryu-IA mantiene el host bloqueado; solo el operador puede desbloquearlo desde el panel."
MAX_EXPLICACION = 400
SUFIJO_RESPALDO = " [respaldo por reglas]"


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


def _linea_contexto(incidente: IncidenteSalida) -> str:
    """Una línea con lo que aportó el contexto (C6), o "no utilizado"."""
    contexto = incidente.contexto_usado
    if incidente.variante != "con_contexto" or not contexto:
        return "Contexto: no utilizado"
    resumen = contexto.get("resumen", {})
    linea = (
        f"Contexto: {resumen.get('total_previos', 0)} incidentes previos del host "
        f"({resumen.get('falsos_positivos_operador', 0)} falsos positivos verificados"
    )
    if resumen.get("amenazas_contenidas_operador"):
        linea += f", {resumen['amenazas_contenidas_operador']} amenazas contenidas"
    linea += ")"
    otros = len(contexto.get("actividad_red", []))
    return f"{linea} y {otros} en otros hosts recientemente" if otros else linea


_ACCION_TEXTO = {"pasar_a_cuarentena": "pasar a cuarentena", "escalar_a_drop": "volver a bloqueo total"}


def _linea_accion(incidente: IncidenteSalida) -> str:
    """Una línea con lo que decidió la política (C7)."""
    if incidente.informe and incidente.informe["trazabilidad_y_accion"].get("escalera"):  # Fase 9a
        return informe_svc.linea_accion(incidente.informe["trazabilidad_y_accion"])
    accion = _ACCION_TEXTO.get(incidente.accion_propuesta or "")
    if incidente.accion_final == "pasar_a_cuarentena":
        return "Acción: pasado a cuarentena automáticamente por la política"
    if incidente.accion_final == "escalar_a_drop":
        return "Acción: vuelto a bloqueo total automáticamente por la política"
    if incidente.accion_final in ("recomendada", "fallida") and accion:
        return f"Recomendación pendiente: {accion}"
    return "Acción: se mantiene el bloqueo total"


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
        if incidente.informe:  # Fase 6.8b: una línea por dimensión del informe + enlace (C3: 3 a 6 líneas)
            evidencia, mitre, razonamiento, _ = informe_svc.lineas(incidente.informe, incidente.severidad)
            lineas = [evidencia, mitre, f"{razonamiento} · {_linea_contexto(incidente)}",  # contexto: Fase 6.7
                      _linea_accion(incidente),  # acción: mismas frases de la Fase 6.8
                      f"Informe completo: {panel}/#/incidentes/{incidente.id}"]
        else:  # incidentes previos a la Fase 6.8b (sin informe)
            lineas = [
                _linea_veredicto(incidente),
                f"Explicación: {explicacion}",
                # C3 permite 3 a 6 líneas: el contexto va en la misma línea que la fuente
                f"Alertas agrupadas: {incidente.contador_alertas} | Fuente de la explicación: {fuente} | {_linea_contexto(incidente)}",
                _linea_accion(incidente),
                f"Hora de detección (UTC): {_hora(fecha)}",
                CIERRE_INCIDENTE,
            ]
            if incidente.fuente_explicacion == "reglas":  # cabe la línea de respaldo: hora y cierre en una
                lineas[-2:] = [f"{lineas[-2]} · {CIERRE_INCIDENTE}"]
        if incidente.fuente_explicacion == "reglas":  # el operador debe ver que no hubo LLM y por qué
            asunto += SUFIJO_RESPALDO
            lineas.insert(0, f"Explicación de respaldo por reglas: {incidente.motivo_respaldo_legible}. Revisar en el panel.")
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
