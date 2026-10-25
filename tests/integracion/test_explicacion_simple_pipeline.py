"""Fase D3 en el pipeline: prompt v3/v4 según PROMPT_V4, informe 1.2 con la explicación simple, reemplazos,
penalización, respaldo por reglas, versión fija por corrida y versión exacta en la comparación."""

import csv
import io
import json

import pytest
from conftest import SALIDA_LLM_V3

from app.config import get_settings
from app.db import repository
from app.db.models import Incidente
from app.integrations.llm import RespuestaLLM
from app.services import comparacion, laboratorio, pipeline
from app.services.prompt import SYSTEM_PROMPT_V30
from app.services.prompt_v4 import SYSTEM_PROMPT_V40

BLOQUES = {"que_paso": "Este equipo intentó saltar a otros equipos de la red y el sistema lo aisló.",
           "por_que": ["Intentó conectarse a 9 equipos distintos en 30 segundos; lo esperado es 8 o menos.",
                       "Abrió 42 conexiones nuevas en 30 segundos; lo esperado es 20 o menos."],
           "podria_equivocarnos": "Podría ser una copia de seguridad que se conecta a muchos equipos a la vez."}


@pytest.fixture
def llm(bd_temporal, monkeypatch):
    """LLM falso que responde lo pedido y anota el system prompt que recibió."""
    monkeypatch.setenv("LLM_HABILITADO", "true")
    monkeypatch.setenv("OPENROUTER_API_KEY", "clave-ficticia")
    get_settings.cache_clear()
    prompts: list[str] = []

    def instalar(**cambios) -> list[str]:
        texto = json.dumps({**SALIDA_LLM_V3, **cambios})

        class LLMFalso:
            def completar(self, system, usuario):
                prompts.append(system)
                return RespuestaLLM(texto, tokens_entrada=1, tokens_salida=1, latencia_ms=1, peticiones=1)

        monkeypatch.setattr(pipeline, "obtener_cliente_llm", LLMFalso)
        return prompts

    return instalar


@pytest.fixture
def v4(cliente, cabecera_operador):
    assert cliente.put("/api/config", json={"PROMPT_V4": True}, headers=cabecera_operador).json()["PROMPT_V4"] is True


def _incidente(cliente, cabecera, payload) -> Incidente:
    incidente_id = cliente.post("/api/alertas", json=payload, headers=cabecera).json()["incidente_id"]
    with repository.SesionLocal() as sesion:
        return sesion.get(Incidente, incidente_id)


def test_con_prompt_v4_los_bloques_del_llm_llegan_al_informe_1_2(cliente, bd_temporal, cabecera_agente, payload_c1, llm, v4):
    prompts = llm(**BLOQUES)
    i = _incidente(cliente, cabecera_agente, payload_c1)
    assert prompts == [SYSTEM_PROMPT_V40] and i.version_prompt == "v4.0-sin-contexto"
    assert i.informe["version_informe"] == "1.2" and i.informe["trazabilidad_y_accion"]["version_prompt"] == "v4.0-sin-contexto"
    simple = i.informe["explicacion_simple"]
    assert {k: simple[k] for k in BLOQUES} == BLOQUES and simple["reemplazos"] == []
    assert simple["fuentes"] == dict.fromkeys(BLOQUES, "llm")
    assert simple["que_recomendamos"].startswith("Recomendamos pasar el equipo a cuarentena")  # 0.80 → A6, del código
    assert i.informe["razonamiento"]["verificacion"]["resultado"] == "verificado"


def test_con_prompt_v3_por_defecto_los_bloques_salen_de_plantillas(cliente, bd_temporal, cabecera_agente, payload_c1, llm):
    prompts = llm()
    i = _incidente(cliente, cabecera_agente, payload_c1)
    assert prompts == [SYSTEM_PROMPT_V30] and i.version_prompt == "v3.0-sin-contexto" and i.fuente_explicacion == "llm"
    simple = i.informe["explicacion_simple"]
    assert simple["fuentes"] == dict.fromkeys(BLOQUES, "reglas") and len(simple["por_que"]) == 3
    assert simple["que_paso"].startswith("Este equipo (10.0.0.3) se comportó como un programa malicioso")


def test_jerga_reemplaza_sin_penalizar(cliente, bd_temporal, cabecera_agente, payload_c1, llm, v4):
    llm(**{**BLOQUES, "que_paso": "El host superó el umbral M2 con flujos east-west."})
    i = _incidente(cliente, cabecera_agente, payload_c1)
    simple = i.informe["explicacion_simple"]
    assert simple["fuentes"]["que_paso"] == "reglas" and simple["reemplazos"][0]["motivo"].startswith("jerga: ")
    assert i.informe["razonamiento"]["verificacion"]["resultado"] == "verificado"
    assert i.informe["trazabilidad_y_accion"]["confianza_calculada"]["penalizacion"] == 0


