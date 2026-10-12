"""Pipeline de explicación con un LLM FALSO (sin red), disparado por POST /api/alertas."""

import json
import time

import httpx
import pytest
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Incidente, UsoLlm
from app.integrations.llm import ClienteOpenRouter, LLMError, RespuestaLLM
from app.services import pipeline, presupuesto
from conftest import SALIDA_LLM_V3

SALIDA_VALIDA = {**SALIDA_LLM_V3, "veredicto": "falso_positivo", "severidad": "media", "confianza": 0.7,
                 "tecnica_id": "T1046", "recomendacion_operador": "Revisar antes de desbloquear."}  # Fase 6.8a+b: salida v3


class LLMFalso:
    """Devuelve el texto programado (o lanza la excepción) y cuenta las llamadas."""

    def __init__(self, respuesta: str | Exception) -> None:
        self.respuesta = respuesta
        self.llamadas = 0

    def completar(self, system: str, usuario: str) -> RespuestaLLM:
        self.llamadas += 1
        if isinstance(self.respuesta, Exception):
            raise self.respuesta
        return RespuestaLLM(self.respuesta, tokens_entrada=100, tokens_salida=50, latencia_ms=1234, peticiones=1)


@pytest.fixture
def usar_llm(bd_temporal, monkeypatch):
    """Activa el LLM con una clave ficticia y devuelve una función para instalar el cliente falso."""
    monkeypatch.setenv("LLM_HABILITADO", "true")
    monkeypatch.setenv("OPENROUTER_API_KEY", "clave-ficticia")
    get_settings.cache_clear()

    def instalar(respuesta: str | Exception) -> LLMFalso:
        falso = LLMFalso(respuesta)
        monkeypatch.setattr(pipeline, "obtener_cliente_llm", lambda: falso)
        return falso

    return instalar


def _alerta(cliente, cabecera, payload) -> int:
    respuesta = cliente.post("/api/alertas", json=payload, headers=cabecera)
    assert respuesta.status_code == 202
    return respuesta.json()["incidente_id"]


def _incidente(motor, incidente_id: int) -> Incidente:
    with Session(motor) as sesion:
        return sesion.get(Incidente, incidente_id)


def test_llm_valido_fuente_llm(cliente, bd_temporal, cabecera_agente, payload_c1, usar_llm):
    falso = usar_llm(json.dumps(SALIDA_VALIDA))
    incidente = _incidente(bd_temporal, _alerta(cliente, cabecera_agente, payload_c1))

    assert falso.llamadas == 1
    assert (incidente.fuente_explicacion, incidente.motivo_respaldo) == ("llm", None)
    assert incidente.veredicto == "falso_positivo"
    assert (incidente.tokens_entrada, incidente.tokens_salida, incidente.latencia_llm_ms) == (100, 50, 1234)
    assert incidente.t_explicado is not None
    assert incidente.t_desbloqueado is None  # el veredicto no toca el bloqueo


@pytest.mark.parametrize(
    ("respuesta", "motivo"),
    [
        ("esto no es JSON", "json_invalido"),
        (json.dumps({**SALIDA_VALIDA, "confianza": 1.5}), "schema_invalido"),
        (LLMError("timeout", peticiones=1), "timeout"),
        (LLMError("error_llm", "HTTP 500", peticiones=1), "error_llm"),
        (RuntimeError("fallo inesperado"), "error_interno"),
    ],
)
def test_fallos_del_llm_usan_reglas(cliente, bd_temporal, cabecera_agente, payload_c1, usar_llm, respuesta, motivo):
    falso = usar_llm(respuesta)
    incidente = _incidente(bd_temporal, _alerta(cliente, cabecera_agente, payload_c1))

    assert falso.llamadas == 1
    assert (incidente.fuente_explicacion, incidente.motivo_respaldo) == ("reglas", motivo)
    assert incidente.veredicto == "confirmado"  # 3 umbrales en el ejemplo C1
    assert incidente.informe["mitre"]["hipotesis_fase"].startswith("Reglas (sin LLM)")  # Fase 6.8b: informe


