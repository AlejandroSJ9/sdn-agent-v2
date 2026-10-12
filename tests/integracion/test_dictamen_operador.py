"""El dictamen respeta la clasificación del operador por encima del veredicto del agente (C6).

Defecto corregido: un "confirmado" del agente que el operador clasificó como falso_positivo contaba como
"confirmado reciente" (agravante) e impedía que el atenuante se activara nunca.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import update

from app.db import repository
from app.db.models import Incidente
from app.services.contexto import contexto_para
from app.services.dictamen_historial import dictaminar

UMBRALES = ["M1", "M2", "M3"]


def _incidente(host, minutos_antes, umbrales=UMBRALES, **campos) -> int:
    t = datetime.now(timezone.utc) - timedelta(minutes=minutos_antes)
    incidente_id = repository.crear_incidente(host, None, None, t, t, {"umbrales_superados": list(umbrales)})
    if campos:
        with repository.SesionLocal.begin() as sesion:
            sesion.execute(update(Incidente).where(Incidente.id == incidente_id).values(**campos))
    return incidente_id


def _dictamen_del_actual(host="10.0.0.3") -> dict:
    return dictaminar(contexto_para(_incidente(host, 0)), UMBRALES)


def test_confirmados_recientes_clasificados_falso_positivo_dan_atenuante(bd_temporal):
    """El caso exacto de la verificación end-to-end."""
    for minutos in (10, 20):
        _incidente("10.0.0.3", minutos, veredicto="confirmado", clasificacion_operador="falso_positivo")
    assert _dictamen_del_actual() == {
        "tipo": "atenuante", "veredicto": "indeterminado",
        "motivo": "2 clasificaciones falso_positivo del operador con los mismos umbrales (M1, M2, M3)"}


def test_confirmado_reciente_sin_clasificacion_sigue_siendo_agravante(bd_temporal):
    _incidente("10.0.0.3", 10, veredicto="confirmado")
    _incidente("10.0.0.3", 20, veredicto="confirmado", clasificacion_operador="falso_positivo")
    dictamen = _dictamen_del_actual()
    assert dictamen["tipo"] == "agravante" and "confirmado en los últimos 60 min" in dictamen["motivo"]


def test_campana_no_cuenta_hosts_clasificados_falso_positivo(bd_temporal):
    _incidente("10.0.0.7", 5, veredicto="confirmado")
    _incidente("10.0.0.8", 8, veredicto="confirmado", clasificacion_operador="falso_positivo")
    contexto = contexto_para(_incidente("10.0.0.3", 0))
    assert {a["host"]: a.get("clasificacion_operador") for a in contexto["actividad_red"]} == {"10.0.0.7": None, "10.0.0.8": "falso_positivo"}
    assert dictaminar(contexto, UMBRALES)["tipo"] is None
    _incidente("10.0.0.9", 9, veredicto="confirmado")  # un segundo host confirmado SIN clasificación: sí hay campaña
    assert _dictamen_del_actual()["motivo"] == "2 hosts confirmados en la actividad de la red"


def test_amenaza_contenida_activa_agravante_aunque_el_agente_no_confirmara(bd_temporal):
    _incidente("10.0.0.3", 3 * 1440, veredicto="indeterminado", clasificacion_operador="amenaza_contenida")
    _incidente("10.0.0.3", 10, veredicto="falso_positivo", clasificacion_operador="falso_positivo")
    _incidente("10.0.0.3", 20, veredicto="falso_positivo", clasificacion_operador="falso_positivo")
    dictamen = _dictamen_del_actual()
    assert dictamen == {"tipo": "agravante", "veredicto": "confirmado",
                        "motivo": "1 amenaza(s) contenida(s) por el operador en el host"}  # gana al atenuante
