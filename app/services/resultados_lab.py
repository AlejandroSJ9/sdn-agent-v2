"""Métricas y exportaciones del laboratorio (resumen de corrida, CSV de corrida y de incidentes)."""

import csv
import io
import math
import statistics
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from app.db import repository, repository_laboratorio

# Motivos de respaldo que implican que SÍ se llamó al LLM (consumieron cupo).
MOTIVOS_TRAS_LLAMAR = {"timeout", "error_llm", "json_invalido", "schema_invalido"}
COLUMNAS_CORRIDA = [
    "caso_id", "repeticion", "conjunto", "clase", "decidible", "veredicto_esperado", "aceptables",
    "veredicto_obtenido", "acierto", "fuente", "motivo_respaldo", "latencia_explicacion_ms", "incidente_id",
    "veredicto_llm", "dictamen_historial", "verificacion",  # Fase 6.8a+b
]
COLUMNAS_INCIDENTES = [
    "id", "origen", "corrida_id", "caso_id", "repeticion", "host_origen", "veredicto", "severidad", "confianza",
    "tecnica_mitre", "fuente_explicacion", "motivo_respaldo", "contador_alertas",
    "t_ryu", "t_recibido", "t_explicado", "t_notificado", "t_desbloqueado",
]


def _escenarios() -> dict[str, dict]:
    from app.services.laboratorio import cargar_escenarios  # import tardío: evita el ciclo laboratorio ↔ resultados

    return {caso["id"]: caso for caso in cargar_escenarios()}


def _exactitud(filas: list[dict]) -> dict[str, Any]:
    aciertos = sum(1 for f in filas if f["acierto"])
    return {"aciertos": aciertos, "total": len(filas), "exactitud": round(aciertos / len(filas), 4) if filas else None}


def metricas_xai(filas: list[dict], casos: dict[str, dict]) -> dict[str, Any]:
    """Fase 6.8a+b: el acierto es sobre el veredicto FINAL; esto añade lo que dijo el LLM, los dictámenes
    y los informes con posible alucinación (sobre las filas recibidas).

    Un respaldo por reglas cuenta como "sin veredicto del LLM", nunca como acierto o fallo del LLM: se filtra
    por fuente (también corrige filas antiguas que guardaban el veredicto de las reglas como si fuera del LLM).
    """
    con_llm = [f for f in filas if f["fuente"] == "llm"]
    llm = [{"acierto": f["veredicto_llm"] in casos[f["caso_id"]]["esperado"]["aceptables"]} for f in con_llm]
    dictamenes = Counter(f["dictamen_historial"] for f in filas if f["dictamen_historial"])
    return {
        "exactitud_veredicto_llm": _exactitud(llm),
        "sin_veredicto_llm": len(filas) - len(con_llm),
        "cobertura_llm": {"llm": len(con_llm), "total": len(filas),
                          "porcentaje": round(100 * len(con_llm) / len(filas), 1) if filas else None},
        "dictamenes": {"agravante": dictamenes["agravante"], "atenuante": dictamenes["atenuante"]},
        "posible_alucinacion": sum(1 for f in filas if f["verificacion"] == "posible_alucinacion"),
    }


def _latencias(valores: list[int]) -> dict[str, Any]:
    if not valores:
        return {"n": 0, "media": None, "mediana": None, "p95": None}
    ordenados = sorted(valores)
    return {
        "n": len(ordenados),
        "media": round(statistics.mean(ordenados), 1),
        "mediana": statistics.median(ordenados),
        "p95": ordenados[math.ceil(0.95 * len(ordenados)) - 1],  # rango más cercano
    }


