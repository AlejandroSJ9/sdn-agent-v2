"""Correo en lenguaje simple (Fase D4): asunto, 4 bloques, bloque 3 corto, referencia MITRE y formatos anteriores."""

import itertools

import pytest
from test_informe import CASOS, _armar
from test_notificacion_payload import _incidente, panel  # noqa: F401  (panel: fixture autouse con PANEL_URL)

from app.services import escalera, explicacion_simple as es
from app.services.informe import CATALOGO_MITRE
from app.services.notificacion import FRASE_MITRE, SUFIJO_RESPALDO, TITULOS, TITULO_MITRE, construir_payload
from app.services.verificador import JERGA

SIN = {"tipo": None, "veredicto": None, "motivo": None}
AGRAVANTE = {"tipo": "agravante", "veredicto": "confirmado", "motivo": "3 hosts confirmados en la actividad de la red"}
ATENUANTE = {"tipo": "atenuante", "veredicto": "indeterminado", "motivo": "3 clasificaciones falso_positivo del operador"}
SIMPLE = {"que_paso": "Este equipo intentó saltar a otros equipos de la red y el sistema lo aisló.",
          "por_que": ["Intentó conectarse a 9 equipos distintos en 30 segundos; lo esperado es 8 o menos.",
                      "Abrió 42 conexiones nuevas en 30 segundos; lo esperado es 20 o menos."],
          "que_recomendamos": "versión larga (panel)", "podria_equivocarnos": "Podría ser una copia de seguridad.",
          "fuentes": {"que_paso": "llm", "por_que": "llm", "podria_equivocarnos": "llm"}, "reemplazos": []}


def _decidir(veredicto, puntaje, dictamen=SIN):
    confianza = escalera.calcular_confianza(veredicto, puntaje, 135 if puntaje is not None else None, ["M1", "M2", "M3"],
                                            "verificado", "x" * 40)
    return escalera.decidir(confianza, dictamen, 0.70, 0.95), confianza


def _informe(veredicto="confirmado", puntaje=None, dictamen=SIN, tecnica="T1021.002") -> dict:
    resultado, confianza = _decidir(veredicto, puntaje, dictamen)
    nombre, tactica = CATALOGO_MITRE.get(tecnica, (None, None))
    return {"version_informe": "1.2", "explicacion_simple": SIMPLE,
            "mitre": {"tecnica_id": tecnica, "nombre": nombre, "tactica": tactica},
            "razonamiento": {"dictamen_historial": dictamen},
            "trazabilidad_y_accion": {"accion_propuesta": resultado["accion_recomendada"], "escalera": resultado,
                                      "confianza_calculada": confianza}}


def _payload(veredicto="confirmado", puntaje=None, dictamen=SIN, fuente="llm", tecnica="T1021.002"):
    incidente = _incidente(veredicto=veredicto, fuente_explicacion=fuente, motivo_respaldo="timeout" if fuente == "reglas" else None,
                           informe=_informe(veredicto, puntaje, dictamen, tecnica))
    return construir_payload(incidente, "incidente")


@pytest.mark.parametrize(("veredicto", "puntaje", "dictamen", "asunto"), [
    ("indeterminado", 60, SIN, "actividad sospechosa. Recomendación: mantenerlo aislado"),
    ("falso_positivo", 60, SIN, "probable falsa alarma. Recomendación: devolverle la red a velocidad reducida"),
    ("confirmado", None, SIN, "posible ransomware. Recomendación: cuarentena de puertos"),
    ("confirmado", 135, SIN, "posible ransomware. Recomendación: cuarentena en una red separada"),
    ("confirmado", 135, AGRAVANTE, "posible ransomware. Recomendación: mantenerlo aislado"),
])
def test_asunto_dice_que_paso_y_que_se_recomienda(veredicto, puntaje, dictamen, asunto):
    assert _payload(veredicto, puntaje, dictamen).asunto == f"[SDN] Equipo 10.0.0.3 aislado: {asunto}"


def test_respaldo_conserva_sufijo_y_linea_del_motivo():
    payload = _payload(fuente="reglas")
    assert payload.asunto.endswith(SUFIJO_RESPALDO)
    lineas = payload.mensaje.splitlines()
    assert lineas[:3] == ["Explicación de respaldo por reglas: el modelo no respondió a tiempo. Revisar en el panel.", "", "QUÉ PASÓ"]


