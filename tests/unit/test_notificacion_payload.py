"""Payload C3: asunto, mensaje y enlace para los 3 tipos (sin red ni base de datos)."""

from datetime import datetime, timezone

import pytest

from app.config import get_settings
from app.schemas.alerta import IncidenteSalida
from app.schemas.notificacion import PayloadC3
from app.services.notificacion import construir_payload

EXPLICACION_REGLAS = (
    "Explicación generada por reglas (sin LLM). El host 10.0.0.3 superó 3 umbral(es) de detección "
    "(M1, M2, M3). El patrón es coherente con movimiento lateral; Ryu-IA mantiene el host aislado."
)


@pytest.fixture(autouse=True)
def panel(monkeypatch):
    monkeypatch.setenv("PANEL_URL", "http://panel.test:8081/")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _incidente(**cambios) -> IncidenteSalida:
    datos = {
        "id": 17,
        "host_origen": "10.0.0.3",
        "dpid_switch": "0x0000000000000001",
        "alerta_nivel": "CRITICO",
        "veredicto": "confirmado",
        "severidad": "critica",
        "confianza": 0.6,
        "tecnica_mitre": "T1021.002",
        "explicacion": EXPLICACION_REGLAS,
        "evidencias": [],
        "recomendacion_operador": "Revisar.",
        "fuente_explicacion": "reglas",
        "t_ryu": datetime(2026, 10, 10, 20, 31, 5, 123000, tzinfo=timezone.utc),
        "t_recibido": datetime(2026, 10, 10, 20, 31, 6, tzinfo=timezone.utc),
        "t_explicado": None,
        "t_notificado": None,
        "t_desbloqueado": None,
        "contador_alertas": 1,
    }
    return IncidenteSalida.model_validate({**datos, **cambios})


def _valida_c3(payload: PayloadC3) -> None:
    assert PayloadC3.model_validate(payload.model_dump(mode="json")) == payload
    assert 3 <= len(payload.mensaje.splitlines()) <= 6


def test_incidente():
    payload = construir_payload(_incidente(), "incidente")

    _valida_c3(payload)
    assert payload.asunto == "[SDN] Host 10.0.0.3 aislado por posible movimiento lateral"
    assert payload.mensaje == (
        "Veredicto: confirmado | Severidad: critica | Técnica MITRE: T1021.002\n"
        f"Explicación: {EXPLICACION_REGLAS}\n"
        "Alertas agrupadas: 1 | Fuente de la explicación: reglas | Contexto: no utilizado\n"  # Fase 6.7
        "Acción: se mantiene el bloqueo total\n"  # Fase 6.8 (C3: máximo 6 líneas)
        "Hora de detección (UTC): 2026-10-10T20:31:05Z\n"
        "Ryu-IA mantiene el host bloqueado; solo el operador puede desbloquearlo desde el panel."
    )
    assert payload.enlace_panel == "http://panel.test:8081/#/incidentes/17"
    assert payload.model_dump(mode="json")["fecha"] == "2026-10-10T20:31:05Z"
    assert (payload.tipo, payload.incidente_id, payload.host) == ("incidente", 17, "10.0.0.3")
    assert (payload.severidad, payload.veredicto) == ("critica", "confirmado")


def test_explicacion_recortada_a_400_y_en_una_linea():
    payload = construir_payload(_incidente(explicacion="x" * 1000 + "\nlinea extra", fuente_explicacion="llm"), "incidente")

    _valida_c3(payload)
    linea = payload.mensaje.splitlines()[1]
    assert linea == "Explicación: " + "x" * 400
    assert "Fuente de la explicación: LLM" in payload.mensaje


def test_desbloqueo():
    desbloqueo = datetime(2026, 10, 10, 21, 0, 0, tzinfo=timezone.utc)
    payload = construir_payload(_incidente(t_desbloqueado=desbloqueo), "desbloqueo")

    _valida_c3(payload)
    assert payload.asunto == "[SDN] Host 10.0.0.3 desbloqueado por el operador"
    assert payload.mensaje.splitlines()[0] == "El operador desbloqueó el host 10.0.0.3 (incidente 17)."
    assert "Hora del desbloqueo (UTC): 2026-10-10T21:00:00Z" in payload.mensaje
    assert payload.enlace_panel == "http://panel.test:8081/#/incidentes/17"


def test_prueba():
    payload = construir_payload(None, "prueba")

    _valida_c3(payload)
    assert payload.asunto == "[SDN] Prueba de notificación"
    assert (payload.tipo, payload.incidente_id, payload.host) == ("prueba", None, "prueba")
    assert payload.enlace_panel == "http://panel.test:8081/"


def test_incidente_sin_explicar_usa_guiones():
    payload = construir_payload(
        _incidente(veredicto=None, severidad=None, tecnica_mitre=None, explicacion=None, fuente_explicacion=None),
        "incidente",
    )
    _valida_c3(payload)
    assert payload.mensaje.splitlines()[0] == "Veredicto: — | Severidad: — | Técnica MITRE: —"
