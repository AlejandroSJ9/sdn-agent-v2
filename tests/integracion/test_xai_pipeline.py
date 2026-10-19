"""Fase 6.8a+b en el pipeline: dictamen → LLM → veredicto final → acción → verificador → política → informe."""

import json
from datetime import datetime, timedelta, timezone

import pytest
from conftest import SALIDA_LLM_V3
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import repository
from app.db.models import Auditoria, Incidente, Notificacion, Recomendacion
from app.integrations.llm import RespuestaLLM
from app.services import pipeline


class LLMQueGuarda:
    def __init__(self, salida: dict) -> None:
        self.salida, self.mensajes = salida, []

    def completar(self, system: str, usuario: str) -> RespuestaLLM:
        self.mensajes.append(usuario)
        return RespuestaLLM(json.dumps(self.salida), tokens_entrada=1, tokens_salida=1, latencia_ms=1, peticiones=1)


@pytest.fixture
def llm(bd_temporal, monkeypatch):
    monkeypatch.setenv("LLM_HABILITADO", "true")
    monkeypatch.setenv("OPENROUTER_API_KEY", "clave-ficticia")
    monkeypatch.setenv("LLM_MODELO", "modelo-configurado/prueba")
    get_settings.cache_clear()

    def instalar(**cambios) -> LLMQueGuarda:
        falso = LLMQueGuarda({**SALIDA_LLM_V3, **cambios})
        monkeypatch.setattr(pipeline, "obtener_cliente_llm", lambda: falso)
        return falso

    return instalar


def _previo(host, minutos, **campos) -> None:
    t = datetime.now(timezone.utc) - timedelta(minutes=minutos)
    incidente_id = repository.crear_incidente(host, None, None, t, t, {"umbrales_superados": ["M1", "M2", "M3"]})
    with repository.SesionLocal.begin() as sesion:
        sesion.execute(update(Incidente).where(Incidente.id == incidente_id).values(t_explicado=t, **campos))


def _alerta(cliente, cabecera, payload) -> Incidente:
    incidente_id = cliente.post("/api/alertas", json=payload, headers=cabecera).json()["incidente_id"]
    with repository.SesionLocal() as sesion:
        return sesion.get(Incidente, incidente_id)


def _auditados(motor, evento) -> list[dict]:
    with Session(motor) as sesion:
        return [a.datos for a in sesion.scalars(select(Auditoria).where(Auditoria.evento == evento))]


@pytest.fixture
def con_contexto(cliente, cabecera_operador):
    assert cliente.put("/api/config", json={"CONTEXTO_HABILITADO": True}, headers=cabecera_operador).status_code == 200


