"""Incidentes sembrados del laboratorio (variante con contexto): siembra literal de la v1 y sus tres garantías."""

import csv
import io
import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db import repository, repository_contexto, repository_laboratorio, repository_operador
from app.db.models import Incidente, Notificacion, ResultadoCaso
from app.schemas.alerta import AlertaEntrada
from app.schemas.laboratorio import AmbitoPrueba
from app.services import alertas, recuperacion

V1 = {c["id"]: c for c in json.loads(
    (Path(__file__).resolve().parents[2] / "docs/referencia/escenarios_v1.json").read_text(encoding="utf-8"))["casos"]}


def _corrida(cliente, cabecera, **cuerpo) -> dict:
    respuesta = cliente.post("/api/laboratorio/corridas", json=cuerpo, headers=cabecera)
    assert respuesta.status_code == 202, respuesta.text
    return cliente.get(f"/api/laboratorio/corridas/{respuesta.json()['corrida_id']}", headers=cabecera).json()


def _sembrados(motor) -> list[Incidente]:
    with Session(motor) as sesion:
        return list(sesion.scalars(select(Incidente).where(Incidente.sembrado.is_(True)).order_by(Incidente.id)))


def _sembrar_reciente(host="10.0.10.3") -> tuple[int, AmbitoPrueba]:
    """Un sembrado ABIERTO de ahora mismo: el peor caso para la agrupación."""
    corrida = repository_laboratorio.crear_corrida(["AT-01"], None, False, False, 1, 0)
    ambito = AmbitoPrueba(corrida, "AT-01", 1)
    sembrado = repository_contexto.crear_sembrado(ambito, host, datetime.now(timezone.utc), "confirmado", 0.9, None,
                                                  ["M1", "M3", "M5"], "escenarios_v1:prueba")
    return sembrado, ambito


# ---- Siembra desde el laboratorio ----

def test_corrida_con_contexto_siembra_literal_y_acierta(cliente, bd_temporal, cabecera_operador):
    corrida = _corrida(cliente, cabecera_operador, casos=["LG-05", "DU-01", "AT-09", "AT-10"], variante="con_contexto")
    assert (corrida["variante"], corrida["version_prompt"]) == ("con_contexto", "v3.1-contexto-historial")
    resultados = {r["caso_id"]: r for r in corrida["resultados"]}

    assert all(r["decidible"] for r in resultados.values())  # decidibles con contexto
    assert resultados["LG-05"]["veredicto_obtenido"] == "indeterminado" and resultados["LG-05"]["acierto"]
    assert resultados["DU-01"]["detalle"]["agrupacion_ok"] and resultados["DU-01"]["veredicto_obtenido"] == "confirmado"
    assert resultados["AT-10"]["veredicto_obtenido"] == "confirmado"

    sembrados = _sembrados(bd_temporal)
    assert len(sembrados) == 3 + 1 + 1 + 4  # incidentes_previos de LG-05, DU-01, AT-09 y AT-10
    assert all(s.t_explicado is None and s.variante is None for s in sembrados)  # nunca se explican
    lg05 = [s for s in sembrados if s.caso_id == "LG-05"]
    previos = V1["LG-05"]["contexto"]["incidentes_previos"]
    assert sorted(s.confianza for s in lg05) == sorted(p["confianza"] / 100 for p in previos)
    assert {(s.veredicto, s.clasificacion_operador) for s in lg05} == {("falso_positivo", "falso_positivo")}

    detalle = cliente.get(f"/api/incidentes/{resultados['LG-05']['incidente_id']}", headers=cabecera_operador).json()
    contexto = detalle["contexto_usado"]
    assert [p["minutos_antes"] for p in contexto["historial_host"]] == [10080, 20160, 30240]
    assert contexto["resumen"]["falsos_positivos_operador"] == 3
    at09 = cliente.get(f"/api/incidentes/{resultados['AT-09']['incidente_id']}", headers=cabecera_operador).json()
    assert at09["contexto_usado"]["actividad_red"][0]["host"] == "10.0.10.3"

    with Session(bd_temporal) as sesion:
        ids_sembrados = {s.id for s in sembrados}
        assert not sesion.scalars(select(Notificacion).where(Notificacion.incidente_id.in_(ids_sembrados))).all()
        assert not sesion.scalars(select(ResultadoCaso).where(ResultadoCaso.incidente_id.in_(ids_sembrados))).all()