def test_cuerpo_en_bloques_separados_por_linea_en_blanco_y_sin_enlace():
    payload = _payload()
    assert payload.mensaje.split("\n\n") == [
        f"QUÉ PASÓ\n{SIMPLE['que_paso']}",
        "POR QUÉ CREEMOS QUE ES ESTO\n" + "\n".join(f"- {f}" for f in SIMPLE["por_que"]),
        "QUÉ RECOMENDAMOS\nCuarentena de puertos: conserva lo básico de la red, sin caminos para saltar a otros equipos. "
        "Confianza media. Solo se aplica si un administrador la aprueba en el panel.",
        f"QUÉ PODRÍA HACER QUE ESTEMOS EQUIVOCADOS\n{SIMPLE['podria_equivocarnos']}",
        f"{TITULO_MITRE}\nT1021.002 - Remote Services: SMB/Windows Admin Shares (Lateral Movement)\n{FRASE_MITRE['T1021.002']}",
    ]
    assert "http" not in payload.mensaje and payload.enlace_panel == "http://panel.test:8081/#/incidentes/17"
    assert "versión larga" not in payload.mensaje  # el correo usa el bloque 3 corto, no el del panel
    assert len(payload.mensaje.splitlines()) <= 25


def test_sin_tecnica_no_hay_seccion_mitre():
    mensaje = _payload(tecnica=None).mensaje
    assert TITULO_MITRE not in mensaje and mensaje.splitlines()[-1] == SIMPLE["podria_equivocarnos"]


def test_una_frase_simple_por_cada_tecnica_del_catalogo():
    assert set(FRASE_MITRE) == set(CATALOGO_MITRE)
    for tecnica, frase in FRASE_MITRE.items():
        assert frase.startswith("Técnica en la que un atacante") and not JERGA.search(frase), tecnica
        assert _payload(tecnica=tecnica).mensaje.splitlines()[-1] == frase


def test_los_4_bloques_no_tienen_jerga_y_mitre_solo_en_su_seccion():
    for veredicto, puntaje, dictamen in itertools.product(("confirmado", "falso_positivo", "indeterminado"), (None, 60, 135),
                                                         (SIN, AGRAVANTE, ATENUANTE)):
        bloques = _payload(veredicto, puntaje, dictamen).mensaje.split(TITULO_MITRE)[0]
        assert not JERGA.search(bloques), bloques
        assert [l for l in bloques.splitlines() if l.isupper()] == list(TITULOS)


def test_bloque3_corto_cierre_segun_accion_y_200_caracteres_en_todas_las_combinaciones():
    for veredicto, puntaje, dictamen in itertools.product(("confirmado", "falso_positivo", "indeterminado"),
                                                         (None, 0, 60, 100, 135), (SIN, AGRAVANTE, ATENUANTE)):
        resultado, confianza = _decidir(veredicto, puntaje, dictamen)
        texto = es.recomendacion_corta(resultado, confianza, dictamen)
        assert len(texto) <= es.MAX_CORTA and not any(c.isdigit() for c in texto) and not JERGA.search(texto), texto
        cierre = es.CIERRE_MANTENER if resultado["accion_recomendada"] == "mantener_drop" else es.CIERRE_CORTO_RELAJAR
        assert texto.endswith(cierre), texto


@pytest.mark.parametrize(("veredicto", "puntaje", "dictamen", "inicio"), [
    ("confirmado", 135, AGRAVANTE, es.CORTA_AGRAVANTE),
    ("confirmado", None, ATENUANTE, es.CORTA_CONTRADICCION),
    ("falso_positivo", 60, ATENUANTE, es.CORTA["limitar_tasa"].format(nivel="media") + es.CORTA_EXTRA["atenuante"]),
    ("indeterminado", 60, AGRAVANTE, es.CORTA_INDETERMINADO + es.CORTA_EXTRA["agravante"]),
    ("confirmado", 0, SIN, es.CORTA_BAJA.format(veredicto="parece un ataque")),
])
def test_bloque3_corto_frase_del_dictamen(veredicto, puntaje, dictamen, inicio):
    resultado, confianza = _decidir(veredicto, puntaje, dictamen)
    assert es.recomendacion_corta(resultado, confianza, dictamen).startswith(inicio)


def test_informes_sin_explicacion_simple_desbloqueo_y_prueba_no_cambian():
    informe_11 = _armar(CASOS["LG-05"]["payload"])  # informe 1.1 real, sin explicacion_simple
    assert informe_11["version_informe"] == "1.1"
    payload = construir_payload(_incidente(fuente_explicacion="llm", informe=informe_11), "incidente")
    assert payload.asunto == "[SDN] Host 10.0.0.3 aislado por posible movimiento lateral"
    assert [l.split(":")[0] for l in payload.mensaje.splitlines()] == [
        "Evidencia", "MITRE", "Razonamiento", "Acción", "Informe completo"]
    assert construir_payload(_incidente(), "desbloqueo").asunto == "[SDN] Host 10.0.0.3 desbloqueado por el operador"
    assert construir_payload(None, "prueba").asunto == "[SDN] Prueba de notificación"
