"""QA F6: un incidente cuyo LLM no responde a tiempo avisa el respaldo en el correo y en la API."""

import csv
import io
import json

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Auditoria, Corrida
from app.integrations.llm import LLMError
from app.services import notificacion, pipeline


class LLMQueNoResponde:
    def completar(self, system, usuario):
        raise LLMError("timeout", "tope superado", peticiones=1)


@pytest.fixture
def correo(bd_temporal, monkeypatch) -> list[dict]:
    monkeypatch.setenv("LLM_HABILITADO", "true")
    monkeypatch.setenv("OPENROUTER_API_KEY", "clave-ficticia")
    monkeypatch.setenv("LLM_MODELO", "proveedor/modelo-z")
    monkeypatch.setenv("APPSCRIPT_URL", "https://script.google.com/macros/s/FALSO/exec")
    get_settings.cache_clear()
    monkeypatch.setattr(pipeline, "obtener_cliente_llm", lambda: LLMQueNoResponde())
    enviados: list[dict] = []

    def manejar(peticion: httpx.Request) -> httpx.Response:
        enviados.append(json.loads(peticion.content))
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(notificacion, "transporte_appscript", lambda: httpx.MockTransport(manejar))
    return enviados


def test_timeout_avisa_el_respaldo_en_correo_y_api(cliente, cabecera_agente, cabecera_operador, payload_c1, correo):
    incidente_id = cliente.post("/api/alertas", json=payload_c1, headers=cabecera_agente).json()["incidente_id"]

    [enviado] = correo
    assert enviado["asunto"] == "[SDN] Host 10.0.0.3 aislado por posible movimiento lateral [respaldo por reglas]"
    lineas = enviado["mensaje"].splitlines()
    assert lineas[0] == "Explicación de respaldo por reglas: el modelo no respondió a tiempo. Revisar en el panel."
    assert 3 <= len(lineas) <= 6
    assert "(sin LLM: respaldo por reglas" in lineas[3] and "LLM: confirmado" not in enviado["mensaje"]
    assert "proveedor/modelo-z" not in enviado["mensaje"]

    detalle = cliente.get(f"/api/incidentes/{incidente_id}", headers=cabecera_operador).json()
    assert (detalle["fuente_explicacion"], detalle["motivo_respaldo"], detalle["motivo_respaldo_legible"]) == (
        "reglas", "timeout", "el modelo no respondió a tiempo")
    [fila] = cliente.get("/api/incidentes", headers=cabecera_operador).json()
    assert fila["motivo_respaldo_legible"] == "el modelo no respondió a tiempo"  # actividad reciente (tooltip)


# ---- QA F6 (2): en un respaldo el LLM no emitió veredicto ni se usó modelo ----

def test_respaldo_no_atribuye_veredicto_ni_modelo_al_llm(cliente, bd_temporal, cabecera_agente, cabecera_operador,
                                                          payload_c1, correo):
    incidente_id = cliente.post("/api/alertas", json=payload_c1, headers=cabecera_agente).json()["incidente_id"]
    detalle = cliente.get(f"/api/incidentes/{incidente_id}", headers=cabecera_operador).json()
    t = detalle["informe"]["trazabilidad_y_accion"]
    assert (detalle["veredicto_llm"], t["veredicto_llm"]) == (None, None)
    assert detalle["veredicto"] == t["veredicto_final"] == "confirmado"  # el de las reglas, solo como final
    assert (t["modelo"], t["modelo_configurado"]) == (None, "proveedor/modelo-z")
    with Session(bd_temporal) as sesion:
        eventos = {a.evento: a.datos for a in sesion.scalars(select(Auditoria))}
    assert "dictamen_aplicado" not in eventos  # sin dictamen, nada cambió el veredicto de la salida
    assert eventos["explicacion_generada"]["veredicto_llm"] is None


def test_corrida_sin_llm_cuenta_sin_veredicto_del_llm(cliente, bd_temporal, cabecera_operador):
    respuesta = cliente.post("/api/laboratorio/corridas", headers=cabecera_operador,
                             json={"casos": ["LG-05", "AT-01"], "variante": "con_contexto", "usar_llm": False})
    corrida_id = respuesta.json()["corrida_id"]
    corrida = cliente.get(f"/api/laboratorio/corridas/{corrida_id}", headers=cabecera_operador).json()
    xai = corrida["resumen"]["xai"]
    assert xai["exactitud_veredicto_llm"] == {"aciertos": 0, "total": 0, "exactitud": None}  # ni acierto ni fallo
    assert xai["sin_veredicto_llm"] == 2
    assert xai["cobertura_llm"] == {"llm": 0, "total": 2, "porcentaje": 0.0}
    assert all(r["veredicto_llm"] is None for r in corrida["resultados"])
    texto = cliente.get(f"/api/export/corrida/{corrida_id}.csv", headers=cabecera_operador).text
    assert {f["veredicto_llm"] for f in csv.DictReader(io.StringIO(texto))} == {""}


def test_corrida_terminada_recalcula_al_leer_e_ignora_el_resumen_guardado(cliente, bd_temporal, cabecera_operador):
    respuesta = cliente.post("/api/laboratorio/corridas", headers=cabecera_operador,
                             json={"casos": ["LG-05", "AT-01"], "variante": "con_contexto", "usar_llm": False})
    corrida_id = respuesta.json()["corrida_id"]
    # resumen guardado con el cálculo antiguo: contaba el veredicto de las reglas como acierto del LLM
    viejo = {"xai": {"exactitud_veredicto_llm": {"aciertos": 2, "total": 2, "exactitud": 1.0}}}
    with Session(bd_temporal) as sesion, sesion.begin():
        sesion.get(Corrida, corrida_id).resumen = viejo

    for ruta in (f"/api/laboratorio/corridas/{corrida_id}", f"/api/export/corrida/{corrida_id}.json"):
        xai = cliente.get(ruta, headers=cabecera_operador).json()["resumen"]["xai"]
        assert xai["exactitud_veredicto_llm"] == {"aciertos": 0, "total": 0, "exactitud": None}
        assert (xai["sin_veredicto_llm"], xai["cobertura_llm"]["llm"]) == (2, 0)
    with Session(bd_temporal) as sesion:
        assert sesion.get(Corrida, corrida_id).resumen == viejo  # el guardado queda como respaldo histórico
