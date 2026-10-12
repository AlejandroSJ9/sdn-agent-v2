"""Evidencia objetiva calculada por CÓDIGO (Fase 6.8b): umbrales vigentes, desviación y "supera".

Umbrales: laboratorio → los de tests/escenarios/escenarios.json (v1, con los que se generaron los casos);
real → los de Ryu (GET /ryu/umbrales) y, si no responde, los valores por defecto de docs/ajustes.md (A4).
"supera" se calcula como Ryu: ">" para M1, M2 y M4; ">=" para M5_count.
"""

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.integrations.ryu import RyuError
from app.schemas.alerta import Metricas
from app.services import operador

RUTA_ESCENARIOS = Path(__file__).resolve().parents[2] / "tests" / "escenarios" / "escenarios.json"
POR_DEFECTO = {"M1": 20, "M2": 8, "M4": 200, "M5": 5}  # docs/ajustes.md A4
# campo de la alerta → (clave del umbral, comparación de Ryu, nombre corto para el prompt)
METRICAS = {
    "M1_nuevos_flujos_por_30s": ("M1", ">", "M1"),
    "M2_destinos_unicos_por_30s": ("M2", ">", "M2"),
    "M4_packet_in_por_min": ("M4", ">", "M4"),
    "M5_count": ("M5", ">=", "M5_count"),
}
FUENTE_LAB, FUENTE_RYU, FUENTE_DEFECTO = "escenarios_v1 (laboratorio)", "ryu", "ajustes_por_defecto"


def _numero(valor: Any) -> float | int:
    """20.0 → 20 (así se ve igual en el prompt, el informe y el correo)."""
    valor = float(valor)
    return int(valor) if valor.is_integer() else valor


@lru_cache
def _umbrales_escenarios() -> dict[str, float]:
    texto = json.loads(RUTA_ESCENARIOS.read_text(encoding="utf-8"))["umbrales"]
    return {clave: _numero(re.search(r"\d+", texto[clave]).group()) for clave in ("M1", "M2", "M4", "M5")}


def umbrales_vigentes(laboratorio: bool) -> tuple[dict[str, float], str]:
    if laboratorio:
        return _umbrales_escenarios(), FUENTE_LAB
    try:
        de_ryu = operador.obtener_cliente_ryu().umbrales()
        return {clave: _numero(de_ryu[clave]) for clave in POR_DEFECTO}, FUENTE_RYU
    except (RyuError, KeyError, TypeError, ValueError):
        return dict(POR_DEFECTO), FUENTE_DEFECTO


def evaluar(metricas: Metricas, umbrales: dict[str, float]) -> dict[str, dict[str, Any]]:
    evaluadas = {}
    for campo, (clave, comparacion, _) in METRICAS.items():
        valor, umbral = getattr(metricas, campo), umbrales[clave]
        supera = valor > umbral if comparacion == ">" else valor >= umbral
        evaluadas[campo] = {"valor": valor, "umbral": umbral, "comparacion": comparacion,
                            "desviacion": round(valor / umbral, 2) if umbral else 0.0, "supera": supera}
    return evaluadas


def para_prompt(evaluadas: dict[str, dict[str, Any]]) -> dict[str, dict[str, float]]:
    """{"umbrales": {M1, M2, M4, M5_count}, "desviaciones": {…}} para <datos_red>."""
    nombre = {campo: corto for campo, (_, _, corto) in METRICAS.items()}
    return {
        "umbrales": {nombre[c]: e["umbral"] for c, e in evaluadas.items()},
        "desviaciones": {nombre[c]: e["desviacion"] for c, e in evaluadas.items()},
    }
