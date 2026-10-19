"""Motor de políticas (C7, Fase 9b): decide si la acción de la escalera se ejecuta, se recomienda o se descarta.

Todo aquí es PURO (sin BD, sin red, sin reloj): recibe los datos y devuelve una decisión. Quien la aplica es
services/acciones.py. La escalera (services/escalera.py) solo relaja el DROP; nunca se desbloquea desde aquí.

Una acción se ejecuta sola solo si se cumple TODO:
  1. MODO_DECISION = autonomo_acotado
  2. Ryu declara la capacidad de la acción (A6 cuarentena_puertos, A7 limitar_tasa, A8 cuarentena_vlan)
  3. el host no está en HOSTS_PROTEGIDOS
  4. no se supera MAX_ACCIONES_POR_MIN (no aplica en el laboratorio: una corrida dura segundos)
  5. el host sigue en DROP total
(UMBRAL_CONFIANZA se retiró en la Fase 9b: ESCALON_HITL es el único umbral y ya lo aplicó la escalera.)
Si algo falla → recomendación para el operador con los motivos. "mantener_drop" y el modo solo_explicar → descartar.
Excepción: limitar_tasa con veredicto confirmado → DESCARTAR (motivo limitar_tasa_incompatible_con_confirmado):
un ataque confirmado siempre conserva cortados los puertos de movimiento lateral.
"""

from dataclasses import dataclass, field
from typing import Any, Literal

ACCIONES = ("mantener_drop", "limitar_tasa", "cuarentena_puertos", "cuarentena_vlan")  # = escalera.ACCIONES
# acción → (capacidad que Ryu declara en C2, motivo si falta)
CAPACIDAD = {"cuarentena_puertos": ("cuarentena", "sin capacidad A6"), "limitar_tasa": ("limitar_tasa", "sin capacidad A7"),
             "cuarentena_vlan": ("cuarentena_vlan", "sin capacidad A8")}

MOTIVO_MODO = "modo de decisión distinto de autonomo_acotado"
MOTIVO_SIN_A6 = CAPACIDAD["cuarentena_puertos"][1]
MOTIVO_SIN_A7 = CAPACIDAD["limitar_tasa"][1]
MOTIVO_SIN_A8 = CAPACIDAD["cuarentena_vlan"][1]
MOTIVO_PROTEGIDO = "host protegido"
MOTIVO_LIMITE = "límite de acciones por minuto alcanzado"
MOTIVO_NO_DROP = "el host ya no está en DROP total"
MOTIVO_INCOMPATIBLE = "limitar_tasa_incompatible_con_confirmado"


@dataclass(frozen=True)
class Decision:
    resultado: Literal["ejecutar", "recomendar", "descartar"]
    motivos: list[str] = field(default_factory=list)


def evaluar(propuesta: dict[str, Any], incidente: dict[str, Any], contexto_sistema: dict[str, Any]) -> Decision:
    """propuesta: accion, veredicto (final), veredicto_salida · incidente: host, laboratorio, modo_actual ·
    contexto_sistema: modo_decision, capacidades {cuarentena, limitar_tasa, cuarentena_vlan}, hosts_protegidos,
    acciones_ultimo_minuto, max_acciones_por_min."""
    accion = propuesta["accion"]
    if accion not in CAPACIDAD:  # mantener_drop o fuera de la lista cerrada
        return Decision("descartar")
    if accion == "limitar_tasa" and "confirmado" in (propuesta.get("veredicto"), propuesta.get("veredicto_salida")):
        return Decision("descartar", [MOTIVO_INCOMPATIBLE])
    if contexto_sistema["modo_decision"] == "solo_explicar":  # interruptor de emergencia: ni ejecuta ni recomienda
        return Decision("descartar", ["modo solo_explicar"])
    if incidente.get("modo_actual", "drop_total") != "drop_total":
        return Decision("descartar", [MOTIVO_NO_DROP])

    motivos = []
    if contexto_sistema["modo_decision"] != "autonomo_acotado":
        motivos.append(MOTIVO_MODO)
    capacidad, motivo_sin = CAPACIDAD[accion]
    if not contexto_sistema["capacidades"].get(capacidad):
        motivos.append(motivo_sin)
    if incidente["host"] in contexto_sistema["hosts_protegidos"]:
        motivos.append(MOTIVO_PROTEGIDO)
    if not incidente["laboratorio"] and contexto_sistema["acciones_ultimo_minuto"] >= contexto_sistema["max_acciones_por_min"]:
        motivos.append(MOTIVO_LIMITE)
    return Decision("recomendar", motivos) if motivos else Decision("ejecutar")
