"""Decisión acotada (Fase 6.8, 9a, 9b, D1): escalera → política → recomendación, bandeja, compatibilidad con el modo
autonomo_acotado retirado y laboratorio. La ejecución al aprobar, los fallos de Ryu y la reversión están en
test_ejecucion_escalera.py."""

import json
from datetime import datetime, timedelta, timezone

import httpx
import mock_ryu
import pytest
from conftest import SALIDA_LLM_V3, TOKEN_AGENTE, transporte_asgi
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import repository, repository_operador
from app.db.models import Auditoria, Incidente, Notificacion, Recomendacion
from app.integrations.llm import RespuestaLLM
from app.integrations.ryu import ClienteRyu
from app.services import acciones, configuracion, decision, operador, pipeline, politica


def _instalar_ryu(monkeypatch, cuarentena: bool) -> dict:
    """Mock de Ryu con o sin A6 (sin A7 ni A8); devuelve su estado de hosts bloqueados (10.0.0.3 y 10.0.0.5)."""
    app_mock = mock_ryu.crear_app(TOKEN_AGENTE, cuarentena=cuarentena, limitar_tasa=False, cuarentena_vlan=False)
    cliente = ClienteRyu("http://ryu.test", TOKEN_AGENTE, transporte=transporte_asgi(app_mock))
    monkeypatch.setattr(operador, "obtener_cliente_ryu", lambda: cliente)
    return app_mock.state.bloqueados


@pytest.fixture
def ryu_a6(monkeypatch) -> dict:
    return _instalar_ryu(monkeypatch, True)


def _config(cliente, cabecera, **cambios) -> None:
    assert cliente.put("/api/config", json=cambios, headers=cabecera).status_code == 200, cambios


def _alerta(cliente, cabecera, payload, host="10.0.0.3") -> dict:
    respuesta = cliente.post("/api/alertas", json={**payload, "host_origen": host}, headers=cabecera)
    assert respuesta.status_code == 202
    return cliente.get(f"/api/incidentes/{respuesta.json()['incidente_id']}",
                       headers={"X-Operador-Token": get_settings().OPERADOR_TOKEN}).json()


def _recomendaciones(motor) -> list[Recomendacion]:
    with Session(motor) as sesion:
        return list(sesion.scalars(select(Recomendacion).order_by(Recomendacion.id)))


def _ultimo_correo(motor) -> str:
    with Session(motor) as sesion:
        return sesion.scalars(select(Notificacion).order_by(Notificacion.id.desc())).first().payload["mensaje"]


# ---- Aceptación (Fase 9b) ----
# payload_c1 con reglas: confirmado, M1+M2+M3 = 80/135 → 0.5·0.5926 + 0.5 = 0.80 → cuarentena_puertos.

def test_modo_recomendar_crea_recomendacion_y_al_aprobarla_se_ejecuta(
    cliente, bd_temporal, cabecera_agente, cabecera_operador, payload_c1, ryu_a6
):
    incidente = _alerta(cliente, cabecera_agente, payload_c1)
    assert (incidente["veredicto"], incidente["accion_propuesta"]) == ("confirmado", "cuarentena_puertos")
    assert (incidente["decision_politica"], incidente["accion_final"]) == ("recomendar", "recomendada")
    assert "Cuarentena de puertos: conserva lo básico de la red, sin caminos para saltar a otros equipos. Confianza media." in _ultimo_correo(bd_temporal)  # Fase D4: correo en lenguaje simple
    (recomendacion,) = _recomendaciones(bd_temporal)
    assert (recomendacion.estado, recomendacion.accion, recomendacion.confianza) == ("pendiente", "cuarentena_puertos", 0.8)
    assert recomendacion.motivos_politica == []
    assert ryu_a6["10.0.0.3"]["modo"] == "drop_total"

    aprobada = cliente.post(f"/api/recomendaciones/{recomendacion.id}/aprobar", headers=cabecera_operador).json()
    assert aprobada["estado"] == "ejecutada" and aprobada["resuelta_por"] == "operador"
    assert ryu_a6["10.0.0.3"]["modo"] == "cuarentena"
    detalle = cliente.get(f"/api/incidentes/{incidente['id']}", headers=cabecera_operador).json()
    assert detalle["accion_final"] == "cuarentena_puertos"
    assert [(a["accion"], a["origen"], a["resultado"]) for a in detalle["acciones"]] == [("cuarentena_puertos", "operador", "ejecutada")]


