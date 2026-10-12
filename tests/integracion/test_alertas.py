"""POST /api/alertas y lectura de incidentes con TestClient (SQLite temporal; sin LLM, Ryu ni Apps Script)."""

import time
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Alerta, Incidente
from app.services import pipeline


@pytest.fixture
def llamadas_pipeline(monkeypatch) -> list[int]:
    """Reemplaza el pipeline para registrar cuántas veces se dispara y con qué incidente."""
    llamadas: list[int] = []
    monkeypatch.setattr(pipeline, "procesar", llamadas.append)
    return llamadas


def _enviar(cliente, cabecera, payload) -> int:
    respuesta = cliente.post("/api/alertas", json=payload, headers=cabecera)
    assert respuesta.status_code == 202
    return respuesta.json()["incidente_id"]


def _pasan_segundos(motor, segundos: int) -> None:
    """Simula el paso del tiempo atrasando la llegada de las alertas guardadas (no el t_recibido del incidente).

    Se atrasan todas para conservar la separación entre ellas; la ventana se mide desde la última.
    """
    with Session(motor) as sesion:
        for alerta in sesion.scalars(select(Alerta)):
            alerta.t_recibido -= timedelta(seconds=segundos)
        sesion.commit()


def test_alerta_valida_responde_202(cliente, bd_temporal, cabecera_agente, payload_c1, llamadas_pipeline):
    payload_c1["campo_desconocido"] = "se guarda en crudo"
    respuesta = cliente.post("/api/alertas", json=payload_c1, headers=cabecera_agente)

    assert respuesta.status_code == 202
    assert respuesta.json() == {"estado": "recibido", "incidente_id": 1, "acciones_ryu": ["mantener_bloqueo"]}
    assert llamadas_pipeline == [1]
    with Session(bd_temporal) as sesion:
        guardado = sesion.scalars(select(Alerta.payload)).one()
    assert guardado["campo_desconocido"] == "se guarda en crudo"
    assert "solicitud_al_agente" not in guardado


@pytest.mark.parametrize("cabecera", [{}, {"X-Agent-Token": "incorrecto"}])
def test_token_invalido_401(cliente, payload_c1, cabecera):
    assert cliente.post("/api/alertas", json=payload_c1, headers=cabecera).status_code == 401


def test_payload_invalido_422(cliente, cabecera_agente, payload_c1):
    payload_c1["host_origen"] = "999.0.0.1"
    assert cliente.post("/api/alertas", json=payload_c1, headers=cabecera_agente).status_code == 422


