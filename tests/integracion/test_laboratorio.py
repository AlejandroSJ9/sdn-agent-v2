"""Laboratorio: corridas aisladas de los incidentes reales, costos, exclusión mutua y exportaciones."""

import csv
import io
import json

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from conftest import SALIDA_LLM_V3
from app.config import get_settings
from app.db.models import Auditoria, Incidente, Notificacion
from app.integrations.llm import RespuestaLLM
from app.services import laboratorio, pipeline, resultados_lab

SALIDA_LLM = {
    **SALIDA_LLM_V3, "veredicto": "confirmado", "severidad": "alta", "confianza": 0.8,  # Fase 6.8a+b: salida v3
}


def _corrida(cliente, cabecera, **cuerpo) -> dict:
    respuesta = cliente.post("/api/laboratorio/corridas", json=cuerpo, headers=cabecera)
    assert respuesta.status_code == 202, respuesta.text
    # TestClient ejecuta la tarea de fondo antes de devolver: la corrida ya terminó.
    return cliente.get(f"/api/laboratorio/corridas/{respuesta.json()['corrida_id']}", headers=cabecera).json()


def _alerta_real(cliente, cabecera, payload, host) -> int:
    respuesta = cliente.post("/api/alertas", json={**payload, "host_origen": host}, headers=cabecera)
    assert respuesta.status_code == 202
    return respuesta.json()["incidente_id"]


def test_corrida_sin_llm_completa_todos_los_casos(cliente, bd_temporal, cabecera_operador):
    corrida = _corrida(cliente, cabecera_operador, conjunto="todos")

    assert corrida["estado"] == "terminada" and len(corrida["resultados"]) == 18
    assert {(r["fuente"], r["motivo_respaldo"]) for r in corrida["resultados"]} == {("reglas", "laboratorio_sin_llm")}
    exactitud = corrida["resumen"]["exactitud_decidibles"]
    # Reglas: ≥2 umbrales → confirmado. Falla solo AM-02 (aceptables: indeterminado/falso_positivo).
    assert exactitud["global"] == {"aciertos": 9, "total": 10, "exactitud": 0.9}
    assert (exactitud["desarrollo"]["total"], exactitud["validacion"]["total"]) == (6, 4)
    assert len(corrida["resumen"]["no_decidibles"]) == 8
    with Session(bd_temporal) as sesion:
        assert {i.origen for i in sesion.scalars(select(Incidente))} == {"prueba"}


def test_du01_un_incidente_con_dos_alertas_y_una_explicacion(cliente, cabecera_operador):
    (resultado,) = _corrida(cliente, cabecera_operador, casos=["DU-01"])["resultados"]

    assert resultado["detalle"] == {"incidentes": 1, "alertas": 2, "explicaciones": 1, "agrupacion_ok": True}
    detalle = cliente.get(f"/api/incidentes/{resultado['incidente_id']}", headers=cabecera_operador).json()
    assert detalle["contador_alertas"] == 2


def test_dos_corridas_seguidas_no_se_agrupan(cliente, cabecera_operador):
    primera = _corrida(cliente, cabecera_operador, casos=["AT-01"])["resultados"][0]["incidente_id"]
    segunda = _corrida(cliente, cabecera_operador, casos=["AT-01"])["resultados"][0]["incidente_id"]
    assert primera != segunda


def test_casos_con_la_misma_ip_en_una_corrida_no_se_agrupan(cliente, cabecera_operador):
    # AT-03, AM-02 y DU-01 comparten 10.0.10.2
    resultados = _corrida(cliente, cabecera_operador, casos=["AT-03", "AM-02", "DU-01"])["resultados"]
    assert len({r["incidente_id"] for r in resultados}) == 3


def test_incidente_real_y_de_prueba_con_la_misma_ip_no_se_agrupan(
    cliente, cabecera_agente, cabecera_operador, payload_c1
):
    real = _alerta_real(cliente, cabecera_agente, payload_c1, "10.0.10.3")  # misma IP que AT-01
    prueba = _corrida(cliente, cabecera_operador, casos=["AT-01"])["resultados"][0]["incidente_id"]
    real_despues = _alerta_real(cliente, cabecera_agente, payload_c1, "10.0.10.3")

    assert prueba != real and real_despues == real
    assert cliente.get(f"/api/incidentes/{real}", headers=cabecera_operador).json()["contador_alertas"] == 2


