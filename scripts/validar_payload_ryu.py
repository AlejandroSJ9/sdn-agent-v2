"""Valida una alerta de Ryu-IA contra el MISMO esquema que usa el agente (C1). No necesita el agente ni el .env.

Uso:
    python scripts/validar_payload_ryu.py archivo.json
    python scripts/validar_payload_ryu.py < archivo.json      (o pega el JSON del log de Ryu y termina con Ctrl+D)
Código de salida: 0 si es COMPATIBLE, 1 si no.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pydantic import ValidationError  # noqa: E402

from app.schemas.alerta import CAMPO_IGNORADO, AlertaEntrada, Metricas, describir_errores  # noqa: E402

# Campos de RYU_IA v2 que el agente conoce y guarda en crudo aunque no estén en el modelo validado.
EXTRA_PRIMER_NIVEL = {CAMPO_IGNORADO, "puntaje_total", "puntaje_maximo", "pesos_metricas"}
EXTRA_METRICAS = {"M3_duracion_seg", "M3_bytes"}


def campos_no_reconocidos(datos: dict) -> list[str]:
    """Nombres que el agente no reconoce (p. ej. un campo renombrado): se ignorarían en silencio."""
    sobrantes = [c for c in datos if c not in AlertaEntrada.model_fields and c not in EXTRA_PRIMER_NIVEL]
    metricas = datos.get("metricas")
    if isinstance(metricas, dict):
        sobrantes += [f"metricas.{c}" for c in metricas if c not in Metricas.model_fields and c not in EXTRA_METRICAS]
    return sobrantes


def validar(texto: str) -> list[str]:
    """Lista de problemas en español; vacía si la alerta es compatible."""
    try:
        datos = json.loads(texto)
    except ValueError as error:
        return [f"El texto no es JSON válido: {error}"]
    if not isinstance(datos, dict):
        return ["La alerta debe ser un objeto JSON ({...})."]
    try:
        AlertaEntrada.model_validate(datos)
        problemas = []
    except ValidationError as error:
        problemas = [
            f"campo {e['campo']}: {e['problema']}" + ("" if e["valor"] is None else f" (recibido: {e['valor']!r})")
            for e in describir_errores(error)
        ]
    if problemas:
        problemas += [f"campo {c}: el agente no lo reconoce (¿nombre cambiado?)" for c in campos_no_reconocidos(datos)]
    return problemas


def main(argumentos: list[str]) -> int:
    if len(argumentos) > 1:
        print("Uso: python scripts/validar_payload_ryu.py [archivo.json]", file=sys.stderr)
        return 2
    texto = Path(argumentos[0]).read_text(encoding="utf-8") if argumentos else sys.stdin.read()
    problemas = validar(texto)
    if not problemas:
        print("COMPATIBLE")
        return 0
    print("NO COMPATIBLE:")
    for problema in problemas:
        print(f"  - {problema}")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
