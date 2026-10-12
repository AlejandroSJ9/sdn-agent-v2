"""Motor de políticas (C7): decide si una acción propuesta se ejecuta, se recomienda o se descarta.

Todo aquí es PURO (sin BD, sin red, sin reloj): recibe los datos y devuelve una decisión. Quien la aplica es
services/acciones.py. El LLM y las reglas solo PROPONEN; nunca se desbloquea desde aquí.

Una propuesta se ejecuta sola solo si se cumple TODO:
  1. MODO_DECISION = autonomo_acotado
  2. Ryu tiene capacidad de cuarentena (A6)
  3. confianza ≥ UMBRAL_CONFIANZA
  4. el host no está en HOSTS_PROTEGIDOS
  5. no se supera MAX_ACCIONES_POR_MIN (no aplica en el laboratorio: una corrida dura segundos)
  6. pasar_a_cuarentena solo con veredicto falso_positivo o indeterminado (nunca con confirmado)
  7. en el laboratorio la ejecución es SIMULADA (acciones.py no llama a Ryu)
Si algo falla → recomendación para el operador con los motivos. "mantener" y el modo solo_explicar → descartar.
Excepción: pasar_a_cuarentena con veredicto confirmado → DESCARTAR (motivo cuarentena_incompatible_con_confirmado);
es una contradicción del propio LLM y nunca llega a la bandeja del operador.
"""

from dataclasses import dataclass, field
from typing import Any, Literal

ACCIONES = ("mantener", "pasar_a_cuarentena", "escalar_a_drop")
# C4 (extensión): acciones válidas según el modo actual del host
VALIDAS_POR_MODO = {
    "drop_total": ("mantener", "pasar_a_cuarentena"),
    "cuarentena": ("mantener", "escalar_a_drop"),
}

MOTIVO_MODO = "modo de decisión distinto de autonomo_acotado"
MOTIVO_SIN_A6 = "sin capacidad A6"
MOTIVO_CONFIANZA = "confianza por debajo del umbral"
MOTIVO_PROTEGIDO = "host protegido"
MOTIVO_LIMITE = "límite de acciones por minuto alcanzado"
MOTIVO_CONFIRMADO = "cuarentena no permitida con veredicto confirmado"
MOTIVO_INCOMPATIBLE = "cuarentena_incompatible_con_confirmado"


@dataclass(frozen=True)
class Decision:
    resultado: Literal["ejecutar", "recomendar", "descartar"]
    motivos: list[str] = field(default_factory=list)


def normalizar_accion(accion: str | None, modo_actual: str) -> tuple[str, bool]:
    """(acción válida para el modo, si hubo corrección). Ausente, desconocida o inválida → "mantener"."""
    validas = VALIDAS_POR_MODO.get(modo_actual, VALIDAS_POR_MODO["drop_total"])
    if accion in validas:
        return accion, False
    return "mantener", accion != "mantener"


def evaluar(propuesta: dict[str, Any], incidente: dict[str, Any], contexto_sistema: dict[str, Any]) -> Decision:
    """propuesta: accion, confianza, veredicto · incidente: host, laboratorio ·
    contexto_sistema: modo_decision, capacidad_a6, umbral_confianza, hosts_protegidos,
    acciones_ultimo_minuto, max_acciones_por_min."""
    accion = propuesta["accion"]
    if accion not in ACCIONES or accion == "mantener":
        return Decision("descartar")
    if accion == "pasar_a_cuarentena" and propuesta.get("veredicto") == "confirmado":
        return Decision("descartar", [MOTIVO_INCOMPATIBLE])
    if contexto_sistema["modo_decision"] == "solo_explicar":  # interruptor de emergencia: ni ejecuta ni recomienda
        return Decision("descartar", ["modo solo_explicar"])

    motivos = []
    if contexto_sistema["modo_decision"] != "autonomo_acotado":
        motivos.append(MOTIVO_MODO)
    if not contexto_sistema["capacidad_a6"]:
        motivos.append(MOTIVO_SIN_A6)
    confianza = propuesta.get("confianza")
    if confianza is None or confianza < contexto_sistema["umbral_confianza"]:
        motivos.append(MOTIVO_CONFIANZA)
    if incidente["host"] in contexto_sistema["hosts_protegidos"]:
        motivos.append(MOTIVO_PROTEGIDO)
    if not incidente["laboratorio"] and contexto_sistema["acciones_ultimo_minuto"] >= contexto_sistema["max_acciones_por_min"]:
        motivos.append(MOTIVO_LIMITE)
    if accion == "pasar_a_cuarentena" and propuesta.get("veredicto") not in ("falso_positivo", "indeterminado"):
        motivos.append(MOTIVO_CONFIRMADO)
    return Decision("recomendar", motivos) if motivos else Decision("ejecutar")
