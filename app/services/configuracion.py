"""Configuración editable en caliente: LLM, agrupación, contexto dinámico (C6) y motor de políticas (C7).

Valor efectivo = el de la tabla configuracion si existe; si no, el de .env (Settings). Se lee en cada
llamada (consulta por clave primaria), así que un cambio aplica de inmediato sin reiniciar.
Fase D1: MODO_DECISION=autonomo_acotado guardado o en .env se lee como "recomendar" (al arrancar se registra en el
log y la auditoría); pedirlo por PUT → 422. UMBRAL_CONFIANZA, MAX_ACCIONES_POR_MIN y HOSTS_PROTEGIDOS se aceptan sin efecto.
"""

import json
import logging
import threading
from typing import Any

from app.config import get_settings
from app.db import repository_operador
from app.services import auditoria, eventos

logger = logging.getLogger(__name__)

CLAVES = (
    "LLM_HABILITADO", "DEDUP_SEGUNDOS", "CONTEXTO_HABILITADO", "HISTORIAL_DIAS", "CAMPANA_MINUTOS", "PROMPT_V4",
    "MODO_DECISION", "RECOMENDACION_TTL_MIN", "ESCALON_HITL", "ESCALON_VLAN",
)
RETIRADAS = ("UMBRAL_CONFIANZA", "MAX_ACCIONES_POR_MIN", "HOSTS_PROTEGIDOS")  # se aceptan sin efecto
_BOOLEANAS = ("LLM_HABILITADO", "CONTEXTO_HABILITADO", "PROMPT_V4")
MODOS_DECISION = ("solo_explicar", "recomendar")
MODO_RETIRADO = "autonomo_acotado"
MENSAJE_MODO_RETIRADO = "El modo autonomo_acotado fue retirado en la Fase D1: el agente solo recomienda; el operador aprueba."
ESCALONES = ("ESCALON_HITL", "ESCALON_VLAN")  # C7: 0.50 ≤ HITL < VLAN ≤ 1
_candado = threading.Lock()  # leer-comparar-guardar-auditar de forma atómica (un solo proceso)


def _a_texto(valor: Any) -> str:
    if isinstance(valor, bool):
        return "true" if valor else "false"
    return json.dumps(valor) if isinstance(valor, list) else str(valor)


def _desde_texto(clave: str, texto: str) -> Any:
    if clave in _BOOLEANAS:
        if texto not in ("true", "false"):
            raise ValueError(texto)
        return texto == "true"
    if clave == "MODO_DECISION":
        if texto == MODO_RETIRADO:
            return "recomendar"
        if texto not in MODOS_DECISION:
            raise ValueError(texto)
        return texto
    if clave in ESCALONES:
        return float(texto)
    return int(texto)


def obtener() -> dict[str, Any]:
    guardados = repository_operador.leer_configuracion()
    efectivos = {}
    for clave in CLAVES:
        efectivos[clave] = getattr(get_settings(), clave)
        if clave == "MODO_DECISION" and efectivos[clave] == MODO_RETIRADO:
            efectivos[clave] = "recomendar"
        if clave in guardados:
            try:
                efectivos[clave] = _desde_texto(clave, guardados[clave])
            except ValueError:
                logger.warning("Valor inválido de %s en la BD (%r); se usa el de .env", clave, guardados[clave])
    return efectivos


def llm_habilitado() -> bool:
    return obtener()["LLM_HABILITADO"]


def dedup_segundos() -> int:
    return obtener()["DEDUP_SEGUNDOS"]


def prompt_v4() -> bool:
    """Fase D3 (temporal): True → prompts v4.0 / v4.1 (explicación simple)."""
    return obtener()["PROMPT_V4"]


def politica() -> dict[str, Any]:
    """Parámetros vigentes del motor de políticas (C7)."""
    valores = obtener()
    return {"modo_decision": valores["MODO_DECISION"], "recomendacion_ttl_min": valores["RECOMENDACION_TTL_MIN"],
            "escalon_hitl": valores["ESCALON_HITL"], "escalon_vlan": valores["ESCALON_VLAN"]}


def contexto() -> dict[str, Any]:
    """{"habilitado", "historial_dias", "campana_minutos"} vigentes."""
    valores = obtener()
    return {"habilitado": valores["CONTEXTO_HABILITADO"], "historial_dias": valores["HISTORIAL_DIAS"],
            "campana_minutos": valores["CAMPANA_MINUTOS"]}


class ConfiguracionInvalida(ValueError):
    """La combinación de valores no es válida (422)."""


def actualizar(cambios: dict[str, Any]) -> dict[str, Any]:
    """Guarda solo lo que realmente cambia; cada cambio se audita y se emite como config_cambiada."""
    if cambios.get("MODO_DECISION") == MODO_RETIRADO:
        raise ConfiguracionInvalida(MENSAJE_MODO_RETIRADO)
    with _candado:
        anteriores = obtener()
        hitl, vlan = (cambios.get(c) if cambios.get(c) is not None else anteriores[c] for c in ESCALONES)
        if not 0.5 <= hitl < vlan <= 1:
            raise ConfiguracionInvalida(f"Se requiere 0.50 ≤ ESCALON_HITL < ESCALON_VLAN ≤ 1 (HITL={hitl}, VLAN={vlan})")
        for clave, nuevo in cambios.items():
            if clave in RETIRADAS or nuevo is None or nuevo == anteriores[clave]:
                continue
            repository_operador.guardar_configuracion(clave, _a_texto(nuevo))
            datos = {"clave": clave, "anterior": anteriores[clave], "nuevo": nuevo}
            auditoria.registrar("config_cambiada", datos)
            eventos.emitir("config_cambiada", datos)
            if clave == "MODO_DECISION":  # C5: el interruptor de emergencia también tiene su propio evento
                eventos.emitir("modo_decision_cambiado", {"anterior": anteriores[clave], "nuevo": nuevo})
    return obtener()


def retirar_modo_autonomo() -> dict[str, Any] | None:
    """Al arrancar (Fase D1): si la BD o .env traen autonomo_acotado, el agente queda en recomendar y lo registra.
    El valor guardado se reescribe; el de .env no se puede tocar y se sigue leyendo como recomendar."""
    with _candado:
        guardado = repository_operador.leer_configuracion().get("MODO_DECISION")
        if guardado == MODO_RETIRADO:
            repository_operador.guardar_configuracion("MODO_DECISION", "recomendar")
            origen = "configuracion_guardada"
        elif guardado is None and get_settings().MODO_DECISION == MODO_RETIRADO:
            origen = ".env"
        else:
            return None
        datos = {"clave": "MODO_DECISION", "anterior": MODO_RETIRADO, "nuevo": "recomendar", "origen": origen,
                 "motivo": "modo retirado en la Fase D1"}
        auditoria.registrar("config_cambiada", datos)
    logger.warning("MODO_DECISION=autonomo_acotado (%s) fue retirado en la Fase D1: el agente arranca en recomendar", origen)
    return datos