def test_corrida_sin_contexto_no_siembra(cliente, bd_temporal, cabecera_operador):
    corrida = _corrida(cliente, cabecera_operador, casos=["LG-05"])
    assert corrida["variante"] == "sin_contexto" and _sembrados(bd_temporal) == []
    assert corrida["resultados"][0]["decidible"] is False


# ---- Garantía 1: la agrupación nunca considera sembrados ----

def test_una_alerta_nueva_jamas_se_agrupa_con_un_sembrado(bd_temporal, payload_c1):
    sembrado, ambito = _sembrar_reciente("10.0.0.3")
    agendados = []
    incidente = alertas.recibir(AlertaEntrada.model_validate(payload_c1), lambda *a: agendados.append(a), ambito=ambito)
    assert incidente != sembrado and len(agendados) == 1  # incidente nuevo, con su pipeline
    assert repository.buscar_incidente_abierto_reciente("10.0.0.3", 3600, ambito) == incidente


# ---- Garantía 2: la recuperación al arrancar nunca reprocesa sembrados ----

def test_recuperacion_no_reprocesa_sembrados(bd_temporal):
    sembrado, _ = _sembrar_reciente()
    with Session(bd_temporal) as sesion, sesion.begin():  # aunque alguien lo marcara como real por error
        sesion.execute(update(Incidente).where(Incidente.id == sembrado).values(origen="real", corrida_id=None))
    assert repository_operador.incidentes_reales_sin_explicar() == []
    assert recuperacion.recuperar_al_arrancar(lambda *a: None)["incidentes_reprocesados"] == []


# ---- Garantía 3: los sembrados no aparecen en tabla, resumen, cabecera ni exportaciones ----

def test_sembrados_invisibles_salvo_como_contexto(cliente, bd_temporal, cabecera_operador):
    corrida = _corrida(cliente, cabecera_operador, casos=["LG-05"], variante="con_contexto")
    ids_sembrados = {s.id for s in _sembrados(bd_temporal)}
    assert len(ids_sembrados) == 3

    for origen in ("real", "prueba", "todos"):
        lista = cliente.get(f"/api/incidentes?origen={origen}&limite=200", headers=cabecera_operador).json()
        assert not ids_sembrados & {i["id"] for i in lista}
        exportado = cliente.get(f"/api/export/incidentes.csv?origen={origen}", headers=cabecera_operador).text
        assert not ids_sembrados & {int(f["id"]) for f in csv.DictReader(io.StringIO(exportado))}
    resumen = cliente.get("/api/resumen", headers=cabecera_operador).json()
    previos = ("incidentes_abiertos", "incidentes_hoy", "notificaciones_fallidas_hoy", "ultimo_incidente_id")
    assert {k: resumen[k] for k in previos} == {"incidentes_abiertos": 0, "incidentes_hoy": 0,
                                                "notificaciones_fallidas_hoy": 0, "ultimo_incidente_id": None}
    assert sum(resumen["veredictos_hoy"].values()) == 0  # los agregados del tablero tampoco los cuentan
    assert sum(h["cantidad"] for h in resumen["incidentes_por_hora"]) == 0
    caso = corrida["resultados"][0]["incidente_id"]
    detalle = cliente.get(f"/api/incidentes/{caso}", headers=cabecera_operador).json()
    assert detalle["contexto_usado"]["resumen"]["total_previos"] == 3  # visibles solo aquí
