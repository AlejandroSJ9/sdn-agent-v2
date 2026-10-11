"""Ejecutar pytest desde el panel: solo tests/unit o tests/integracion, una ejecución a la vez, 300 s máx.

El comando es una lista FIJA (sin shell) elegida por una clave cerrada; el texto del usuario nunca entra.
"""

import logging
import re
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.db import repository_laboratorio
from app.services import eventos

logger = logging.getLogger(__name__)

RAIZ = Path(__file__).resolve().parents[2]
CARPETAS = {"unit": "tests/unit", "integracion": "tests/integracion"}
TIEMPO_MAXIMO = 300
MAX_SALIDA = 20_000
crear_proceso = subprocess.Popen  # punto de inyección: las pruebas usan un proceso falso
_candado = threading.Lock()


class PytestEnCurso(Exception):
    """Ya hay una ejecución de pytest en curso (409)."""


def comando(carpeta: str) -> list[str]:
    return [sys.executable, "-m", "pytest", CARPETAS[carpeta], "-q"]


def lanzar(carpeta: str, programar: Callable[..., Any]) -> int:
    if carpeta not in CARPETAS:
        raise ValueError(f"Carpeta no permitida: {carpeta!r}")
    if not _candado.acquire(blocking=False):
        raise PytestEnCurso("Ya hay una ejecución de pytest en curso.")
    try:
        ejecucion_id = repository_laboratorio.crear_ejecucion_pytest(carpeta)
        programar(_ejecutar, ejecucion_id, carpeta)
    except Exception:
        _candado.release()
        raise
    return ejecucion_id


def _contar(lineas: list[str]) -> tuple[int | None, int | None]:
    """Cifras de la línea final de pytest (p. ej. "12 passed, 1 failed, 2 errors in 3.1s")."""
    for linea in reversed(lineas):
        cifras = dict((tipo, int(n)) for n, tipo in re.findall(r"(\d+) (passed|failed|errors?)\b", linea))
        if cifras:
            return cifras.get("passed", 0), cifras.get("failed", 0) + cifras.get("error", 0) + cifras.get("errors", 0)
    return None, None


def _ejecutar(ejecucion_id: int, carpeta: str) -> None:
    inicio = time.monotonic()
    lineas: list[str] = []
    estado = "error"
    try:
        proceso = crear_proceso(
            comando(carpeta), cwd=RAIZ, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
        )
        vencido = threading.Event()

        def matar() -> None:
            vencido.set()
            proceso.kill()

        reloj = threading.Timer(TIEMPO_MAXIMO, matar)
        reloj.daemon = True
        reloj.start()
        try:
            for linea in proceso.stdout:
                linea = linea.rstrip("\n")
                lineas.append(linea)
                eventos.emitir(
                    "prueba_progreso",
                    {"tipo": "pytest", "ejecucion_id": ejecucion_id, "carpeta": carpeta, "linea": linea[:500]},
                )
            proceso.wait()
        finally:
            reloj.cancel()
        estado = "timeout" if vencido.is_set() else "terminada"
    except Exception:
        logger.exception("No se pudo ejecutar pytest (%s)", carpeta)
        lineas.append("Error interno al ejecutar pytest (ver el log del agente).")
    finally:
        aprobadas, fallidas = _contar(lineas)
        duracion_ms = int((time.monotonic() - inicio) * 1000)
        salida = "\n".join(lineas)[-MAX_SALIDA:]
        try:
            repository_laboratorio.terminar_ejecucion_pytest(ejecucion_id, estado, aprobadas, fallidas, duracion_ms, salida)
        finally:
            eventos.emitir(
                "prueba_terminada",
                {"tipo": "pytest", "ejecucion_id": ejecucion_id, "carpeta": carpeta, "estado": estado,
                 "aprobadas": aprobadas, "fallidas": fallidas, "duracion_ms": duracion_ms},
            )
            if _candado.locked():
                _candado.release()


def ultimo() -> dict[str, Any] | None:
    return repository_laboratorio.ultima_ejecucion_pytest()
