"""Pipeline de un incidente nuevo. Corre en segundo plano (BackgroundTasks).

Orden estricto (Fase 6.8a+b): alerta → contexto → dictamen determinista → LLM v3 (recibe el dictamen como
dato) → veredicto final → coherencia de acción → verificador → política → informe → notificación.
El CÓDIGO decide lo concluyente (dictamen) y aporta los hechos; el LLM solo interpreta. Solo
services/acciones.py puede actuar sobre Ryu, y nunca para desbloquear.
"""

import logging
from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError

from app.config import get_settings
from app.db import repository, repository_acciones, repository_laboratorio
from app.integrations.llm import ClienteLLM, ClienteOpenRouter, LLMError, RespuestaLLM
from app.schemas.alerta import AlertaEntrada
from app.schemas.explicacion import SalidaLLMv3
from app.services import (
    acciones, auditoria, configuracion, contexto, decision, evidencia, eventos, informe, notificacion, politica,
    presupuesto, verificador,
)
from app.services.dictamen_historial import dictaminar
from app.services.explicacion_reglas import CONFIANZA_REGLAS, explicar_por_reglas
from app.services.prompt import VERSIONES, construir_mensaje_usuario, system_prompt

logger = logging.getLogger(__name__)

Intento = tuple[SalidaLLMv3 | None, str | None, RespuestaLLM | None]  # (salida, motivo_respaldo, respuesta)
JUSTIFICACION_ATENUANTE = "2 o más falsos positivos verificados por el operador con el mismo patrón"


def obtener_cliente_llm() -> ClienteLLM:
    """Punto de inyección: las pruebas lo reemplazan por un cliente falso."""
    ajustes = get_settings()
    return ClienteOpenRouter(ajustes.OPENROUTER_API_KEY, ajustes.LLM_MODELO, ajustes.LLM_TIMEOUT)


def _intentar_llm(alerta: AlertaEntrada, mensaje: str, con_contexto: bool, cliente: ClienteLLM | None,
                  laboratorio: dict | None) -> Intento:
    ajustes = get_settings()
    if laboratorio is not None and not laboratorio["usar_llm"]:
        return None, "laboratorio_sin_llm", None  # corrida sin LLM: no toca el cupo ni el interruptor global
    if not configuracion.llm_habilitado():
        return None, "llm_deshabilitado", None
    if not ajustes.OPENROUTER_API_KEY:
        return None, "sin_api_key", None
    if not presupuesto.puede_llamar():
        return None, "sin_cupo", None

    cliente = cliente or obtener_cliente_llm()
    try:
        respuesta = cliente.completar(system_prompt(con_contexto), mensaje)
    except LLMError as error:
        presupuesto.registrar_uso(0, 0, 0, exito=False, peticiones=max(error.peticiones, 1))
        return None, error.motivo, None

    try:
        salida = SalidaLLMv3.model_validate_json(respuesta.texto)
        motivo = None
    except ValidationError as error:
        salida = None
        motivo = "json_invalido" if any(e["type"] == "json_invalid" for e in error.errors()) else "schema_invalido"
    presupuesto.registrar_uso(
        respuesta.tokens_entrada, respuesta.tokens_salida, respuesta.latencia_ms, motivo is None, respuesta.peticiones
    )
    return salida, motivo, respuesta


def _variante(laboratorio: dict | None) -> str:
    """Real → CONTEXTO_HABILITADO (en caliente); laboratorio → la variante de su corrida."""
    if laboratorio is not None:
        return laboratorio["variante"]
    return "con_contexto" if configuracion.contexto()["habilitado"] else "sin_contexto"