def test_atenuante_fija_veredicto_final_y_la_alucinacion_penaliza_la_confianza(
    cliente, bd_temporal, cabecera_agente, payload_c1, llm, con_contexto
):
    for dias in (1, 2):
        _previo("10.0.0.3", dias * 1440, clasificacion_operador="falso_positivo", t_desbloqueado=datetime.now(timezone.utc))
    falso = llm(veredicto="confirmado", accion_propuesta="mantener", tecnica_id="T9999",
                hipotesis_fase="Conexiones al puerto 445 de 10.0.0.99.")
    i = _alerta(cliente, cabecera_agente, payload_c1)

    contexto = json.loads(falso.mensajes[0].split("<contexto_host>\n")[1].split("\n</contexto_host>")[0])
    assert contexto["dictamen_historial"] == {"tipo": "atenuante", "veredicto": "indeterminado",
                                              "motivo": "2 clasificaciones falso_positivo del operador con los mismos umbrales (M1, M2, M3)"}
    assert (i.veredicto_llm, i.veredicto, i.dictamen_historial) == ("confirmado", "indeterminado", "atenuante")
    # Fase 9a: confirmado (salida) 0.80 − 0.20 (posible alucinación) = 0.60 < HITL → mantener_drop por confianza
    assert (i.accion_propuesta, i.decision_politica, i.accion_final) == ("mantener_drop", "descartar", "ninguna")
    with Session(bd_temporal) as sesion:
        assert sesion.scalars(select(Recomendacion)).all() == []
    assert _auditados(bd_temporal, "dictamen_aplicado") == [{"incidente_id": i.id, "tipo": "atenuante",
                                                             "veredicto_llm": "confirmado", "veredicto_final": "indeterminado"}]
    assert _auditados(bd_temporal, "accion_ajustada") == []  # el escalón ya era mantener_drop: el dictamen no lo cambió
    assert _auditados(bd_temporal, "tecnica_corregida")[0] == {"incidente_id": i.id, "propuesta": "T9999", "corregida_a": "T1021"}

    r = i.informe
    assert r["mitre"]["tecnica_id"] == "T1021" and r["mitre"]["tecnica_corregida"] is True
    assert r["razonamiento"]["verificacion"] == {"resultado": "posible_alucinacion", "numeros_no_respaldados": ["10.0.0.99", "445"]}
    assert _auditados(bd_temporal, "verificacion_informe")[0]["resultado"] == "posible_alucinacion"
    t = r["trazabilidad_y_accion"]
    assert (t["modelo"], t["version_prompt"], t["origen_accion"]) == ("modelo-configurado/prueba", "v3.1-contexto-historial", "escalera")
    assert (t["confianza_calculada"]["total"], t["confianza_calculada"]["penalizacion"]) == (0.6, 0.2)
    assert t["escalera"]["modificada_por_dictamen"] is False and t["accion_sugerida_llm"] == "mantener"
    explicacion_auditada = [a for a in _auditados(bd_temporal, "explicacion_generada") if a["incidente_id"] == i.id]
    assert len(explicacion_auditada) == 1 and len(t["hash_auditoria_explicacion"]) == 64
    assert i.explicacion == pipeline.informe.resumen(r, i.severidad) and len(i.explicacion) <= 600


def test_agravante_fija_confirmado_y_mantener_drop(cliente, bd_temporal, cabecera_agente, payload_c1, llm, con_contexto):
    _previo("10.0.0.7", 5, veredicto="confirmado")
    _previo("10.0.0.8", 10, veredicto="confirmado")
    llm(veredicto="indeterminado", accion_propuesta="pasar_a_cuarentena", confianza=0.99)
    i = _alerta(cliente, cabecera_agente, payload_c1)
    assert (i.veredicto_llm, i.veredicto, i.dictamen_historial) == ("indeterminado", "confirmado", "agravante")
    # Fase 9a: la salida es indeterminado → sin escalón (mantener_drop); el agravante con confianza alta está en test_escalera_pipeline
    assert (i.accion_propuesta, i.decision_politica, i.accion_final) == ("mantener_drop", "descartar", "ninguna")
    assert i.informe["trazabilidad_y_accion"]["confianza_calculada"]["total"] is None


def test_sin_dictamen_manda_el_llm_y_el_informe_queda_verificado(cliente, bd_temporal, cabecera_agente, payload_c1, llm):
    llm(veredicto="confirmado", factores_ponderados=[
        {"factor": "Flujos nuevos", "peso": "alto", "porque": "M1=42 supera el umbral 20 (desviación 2.10)."},
        {"factor": "Destinos", "peso": "medio", "porque": "M2=9 supera 8, en 2 o más métricas."}])
    i = _alerta(cliente, cabecera_agente, payload_c1)
    assert (i.veredicto_llm, i.veredicto, i.dictamen_historial) == ("confirmado", "confirmado", None)
    assert i.informe["razonamiento"]["verificacion"]["resultado"] == "verificado"
    assert i.informe["evidencia_objetiva"]["fuente_umbrales"] == "ajustes_por_defecto"
    assert i.informe["razonamiento"]["dictamen_historial"] == {"tipo": None, "veredicto": None, "motivo": None}


def test_correo_en_4_lineas_mas_enlace(cliente, bd_temporal, cabecera_agente, payload_c1, llm):
    llm()
    i = _alerta(cliente, cabecera_agente, payload_c1)
    with Session(bd_temporal) as sesion:
        lineas = sesion.scalars(select(Notificacion)).one().payload["mensaje"].splitlines()
    assert [l.split(":")[0] for l in lineas] == ["Evidencia", "MITRE", "Razonamiento", "Acción recomendada", "Informe completo"]
    assert lineas[3] == "Acción recomendada: Cuarentena de puertos (confianza 0.80)"  # Fase 9a: confirmado, 80/135
    assert lineas[-1] == f"Informe completo: http://localhost:8081/#/incidentes/{i.id}"


