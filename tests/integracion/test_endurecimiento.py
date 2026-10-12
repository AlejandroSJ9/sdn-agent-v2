"""Fase 6.5: PRAGMA de SQLite, fechas UTC, triggers y HMAC de auditoría, secretos al arrancar,
recuperación al arrancar y registro del 429."""

import hashlib
import hmac
import logging
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import repository, repository_laboratorio
from app.db.models import Alerta, Auditoria, Corrida, EjecucionPytest, Incidente
from app.main import app
from app.schemas.laboratorio import AmbitoPrueba
from app.services import auditoria, recuperacion
from conftest import CLAVE_AUDITORIA

# ---- 5. PRAGMA ----


def test_foreign_keys_y_wal_activos(bd_temporal):
    with bd_temporal.connect() as conexion:
        assert conexion.execute(text("PRAGMA foreign_keys")).scalar() == 1
        assert conexion.execute(text("PRAGMA journal_mode")).scalar() == "wal"
    with pytest.raises(DBAPIError), Session(bd_temporal) as sesion:
        sesion.add(Alerta(incidente_id=999, payload={}, t_recibido=datetime.now(timezone.utc)))
        sesion.commit()


# ---- 6. Fechas UTC "aware" ----


def test_fechas_leidas_de_la_bd_son_utc_y_se_pueden_restar(cliente, bd_temporal, cabecera_agente, payload_c1):
    incidente_id = cliente.post("/api/alertas", json=payload_c1, headers=cabecera_agente).json()["incidente_id"]
    with Session(bd_temporal) as sesion:
        incidente = sesion.get(Incidente, incidente_id)
    assert incidente.t_ryu.tzinfo == timezone.utc and incidente.t_explicado.tzinfo == timezone.utc
    assert isinstance(incidente.t_explicado - incidente.t_ryu, timedelta)
    assert incidente.t_ryu == datetime(2026, 10, 10, 20, 31, 5, 123000, tzinfo=timezone.utc)


def test_fecha_con_otra_zona_se_guarda_en_utc(bd_temporal):
    hora_local = datetime(2026, 10, 10, 15, 0, tzinfo=timezone(timedelta(hours=-5)))
    incidente_id = repository.crear_incidente("10.0.0.9", None, None, hora_local, hora_local, {})
    with Session(bd_temporal) as sesion:
        assert sesion.get(Incidente, incidente_id).t_ryu == datetime(2026, 10, 10, 20, 0, tzinfo=timezone.utc)


# ---- 7 y 8. Triggers y HMAC ----


def test_update_y_delete_directos_en_auditoria_fallan(bd_temporal):
    auditoria.registrar("evento", {"n": 1})
    for sentencia in ("UPDATE auditoria SET evento = 'otro'", "DELETE FROM auditoria"):
        with pytest.raises(DBAPIError, match="solo insercion"), bd_temporal.begin() as conexion:
            conexion.execute(text(sentencia))
    assert auditoria.verificar_cadena() == {"integra": True, "filas": 1, "primera_rota": None}


def _rehacer_cadena_desde(motor, fila_id: int, firmar) -> None:
    """Simula a un atacante con acceso a la BD: quita los triggers, altera una fila y recalcula la cadena."""
    with Session(motor) as sesion:
        sesion.execute(text("DROP TRIGGER auditoria_sin_update"))
        filas = sesion.scalars(select(Auditoria).order_by(Auditoria.id)).all()
        anterior = "0" * 64
        for fila in filas:
            if fila.id == fila_id:
                fila.datos = {"n": 99}
            if fila.id >= fila_id:
                fila.hash_previo = anterior
                contenido = anterior + fila.evento + auditoria._json_canonico(fila.datos) + auditoria._iso_utc(fila.ts)
                fila.hash = firmar(contenido.encode("utf-8"))
            anterior = fila.hash
        sesion.commit()


@pytest.mark.parametrize(
    "firmar",
    [
        lambda contenido: hashlib.sha256(contenido).hexdigest(),  # sha256 sin clave (la fórmula de antes)
        lambda contenido: hmac.new(b"otra-clave", contenido, hashlib.sha256).hexdigest(),
    ],
    ids=["sha256_sin_clave", "hmac_con_otra_clave"],
)
def test_cadena_recalculada_sin_la_clave_se_detecta(bd_temporal, firmar):
    for n in range(3):
        auditoria.registrar(f"evento_{n}", {"n": n})
    _rehacer_cadena_desde(bd_temporal, 2, firmar)
    assert auditoria.verificar_cadena() == {"integra": False, "filas": 3, "primera_rota": 2}


def test_solo_con_la_clave_se_puede_rehacer_la_cadena(bd_temporal):
    """Contraprueba: con la clave correcta la alteración pasaría; por eso la clave es el secreto que protege."""
    for n in range(3):
        auditoria.registrar(f"evento_{n}", {"n": n})
    _rehacer_cadena_desde(bd_temporal, 2, lambda c: hmac.new(CLAVE_AUDITORIA.encode(), c, hashlib.sha256).hexdigest())
    assert auditoria.verificar_cadena()["integra"] is True


# ---- 9. Secretos obligatorios al arrancar ----