def _accion_coherente(incidente_id: int, salida: SalidaLLMv3, fuente: str, dictamen: dict, modo_actual: str) -> dict[str, Any]:
    """Acción final propuesta: el dictamen manda; sin dictamen, la del LLM/reglas normalizada al modo del host."""
    propuesta, corregida = politica.normalizar_accion(salida.accion_propuesta, modo_actual)
    if corregida:
        auditoria.registrar("accion_propuesta_corregida", {
            "incidente_id": incidente_id, "propuesta": salida.accion_propuesta, "modo_actual": modo_actual,
            "corregida_a": propuesta,
        })
    if dictamen["tipo"] == "agravante":
        accion = {"accion": "mantener", "confianza": salida.confianza, "origen": "dictamen",
                  "justificacion": f"Dictamen agravante: {dictamen['motivo']}"}
    elif dictamen["tipo"] == "atenuante":
        cuarentena, _ = politica.normalizar_accion("pasar_a_cuarentena", modo_actual)  # ya en cuarentena → mantener
        accion = {"accion": cuarentena, "confianza": CONFIANZA_REGLAS, "origen": "dictamen",
                  "justificacion": JUSTIFICACION_ATENUANTE}
    else:
        accion = {"accion": propuesta, "confianza": salida.confianza, "origen": fuente,
                  "justificacion": salida.justificacion_accion}
    if accion["accion"] != propuesta:
        auditoria.registrar("accion_ajustada", {"incidente_id": incidente_id, "propuesta": propuesta,
                                                "final": accion["accion"], "origen": accion["origen"]})
    return accion