def test_limite_de_tasa_429(cliente, cabecera_agente, payload_c1, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_POR_MIN", "3")
    get_settings.cache_clear()
    codigos = [cliente.post("/api/alertas", json=payload_c1, headers=cabecera_agente).status_code for _ in range(4)]
    assert codigos == [202, 202, 202, 429]


def test_agrupa_dentro_de_la_ventana(cliente, cabecera_agente, cabecera_operador, payload_c1, llamadas_pipeline):
    primero = _enviar(cliente, cabecera_agente, payload_c1)
    segundo = _enviar(cliente, cabecera_agente, payload_c1)

    assert segundo == primero
    detalle = cliente.get(f"/api/incidentes/{primero}", headers=cabecera_operador).json()
    assert detalle["contador_alertas"] == 2
    assert llamadas_pipeline == [primero]  # la alerta agrupada no vuelve a disparar el pipeline


def test_incidente_nuevo_pasada_la_ventana(cliente, bd_temporal, cabecera_agente, payload_c1, llamadas_pipeline):
    primero = _enviar(cliente, cabecera_agente, payload_c1)
    _pasan_segundos(bd_temporal, 61)
    segundo = _enviar(cliente, cabecera_agente, payload_c1)

    assert segundo != primero
    assert llamadas_pipeline == [primero, segundo]


def test_ventana_deslizante_desde_la_ultima_alerta(cliente, bd_temporal, cabecera_agente, cabecera_operador, payload_c1):
    """3 alertas cada 40 s (80 s en total > ventana de 60) quedan juntas; una 4.ª 61 s después abre otro incidente."""
    ids = [_enviar(cliente, cabecera_agente, payload_c1)]
    for _ in range(2):
        _pasan_segundos(bd_temporal, 40)
        ids.append(_enviar(cliente, cabecera_agente, payload_c1))
    _pasan_segundos(bd_temporal, 61)
    cuarto = _enviar(cliente, cabecera_agente, payload_c1)

    assert ids == [ids[0]] * 3
    assert cuarto != ids[0]
    detalle = cliente.get(f"/api/incidentes/{ids[0]}", headers=cabecera_operador).json()
    assert detalle["contador_alertas"] == 3


def test_incidente_desbloqueado_no_agrupa(cliente, bd_temporal, cabecera_agente, payload_c1):
    primero = _enviar(cliente, cabecera_agente, payload_c1)
    with Session(bd_temporal) as sesion:
        sesion.get(Incidente, primero).t_desbloqueado = datetime.now(timezone.utc)
        sesion.commit()

    assert _enviar(cliente, cabecera_agente, payload_c1) != primero


def test_respuesta_en_menos_de_300_ms(cliente, cabecera_agente, payload_c1):
    inicio = time.perf_counter()
    respuesta = cliente.post("/api/alertas", json=payload_c1, headers=cabecera_agente)
    duracion = time.perf_counter() - inicio

    assert respuesta.status_code == 202
    assert duracion < 0.3


def test_lectura_de_incidentes(cliente, cabecera_agente, cabecera_operador, payload_c1, llamadas_pipeline):
    incidente_id = _enviar(cliente, cabecera_agente, payload_c1)

    lista = cliente.get("/api/incidentes", headers=cabecera_operador)
    assert lista.status_code == 200
    (incidente,) = lista.json()
    assert incidente["id"] == incidente_id
    assert incidente["host_origen"] == "10.0.0.3"
    assert incidente["contador_alertas"] == 1
    assert incidente["fuente_explicacion"] is None
    assert incidente["t_ryu"].startswith("2026-10-10T20:31:05.123") and incidente["t_ryu"].endswith("Z")
    assert incidente["t_recibido"].endswith("Z")

    detalle = cliente.get(f"/api/incidentes/{incidente_id}", headers=cabecera_operador).json()
    # La lista y el detalle traen la última notificación (Fases 3 y 5); solo el detalle trae métricas y umbrales.
    assert detalle.pop("ultima_notificacion") is None and incidente.pop("ultima_notificacion") is None
    assert detalle.pop("metricas")["M1_nuevos_flujos_por_30s"] == 42 and incidente.pop("metricas") is None
    assert detalle.pop("umbrales_superados") == ["M1", "M2", "M3"] and incidente.pop("umbrales_superados") is None
    assert detalle.pop("acciones") == [] and incidente.pop("acciones") is None  # Fase 6.8: solo en el detalle
    assert detalle == incidente
    assert cliente.get("/api/incidentes/999", headers=cabecera_operador).status_code == 404
    assert cliente.get("/api/incidentes").status_code == 401
    assert cliente.get("/api/incidentes", headers={"X-Operador-Token": "incorrecto"}).status_code == 401


def test_auditoria_integra_tras_las_alertas(cliente, cabecera_agente, cabecera_operador, payload_c1):
    _enviar(cliente, cabecera_agente, payload_c1)  # alerta_recibida
    _enviar(cliente, cabecera_agente, payload_c1)  # alerta_agrupada

    respuesta = cliente.get("/api/auditoria/verificar", headers=cabecera_operador)
    assert respuesta.status_code == 200
    # alerta_recibida + alerta_agrupada + verificacion_informe (Fase 6.8b) + explicacion_generada (Fase 2)
    # + notificacion_omitida (Fase 3)
    assert respuesta.json() == {"integra": True, "filas": 5, "primera_rota": None}
