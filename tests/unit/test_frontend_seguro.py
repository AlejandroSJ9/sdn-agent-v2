"""El frontend nunca interpreta texto del servidor como HTML/JS y no carga recursos externos."""

import re
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[2] / "app" / "web"
PROHIBIDOS_JS = ("innerHTML", "outerHTML", "insertAdjacentHTML", "eval(", "document.write")
MODULOS = {"api.js", "ws.js", "estado.js", "bloqueados.js", "incidentes.js", "umbrales.js", "timeline.js",
           "auditoria.js", "main.js", "dom.js", "laboratorio.js", "pruebas.js"}  # Fase 6: + laboratorio y pruebas
EXTERNO = re.compile(r"""(?:src|href|action)\s*=\s*["']?\s*(?:https?:)?//""", re.IGNORECASE)


def _js() -> list[Path]:
    return sorted(WEB.glob("*.js"))


def test_estan_todos_los_modulos_y_ninguno_pasa_de_250_lineas():
    assert {archivo.name for archivo in _js()} == MODULOS
    for archivo in _js():
        assert len(archivo.read_text(encoding="utf-8").splitlines()) <= 250, archivo.name


@pytest.mark.parametrize("archivo", sorted(MODULOS))
def test_js_sin_apis_que_interpretan_html_o_codigo(archivo):
    texto = (WEB / archivo).read_text(encoding="utf-8")
    for prohibido in PROHIBIDOS_JS:
        assert prohibido not in texto, f"{archivo} contiene {prohibido!r}"


def test_index_sin_recursos_externos():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    assert not EXTERNO.search(html), EXTERNO.search(html)
    assert "@import" not in html


def test_css_sin_recursos_externos():
    css = (WEB / "styles.css").read_text(encoding="utf-8")
    assert "@import" not in css
    assert not re.search(r"url\(\s*['\"]?\s*(?:https?:)?//", css, re.IGNORECASE)


def test_el_detector_encuentra_un_recurso_externo():
    assert EXTERNO.search('<script src="https://cdn.ejemplo.com/x.js"></script>')
    assert EXTERNO.search("<link href=//cdn.ejemplo.com/x.css>")
    assert not EXTERNO.search('<script type="module" src="/static/main.js"></script>')