def procesar(incidente_id: int, cliente: ClienteLLM | None = None) -> None:
    """Explica, decide y arma el informe. Nunca lanza excepción: cualquier error del LLM termina en reglas."""
    try:
        datos = repository.obtener_datos_para_explicar(incidente_id)
        if datos is None:
            logger.warning("Incidente %s sin alertas: no hay nada que explicar", incidente_id)
            return
        payload, alertas_agrupadas = datos
        alerta = AlertaEntrada.model_validate(payload)  # al prompt solo llegan campos validados
        laboratorio = repository_laboratorio.opciones_corrida(incidente_id)  # None si es un incidente real
        variante = _variante(laboratorio)
        superados = informe.umbrales_superados(alerta)

        # contexto → dictamen determinista (va al LLM dentro de <contexto_host>)
        contexto_host = contexto.contexto_para(incidente_id) if variante == "con_contexto" else None
        dictamen = dictaminar(contexto_host, superados)
        if contexto_host is not None:
            contexto_host = {**contexto_host, "dictamen_historial": dictamen}
        # Laboratorio: todo host empieza en bloqueo total (simulado). Real: se lee de Ryu (solo lectura).
        modo_actual = "drop_total" if laboratorio is not None else acciones.modo_actual(alerta.host_origen)
        umbrales, fuente_umbrales = evidencia.umbrales_vigentes(laboratorio is not None)
        evaluadas = evidencia.evaluar(alerta.metricas, umbrales)
        mensaje = construir_mensaje_usuario(alerta, alertas_agrupadas, contexto_host, modo_actual,
                                            evidencia.para_prompt(evaluadas))

        try:
            salida, motivo, respuesta = _intentar_llm(alerta, mensaje, contexto_host is not None, cliente, laboratorio)
        except Exception:
            logger.exception("Error inesperado al usar el LLM en el incidente %s", incidente_id)
            salida, motivo, respuesta = None, "error_interno", None
        if salida is None:
            salida = explicar_por_reglas(alerta, contexto_host)
        fuente = "llm" if motivo is None else "reglas"

        # veredicto final y coherencia de acción (el código decide lo concluyente)
        veredicto_llm = salida.veredicto
        veredicto_final = dictamen["veredicto"] or veredicto_llm
        if veredicto_final != veredicto_llm:
            auditoria.registrar("dictamen_aplicado", {"incidente_id": incidente_id, "tipo": dictamen["tipo"],
                                                      "veredicto_llm": veredicto_llm, "veredicto_final": veredicto_final})
        tecnica = informe.normalizar_tecnica(salida.tecnica_id)
        if tecnica[1]:
            auditoria.registrar("tecnica_corregida", {"incidente_id": incidente_id, "propuesta": salida.tecnica_id,
                                                      "corregida_a": tecnica[0]})
        accion = _accion_coherente(incidente_id, salida, fuente, dictamen, modo_actual)

        # verificador anti-alucinación (no bloquea nada)
        numeros, ips = informe.respaldados(alerta, evaluadas, alertas_agrupadas, contexto_host)
        verificacion = verificador.verificar(informe.textos_llm(salida), numeros, ips)
        auditoria.registrar("verificacion_informe", {"incidente_id": incidente_id, **verificacion})

        t_explicado = datetime.now(timezone.utc)
        repository.guardar_explicacion(
            incidente_id,
            {"veredicto": veredicto_final, "severidad": salida.severidad, "confianza": salida.confianza,
             "tecnica_mitre": tecnica[0], "recomendacion_operador": salida.recomendacion_operador,
             "evidencias": [f"{f.factor} ({f.peso}): {f.porque}" for f in salida.factores_ponderados]},
            fuente, motivo, t_explicado,
            respuesta.tokens_entrada if respuesta else None,
            respuesta.tokens_salida if respuesta else None,
            respuesta.latencia_ms if respuesta else None,
            variante, VERSIONES[variante], contexto_host, veredicto_llm, dictamen["tipo"],
        )
        hash_explicacion = auditoria.registrar(
            "explicacion_generada",
            {"incidente_id": incidente_id, "fuente": fuente, "motivo_respaldo": motivo, "variante": variante,
             "version_prompt": VERSIONES[variante], "contexto_usado": contexto_host, "veredicto_llm": veredicto_llm,
             "veredicto_final": veredicto_final},
        )
        eventos.emitir("explicacion_lista", {"incidente_id": incidente_id, "fuente": fuente, "motivo_respaldo": motivo,
                                             "veredicto": veredicto_final, "severidad": salida.severidad})

        try:
            decidido = decision.decidir(incidente_id, {**accion, "veredicto": veredicto_final}, laboratorio)
        except Exception:  # la explicación ya quedó; la acción queda sin decidir y el correo sale igual
            logger.exception("Falló la decisión de la política en el incidente %s", incidente_id)
            decidido = {"decision_politica": None, "motivos": [], "accion_final": None}

        incidente = repository.obtener_incidente(incidente_id)
        traza = {
            "incidente_id": incidente_id, "hash_auditoria_explicacion": hash_explicacion,
            "t_ryu": incidente["t_ryu"].isoformat().replace("+00:00", "Z") if incidente["t_ryu"] else None,
            "t_explicado": t_explicado.isoformat().replace("+00:00", "Z"),
            # el modelo de la llamada real; sin llamada (LLM apagado o respaldo), el configurado (LLM_MODELO)
            "modelo": (respuesta.modelo if respuesta and respuesta.modelo else None) or get_settings().LLM_MODELO,
            "fuente_explicacion": fuente, "version_prompt": VERSIONES[variante], "variante": variante,
            "veredicto_llm": veredicto_llm, "veredicto_final": veredicto_final, "accion_propuesta": accion["accion"],
            "origen_accion": accion["origen"], "confianza_accion": accion["confianza"],
            "justificacion_accion": accion["justificacion"],
            "decision_politica": {"resultado": decidido["decision_politica"], "motivos": decidido["motivos"]},
            "accion_final": decidido["accion_final"],
        }
        armado = informe.ensamblar(alerta, evaluadas, fuente_umbrales, salida, tecnica, dictamen, verificacion, traza)
        repository_acciones.guardar_informe(incidente_id, armado, informe.resumen(armado, salida.severidad))

        omitir = "laboratorio" if laboratorio is not None and not laboratorio["enviar_correos"] else None
        notificacion.notificar(incidente_id, "incidente", omitir=omitir)  # nunca lanza; la explicación ya quedó
    except Exception:
        logger.exception("Falló el pipeline del incidente %s", incidente_id)