def test_filtro_de_origen_en_la_lista_de_incidentes(cliente, cabecera_agente, cabecera_operador, payload_c1):
    real = _alerta_real(cliente, cabecera_agente, payload_c1, "10.0.0.3")
    _corrida(cliente, cabecera_operador, casos=["AT-01"])

    por_origen = {
        origen: cliente.get(f"/api/incidentes?origen={origen}", headers=cabecera_operador).json()
        for origen in ("real", "prueba", "todos")
    }
    assert [i["id"] for i in cliente.get("/api/incidentes", headers=cabecera_operador).json()] == [real]
    assert [i["id"] for i in por_origen["real"]] == [real]
    assert [(i["origen"], i["caso_id"]) for i in por_origen["prueba"]] == [("prueba", "AT-01")]
    assert len(por_origen["todos"]) == 2


def test_costo_mayor_que_el_cupo_se_rechaza_sin_ejecutar(cliente, cabecera_operador, monkeypatch):
    for clave, valor in {"LLM_HABILITADO": "true", "OPENROUTER_API_KEY": "x", "LLM_CUPO_DIARIO": "5"}.items():
        monkeypatch.setenv(clave, valor)
    get_settings.cache_clear()

    respuesta = cliente.post("/api/laboratorio/corridas", json={"conjunto": "todos", "usar_llm": True}, headers=cabecera_operador)
    assert respuesta.status_code == 400
    assert "costaría 18 peticiones" in respuesta.json()["detail"] and "quedan 5" in respuesta.json()["detail"]
    assert cliente.get("/api/laboratorio/corridas", headers=cabecera_operador).json() == []


def test_con_el_llm_global_apagado_una_corrida_con_llm_se_rechaza(cliente, cabecera_operador):
    respuesta = cliente.post("/api/laboratorio/corridas", json={"casos": ["AT-01"], "usar_llm": True}, headers=cabecera_operador)
    assert respuesta.status_code == 400 and "apagado" in respuesta.json()["detail"]


def test_corrida_con_llm_usa_el_llm_y_cuenta_el_cupo(cliente, cabecera_operador, monkeypatch):
    for clave, valor in {"LLM_HABILITADO": "true", "OPENROUTER_API_KEY": "x"}.items():
        monkeypatch.setenv(clave, valor)
    get_settings.cache_clear()

    class LLMFalso:
        def completar(self, system, usuario):
            return RespuestaLLM(json.dumps(SALIDA_LLM), 10, 10, 50, 1)

    monkeypatch.setattr(pipeline, "obtener_cliente_llm", LLMFalso)
    corrida = _corrida(cliente, cabecera_operador, casos=["AT-01"], usar_llm=True)

    assert corrida["resultados"][0]["fuente"] == "llm"
    assert corrida["resumen"]["cupo_consumido"] == {"llamadas_llm": 1, "costo_estimado": 1}
    assert cliente.get("/api/llm/cupo", headers=cabecera_operador).json()["usadas"] == 1


def test_corrida_simultanea_409(cliente, cabecera_operador):
    assert laboratorio._candado.acquire(blocking=False)
    try:
        respuesta = cliente.post("/api/laboratorio/corridas", json={"casos": ["AT-01"]}, headers=cabecera_operador)
        assert respuesta.status_code == 409
    finally:
        laboratorio._candado.release()


def test_correos_apagados_dejan_notificaciones_omitidas(cliente, bd_temporal, cabecera_operador):
    _corrida(cliente, cabecera_operador, casos=["AT-01", "AT-02"])
    with Session(bd_temporal) as sesion:
        notificaciones = sesion.scalars(select(Notificacion)).all()
    assert [(n.estado, n.error) for n in notificaciones] == [("omitida", "laboratorio")] * 2


def test_el_interruptor_global_del_llm_no_cambia(cliente, bd_temporal, cabecera_operador):
    antes = cliente.get("/api/config", headers=cabecera_operador).json()
    _corrida(cliente, cabecera_operador, casos=["AT-01"])
    assert cliente.get("/api/config", headers=cabecera_operador).json() == antes
    with Session(bd_temporal) as sesion:
        assert sesion.scalars(select(Auditoria).where(Auditoria.evento == "config_cambiada")).all() == []


def test_repeticiones_calculan_la_estabilidad(cliente, cabecera_operador):
    corrida = _corrida(cliente, cabecera_operador, casos=["AT-01", "AM-02"], repeticiones=2)
    assert len(corrida["resultados"]) == 4
    assert corrida["resumen"]["estabilidad"]["AT-01"] == {"veredicto_mas_comun": "confirmado", "coinciden": 2, "de": 2}


