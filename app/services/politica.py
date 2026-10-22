"""Motor de políticas (C7, Fase D1): decide si la acción de la escalera se recomienda o se descarta.

Todo aquí es PURO (sin BD, sin red, sin reloj): recibe los datos y devuelve una decisión. El agente NUNCA ejecuta
por su cuenta: toda acción distinta de mantener_drop queda como recomendación para el operador, que al aprobarla la
ejecuta por services/acciones.py. La escalera (services/escalera.py) solo relaja el DROP; nunca se desbloquea desde aquí.

Se descarta: "mantener_drop", el modo solo_explicar y un host que ya no está en DROP total.
Si Ryu no declara la capacidad de la acción (A6 cuarentena_puertos, A7 limitar_tasa, A8 cuarentena_vlan), se
recomienda igual con ese motivo (aprobarla fallaría). autonomo_acotado y sus límites se retiraron en la Fase D1.
Excepción: limitar_tasa con veredicto confirmado → DESCARTAR (motivo limitar_tasa_incompatible_con_confirmado):
un ataque confirmado siempre conserva cortados los puertos de movimiento lateral.
"""

from dataclasses import dataclass, field
from typing import Any, Literal

ACCIONES = ("mantener_drop", "limitar_tasa", "cuarentena_puertos", "cuarentena_vlan")  # = escalera.ACCIONES
# acción → (capacidad que Ryu declara en C2, motivo si falta)
CAPACIDAD = {"cuarentena_puertos": ("cuarentena", "sin capacidad A6"), "limitar_tasa": ("limitar_tasa", "sin capacidad A7"),
             "cuarentena_vlan": ("cuarentena_vlan", "sin capacidad A8")}

MOTIVO_SIN_A6 = CAPACIDAD["cuarentena_puertos"][1]
MOTIVO_SIN_A7 = CAPACIDAD["limitar_tasa"][1]
MOTIVO_SIN_A8 = CAPACIDAD["cuarentena_vlan"][1]
MOTIVO_NO_DROP = "el host ya no está en DROP total"
MOTIVO_INCOMPATIBLE = "limitar_tasa_incompatible_con_confirmado"


@dataclass(frozen=True)
class Decision:
    resultado: Literal["recomendar", "descartar"]
    motivos: list[str] = field(default_factory=list)


def evaluar(propuesta: dict[str, Any], incidente: dict[str, Any], contexto_sistema: dict[str, Any]) -> Decision:
    """propuesta: accion, veredicto (final), veredicto_salida · incidente: modo_actual ·
    contexto_sistema: modo_decision, capacidades {cuarentena, limitar_tasa, cuarentena_vlan}."""
    accion = propuesta["accion"]
    if accion not in CAPACIDAD:  # mantener_drop o fuera de la lista cerrada
        return Decision("descartar")
    if accion == "limitar_tasa" and "confirmado" in (propuesta.get("veredicto"), propuesta.get("veredicto_salida")):
        return Decision("descartar", [MOTIVO_INCOMPATIBLE])
    if contexto_sistema["modo_decision"] == "solo_explicar":  # interruptor de emergencia: ni recomienda
        return Decision("descartar", ["modo solo_explicar"])
    if incidente.get("modo_actual", "drop_total") != "drop_total":
        return Decision("descartar", [MOTIVO_NO_DROP])

    capacidad, motivo_sin = CAPACIDAD[accion]
    return Decision("recomendar", [] if contexto_sistema["capacidades"].get(capacidad) else [motivo_sin])
