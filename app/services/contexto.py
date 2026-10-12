"""Contexto dinámico del host (C6), construido SOLO desde la base de datos del agente.

Es evidencia, no absolución: los veredictos previos del agente van marcados "no_verificado" y solo las
clasificaciones del operador cuentan como verificadas. El contexto solo se LEE: nunca bloquea, desbloquea
ni cambia el estado de un incidente (tests/unit/test_unico_camino_desbloqueo.py lo comprueba).

Ámbito: los reales solo ven reales; un incidente del laboratorio solo ve los de su misma corrida, caso y
repetición (incluidos los sembrados), para que un caso no contamine a otro.
"""

from datetime import datetime, timedelta
from typing import Any

from app.db import repository_contexto
from app.services import configuracion
from app.services.prompt import UMBRALES_VALIDOS

MAX_HISTORIAL = 10
MAX_ACTIVIDAD = 10
MAX_MOTIVO = 200
MINUTOS_CONFIRMADO_RECIENTE = 60


def _minutos_antes(referencia: datetime, momento: datetime) -> int:
    return max(0, int((referencia - momento).total_seconds() // 60))


def _umbrales(fila: dict) -> list[str]:
    return [u for u in UMBRALES_VALIDOS if u in fila["umbrales_superados"]]


def _previo(referencia: datetime, fila: dict) -> dict[str, Any]:
    previo = {
        "minutos_antes": _minutos_antes(referencia, fila["t_recibido"]),
        "veredicto_agente": fila["veredicto"],
        "estado_veredicto": "no_verificado",
        "umbrales_superados": _umbrales(fila),
    }
    if fila["clasificacion_operador"]:
        previo["clasificacion_operador"] = fila["clasificacion_operador"]
    if fila["motivo_operador"]:
        previo["motivo_operador"] = fila["motivo_operador"][:MAX_MOTIVO]
    return previo


def contexto_para(incidente_id: int) -> dict[str, Any] | None:
    """{"historial_host", "resumen", "actividad_red"} del incidente, o None si no existe."""
    ref = repository_contexto.referencia(incidente_id)
    if ref is None:
        return None
    ventanas = configuracion.contexto()
    t_ref, host, ambito = ref["t_recibido"], ref["host"], ref["ambito"]

    del_host = repository_contexto.incidentes_en_ventana(
        ambito, t_ref - timedelta(days=ventanas["historial_dias"]), t_ref, incidente_id, host=host
    )
    otros = repository_contexto.incidentes_en_ventana(
        ambito, t_ref - timedelta(minutes=ventanas["campana_minutos"]), t_ref, incidente_id, otros_que=host,
        limite=MAX_ACTIVIDAD,
    )
    reciente = t_ref - timedelta(minutes=MINUTOS_CONFIRMADO_RECIENTE)
    return {
        "historial_host": [_previo(t_ref, fila) for fila in del_host[:MAX_HISTORIAL]],
        "resumen": {
            "total_previos": len(del_host),
            "falsos_positivos_operador": sum(f["clasificacion_operador"] == "falso_positivo" for f in del_host),
            "amenazas_contenidas_operador": sum(f["clasificacion_operador"] == "amenaza_contenida" for f in del_host),
            # La clasificación del operador prevalece: un "confirmado" del agente que el operador clasificó
            # después como falso_positivo NO cuenta como confirmado reciente (C6: solo eso está verificado).
            "confirmado_ultimos_60_min": any(
                f["veredicto"] == "confirmado" and f["clasificacion_operador"] != "falso_positivo"
                and f["t_recibido"] >= reciente for f in del_host
            ),
        },
        "actividad_red": [
            {"host": f["host_origen"], "minutos_antes": _minutos_antes(t_ref, f["t_recibido"]),
             "veredicto": f["veredicto"], "umbrales_superados": _umbrales(f),
             **({"clasificacion_operador": f["clasificacion_operador"]} if f["clasificacion_operador"] else {})}
            for f in otros
        ],
    }