# ---- Compatibilidad: autonomo_acotado retirado en la Fase D1 ----

def test_pedir_autonomo_acotado_por_api_responde_422_y_no_cambia_nada(cliente, bd_temporal, cabecera_operador):
    respuesta = cliente.put("/api/config", json={"MODO_DECISION": "autonomo_acotado"}, headers=cabecera_operador)
    assert (respuesta.status_code, respuesta.json()["detail"]) == (422, configuracion.MENSAJE_MODO_RETIRADO)
    assert cliente.get("/api/config", headers=cabecera_operador).json()["MODO_DECISION"] == "recomendar"
    with Session(bd_temporal) as sesion:
        assert sesion.scalars(select(Auditoria).where(Auditoria.evento == "config_cambiada")).all() == []


def test_autonomo_guardado_en_la_bd_arranca_en_recomendar_con_log_y_auditoria(
    cliente, bd_temporal, cabecera_agente, cabecera_operador, payload_c1, ryu_a6, caplog
):
    repository_operador.guardar_configuracion("MODO_DECISION", "autonomo_acotado")  # configuración de antes de la D1
    assert cliente.get("/api/config", headers=cabecera_operador).json()["MODO_DECISION"] == "recomendar"
    datos = configuracion.retirar_modo_autonomo()  # lo que hace el arranque
    assert datos["origen"] == "configuracion_guardada" and "Fase D1" in caplog.text
    assert repository_operador.leer_configuracion()["MODO_DECISION"] == "recomendar"
    assert configuracion.retirar_modo_autonomo() is None  # ya migrado: no se repite
    with Session(bd_temporal) as sesion:
        (cambio,) = sesion.scalars(select(Auditoria).where(Auditoria.evento == "config_cambiada")).all()
    assert (cambio.datos["anterior"], cambio.datos["nuevo"]) == ("autonomo_acotado", "recomendar")
    incidente = _alerta(cliente, cabecera_agente, payload_c1)
    assert (incidente["decision_politica"], incidente["accion_final"]) == ("recomendar", "recomendada")
    assert ryu_a6["10.0.0.3"]["modo"] == "drop_total"


def test_autonomo_en_env_se_lee_como_recomendar(cliente, bd_temporal, cabecera_agente, payload_c1, ryu_a6, monkeypatch):
    monkeypatch.setenv("MODO_DECISION", "autonomo_acotado")
    monkeypatch.setenv("MAX_ACCIONES_POR_MIN", "3")  # claves retiradas en .env: se ignoran
    monkeypatch.setenv("HOSTS_PROTEGIDOS", "[]")
    get_settings.cache_clear()
    assert configuracion.obtener()["MODO_DECISION"] == "recomendar"
    assert configuracion.retirar_modo_autonomo()["origen"] == ".env"
    incidente = _alerta(cliente, cabecera_agente, payload_c1)
    assert incidente["decision_politica"] == "recomendar" and ryu_a6["10.0.0.3"]["modo"] == "drop_total"


def test_sin_a6_queda_como_recomendacion_con_el_motivo(
    cliente, bd_temporal, cabecera_agente, payload_c1, monkeypatch
):
    bloqueados = _instalar_ryu(monkeypatch, False)
    incidente = _alerta(cliente, cabecera_agente, payload_c1)
    assert incidente["decision_politica"] == "recomendar" and incidente["motivos_politica"] == ["sin capacidad A6"]
    (recomendacion,) = _recomendaciones(bd_temporal)
    assert recomendacion.motivos_politica == ["sin capacidad A6"] and "modo" not in bloqueados["10.0.0.3"]


