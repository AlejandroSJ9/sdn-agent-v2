"""Ejecutar pytest desde el panel, con un subproceso FALSO (no se ejecuta pytest dentro de pytest)."""

import sys
import threading

import pytest

from app.services import pytest_panel


class ProcesoFalso:
    """Imita a subprocess.Popen: entrega líneas por stdout y registra cómo se le llamó."""

    llamadas: list = []

    def __init__(self, args, **kwargs):
        ProcesoFalso.llamadas.append((args, kwargs))
        self.stdout = iter(["tests/unit/test_x.py ...", "3 passed, 1 failed in 0.12s"])

    def wait(self):
        return 0

    def kill(self):
        pass


@pytest.fixture
def proceso_falso(monkeypatch):
    ProcesoFalso.llamadas = []
    monkeypatch.setattr(pytest_panel, "crear_proceso", ProcesoFalso)
    return ProcesoFalso


@pytest.mark.parametrize("carpeta", ["e2e", "../app", "unit; rm -rf /", "", "tests/unit"])
def test_carpeta_invalida_422(cliente, cabecera_operador, proceso_falso, carpeta):
    respuesta = cliente.post("/api/laboratorio/pytest", json={"carpeta": carpeta}, headers=cabecera_operador)
    assert respuesta.status_code == 422
    assert proceso_falso.llamadas == []


def test_campos_extra_422(cliente, cabecera_operador, proceso_falso):
    cuerpo = {"carpeta": "unit", "args": ["-k", "x"]}
    assert cliente.post("/api/laboratorio/pytest", json=cuerpo, headers=cabecera_operador).status_code == 422


@pytest.mark.parametrize(("carpeta", "ruta"), [("unit", "tests/unit"), ("integracion", "tests/integracion")])
def test_comando_exacto_y_resultado_guardado(cliente, cabecera_operador, proceso_falso, carpeta, ruta):
    respuesta = cliente.post("/api/laboratorio/pytest", json={"carpeta": carpeta}, headers=cabecera_operador)
    assert respuesta.status_code == 202

    ((args, kwargs),) = proceso_falso.llamadas
    assert args == [sys.executable, "-m", "pytest", ruta, "-q"]
    assert kwargs["cwd"] == pytest_panel.RAIZ and "shell" not in kwargs

    ultimo = cliente.get("/api/laboratorio/pytest/ultimo", headers=cabecera_operador).json()
    assert (ultimo["carpeta"], ultimo["estado"], ultimo["aprobadas"], ultimo["fallidas"]) == (carpeta, "terminada", 3, 1)
    assert "3 passed, 1 failed" in ultimo["salida"]


def test_ejecucion_simultanea_409(cliente, cabecera_operador, proceso_falso):
    assert pytest_panel._candado.acquire(blocking=False)
    try:
        assert cliente.post("/api/laboratorio/pytest", json={"carpeta": "unit"}, headers=cabecera_operador).status_code == 409
    finally:
        pytest_panel._candado.release()
    assert proceso_falso.llamadas == []


def test_timeout_mata_el_proceso(cliente, cabecera_operador, monkeypatch):
    class ProcesoColgado:
        def __init__(self, args, **kwargs):
            self.muerto = threading.Event()
            self.stdout = self._lineas()

        def _lineas(self):
            yield "empezando…"
            self.muerto.wait(5)  # se queda colgado hasta que lo maten

        def wait(self):
            return -9

        def kill(self):
            self.muerto.set()

    monkeypatch.setattr(pytest_panel, "crear_proceso", ProcesoColgado)
    monkeypatch.setattr(pytest_panel, "TIEMPO_MAXIMO", 0.2)
    cliente.post("/api/laboratorio/pytest", json={"carpeta": "unit"}, headers=cabecera_operador)

    assert cliente.get("/api/laboratorio/pytest/ultimo", headers=cabecera_operador).json()["estado"] == "timeout"


def test_sin_ejecuciones_ultimo_es_null_y_401(cliente, cabecera_operador):
    assert cliente.get("/api/laboratorio/pytest/ultimo", headers=cabecera_operador).json() is None
    assert cliente.post("/api/laboratorio/pytest", json={"carpeta": "unit"}).status_code == 401
