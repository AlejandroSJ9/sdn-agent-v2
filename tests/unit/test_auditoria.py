"""Cadena de hashes de la auditoría (SQLite temporal, sin servidor)."""

from sqlalchemy import text

from app.services import auditoria


def _registrar_tres() -> None:
    auditoria.registrar("evento_a", {"n": 1})
    auditoria.registrar("evento_b", {"n": 2, "texto": "ñandú"})
    auditoria.registrar("evento_c", {})


def test_cadena_integra(bd_temporal):
    _registrar_tres()
    assert auditoria.verificar_cadena() == {"integra": True, "filas": 3, "primera_rota": None}
    with bd_temporal.connect() as conexion:
        primera = conexion.execute(text("SELECT hash_previo FROM auditoria ORDER BY id LIMIT 1")).scalar_one()
    assert primera == "0" * 64


def test_alteracion_directa_en_la_bd_se_detecta(bd_temporal):
    _registrar_tres()
    with bd_temporal.begin() as conexion:  # quien tiene acceso a la BD puede quitar los triggers (Fase 6.5)
        conexion.execute(text("DROP TRIGGER auditoria_sin_update"))
        conexion.execute(text("""UPDATE auditoria SET datos = '{"n": 99}' WHERE id = 2"""))
    assert auditoria.verificar_cadena() == {"integra": False, "filas": 3, "primera_rota": 2}