def test_solo_explicar_detiene_toda_accion_automatica(
    cliente, bd_temporal, cabecera_agente, cabecera_operador, payload_c1, ryu_a6
):
    _config(cliente, cabecera_operador, MODO_DECISION="solo_explicar")  # interruptor de emergencia
    incidente = _alerta(cliente, cabecera_agente, payload_c1)
    assert (incidente["decision_politica"], incidente["accion_final"]) == ("descartar", "ninguna")
    assert _recomendaciones(bd_temporal) == [] and ryu_a6["10.0.0.3"]["modo"] == "drop_total"
    with Session(bd_temporal) as sesion:
        assert sesion.scalars(select(Auditoria).where(Auditoria.evento == "accion_ejecutada")).all() == []


def test_solo_explicar_no_borra_pendientes_y_el_operador_puede_aprobarlas(
    cliente, bd_temporal, cabecera_agente, cabecera_operador, payload_c1, ryu_a6
):
    _alerta(cliente, cabecera_agente, payload_c1)
    _config(cliente, cabecera_operador, MODO_DECISION="solo_explicar")
    (recomendacion,) = _recomendaciones(bd_temporal)
    assert recomendacion.estado == "pendiente"
    assert cliente.post(f"/api/recomendaciones/{recomendacion.id}/aprobar", headers=cabecera_operador).json()["estado"] == "ejecutada"


def test_recomendacion_previa_a_la_fase_9a_se_sigue_aprobando(cliente, bd_temporal, cabecera_operador, ryu_a6):
    """Vocabulario de la 6.8 en la bandeja: aprobar ejecuta A6 como antes (camino del operador)."""
    from app.services import recomendaciones

    previa = recomendaciones.crear(None, "10.0.0.3", "pasar_a_cuarentena", "6.8", 0.6, ["modo de decisión distinto de autonomo_acotado"])
    aprobada = cliente.post(f"/api/recomendaciones/{previa['id']}/aprobar", headers=cabecera_operador).json()
    assert aprobada["estado"] == "ejecutada" and ryu_a6["10.0.0.3"]["modo"] == "cuarentena"


def test_la_accion_del_llm_es_informativa_y_no_decide(
    cliente, bd_temporal, cabecera_agente, payload_c1, ryu_a6, monkeypatch
):
    salida = {**SALIDA_LLM_V3, "veredicto": "indeterminado", "severidad": "media", "confianza": 0.95,
              "accion_propuesta": "escalar_a_drop", "justificacion_accion": "inválida en drop_total"}

    class LLMFalso:
        def completar(self, system, usuario):
            assert json.loads(usuario.split("\n")[1])["modo_actual"] == "drop_total"
            return RespuestaLLM(json.dumps(salida), tokens_entrada=1, tokens_salida=1, latencia_ms=1, peticiones=1)

    monkeypatch.setenv("LLM_HABILITADO", "true")
    monkeypatch.setenv("OPENROUTER_API_KEY", "clave-ficticia")
    get_settings.cache_clear()
    monkeypatch.setattr(pipeline, "obtener_cliente_llm", LLMFalso)
    incidente = _alerta(cliente, cabecera_agente, payload_c1)
    assert (incidente["accion_propuesta"], incidente["decision_politica"]) == ("mantener_drop", "descartar")
    assert incidente["informe"]["trazabilidad_y_accion"]["accion_sugerida_llm"] == "escalar_a_drop"
    assert incidente["confianza"] == 0.95  # la declarada por el LLM se guarda, pero no decide


