"""Cupo diario del LLM sobre uso_llm (SQLite temporal)."""

import threading

import pytest
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import repository
from app.db.models import UsoLlm
from app.services import presupuesto


@pytest.fixture
def cupo_3(bd_temporal, monkeypatch):
    monkeypatch.setenv("LLM_CUPO_DIARIO", "3")
    get_settings.cache_clear()
    dia = {"hoy": "2026-10-10"}
    monkeypatch.setattr(presupuesto, "_hoy_utc", lambda: dia["hoy"])
    return dia


def test_cuenta_por_dia_y_se_niega_al_llegar_al_cupo(cupo_3):
    assert [presupuesto.puede_llamar() for _ in range(4)] == [True, True, True, False]
    assert repository.peticiones_llm_del_dia("2026-10-10") == 3


def test_reinicia_al_cambiar_el_dia(cupo_3):
    for _ in range(3):
        presupuesto.puede_llamar()
    assert presupuesto.puede_llamar() is False
    cupo_3["hoy"] = "2026-10-11"
    assert presupuesto.puede_llamar() is True
    assert repository.peticiones_llm_del_dia("2026-10-11") == 1


def test_registrar_uso_suma_tokens_fallidas_y_reintento(cupo_3, bd_temporal):
    presupuesto.puede_llamar()
    presupuesto.registrar_uso(100, 50, 800, exito=False, peticiones=2)
    with Session(bd_temporal) as sesion:
        uso = sesion.get(UsoLlm, "2026-10-10")
    assert (uso.peticiones, uso.fallidas, uso.tokens_entrada, uso.tokens_salida) == (2, 1, 100, 50)


def test_reserva_segura_con_llamadas_simultaneas(cupo_3):
    resultados: list[bool] = []
    hilos = [threading.Thread(target=lambda: resultados.append(presupuesto.puede_llamar())) for _ in range(10)]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join()
    assert resultados.count(True) == 3
    assert repository.peticiones_llm_del_dia("2026-10-10") == 3
