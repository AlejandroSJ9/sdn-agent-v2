"""El frontend nunca interpreta texto del servidor como HTML/JS y no carga recursos externos."""

import re
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[2] / "app" / "web"
PROHIBIDOS_JS = ("innerHTML", "outerHTML", "insertAdjacentHTML", "eval(", "document.write")
MODULOS = {"api.js", "ws.js", "estado.js", "bloqueados.js", "incidentes.js", "umbrales.js", "timeline.js",
           "auditoria.js", "main.js", "dom.js", "laboratorio.js", "pruebas.js",  # Fase 6: + laboratorio y pruebas
           "contexto.js", "comparar.js",  # Fase 6.7: contexto usado y comparar corridas
           "politica.js", "recomendaciones.js",  # Fase 6.8: decisión acotada
           "informe.js",  # Fase 6.8b: informe auditable
           "detalle.js", "tablero.js", "grafico.js"}  # rediseño: detalle a página completa y tablero
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


# ---- Imports entre módulos: cada nombre importado debe existir como export real del archivo destino ----
IMPORT_NOMBRADO = re.compile(r'^\s*import\s*\{([^}]*)\}\s*from\s*["\'](\./[^"\']+)["\']', re.MULTILINE)
IMPORT_ESPACIO = re.compile(r'^\s*import\s*\*\s*as\s+\w+\s+from\s*["\'](\./[^"\']+)["\']', re.MULTILINE)
EXPORT_DECLARADO = re.compile(r'^\s*export\s+(?:async\s+)?(?:function\*?|const|let|var|class)\s+([A-Za-z_$][\w$]*)', re.MULTILINE)
EXPORT_LISTA = re.compile(r'^\s*export\s*\{([^}]*)\}', re.MULTILINE)


def _exportados(archivo: Path) -> set[str]:
    texto = archivo.read_text(encoding="utf-8")
    nombres = set(EXPORT_DECLARADO.findall(texto))
    for lista in EXPORT_LISTA.findall(texto):
        nombres |= {n.split(" as ")[-1].strip() for n in lista.split(",") if n.strip()}
    return nombres


def _importados(texto: str) -> list[tuple[str, str]]:
    """(nombre exportado que se pide, módulo) de cada `import { a, b as c } from "./x.js"`."""
    return [(n.split(" as ")[0].strip(), modulo)
            for lista, modulo in IMPORT_NOMBRADO.findall(texto) for n in lista.split(",") if n.strip()]


@pytest.mark.parametrize("archivo", sorted(MODULOS))
def test_cada_import_existe_como_export(archivo):
    texto = (WEB / archivo).read_text(encoding="utf-8")
    for modulo in IMPORT_ESPACIO.findall(texto):
        assert (WEB / modulo).is_file(), f"{archivo} importa {modulo}, que no existe"
    for nombre, modulo in _importados(texto):
        assert (WEB / modulo).is_file(), f"{archivo} importa {modulo}, que no existe"
        assert nombre in _exportados(WEB / modulo), f"{archivo}: {modulo} no exporta {nombre!r}"


def test_el_detector_de_imports_encuentra_un_export_faltante(tmp_path):
    (tmp_path / "a.js").write_text("export function uno() {}\nexport const dos = 2;\nexport { tres as cuatro };\n", encoding="utf-8")
    assert _exportados(tmp_path / "a.js") == {"uno", "dos", "cuatro"}
    pedidos = _importados('import { uno, cinco as c } from "./a.js";\nimport {\n  dos,\n} from "./a.js";\n')
    assert pedidos == [("uno", "./a.js"), ("cinco", "./a.js"), ("dos", "./a.js")]
    assert "cinco" not in _exportados(tmp_path / "a.js")


def test_index_carga_un_modulo_que_existe():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    for ruta in re.findall(r'<script[^>]*src="/static/([^"]+)"', html):
        assert (WEB / ruta).is_file(), ruta
