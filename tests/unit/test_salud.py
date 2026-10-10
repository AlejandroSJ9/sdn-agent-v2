"""Prueba de GET /api/salud con una base SQLite temporal."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect

from app.config import get_settings
from app.main import app

TABLAS = {"incidentes", "alertas", "auditoria", "uso_llm", "notificaciones", "configuracion"}


@pytest.fixture
def db_url(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'prueba.db'}"
    monkeypatch.setenv("DB_URL", url)
    get_settings.cache_clear()
    yield url
    get_settings.cache_clear()


def test_salud_responde_ok(db_url):
    with TestClient(app) as cliente:
        respuesta = cliente.get("/api/salud")
    assert respuesta.status_code == 200
    assert respuesta.json() == {"ok": True, "version": "0.1.0"}


def test_tablas_creadas_al_arrancar(db_url):
    with TestClient(app):
        pass
    assert TABLAS <= set(inspect(create_engine(db_url)).get_table_names())
