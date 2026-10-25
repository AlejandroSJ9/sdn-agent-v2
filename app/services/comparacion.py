"""Comparación de corridas del laboratorio (Fase 6.7): {LLM, reglas} × {sin contexto, con contexto}.

Todas las corridas se comparan sobre el MISMO conjunto de casos: los del conjunto común (decidible_con_contexto,
14 casos) que estén en todas las corridas elegidas. Los demás se listan como excluidos.
"""

from typing import Any

from app.db import repository_laboratorio
from app.services.resultados_lab import _a_csv, _escenarios, _exactitud, metricas_xai

MIN_CORRIDAS, MAX_CORRIDAS = 2, 4
COLUMNAS_CSV = ["caso_id", "clase", "conjunto", "veredicto_esperado", "aceptables", "corrida_id", "etiqueta", "variante",
                "version_prompt", "usar_llm", "veredictos", "aciertos", "de", "veredictos_llm", "dictamenes", "verificaciones"]

# Registradas ANTES de correr las variantes; viajan junto a los resultados en la exportación JSON.
HIPOTESIS = {
    "H1": "Sin contexto, LLM y reglas obtienen la misma exactitud (ya observado en la Fase 6).",
    "H2": "Con contexto, tanto el LLM como las reglas mejoran en los casos con historial (LG-05, DU-01, AT-09, AT-10).",
    "H3": "Con contexto, el LLM supera a las reglas en los casos donde el contexto tiene matices que una regla fija no capta.",
    "nota": "Se reportarán todos los resultados, confirmen o no las hipótesis.",
    "limitacion": (
        "Limitación declarada: los escenarios sintéticos usan la regla de la v1 (≥ 3 métricas superadas); RYU_IA v2 "
        "decide por puntaje ponderado y puede alertar con 2 métricas; eso se evalúa en la Fase 7."
    ),
}


class ComparacionInvalida(ValueError):
    """Cantidad de corridas fuera de 2–4, ids repetidos o corridas inexistentes (422)."""


def _etiqueta(corrida: dict) -> str:
    """Fase D3: incluye la versión exacta del prompt (v3.0, v3.1, v4.0 o v4.1) para medir v3 contra v4."""
    fuente = "LLM" if corrida["usar_llm"] else "reglas"
    version = (corrida.get("version_prompt") or "—").split("-")[0]
    return f"#{corrida['id']} · {fuente} · {corrida['variante'].replace('_', ' ')} · {version}"


def _acierta(celda: dict) -> bool:
    """Con repeticiones, un caso cuenta como acertado si acierta en la mayoría."""
    return celda["de"] > 0 and 2 * celda["aciertos"] > celda["de"]


def _buscar(corridas: list[dict], usar_llm: bool) -> dict | None:
    return next((c for c in corridas if c["usar_llm"] is usar_llm and c["variante"] == "con_contexto"), None)