def resumen_corrida(corrida_id: int) -> dict[str, Any]:
    corrida = repository_laboratorio.obtener_corrida(corrida_id)
    filas = repository_laboratorio.resultados_de(corrida_id)
    casos = _escenarios()
    decidibles = [f for f in filas if f["decidible"]]

    matriz: dict[str, Counter] = {}
    for f in decidibles:
        matriz.setdefault(f["veredicto_esperado"], Counter())[f["veredicto_obtenido"] or "sin_veredicto"] += 1

    estabilidad = None
    if corrida["repeticiones"] > 1:
        estabilidad = {}
        for caso_id in corrida["casos"]:
            veredictos = Counter(f["veredicto_obtenido"] for f in filas if f["caso_id"] == caso_id)
            if veredictos:
                mas_comun, coinciden = veredictos.most_common(1)[0]
                estabilidad[caso_id] = {"veredicto_mas_comun": mas_comun, "coinciden": coinciden, "de": sum(veredictos.values())}

    fuentes = Counter(f["fuente"] for f in filas)
    llamadas = sum(1 for f in filas if f["fuente"] == "llm" or f["motivo_respaldo"] in MOTIVOS_TRAS_LLAMAR)
    return {
        "corrida_id": corrida_id,
        "usar_llm": corrida["usar_llm"],
        "repeticiones": corrida["repeticiones"],
        "casos_ejecutados": len(filas),
        "exactitud_decidibles": {
            "global": _exactitud(decidibles),
            "desarrollo": _exactitud([f for f in decidibles if casos[f["caso_id"]]["conjunto"] == "desarrollo"]),
            "validacion": _exactitud([f for f in decidibles if casos[f["caso_id"]]["conjunto"] == "validacion"]),
        },
        "aciertos_por_clase": {
            clase: {
                "decidibles": _exactitud([f for f in decidibles if casos[f["caso_id"]]["clase"] == clase]),
                "todos": _exactitud([f for f in filas if casos[f["caso_id"]]["clase"] == clase]),
            }
            for clase in sorted({casos[f["caso_id"]]["clase"] for f in filas})
        },
        "matriz_esperado_vs_obtenido": {esperado: dict(c) for esperado, c in matriz.items()},
        "no_decidibles": [
            {
                "caso_id": f["caso_id"], "repeticion": f["repeticion"], "esperado": f["veredicto_esperado"],
                "obtenido": f["veredicto_obtenido"], "acierto": f["acierto"], "detalle": f["detalle"],
                "motivo_no_decidible": casos[f["caso_id"]]["motivo_no_decidible"],
            }
            for f in filas if not f["decidible"]
        ],
        "fuente_explicacion": {
            fuente: round(100 * n / len(filas), 1) for fuente, n in fuentes.items() if fuente
        } if filas else {},
        "latencia_explicacion_ms": _latencias([f["latencia_explicacion_ms"] for f in filas if f["latencia_explicacion_ms"] is not None]),
        "estabilidad": estabilidad,
        "cupo_consumido": {"llamadas_llm": llamadas, "costo_estimado": corrida["costo_estimado"]},
        # decidibles: misma base que la exactitud del veredicto final; alucinaciones y dictámenes: todos los casos
        # cobertura del LLM: sobre el total de la corrida
        "xai": {**metricas_xai(filas, casos), **{k: v for k, v in metricas_xai(decidibles, casos).items()
                                                 if k in ("exactitud_veredicto_llm", "sin_veredicto_llm")}},
    }


def _celda(valor: Any) -> Any:
    """Evita inyección de fórmulas al abrir el CSV en una hoja de cálculo."""
    if isinstance(valor, datetime):
        valor = valor.replace(tzinfo=timezone.utc) if valor.tzinfo is None else valor.astimezone(timezone.utc)
        return valor.isoformat().replace("+00:00", "Z")
    if isinstance(valor, str) and valor[:1] in ("=", "+", "-", "@"):
        return "'" + valor
    return valor


def _a_csv(columnas: list[str], filas: list[dict]) -> str:
    salida = io.StringIO()
    escritor = csv.DictWriter(salida, fieldnames=columnas, extrasaction="ignore")
    escritor.writeheader()
    for fila in filas:
        escritor.writerow({c: _celda(fila.get(c)) for c in columnas})
    return salida.getvalue()


def csv_corrida(corrida_id: int) -> str:
    casos = _escenarios()
    filas = [
        {
            **f,
            "conjunto": casos[f["caso_id"]]["conjunto"],
            "clase": casos[f["caso_id"]]["clase"],
            "aceptables": "|".join(casos[f["caso_id"]]["esperado"]["aceptables"]),
        }
        for f in repository_laboratorio.resultados_de(corrida_id)
    ]
    return _a_csv(COLUMNAS_CORRIDA, filas)


def csv_incidentes(origen: str) -> str:
    """Sin textos del LLM (explicación, evidencias): solo campos acotados."""
    return _a_csv(COLUMNAS_INCIDENTES, repository.listar_incidentes(1_000_000, 0, origen))
