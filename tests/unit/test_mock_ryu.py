"""El mock de Ryu responde exactamente según C2 (rutas, códigos y nombres de umbrales), con y sin A6."""

import mock_ryu
import pytest
from conftest import TOKEN_AGENTE
from fastapi.testclient import TestClient

CABECERA = {"X-Agent-Token": TOKEN_AGENTE}
UMBRALES_C2 = {"M1", "M2", "M3_DURACION_S", "M3_BYTES", "M4", "M5", "SCORE_SOSPECHOSO", "SCORE_CRITICO"}


def _mock(cuarentena: bool | None = False) -> TestClient:
    return TestClient(mock_ryu.crear_app(TOKEN_AGENTE, cuarentena=cuarentena))


def test_umbrales_con_los_nombres_y_valores_de_a5():
    umbrales = _mock().get("/ryu/umbrales", headers=CABECERA).json()
    assert set(umbrales) == UMBRALES_C2
    assert umbrales["M2"] == 8 and umbrales["SCORE_CRITICO"] == 60
    assert all(type(v) is int and v > 0 for v in umbrales.values())


def test_estado_con_pesos_y_version():
    estado = _mock().get("/ryu/estado", headers=CABECERA).json()
    assert set(estado) == {"switches", "hosts_bloqueados", "umbrales", "pesos", "version"}
    assert estado["pesos"] == {"M5": 40, "M3": 35, "M1": 25, "M2": 20, "M4": 15}
    assert estado["version"] == "2.0" and set(estado["umbrales"]) == UMBRALES_C2


@pytest.mark.parametrize(
    ("metodo", "ruta"),
    [("get", "/ryu/estado"), ("get", "/ryu/hosts/bloqueados"), ("post", "/ryu/hosts/10.0.0.3/desbloquear"),
     ("get", "/ryu/umbrales"), ("put", "/ryu/umbrales")],
)
def test_sin_token_401(metodo, ruta):
    kwargs = {"json": {"M2": 7}} if metodo == "put" else {}
    assert getattr(_mock(), metodo)(ruta, headers={"X-Agent-Token": "otro"}, **kwargs).status_code == 401


def test_desbloquear_404_y_422():
    mock = _mock()
    assert mock.post("/ryu/hosts/10.0.0.9/desbloquear", headers=CABECERA).status_code == 404
    assert mock.post("/ryu/hosts/no-es-ip/desbloquear", headers=CABECERA).status_code == 422
    assert mock.post("/ryu/hosts/10.0.0.3/desbloquear", headers=CABECERA).json() == {
        "ok": True, "ip": "10.0.0.3", "reglas_borradas": 1}


@pytest.mark.parametrize("cuerpo", [{"CRITICAL_THRESHOLD": 2}, {"M2": 0}, {"M2": 2.5}, {"M2": True}])
def test_put_umbrales_invalidos_422(cuerpo):
    mock = _mock()
    assert mock.put("/ryu/umbrales", json=cuerpo, headers=CABECERA).status_code == 422
    assert mock.get("/ryu/umbrales", headers=CABECERA).json() == mock_ryu.UMBRALES_INICIALES


def test_sin_a6_no_hay_modo_ni_rutas_de_cuarentena():
    mock = _mock(cuarentena=False)
    assert all("modo" not in host for host in mock.get("/ryu/hosts/bloqueados", headers=CABECERA).json())
    assert mock.post("/ryu/hosts/10.0.0.3/cuarentena", headers=CABECERA).status_code == 404
    assert mock.post("/ryu/hosts/10.0.0.3/bloqueo_total", headers=CABECERA).status_code == 404


def test_con_a6_modo_y_rutas_de_cuarentena():
    mock = _mock(cuarentena=True)
    assert {host["modo"] for host in mock.get("/ryu/hosts/bloqueados", headers=CABECERA).json()} == {"drop_total"}
    assert mock.post("/ryu/hosts/10.0.0.3/cuarentena", headers=CABECERA).json() == {
        "ok": True, "ip": "10.0.0.3", "modo": "cuarentena"}
    assert mock.post("/ryu/hosts/10.0.0.3/bloqueo_total", headers=CABECERA).json()["modo"] == "drop_total"
    assert mock.post("/ryu/hosts/10.0.0.9/cuarentena", headers=CABECERA).status_code == 404


def test_mock_cuarentena_desde_el_entorno(monkeypatch):
    monkeypatch.setenv("MOCK_CUARENTENA", "1")
    assert _mock(cuarentena=None).post("/ryu/hosts/10.0.0.3/cuarentena", headers=CABECERA).status_code == 200
    monkeypatch.delenv("MOCK_CUARENTENA")
    assert _mock(cuarentena=None).post("/ryu/hosts/10.0.0.3/cuarentena", headers=CABECERA).status_code == 404