def comparar(corrida_ids: list[int]) -> dict[str, Any]:
    if not MIN_CORRIDAS <= len(corrida_ids) <= MAX_CORRIDAS or len(set(corrida_ids)) != len(corrida_ids):
        raise ComparacionInvalida(f"Elige entre {MIN_CORRIDAS} y {MAX_CORRIDAS} corridas distintas.")
    corridas = [repository_laboratorio.obtener_corrida(i) for i in corrida_ids]
    faltan = [i for i, c in zip(corrida_ids, corridas) if c is None]
    if faltan:
        raise ComparacionInvalida(f"Corridas inexistentes: {faltan}")

    escenarios = _escenarios()
    comun = [cid for cid, caso in escenarios.items() if caso["decidible_con_contexto"]]
    casos = [cid for cid in comun if all(cid in c["casos"] for c in corridas)]
    ejecutados = {cid for c in corridas for cid in c["casos"]}
    resultados = {c["id"]: repository_laboratorio.resultados_de(c["id"]) for c in corridas}

    tabla, filas_por_corrida = [], {c["id"]: [] for c in corridas}
    for caso_id in casos:
        caso = escenarios[caso_id]
        celdas = {}
        for c in corridas:
            filas = [f for f in resultados[c["id"]] if f["caso_id"] == caso_id]
            filas_por_corrida[c["id"]] += filas
            celdas[str(c["id"])] = {"veredictos": [f["veredicto_obtenido"] for f in filas],
                                    "aciertos": sum(1 for f in filas if f["acierto"]), "de": len(filas),
                                    # Fase 6.8a+b: acierto sobre el veredicto FINAL; además lo que dijo el LLM
                                    "veredictos_llm": [f["veredicto_llm"] for f in filas],
                                    "dictamenes": [f["dictamen_historial"] for f in filas],
                                    "verificaciones": [f["verificacion"] for f in filas]}
        tabla.append({"caso_id": caso_id, "clase": caso["clase"], "conjunto": caso["conjunto"],
                      "veredicto_esperado": caso["esperado"]["veredicto"],
                      "aceptables": caso["esperado"]["aceptables"], "celdas": celdas})

    def resumen(filas: list[dict]) -> dict[str, Any]:
        por = lambda campo: {  # noqa: E731
            valor: _exactitud([f for f in filas if escenarios[f["caso_id"]][campo] == valor])
            for valor in sorted({escenarios[f["caso_id"]][campo] for f in filas})
        }
        return {"global": _exactitud(filas), "por_clase": por("clase"), "por_conjunto": por("conjunto"),
                **metricas_xai(filas, escenarios)}

    llm, reglas = _buscar(corridas, True), _buscar(corridas, False)
    destacados = {"llm_con_contexto_acierta_reglas_con_contexto_falla": [],
                  "reglas_con_contexto_acierta_llm_con_contexto_falla": []}
    if llm and reglas:
        for fila in tabla:
            a, b = _acierta(fila["celdas"][str(llm["id"])]), _acierta(fila["celdas"][str(reglas["id"])])
            if a and not b:
                destacados["llm_con_contexto_acierta_reglas_con_contexto_falla"].append(fila["caso_id"])
            if b and not a:
                destacados["reglas_con_contexto_acierta_llm_con_contexto_falla"].append(fila["caso_id"])

    return {
        "corridas": [{"id": c["id"], "etiqueta": _etiqueta(c), "usar_llm": c["usar_llm"], "variante": c["variante"],
                      "version_prompt": c["version_prompt"], "repeticiones": c["repeticiones"], "estado": c["estado"]}
                     for c in corridas],
        "casos_comparados": casos,
        "casos_excluidos": sorted(ejecutados - set(casos)),
        "tabla": tabla,
        "resumen": {str(c["id"]): resumen(filas_por_corrida[c["id"]]) for c in corridas},
        "destacados": destacados if llm and reglas else None,
    }


def exportar_json(corrida_ids: list[int]) -> dict[str, Any]:
    return {"hipotesis": HIPOTESIS, **comparar(corrida_ids)}


def exportar_csv(corrida_ids: list[int]) -> str:
    datos = comparar(corrida_ids)
    corridas = {str(c["id"]): c for c in datos["corridas"]}
    filas = [
        {**{k: fila[k] for k in ("caso_id", "clase", "conjunto", "veredicto_esperado")},
         "aceptables": "|".join(fila["aceptables"]), "corrida_id": cid, "etiqueta": corridas[cid]["etiqueta"],
         "variante": corridas[cid]["variante"], "version_prompt": corridas[cid]["version_prompt"],
         "usar_llm": corridas[cid]["usar_llm"], "veredictos": "|".join(v or "—" for v in celda["veredictos"]),
         "aciertos": celda["aciertos"], "de": celda["de"],
         "veredictos_llm": "|".join(v or "—" for v in celda["veredictos_llm"]),
         "dictamenes": "|".join(v or "—" for v in celda["dictamenes"]),
         "verificaciones": "|".join(v or "—" for v in celda["verificaciones"])}
        for fila in datos["tabla"] for cid, celda in fila["celdas"].items()
    ]
    return _a_csv(COLUMNAS_CSV, filas)