@pytest.mark.parametrize("secreto", ["AGENT_TOKEN", "OPERADOR_TOKEN", "AUDITORIA_CLAVE", "LLM_MODELO"])
@pytest.mark.parametrize("valor", ["", "   "])
def test_sin_secretos_el_agente_no_arranca(bd_temporal, monkeypatch, secreto, valor):
    monkeypatch.setenv(secreto, valor)
    get_settings.cache_clear()
    with pytest.raises(RuntimeError, match=f"Faltan en .env.*{secreto}"):
        with TestClient(app):
            pass


# ---- 10. Recuperación al arrancar ----


def test_recuperacion_reprocesa_reales_e_interrumpe_lo_que_seguia_en_curso(bd_temporal):
    ahora = datetime.now(timezone.utc)
    real = repository.crear_incidente("10.0.0.3", None, None, ahora, ahora, {
        "timestamp": ahora.isoformat(), "host_origen": "10.0.0.3", "umbrales_superados": ["M1", "M2"],
        "metricas": {"M1_nuevos_flujos_por_30s": 30, "M2_destinos_unicos_por_30s": 20, "M3_smb_anomalo": False,
                     "M4_packet_in_por_min": 10.0, "M5_flujos_east_west": False, "M5_count": 0},
    })
    corrida = repository_laboratorio.crear_corrida(["AT-01"], None, False, False, 1, 0)
    prueba = repository.crear_incidente("10.0.10.3", None, None, ahora, ahora, {}, ambito=AmbitoPrueba(corrida, "AT-01", 1))
    pytest_id = repository_laboratorio.crear_ejecucion_pytest("unit")

    resultado = recuperacion.recuperar_al_arrancar(programar=lambda funcion, *args: funcion(*args))

    assert resultado == {"incidentes_reprocesados": [real], "corridas_interrumpidas": [corrida], "pytest_interrumpidas": [pytest_id],
                         "recomendaciones_expiradas": []}  # Fase 6.8
    with Session(bd_temporal) as sesion:
        assert sesion.get(Incidente, real).fuente_explicacion == "reglas"
        assert sesion.get(Incidente, prueba).t_explicado is None  # los de prueba no se reprocesan
        assert sesion.get(Corrida, corrida).estado == "interrumpida"
        assert sesion.get(EjecucionPytest, pytest_id).estado == "interrumpida"
        auditado = sesion.scalars(select(Auditoria).where(Auditoria.evento == "recuperacion_al_arrancar")).one()
    assert auditado.datos == resultado

    # Una segunda vez no hay nada que recuperar: no se audita de nuevo
    assert not any(recuperacion.recuperar_al_arrancar(programar=lambda f, *a: f(*a)).values())
    with Session(bd_temporal) as sesion:
        assert len(sesion.scalars(select(Auditoria).where(Auditoria.evento == "recuperacion_al_arrancar")).all()) == 1


def test_el_arranque_invoca_la_recuperacion(bd_temporal, monkeypatch):
    llamadas = []
    monkeypatch.setattr(recuperacion, "recuperar_al_arrancar", lambda: llamadas.append(True))
    with TestClient(app):
        pass
    assert llamadas == [True]


# ---- 11. 429 en el log, no en la BD ----


def test_429_registra_advertencia_con_la_ip_sin_escribir_en_la_bd(
    cliente, bd_temporal, cabecera_agente, payload_c1, monkeypatch, caplog
):
    monkeypatch.setenv("RATE_LIMIT_POR_MIN", "1")
    get_settings.cache_clear()
    cliente.post("/api/alertas", json=payload_c1, headers=cabecera_agente)
    with Session(bd_temporal) as sesion:
        filas_antes = len(sesion.scalars(select(Auditoria)).all())

    with caplog.at_level(logging.WARNING, logger="app.api.routes_alertas"):
        assert cliente.post("/api/alertas", json=payload_c1, headers=cabecera_agente).status_code == 429
    assert any("429" in r.getMessage() and "testclient" in r.getMessage() for r in caplog.records)
    with Session(bd_temporal) as sesion:
        assert len(sesion.scalars(select(Auditoria)).all()) == filas_antes


# ---- 2. Error y código HTTP de la última notificación en el detalle ----


def test_detalle_trae_error_y_codigo_http_de_la_notificacion(
    cliente, cabecera_agente, cabecera_operador, payload_c1, monkeypatch
):
    import httpx

    from app.services import notificacion

    monkeypatch.setenv("APPSCRIPT_URL", "https://script.google.com/macros/s/FALSO/exec")
    get_settings.cache_clear()
    monkeypatch.setattr(notificacion, "transporte_appscript", lambda: httpx.MockTransport(lambda p: httpx.Response(403)))
    incidente_id = cliente.post("/api/alertas", json=payload_c1, headers=cabecera_agente).json()["incidente_id"]

    for ruta in (f"/api/incidentes/{incidente_id}", "/api/incidentes"):
        datos = cliente.get(ruta, headers=cabecera_operador).json()
        notificacion_ultima = (datos if isinstance(datos, dict) else datos[0])["ultima_notificacion"]
        assert (notificacion_ultima["estado"], notificacion_ultima["codigo_http"], notificacion_ultima["error"]) == (
            "fallida", 403, "http_403",
        )