def test_laboratorio_cuenta_dictamenes_y_alucinaciones(cliente, cabecera_operador, llm):
    llm(veredicto="confirmado", hipotesis_fase="Copia por el puerto 445.")
    respuesta = cliente.post("/api/laboratorio/corridas", headers=cabecera_operador,
                             json={"casos": ["LG-05", "AT-10", "AT-01"], "variante": "con_contexto", "usar_llm": True})
    corrida = cliente.get(f"/api/laboratorio/corridas/{respuesta.json()['corrida_id']}", headers=cabecera_operador).json()
    filas = {r["caso_id"]: r for r in corrida["resultados"]}
    assert (filas["LG-05"]["veredicto_llm"], filas["LG-05"]["veredicto_obtenido"], filas["LG-05"]["dictamen_historial"]) == (
        "confirmado", "indeterminado", "atenuante")
    assert filas["LG-05"]["acierto"] is True  # sobre el veredicto FINAL
    assert filas["AT-10"]["dictamen_historial"] == "agravante" and filas["AT-01"]["dictamen_historial"] is None
    xai = corrida["resumen"]["xai"]
    assert xai["dictamenes"] == {"agravante": 1, "atenuante": 1} and xai["posible_alucinacion"] == 3
    assert xai["exactitud_veredicto_llm"] == {"aciertos": 2, "total": 3, "exactitud": 0.6667}  # el LLM falla LG-05


# ---- Trazabilidad del modelo: el informe registra el modelo que se envió a OpenRouter ----

def test_el_informe_registra_el_modelo_enviado_a_openrouter(cliente, bd_temporal, cabecera_agente, payload_c1, monkeypatch):
    import httpx

    from app.integrations.llm import ClienteOpenRouter

    monkeypatch.setenv("LLM_HABILITADO", "true")
    monkeypatch.setenv("OPENROUTER_API_KEY", "clave-ficticia")
    monkeypatch.setenv("LLM_MODELO", "proveedor/modelo-x")
    get_settings.cache_clear()
    enviados = []

    def openrouter_falso(peticion: httpx.Request) -> httpx.Response:
        enviados.append(json.loads(peticion.content)["model"])
        contenido = json.dumps({**SALIDA_LLM_V3, "veredicto": "confirmado"})
        return httpx.Response(200, json={"choices": [{"message": {"content": contenido}}], "usage": {}})

    def cliente_real_con_red_falsa():
        ajustes = get_settings()  # mismo camino que producción: .env → config → cliente
        return ClienteOpenRouter(ajustes.OPENROUTER_API_KEY, ajustes.LLM_MODELO, ajustes.LLM_TIMEOUT,
                                 transporte=httpx.MockTransport(openrouter_falso))

    monkeypatch.setattr(pipeline, "obtener_cliente_llm", cliente_real_con_red_falsa)
    i = _alerta(cliente, cabecera_agente, payload_c1)
    assert enviados == ["proveedor/modelo-x"]
    assert (i.fuente_explicacion, i.informe["trazabilidad_y_accion"]["modelo"]) == ("llm", "proveedor/modelo-x")


def test_sin_llamada_el_informe_registra_el_modelo_configurado(cliente, bd_temporal, cabecera_agente, payload_c1, monkeypatch):
    monkeypatch.setenv("LLM_MODELO", "proveedor/modelo-y")  # LLM apagado (bd_temporal): respaldo por reglas
    get_settings.cache_clear()
    i = _alerta(cliente, cabecera_agente, payload_c1)
    t = i.informe["trazabilidad_y_accion"]
    # QA F6: no se usó modelo; el configurado queda solo para la auditoría
    assert (i.fuente_explicacion, t["modelo"], t["modelo_configurado"]) == ("reglas", None, "proveedor/modelo-y")
