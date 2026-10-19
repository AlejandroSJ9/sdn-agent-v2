"""Confianza calculada y escalera de respuesta (C7, Fase 9a). Funciones PURAS: sin BD, sin red, sin reloj.

La confianza es confianza en el DIAGNÓSTICO (no en "es ataque") y la calcula el código; la del LLM es
informativa. Un humano debe poder recalcularla a mano con el desglose que se guarda en el informe:
  confirmado     → 0.5·s + 0.5 − penalización
  falso_positivo → 0.5·(1 − s) + 0.5 − penalización
  indeterminado  → sin confianza: mantener_drop
s = puntaje_total/puntaje_maximo de Ryu o, si la alerta no lo trae, Σ pesos C1 de umbrales_superados / 135.
Se redondea a 2 decimales (mitad hacia arriba) y se decide sobre ese valor.

Escalera: < HITL o indeterminado → mantener_drop; falso_positivo ≥ HITL → limitar_tasa;
confirmado en [HITL, VLAN] → cuarentena_puertos; confirmado > VLAN → cuarentena_vlan.
Dictamen: agravante → mantener_drop; atenuante → como máximo limitar_tasa (si el escalón era una cuarentena,
el diagnóstico confirmado contradice el historial del operador → mantener_drop). Confirmado NUNCA da limitar_tasa.
"""

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

PESOS_C1 = {"M5": 40, "M3": 35, "M1": 25, "M2": 20, "M4": 15}
MAXIMO_C1 = 135
PENALIZACION = Decimal("0.20")
MIN_DESCARTE_BENIGNO = 20
ACCIONES = ("mantener_drop", "limitar_tasa", "cuarentena_puertos", "cuarentena_vlan")
ETIQUETAS = {"mantener_drop": "Mantener DROP total", "limitar_tasa": "Limitar tasa",
             "cuarentena_puertos": "Cuarentena de puertos", "cuarentena_vlan": "Cuarentena en VLAN"}
MOTIVO_CONTRADICCION = "diagnóstico del LLM contradice el historial verificado por el operador: decide el humano"


def _decimal(valor: float | int) -> Decimal:
    return Decimal(str(valor))


def _redondear(valor: Decimal, decimales: int) -> float:
    return float(valor.quantize(Decimal(1).scaleb(-decimales), rounding=ROUND_HALF_UP))


def componente_ryu(puntaje_total: float | None, puntaje_maximo: float | None,
                   umbrales_superados: list[str]) -> tuple[Decimal, str, str]:
    """(s, fuente, fracción legible)."""
    if puntaje_total is not None and puntaje_maximo:
        return _decimal(puntaje_total) / _decimal(puntaje_maximo), "puntaje_ryu", f"{puntaje_total}/{puntaje_maximo}"
    suma = sum(PESOS_C1[u] for u in umbrales_superados if u in PESOS_C1)
    return Decimal(suma) / Decimal(MAXIMO_C1), "pesos_c1", f"{suma}/{MAXIMO_C1}"


def calcular_confianza(veredicto: str, puntaje_total: float | None, puntaje_maximo: float | None,
                       umbrales_superados: list[str], verificacion: str, descarte_benigno: str) -> dict[str, Any]:
    """Desglose completo; total es None si el veredicto de la salida es indeterminado."""
    s, fuente, fraccion = componente_ryu(puntaje_total, puntaje_maximo, umbrales_superados)
    motivos = []
    if verificacion == "posible_alucinacion":
        motivos.append("el verificador marcó posible alucinación")
    if len((descarte_benigno or "").strip()) < MIN_DESCARTE_BENIGNO:
        motivos.append(f"descarte benigno ausente (< {MIN_DESCARTE_BENIGNO} caracteres)")
    penalizacion = PENALIZACION if motivos else Decimal(0)  # una sola vez
    desglose = {"s": _redondear(s, 4), "fuente_ryu": fuente, "veredicto_usado": veredicto,
                "penalizacion": float(penalizacion), "motivos_penalizacion": motivos}

    if veredicto not in ("confirmado", "falso_positivo"):
        return {**desglose, "termino_ryu": None, "termino_veredicto": None, "total": None,
                "formula": f"{veredicto}: sin confianza ni escalón; decide el humano"}
    termino_ryu = Decimal("0.5") * (s if veredicto == "confirmado" else 1 - s)
    termino_veredicto = Decimal("0.5")
    exacto = min(max(termino_ryu + termino_veredicto - penalizacion, Decimal(0)), Decimal(1))
    total = _redondear(exacto, 2)
    expresion = f"0.5·({fraccion})" if veredicto == "confirmado" else f"0.5·(1 − {fraccion})"
    return {**desglose, "termino_ryu": _redondear(termino_ryu, 4), "termino_veredicto": float(termino_veredicto),
            "total": total,
            "formula": f"{veredicto}: {expresion} + 0.5 − {penalizacion:.2f} = {_redondear(exacto, 4):.4f} → {total:.2f}"}


def escalon(veredicto: str, total: float | None, hitl: float, vlan: float) -> str:
    if total is None or total < hitl:
        return "mantener_drop"
    if veredicto == "falso_positivo":
        return "limitar_tasa"
    if veredicto != "confirmado":
        return "mantener_drop"
    return "cuarentena_vlan" if total > vlan else "cuarentena_puertos"


def decidir(confianza: dict[str, Any], dictamen: dict[str, Any], hitl: float, vlan: float) -> dict[str, Any]:
    """Escalón por confianza y acción recomendada tras aplicar el dictamen (que manda sobre la confianza)."""
    veredicto = confianza["veredicto_usado"]
    por_confianza = escalon(veredicto, confianza["total"], hitl, vlan)
    accion, motivo = por_confianza, None
    if dictamen.get("tipo") == "agravante":
        accion, motivo = "mantener_drop", f"dictamen agravante: {dictamen['motivo']}"
    elif dictamen.get("tipo") == "atenuante" and por_confianza in ("cuarentena_puertos", "cuarentena_vlan"):
        accion, motivo = "mantener_drop", MOTIVO_CONTRADICCION
    if accion == "limitar_tasa" and veredicto == "confirmado":  # defensa en profundidad (no debería ocurrir)
        accion, motivo = "mantener_drop", "confirmado nunca limitar_tasa"
    modificada = accion != por_confianza
    return {"escalon_por_confianza": por_confianza, "accion_recomendada": accion,
            "modificada_por_dictamen": modificada, "motivo_modificacion": motivo if modificada else None,
            "umbrales": {"ESCALON_HITL": hitl, "ESCALON_VLAN": vlan}}


def texto_accion(accion: str, total: float | None) -> str:
    """'Limitar tasa (confianza 0.78)' para el correo, el resumen y el panel."""
    etiqueta = ETIQUETAS.get(accion, accion)
    return f"{etiqueta} (confianza {total:.2f})" if total is not None else f"{etiqueta} (diagnóstico indeterminado)"