def test_cupo_agotado_usa_reglas_sin_llamar_al_llm(cliente, bd_temporal, cabecera_agente, payload_c1, usar_llm, monkeypatch):
    monkeypatch.setenv("LLM_CUPO_DIARIO", "2")
    get_settings.cache_clear()
    with Session(bd_temporal) as sesion:
        sesion.add(UsoLlm(fecha=presupuesto._hoy_utc(), peticiones=2, fallidas=0, tokens_entrada=0, tokens_salida=0))
        sesion.commit()
    falso = usar_llm(json.dumps(SALIDA_VALIDA))
    incidente = _incidente(bd_temporal, _alerta(cliente, cabecera_agente, payload_c1))

    assert falso.llamadas == 0
    assert (incidente.fuente_explicacion, incidente.motivo_respaldo) == ("reglas", "sin_cupo")


def test_llm_deshabilitado_usa_reglas(cliente, bd_temporal, cabecera_agente, payload_c1, usar_llm, monkeypatch):
    monkeypatch.setenv("LLM_HABILITADO", "false")
    get_settings.cache_clear()
    falso = usar_llm(json.dumps(SALIDA_VALIDA))
    incidente = _incidente(bd_temporal, _alerta(cliente, cabecera_agente, payload_c1))

    assert falso.llamadas == 0
    assert (incidente.fuente_explicacion, incidente.motivo_respaldo) == ("reglas", "llm_deshabilitado")


def test_alerta_agrupada_llama_al_llm_una_sola_vez(cliente, bd_temporal, cabecera_agente, payload_c1, usar_llm):
    falso = usar_llm(json.dumps(SALIDA_VALIDA))
    primero = _alerta(cliente, cabecera_agente, payload_c1)
    segundo = _alerta(cliente, cabecera_agente, payload_c1)

    assert primero == segundo
    assert falso.llamadas == 1
    with Session(bd_temporal) as sesion:
        assert sesion.get(UsoLlm, presupuesto._hoy_utc()).peticiones == 1


def test_auditoria_integra_tras_el_pipeline(cliente, cabecera_agente, cabecera_operador, payload_c1, usar_llm):
    usar_llm("esto no es JSON")
    _alerta(cliente, cabecera_agente, payload_c1)
    payload_c1["host_origen"] = "10.0.0.4"
    usar_llm(json.dumps(SALIDA_VALIDA))
    _alerta(cliente, cabecera_agente, payload_c1)

    verificacion = cliente.get("/api/auditoria/verificar", headers=cabecera_operador).json()
    # 2 × (alerta_recibida + verificacion_informe + explicacion_generada + notificacion_omitida)
    # (Fase 6.8b: el verificador se audita; la salida v3 trae accion_propuesta, así que no hay corrección)
    assert verificacion == {"integra": True, "filas": 8, "primera_rota": None}


def test_cliente_openrouter_corta_en_el_tope_total_con_respuesta_lenta_por_partes():
    """Cada fragmento llega antes de cualquier timeout por operación, pero el total superaría el tope."""

    def goteo():
        for _ in range(20):
            time.sleep(0.1)
            yield b" "

    cliente = ClienteOpenRouter(
        "clave", "modelo-x", 0.5, transporte=httpx.MockTransport(lambda _: httpx.Response(200, content=goteo()))
    )
    inicio = time.perf_counter()
    with pytest.raises(LLMError) as error:
        cliente.completar("system", "usuario")
    duracion = time.perf_counter() - inicio

    assert error.value.motivo == "timeout"
    assert 0.45 <= duracion < 0.7  # tope 0.5 s; la respuesta completa tardaría 2 s


def test_cliente_openrouter_repite_una_vez_sin_response_format_y_extrae_el_json():
    """Cliente real con transporte falso de httpx (sin red)."""
    cuerpos: list[dict] = []

    def responder(peticion: httpx.Request) -> httpx.Response:
        cuerpo = json.loads(peticion.content)
        cuerpos.append(cuerpo)
        if "response_format" in cuerpo:
            return httpx.Response(400, json={"error": {"message": "response_format no soportado"}})
        contenido = f"Claro, aquí está: {json.dumps(SALIDA_VALIDA)} ¡Listo!"
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": contenido}}],
                "usage": {"prompt_tokens": 300, "completion_tokens": 120},
            },
        )

    cliente = ClienteOpenRouter("clave", "modelo-x", 5, transporte=httpx.MockTransport(responder))
    respuesta = cliente.completar("system", "<datos_red>\n{}\n</datos_red>")

    assert json.loads(respuesta.texto) == SALIDA_VALIDA
    assert (respuesta.peticiones, respuesta.tokens_entrada, respuesta.tokens_salida) == (2, 300, 120)
    assert len(cuerpos) == 2 and cuerpos[0]["temperature"] == 0 and cuerpos[0]["model"] == "modelo-x"
