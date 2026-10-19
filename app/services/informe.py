"""Ensamblado del informe auditable (Fase 6.8b). El CÓDIGO aporta todos los hechos (valores, umbrales,
desviaciones, MITRE oficial, dictamen, solidez, auditoría); del LLM solo se toman los textos de interpretación.
"""

from typing import Any

from app.schemas.alerta import AlertaEntrada
from app.schemas.explicacion import SalidaLLMv3
from app.schemas.informe import Informe
from app.services import escalera
from app.services.prompt import TECNICAS_PERMITIDAS, UMBRALES_VALIDOS

TECNICA_POR_DEFECTO = "T1021"
# Nombres y tácticas OFICIALES de MITRE ATT&CK (el LLM solo elige el id).
CATALOGO_MITRE = {
    "T1021.002": ("Remote Services: SMB/Windows Admin Shares", "Lateral Movement (TA0008)"),
    "T1021.001": ("Remote Services: Remote Desktop Protocol", "Lateral Movement (TA0008)"),
    "T1021.006": ("Remote Services: Windows Remote Management", "Lateral Movement (TA0008)"),
    "T1046": ("Network Service Discovery", "Discovery (TA0007)"),
    "T1570": ("Lateral Tool Transfer", "Lateral Movement (TA0008)"),
    "T1021": ("Remote Services", "Lateral Movement (TA0008)"),
}
REVERSIBLES = ("mantener", "pasar_a_cuarentena", "escalar_a_drop", *escalera.ACCIONES)  # ninguna libera el host
_CLAVE_RYU = {"M1_nuevos_flujos_por_30s": "M1", "M2_destinos_unicos_por_30s": "M2", "M4_packet_in_por_min": "M4",
              "M5_count": "M5"}
MAX_RESUMEN = 600


def normalizar_tecnica(tecnica_id: str | None) -> tuple[str, bool]:
    """(id de la lista cerrada, si se corrigió). Fuera de la lista → T1021."""
    return (tecnica_id, False) if tecnica_id in TECNICAS_PERMITIDAS else (TECNICA_POR_DEFECTO, True)


def umbrales_superados(alerta: AlertaEntrada) -> list[str]:
    return [u for u in UMBRALES_VALIDOS if u in alerta.umbrales_superados]


def puntajes(crudo: dict) -> tuple[float | None, float | None]:
    total, maximo = crudo.get("puntaje_total"), crudo.get("puntaje_maximo")
    if isinstance(total, (int, float)) and isinstance(maximo, (int, float)) and maximo > 0:
        return total, maximo
    return None, None


def textos_llm(salida: SalidaLLMv3) -> list[str]:
    """Textos de interpretación que revisa el verificador."""
    return [salida.hipotesis_fase, *(f"{f.factor} {f.porque}" for f in salida.factores_ponderados),
            salida.contexto_aplicado, salida.descarte_benigno, *salida.limitaciones,
            salida.justificacion_accion, salida.recomendacion_operador]


def respaldados(alerta: AlertaEntrada, evaluadas: dict, alertas_agrupadas: int, contexto: dict | None) -> tuple[list, list]:
    """(números, IPs) que el LLM recibió y puede citar."""
    numeros = [alertas_agrupadas]
    for e in evaluadas.values():
        numeros += [e["valor"], e["umbral"], e["desviacion"]]
    numeros += [x for x in puntajes(alerta.crudo) if x is not None]
    superados = len(umbrales_superados(alerta))
    numeros += [superados, 5 - superados]
    ips = [alerta.host_origen]
    if contexto:
        numeros += [p["minutos_antes"] for p in contexto["historial_host"] + contexto["actividad_red"]]
        numeros += [v for v in contexto["resumen"].values() if isinstance(v, int) and not isinstance(v, bool)]
        numeros += [len(contexto["historial_host"]), len(contexto["actividad_red"]),
                    len({a["host"] for a in contexto["actividad_red"] if a["veredicto"] == "confirmado"})]
        ips += [a["host"] for a in contexto["actividad_red"]]
    return numeros, ips


def ensamblar(
    alerta: AlertaEntrada, evaluadas: dict, fuente_umbrales: str, salida: SalidaLLMv3, tecnica: tuple[str, bool],
    dictamen: dict, verificacion: dict, traza: dict[str, Any],
) -> dict[str, Any]:
    """Informe completo (validado con schemas/informe.py) listo para guardar como JSON."""
    superados = umbrales_superados(alerta)
    total, maximo = puntajes(alerta.crudo)
    if total is not None:
        solidez = {"valor": round(total / maximo, 2), "formula": f"puntaje_total / puntaje_maximo = {total}/{maximo}"}
    else:
        solidez = {"valor": round(len(superados) / 5, 2),
                   "formula": f"umbrales superados / 5 = {len(superados)}/5 (la alerta no trae puntaje)"}
    discrepancias = [campo for campo, e in evaluadas.items() if e["supera"] != (_CLAVE_RYU[campo] in superados)]
    nombre, tactica = CATALOGO_MITRE[tecnica[0]]
    informe = Informe(
        evidencia_objetiva={
            "host_origen": alerta.host_origen, "dpid_switch": alerta.dpid_switch, "fuente_umbrales": fuente_umbrales,
            "metricas": evaluadas, "M3_smb_anomalo": alerta.metricas.M3_smb_anomalo,
            "M5_flujos_east_west": alerta.metricas.M5_flujos_east_west, "umbrales_superados_ryu": superados,
            "discrepancias": discrepancias, "puntaje_total": total, "puntaje_maximo": maximo,
        },
        mitre={"tecnica_id": tecnica[0], "nombre": nombre, "tactica": tactica, "tecnica_corregida": tecnica[1],
               "hipotesis_fase": salida.hipotesis_fase},
        razonamiento={
            "factores_ponderados": [f.model_dump() for f in salida.factores_ponderados],
            "contexto_aplicado": salida.contexto_aplicado, "descarte_benigno": salida.descarte_benigno,
            "limitaciones": salida.limitaciones, "dictamen_historial": dictamen,
            "confianza_modelo": {"valor": salida.confianza}, "solidez_evidencia": solidez, "verificacion": verificacion,
        },
        trazabilidad_y_accion={**traza, "reversible": traza["accion_propuesta"] in REVERSIBLES},
    )
    return informe.model_dump(mode="json")