def test_cifra_inventada_reemplaza_y_penaliza(cliente, bd_temporal, cabecera_agente, payload_c1, llm, v4):
    llm(**{**BLOQUES, "por_que": ["Hizo 445 conexiones a otros equipos.", BLOQUES["por_que"][1]]})
    i = _incidente(cliente, cabecera_agente, payload_c1)
    simple = i.informe["explicacion_simple"]
    assert simple["reemplazos"] == [{"bloque": "por_que", "motivo": "cifra no respaldada: 445"}]
    verificacion = i.informe["razonamiento"]["verificacion"]
    assert verificacion == {"resultado": "posible_alucinacion", "numeros_no_respaldados": ["445"]}
    confianza = i.informe["trazabilidad_y_accion"]["confianza_calculada"]
    assert confianza["penalizacion"] == 0.2 and confianza["total"] == 0.6  # 0.80 − 0.20 → bajo HITL
    assert i.accion_propuesta == "mantener_drop"


def test_la_desviacion_no_es_una_cifra_permitida_en_los_bloques(cliente, bd_temporal, cabecera_agente, payload_c1, llm, v4):
    llm(**{**BLOQUES, "podria_equivocarnos": "Supera su límite 2.1 veces."})  # M1 42/20 = 2.1: dato derivado
    i = _incidente(cliente, cabecera_agente, payload_c1)
    assert i.informe["explicacion_simple"]["reemplazos"][0]["bloque"] == "podria_equivocarnos"


def test_respaldo_por_reglas_tambien_trae_los_cuatro_bloques(cliente, bd_temporal, cabecera_agente, payload_c1, v4):
    i = _incidente(cliente, cabecera_agente, payload_c1)  # sin API key: reglas
    assert i.fuente_explicacion == "reglas" and i.version_prompt == "v4.0-sin-contexto"
    simple = i.informe["explicacion_simple"]
    assert all(simple[b] for b in ("que_paso", "por_que", "que_recomendamos", "podria_equivocarnos"))
    assert simple["fuentes"] == dict.fromkeys(BLOQUES, "reglas") and simple["reemplazos"] == []


def test_la_corrida_fija_su_version_al_crearse(cliente, bd_temporal, cabecera_operador):
    pendientes = []
    assert cliente.put("/api/config", json={"PROMPT_V4": True}, headers=cabecera_operador).status_code == 200
    v4_id = laboratorio.iniciar_corrida(["AT-01"], None, False, 1, False, lambda f, *a: pendientes.append((f, a)))["corrida_id"]
    assert cliente.put("/api/config", json={"PROMPT_V4": False}, headers=cabecera_operador).status_code == 200
    for funcion, argumentos in pendientes:  # corre DESPUÉS del cambio de configuración
        funcion(*argumentos)
    v3 = cliente.post("/api/laboratorio/corridas", json={"casos": ["AT-01"]}, headers=cabecera_operador).json()["corrida_id"]
    corridas = {c: cliente.get(f"/api/laboratorio/corridas/{c}", headers=cabecera_operador).json() for c in (v4_id, v3)}
    assert corridas[v4_id]["version_prompt"] == "v4.0-sin-contexto" and corridas[v3]["version_prompt"] == "v3.0-sin-contexto"
    with repository.SesionLocal() as sesion:
        incidente = sesion.get(Incidente, corridas[v4_id]["resultados"][0]["incidente_id"])
    assert incidente.version_prompt == "v4.0-sin-contexto"  # el caso usó la versión de su corrida, no la vigente

    datos = comparacion.comparar([v4_id, v3])
    assert [c["etiqueta"] for c in datos["corridas"]] == [f"#{v4_id} · reglas · sin contexto · v4.0",
                                                          f"#{v3} · reglas · sin contexto · v3.0"]
    filas = list(csv.DictReader(io.StringIO(comparacion.exportar_csv([v4_id, v3]))))
    assert {(f["etiqueta"], f["version_prompt"]) for f in filas} == {
        (f"#{v4_id} · reglas · sin contexto · v4.0", "v4.0-sin-contexto"), (f"#{v3} · reglas · sin contexto · v3.0", "v3.0-sin-contexto")}
    assert {c["version_prompt"] for c in comparacion.exportar_json([v4_id, v3])["corridas"]} == {"v4.0-sin-contexto", "v3.0-sin-contexto"}


def test_prompt_v4_se_cambia_en_caliente_y_se_audita(cliente, bd_temporal, cabecera_operador):
    assert cliente.get("/api/config", headers=cabecera_operador).json()["PROMPT_V4"] is False
    assert cliente.put("/api/config", json={"PROMPT_V4": "si"}, headers=cabecera_operador).status_code == 422
    assert cliente.put("/api/config", json={"PROMPT_V4": True}, headers=cabecera_operador).json()["PROMPT_V4"] is True