def test_exportaciones(cliente, cabecera_agente, cabecera_operador, payload_c1):
    _alerta_real(cliente, cabecera_agente, payload_c1, "10.0.0.3")
    corrida_id = _corrida(cliente, cabecera_operador, conjunto="todos")["id"]

    texto = cliente.get(f"/api/export/corrida/{corrida_id}.csv", headers=cabecera_operador).text
    filas = list(csv.DictReader(io.StringIO(texto)))
    assert list(filas[0]) == resultados_lab.COLUMNAS_CORRIDA and len(filas) == 18

    datos = cliente.get(f"/api/export/corrida/{corrida_id}.json", headers=cabecera_operador).json()
    resumen = datos["resumen"]
    assert resumen["exactitud_decidibles"]["global"]["total"] == 10  # excluye los 8 no decidibles
    assert set(resumen) >= {"aciertos_por_clase", "matriz_esperado_vs_obtenido", "no_decidibles", "fuente_explicacion",
                            "latencia_explicacion_ms", "estabilidad", "cupo_consumido"}
    assert set(resumen["latencia_explicacion_ms"]) == {"n", "media", "mediana", "p95"}

    for origen, esperadas in [("prueba", 18), ("real", 1), ("todos", 19)]:
        respuesta = cliente.get(f"/api/export/incidentes.csv?origen={origen}", headers=cabecera_operador)
        assert respuesta.headers["content-type"].startswith("text/csv")
        filas = list(csv.DictReader(io.StringIO(respuesta.text)))
        assert list(filas[0]) == resultados_lab.COLUMNAS_INCIDENTES and len(filas) == esperadas
    assert cliente.get("/api/export/corrida/999.json", headers=cabecera_operador).status_code == 404


@pytest.mark.parametrize(
    "cuerpo",
    [{"casos": ["XX-99"]}, {"casos": ["AT-01"], "conjunto": "todos"}, {}, {"conjunto": "otro"},
     {"casos": ["AT-01"], "repeticiones": 6}, {"casos": []}, {"casos": ["AT-01"], "comando": "rm"}],
)
def test_cuerpos_invalidos_422(cliente, cabecera_operador, cuerpo):
    assert cliente.post("/api/laboratorio/corridas", json=cuerpo, headers=cabecera_operador).status_code == 422


def test_escenarios_y_401(cliente, cabecera_operador):
    escenarios = cliente.get("/api/laboratorio/escenarios", headers=cabecera_operador).json()
    assert len(escenarios) == 18 and all(e["costo_llm"] == 1 for e in escenarios)
    for ruta in ("/api/laboratorio/escenarios", "/api/laboratorio/corridas", "/api/export/incidentes.csv"):
        assert cliente.get(ruta).status_code == 401


def test_resumen_del_panel_ignora_los_incidentes_de_prueba(cliente, cabecera_agente, cabecera_operador, payload_c1):
    real = _alerta_real(cliente, cabecera_agente, payload_c1, "10.0.0.3")
    _corrida(cliente, cabecera_operador, conjunto="todos")
    resumen = cliente.get("/api/resumen", headers=cabecera_operador).json()
    assert (resumen["incidentes_abiertos"], resumen["incidentes_hoy"], resumen["ultimo_incidente_id"]) == (1, 1, real)


def test_desbloquear_una_ip_real_no_toca_los_incidentes_de_prueba(cliente, bd_temporal, cabecera_operador, ryu_mock):
    # AT-01 usa 10.0.10.3; Ryu (mock) lo tiene bloqueado como host real
    ryu_mock.bloqueados["10.0.10.3"] = {"ip": "10.0.10.3", "dpid": "0x1", "desde": "2026-10-10T00:00:00Z"}
    prueba = _corrida(cliente, cabecera_operador, casos=["AT-01"])["resultados"][0]["incidente_id"]

    bloqueados = cliente.get("/api/ryu/bloqueados", headers=cabecera_operador).json()
    assert {h["ip"]: h["incidente_id"] for h in bloqueados}["10.0.10.3"] is None
    cuerpo = {"confirmacion": "10.0.10.3", "motivo": "Prueba de aislamiento", "clasificacion": "otro"}
    respuesta = cliente.post("/api/hosts/10.0.10.3/desbloquear", json=cuerpo, headers=cabecera_operador)
    assert respuesta.status_code == 200 and respuesta.json()["incidentes"] == []
    with Session(bd_temporal) as sesion:
        assert sesion.get(Incidente, prueba).t_desbloqueado is None
