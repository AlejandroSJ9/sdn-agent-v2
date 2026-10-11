"""Lecturas del panel (/api/auditoria, /api/resumen, lista y detalle) y archivos del frontend."""

from datetime import datetime, timezone

import httpx
import pytest
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Incidente
from app.services import notificacion


def _alerta(cliente, cabecera, payload, host: str) -> int:
    payload = {**payload, "host_origen": host}
    respuesta = cliente.post("/api/alertas", json=payload, headers=cabecera)
    assert respuesta.status_code == 202
    return respuesta.json()["incidente_id"]


def test_auditoria_reciente_orden_y_limite(cliente, cabecera_agente, cabecera_operador, payload_c1):
    _alerta(cliente, cabecera_agente, payload_c1, "10.0.0.3")
    _alerta(cliente, cabecera_agente, payload_c1, "10.0.0.4")

    filas = cliente.get("/api/auditoria", headers=cabecera_operador).json()
    ids = [f["id"] for f in filas]
    assert ids == sorted(ids, reverse=True) and len(filas) == 6  # 2 × (recibida + explicación + omitida)
    assert set(filas[0]) == {"id", "ts", "evento", "datos"}
    assert filas[-1]["evento"] == "alerta_recibida" and filas[-1]["datos"]["host_origen"] == "10.0.0.3"
    assert len(cliente.get("/api/auditoria?limite=2", headers=cabecera_operador).json()) == 2
    assert cliente.get("/api/auditoria?limite=0", headers=cabecera_operador).status_code == 422
    assert cliente.get("/api/auditoria?limite=201", headers=cabecera_operador).status_code == 422


def test_resumen(cliente, bd_temporal, cabecera_agente, cabecera_operador, payload_c1, monkeypatch):
    vacio = cliente.get("/api/resumen", headers=cabecera_operador).json()
    assert vacio == {"incidentes_abiertos": 0, "incidentes_hoy": 0, "notificaciones_fallidas_hoy": 0, "ultimo_incidente_id": None}

    monkeypatch.setenv("APPSCRIPT_URL", "https://script.google.com/macros/s/FALSO/exec")
    get_settings.cache_clear()
    falla = httpx.MockTransport(lambda peticion: httpx.Response(500))
    monkeypatch.setattr(notificacion, "transporte_appscript", lambda: falla)
    primero = _alerta(cliente, cabecera_agente, payload_c1, "10.0.0.3")
    segundo = _alerta(cliente, cabecera_agente, payload_c1, "10.0.0.4")
    with Session(bd_temporal) as sesion:
        sesion.get(Incidente, primero).t_desbloqueado = datetime.now(timezone.utc)
        sesion.commit()

    assert cliente.get("/api/resumen", headers=cabecera_operador).json() == {
        "incidentes_abiertos": 1,
        "incidentes_hoy": 2,
        "notificaciones_fallidas_hoy": 2,
        "ultimo_incidente_id": segundo,
    }


@pytest.mark.parametrize("ruta", ["/api/auditoria", "/api/resumen"])
def test_lecturas_del_panel_exigen_token(cliente, ruta):
    assert cliente.get(ruta).status_code == 401
    assert cliente.get(ruta, headers={"X-Operador-Token": "incorrecto"}).status_code == 401


def test_lista_con_notificacion_y_detalle_con_metricas(cliente, cabecera_agente, cabecera_operador, payload_c1):
    payload_c1["campo_desconocido"] = "no debe salir"
    incidente_id = _alerta(cliente, cabecera_agente, payload_c1, "10.0.0.3")
    _alerta(cliente, cabecera_agente, payload_c1, "10.0.0.3")  # agrupada

    (item,) = cliente.get("/api/incidentes", headers=cabecera_operador).json()
    assert item["ultima_notificacion"]["estado"] == "omitida"
    assert item["metricas"] is None and item["umbrales_superados"] is None

    detalle = cliente.get(f"/api/incidentes/{incidente_id}", headers=cabecera_operador).json()
    assert detalle["contador_alertas"] == 2
    assert detalle["metricas"]["M2_destinos_unicos_por_30s"] == 9
    assert detalle["umbrales_superados"] == ["M1", "M2", "M3"]
    assert "no debe salir" not in str(detalle) and "solicitud_al_agente" not in str(detalle)


def test_pagina_principal_y_estaticos(cliente):
    pagina = cliente.get("/")
    assert pagina.status_code == 200 and pagina.headers["content-type"].startswith("text/html")
    assert '<script type="module" src="/static/main.js">' in pagina.text

    for archivo in ("main.js", "dom.js", "api.js", "ws.js", "incidentes.js"):
        respuesta = cliente.get(f"/static/{archivo}")
        assert respuesta.status_code == 200 and "javascript" in respuesta.headers["content-type"]
    assert cliente.get("/static/styles.css").status_code == 200
    assert cliente.get("/static/no-existe.js").status_code == 404
