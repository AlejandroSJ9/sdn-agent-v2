"""Agregados del tablero en /api/resumen: veredictos de hoy, tiempo medio y franjas por hora (sin BD ni red)."""

from datetime import datetime, timedelta, timezone

from app.services.alertas import agregados_tablero

HOY = datetime(2026, 10, 11, tzinfo=timezone.utc)
INICIO_24H = datetime(2026, 10, 10, 15, tzinfo=timezone.utc)  # ahora = 2026-10-11T14:xx


def _fila(recibido: datetime, veredicto=None, ryu=None, explicado=None) -> dict:
    return {"t_recibido": recibido, "t_ryu": ryu, "t_explicado": explicado, "veredicto": veredicto}


def test_sin_incidentes():
    r = agregados_tablero([], HOY, INICIO_24H)
    assert r["veredictos_hoy"] == {"confirmado": 0, "indeterminado": 0, "falso_positivo": 0}
    assert r["tiempo_medio_explicacion_ms"] is None
    assert len(r["incidentes_por_hora"]) == 24
    assert r["incidentes_por_hora"][0] == {"hora": "2026-10-10T15:00:00Z", "cantidad": 0}
    assert r["incidentes_por_hora"][-1]["hora"] == "2026-10-11T14:00:00Z"


def test_veredictos_y_tiempo_medio_solo_cuentan_hoy():
    ayer = HOY - timedelta(hours=2)
    filas = [
        _fila(ayer, "confirmado", ryu=ayer, explicado=ayer + timedelta(seconds=9)),  # fuera de "hoy"
        _fila(HOY + timedelta(hours=1), "confirmado", ryu=HOY, explicado=HOY + timedelta(seconds=2)),
        _fila(HOY + timedelta(hours=2), "falso_positivo", ryu=HOY, explicado=HOY + timedelta(seconds=4)),
        _fila(HOY + timedelta(hours=3), None),  # sin explicar: no entra en el tiempo medio
    ]
    r = agregados_tablero(filas, HOY, INICIO_24H)
    assert r["veredictos_hoy"] == {"confirmado": 1, "indeterminado": 0, "falso_positivo": 1}
    assert r["tiempo_medio_explicacion_ms"] == 3000


def test_franjas_por_hora():
    filas = [
        _fila(INICIO_24H - timedelta(minutes=1)),  # antes de la ventana
        _fila(INICIO_24H),
        _fila(INICIO_24H + timedelta(minutes=59)),
        _fila(INICIO_24H + timedelta(hours=23, minutes=30)),
        _fila(INICIO_24H + timedelta(hours=30)),  # reloj adelantado: cae en la última franja
    ]
    cantidades = [h["cantidad"] for h in agregados_tablero(filas, HOY, INICIO_24H)["incidentes_por_hora"]]
    assert cantidades[0] == 2 and cantidades[-1] == 2 and sum(cantidades) == 4
