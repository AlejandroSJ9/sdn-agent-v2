"""Configuración editable en caliente: LLM_HABILITADO y DEDUP_SEGUNDOS.

Valor efectivo = el de la tabla configuracion si existe; si no, el de .env (Settings). Se lee en cada
llamada (consulta por clave primaria), así que un cambio aplica de inmediato sin reiniciar.
"""

import logging
import threading
from typing import Any

from app.config import get_settings
from app.db import repository_operador
from app.services import auditoria, eventos

logger = logging.getLogger(__name__)

CLAVES = ("LLM_HABILITADO", "DEDUP_SEGUNDOS")
_candado = threading.Lock()  # leer-comparar-guardar-auditar de forma atómica (un solo proceso)


def _a_texto(valor: Any) -> str:
    return ("true" if valor else "false") if isinstance(valor, bool) else str(valor)


def _desde_texto(clave: str, texto: str) -> Any:
    if clave == "LLM_HABILITADO":
        if texto not in ("true", "false"):
            raise ValueError(texto)
        return texto == "true"
    return int(texto)


def obtener() -> dict[str, Any]:
    guardados = repository_operador.leer_configuracion()
    efectivos = {}
    for clave in CLAVES:
        efectivos[clave] = getattr(get_settings(), clave)
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
    return obtener()