def test_confirmado_con_limitar_tasa_se_descarta_y_audita(bd_temporal, monkeypatch):
    """Defensa en profundidad: la escalera nunca lo produce, pero si llegara, la política lo descarta."""
    incidente_id = repository.crear_incidente("10.0.0.3", None, None, datetime.now(timezone.utc), None, {})
    for veredictos in ({"veredicto": "confirmado", "veredicto_salida": "falso_positivo"},
                       {"veredicto": "indeterminado", "veredicto_salida": "confirmado"}):
        decidido = decision.decidir(incidente_id, {"accion": "limitar_tasa", "confianza": 0.9, "justificacion": "x",
                                                   "modo_actual": "drop_total", **veredictos}, None)
        assert (decidido["decision_politica"], decidido["accion_final"]) == ("descartar", "ninguna")
        assert decidido["motivos"] == [politica.MOTIVO_INCOMPATIBLE]
    assert _recomendaciones(bd_temporal) == []
    with Session(bd_temporal) as sesion:
        auditados = sesion.scalars(select(Auditoria).where(Auditoria.evento == "accion_descartada")).all()
    assert [a.datos["motivo"] for a in auditados] == [politica.MOTIVO_INCOMPATIBLE] * 2


# ---- acciones.ejecutar ----

@pytest.mark.parametrize(("cuarentena", "estado"), [(True, "ejecutada"), (False, "fallida")])
def test_acciones_ejecutar_contra_el_mock(bd_temporal, monkeypatch, cuarentena, estado):
    bloqueados = _instalar_ryu(monkeypatch, cuarentena)
    incidente = {"id": None, "host_origen": "10.0.0.3", "origen": "real"}
    assert acciones.ejecutar("pasar_a_cuarentena", incidente, "politica")["estado"] == estado
    if cuarentena:
        assert bloqueados["10.0.0.3"]["modo"] == "cuarentena"
        assert acciones.ejecutar("escalar_a_drop", incidente, "operador")["estado"] == "ejecutada"
        assert bloqueados["10.0.0.3"]["modo"] == "drop_total"
    with pytest.raises(acciones.AccionNoPermitida):
        acciones.ejecutar("desbloquear", incidente, "operador")


def test_laboratorio_simula_sin_tocar_la_red(bd_temporal, monkeypatch):
    llamadas = []
    cliente = ClienteRyu("http://ryu.test", TOKEN_AGENTE, transporte=httpx.MockTransport(lambda p: llamadas.append(p)))
    monkeypatch.setattr(operador, "obtener_cliente_ryu", lambda: cliente)
    resultado = acciones.ejecutar("pasar_a_cuarentena", {"id": None, "host_origen": "10.0.10.1", "origen": "prueba"}, "politica")
    assert resultado == {"estado": "simulada", "modo": "cuarentena"} and llamadas == []


# ---- Acciones manuales del operador (Hosts bloqueados) ----

def test_operador_pasa_a_cuarentena_y_vuelve_a_bloqueo_total(cliente, cabecera_operador, ryu_a6):
    assert cliente.post("/api/hosts/10.0.0.3/cuarentena", headers=cabecera_operador).json()["modo"] == "cuarentena"
    assert cliente.post("/api/hosts/10.0.0.3/cuarentena", headers=cabecera_operador).status_code == 409  # ya lo está
    assert cliente.post("/api/hosts/10.0.0.3/bloqueo_total", headers=cabecera_operador).json()["modo"] == "drop_total"
    assert ryu_a6["10.0.0.3"]["modo"] == "drop_total"
    assert cliente.post("/api/hosts/10.0.0.0%2F24/cuarentena", headers=cabecera_operador).status_code in (404, 422)  # nunca subredes
    assert cliente.post("/api/hosts/10.0.0.3/cuarentena").status_code == 401


def test_operador_sin_a6_409(cliente, cabecera_operador, monkeypatch):
    _instalar_ryu(monkeypatch, False)
    assert cliente.post("/api/hosts/10.0.0.3/cuarentena", headers=cabecera_operador).status_code == 409


# ---- Recomendaciones: rechazar, expirar, errores ----

