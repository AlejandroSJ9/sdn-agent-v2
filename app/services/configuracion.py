"""Configuración editable en caliente: LLM, agrupación, contexto dinámico (C6) y motor de políticas (C7).

Valor efectivo = el de la tabla configuracion si existe; si no, el de .env (Settings). Se lee en cada
llamada (consulta por clave primaria), así que un cambio aplica de inmediato sin reiniciar.
"""

import ipaddress
import json
import logging
import threading
from typing import Any

from app.config import get_settings
from app.db import repository_operador
from app.services import auditoria, eventos

logger = logging.getLogger(__name__)

CLAVES = (
    "LLM_HABILITADO", "DEDUP_SEGUNDOS", "CONTEXTO_HABILITADO", "HISTORIAL_DIAS", "CAMPANA_MINUTOS",
    "MODO_DECISION", "UMBRAL_CONFIANZA", "MAX_ACCIONES_POR_MIN", "HOSTS_PROTEGIDOS", "RECOMENDACION_TTL_MIN",
)
_BOOLEANAS = ("LLM_HABILITADO", "CONTEXTO_HABILITADO")
MODOS_DECISION = ("solo_explicar", "recomendar", "autonomo_acotado")
_candado = threading.Lock()  # leer-comparar-guardar-auditar de forma atómica (un solo proceso)


def _a_texto(valor: Any) -> str:
    if isinstance(valor, bool):
        return "true" if valor else "false"
    return json.dumps(valor) if isinstance(valor, list) else str(valor)


def _hosts(texto: str) -> list[str]:
    """Lista JSON de IPv4 sueltas (nunca subredes)."""
    lista = json.loads(texto)
    if not isinstance(lista, list):
        raise ValueError(texto)
    return [str(ipaddress.IPv4Address(ip)) for ip in lista]


def _desde_texto(clave: str, texto: str) -> Any:
    if clave in _BOOLEANAS:
        if texto not in ("true", "false"):
            raise ValueError(texto)
        return texto == "true"
    if clave == "MODO_DECISION":
        if texto not in MODOS_DECISION:
            raise ValueError(texto)
        return texto
    if clave == "UMBRAL_CONFIANZA":
        return float(texto)
    if clave == "HOSTS_PROTEGIDOS":
        return _hosts(texto)
    return int(texto)


def obtener() -> dict[str, Any]:
    guardados = repository_operador.leer_configuracion()
    efectivos = {}
    for clave in CLAVES:
        efectivos[clave] = getattr(get_settings(), clave)
        if clave == "HOSTS_PROTEGIDOS":  # en .env es texto JSON
            try:
                efectivos[clave] = _hosts(efectivos[clave])
            except ValueError:
                logger.warning("HOSTS_PROTEGIDOS inválido en .env; se usa la lista vacía")
                efectivos[clave] = []
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


def politica() -> dict[str, Any]:
    """Parámetros vigentes del motor de políticas (C7)."""
    valores = obtener()
    return {"modo_decision": valores["MODO_DECISION"], "umbral_confianza": valores["UMBRAL_CONFIANZA"],
            "max_acciones_por_min": valores["MAX_ACCIONES_POR_MIN"], "hosts_protegidos": valores["HOSTS_PROTEGIDOS"],
            "recomendacion_ttl_min": valores["RECOMENDACION_TTL_MIN"]}


def contexto() -> dict[str, Any]:
    """{"habilitado", "historial_dias", "campana_minutos"} vigentes."""
    valores = obtener()
    return {"habilitado": valores["CONTEXTO_HABILITADO"], "historial_dias": valores["HISTORIAL_DIAS"],
            "campana_minutos": valores["CAMPANA_MINUTOS"]}


def actualizar(cambios: dict[str, Any]) -> dict[str, Any]:
    """Guarda solo lo que realmente cambia; cada cambio se audita y se emite como config_cambiada."""
    with _candado:
        anteriores = obtener()
        for clave, nuevo in cambios.items():
            if nuevo is None or nuevo == anteriores[clave]:
                continue
            repository_operador.guardar_configuracion(clave, _a_texto(nuevo))
            datos = {"clave": clave, "anterior": anteriores[clave], "nuevo": nuevo}
            auditoria.registrar("config_cambiada", datos)
            eventos.emitir("config_cambiada", datos)
            if clave == "MODO_DECISION":  # C5: el interruptor de emergencia también tiene su propio evento
                eventos.emitir("modo_decision_cambiado", {"anterior": anteriores[clave], "nuevo": nuevo})
    return obtener()