def _texto_accion(accion_final: str | None, accion_propuesta: str) -> str:
    simulada = " (simulado)" if accion_final and accion_final.endswith("_simulada") else ""
    final = (accion_final or "").removesuffix("_simulada")
    texto = {"pasar_a_cuarentena": "pasar a cuarentena", "escalar_a_drop": "volver a bloqueo total"}
    if final in texto:
        return f"{texto[final]} por la política{simulada}"
    if final in ("recomendada", "fallida") and accion_propuesta in texto:
        return f"recomendación pendiente: {texto[accion_propuesta]}{simulada}"
    return "se mantiene el bloqueo total"


def linea_accion(traza: dict) -> str:
    """Fase 9a/9b: "Acción ejecutada: …" o "Acción recomendada: … (confianza x)"; informes previos: frases de la 6.8."""
    accion = traza["accion_propuesta"]
    if accion not in escalera.ACCIONES:
        return f"Acción: {_texto_accion(traza['accion_final'], accion)}"
    texto = escalera.texto_accion(accion, traza["confianza_accion"])
    final = traza["accion_final"] or ""
    if final == accion:
        return f"Acción ejecutada: {texto}"
    if final == f"{accion}_simulada":
        return f"Acción ejecutada (simulada): {texto}"
    if final == "fallida":
        motivos = " ".join(traza["decision_politica"]["motivos"])
        aviso = "¡ATENCIÓN! Ryu no confirmó el DROP total: verifique el host" if "Ryu no confirmó el DROP total" in motivos \
            else "el host sigue en DROP total"
        return f"Acción recomendada: {texto} · falló la ejecución; {aviso}"
    return f"Acción recomendada: {texto}"


def _supera(informe: dict) -> str:
    partes = [f"{campo.split('_')[0] if campo != 'M5_count' else 'M5_count'} {_n(e['valor'])}/{_n(e['umbral'])} (×{e['desviacion']:.2f})"
              for campo, e in informe["evidencia_objetiva"]["metricas"].items() if e["supera"]]
    return ", ".join(partes) or "ningún umbral"


def _n(valor: float) -> str:
    return str(int(valor)) if float(valor).is_integer() else str(valor)


def lineas(informe: dict, severidad: str | None) -> list[str]:
    """Una línea por dimensión (correo)."""
    evidencia, mitre, razon, traza = (informe[k] for k in ("evidencia_objetiva", "mitre", "razonamiento", "trazabilidad_y_accion"))
    dictamen = razon["dictamen_historial"]
    detalle_dictamen = f" · dictamen {dictamen['tipo']}" if dictamen["tipo"] else ""
    verificacion = "verificado" if razon["verificacion"]["resultado"] == "verificado" else "posible alucinación"
    # en un respaldo no hubo LLM: no se atribuye un veredicto al modelo
    origen = "sin LLM: respaldo por reglas" if traza["fuente_explicacion"] == "reglas" else f"LLM: {traza['veredicto_llm']}"
    return [
        f"Evidencia: {evidencia['host_origen']} supera {_supera(informe)} · solidez {razon['solidez_evidencia']['valor']:.2f}",
        f"MITRE: {mitre['tecnica_id']} {mitre['nombre']} ({mitre['tactica'].split(' (')[0]}) · {mitre['hipotesis_fase'][:140]}",
        f"Razonamiento: veredicto final {traza['veredicto_final']} ({origen}{detalle_dictamen}) · "
        f"confianza declarada {razon['confianza_modelo']['valor']:.2f} · {verificacion}",
        linea_accion(traza),
    ]


def resumen(informe: dict, severidad: str | None) -> str:
    """Explicación de texto generada por CÓDIGO desde el informe (lista del panel y correo), ≤ 600."""
    evidencia, mitre, razon, traza = (informe[k] for k in ("evidencia_objetiva", "mitre", "razonamiento", "trazabilidad_y_accion"))
    dictamen = razon["dictamen_historial"]
    texto = (
        f"{traza['veredicto_final'].capitalize()} ({severidad or '—'}) en {evidencia['host_origen']}: supera {_supera(informe)}. "
        f"MITRE {mitre['tecnica_id']} {mitre['nombre']}. "
        + (f"Dictamen {dictamen['tipo']}: {dictamen['motivo']}. " if dictamen["tipo"] else "")
        + f"Solidez {razon['solidez_evidencia']['valor']:.2f} · confianza declarada {razon['confianza_modelo']['valor']:.2f} · "
        f"{'verificado' if razon['verificacion']['resultado'] == 'verificado' else 'posible alucinación'}. "
        f"{linea_accion(traza)}."
    )
    return texto[:MAX_RESUMEN]