def test_rechazar_exige_motivo_y_no_se_resuelve_dos_veces(
    cliente, bd_temporal, cabecera_agente, cabecera_operador, payload_c1, ryu_a6
):
    _alerta(cliente, cabecera_agente, payload_c1)
    (recomendacion,) = _recomendaciones(bd_temporal)
    ruta = f"/api/recomendaciones/{recomendacion.id}"
    for cuerpo in ({}, {"motivo": "no"}, {"motivo": "x" * 201}):
        assert cliente.post(f"{ruta}/rechazar", json=cuerpo, headers=cabecera_operador).status_code == 422
    rechazada = cliente.post(f"{ruta}/rechazar", json={"motivo": "Prefiero revisarlo en sitio"}, headers=cabecera_operador).json()
    assert (rechazada["estado"], rechazada["motivo_resolucion"]) == ("rechazada", "Prefiero revisarlo en sitio")
    assert cliente.post(f"{ruta}/aprobar", headers=cabecera_operador).status_code == 409
    assert ryu_a6["10.0.0.3"]["modo"] == "drop_total"
    assert cliente.post("/api/recomendaciones/999/aprobar", headers=cabecera_operador).status_code == 404


def test_recomendaciones_expiran_al_listar(cliente, bd_temporal, cabecera_agente, cabecera_operador, payload_c1, ryu_a6):
    _alerta(cliente, cabecera_agente, payload_c1)
    with Session(bd_temporal) as sesion, sesion.begin():
        sesion.execute(update(Recomendacion).values(t_expira=datetime.now(timezone.utc) - timedelta(seconds=1)))
    assert cliente.get("/api/recomendaciones?estado=pendiente", headers=cabecera_operador).json() == []
    (expirada,) = cliente.get("/api/recomendaciones", headers=cabecera_operador).json()
    assert (expirada["estado"], expirada["resuelta_por"]) == ("expirada", "sistema")


def test_recomendaciones_exigen_token(cliente):
    assert cliente.get("/api/recomendaciones").status_code == 401
    assert cliente.post("/api/recomendaciones/1/aprobar").status_code == 401
    assert cliente.post("/api/recomendaciones/1/rechazar", json={"motivo": "motivo largo"}).status_code == 401


# ---- Laboratorio: siempre simulado (nunca llama a Ryu) ----
# LG-05: reglas con atenuante → indeterminado → mantener_drop. AT-01: confirmado ≥ 0.70 → cuarentena_puertos,
# recomendada simulada (el laboratorio corre por defecto en recomendar y asume A6, A7 y A8).
RESULTADOS_LAB = {"LG-05": ("mantener_drop", "descartar", "ninguna"),
                  "AT-01": ("cuarentena_puertos", "recomendar", "recomendada_simulada")}


def test_laboratorio_guarda_la_decision_simulada_sin_llamar_a_ryu(cliente, bd_temporal, cabecera_operador, monkeypatch):
    llamadas = []
    cliente_ryu = ClienteRyu("http://ryu.test", TOKEN_AGENTE, transporte=httpx.MockTransport(lambda p: llamadas.append(p)))
    monkeypatch.setattr(operador, "obtener_cliente_ryu", lambda: cliente_ryu)

    def correr(**opciones) -> dict:
        respuesta = cliente.post("/api/laboratorio/corridas", headers=cabecera_operador,
                                 json={"casos": ["LG-05", "AT-01"], "variante": "con_contexto", **opciones})
        corrida = cliente.get(f"/api/laboratorio/corridas/{respuesta.json()['corrida_id']}", headers=cabecera_operador).json()
        return {r["caso_id"]: (r["accion_propuesta"], r["decision_politica"], r["accion_final"]) for r in corrida["resultados"]}

    assert correr() == RESULTADOS_LAB
    assert correr(umbral_confianza=0.6) == RESULTADOS_LAB  # UMBRAL_CONFIANZA retirado: se acepta sin efecto
    assert correr(modo_decision="recomendar") == RESULTADOS_LAB
    assert all(r[1] == "descartar" for r in correr(modo_decision="solo_explicar").values())
    assert llamadas == [] and _recomendaciones(bd_temporal) == []  # el laboratorio no llena la bandeja
    retirado = cliente.post("/api/laboratorio/corridas", headers=cabecera_operador,
                            json={"casos": ["AT-01"], "modo_decision": "autonomo_acotado"})
    assert (retirado.status_code, retirado.json()["detail"]) == (422, configuracion.MENSAJE_MODO_RETIRADO)
