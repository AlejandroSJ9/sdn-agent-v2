"""El ÚNICO camino del código que desbloquea en Ryu es services/operador.py:desbloquear_host.

Se analiza el código con ast (no con búsqueda de texto): cualquier uso cuyo nombre final sea
"desbloquear" (x.desbloquear, desbloquear(...) importada o con alias) cuenta; las definiciones no.
"""

import ast
from pathlib import Path

RAIZ_APP = Path(__file__).resolve().parents[2] / "app"
ARCHIVO_PERMITIDO = Path("services/operador.py")
FUNCION_PERMITIDA = "desbloquear_host"
NOMBRE = "desbloquear"


def usos_de_desbloquear(codigo: str) -> list[tuple[int, str | None]]:
    """(línea, función que lo contiene) de cada uso de `desbloquear`, incluidos alias de import."""
    arbol = ast.parse(codigo)
    alias = {NOMBRE}
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.ImportFrom):
            alias |= {n.asname or n.name for n in nodo.names if n.name == NOMBRE}

    usos: list[tuple[int, str | None]] = []

    class Visitante(ast.NodeVisitor):
        def __init__(self) -> None:
            self.funciones: list[str] = []

        def visit_FunctionDef(self, nodo: ast.FunctionDef) -> None:  # la definición en sí no cuenta
            self.funciones.append(nodo.name)
            self.generic_visit(nodo)
            self.funciones.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

        def _registrar(self, nodo: ast.AST) -> None:
            usos.append((nodo.lineno, self.funciones[-1] if self.funciones else None))

        def visit_Attribute(self, nodo: ast.Attribute) -> None:  # ryu.desbloquear, cliente.desbloquear(...)
            if nodo.attr == NOMBRE:
                self._registrar(nodo)
            self.generic_visit(nodo)

        def visit_Name(self, nodo: ast.Name) -> None:  # desbloquear(...) o un alias importado
            if nodo.id in alias:
                self._registrar(nodo)

    Visitante().visit(arbol)
    return usos


def test_detector_encuentra_llamada_importada_con_alias():
    codigo = (
        "from app.integrations.ryu import desbloquear as liberar\n"
        "\n"
        "def atajo(ip):\n"
        "    return liberar(ip)\n"
    )
    assert usos_de_desbloquear(codigo) == [(4, "atajo")]


def test_detector_encuentra_atributo_y_nombre_directo_e_ignora_definiciones():
    codigo = (
        "import app.integrations.ryu as r\n"
        "from x import desbloquear\n"
        "def desbloquear_local():\n"
        "    r.ClienteRyu('u', 't').desbloquear('10.0.0.3')\n"
        "def desbloquear(ip):\n"
        "    pass\n"
        "desbloquear('10.0.0.5')\n"
    )
    assert usos_de_desbloquear(codigo) == [(4, "desbloquear_local"), (7, None)]


def test_solo_operador_desbloquear_host_llama_a_desbloquear():
    archivos = sorted(RAIZ_APP.rglob("*.py"))
    assert RAIZ_APP / ARCHIVO_PERMITIDO in archivos
    for archivo in archivos:
        usos = usos_de_desbloquear(archivo.read_text(encoding="utf-8"))
        relativo = archivo.relative_to(RAIZ_APP)
        if relativo == ARCHIVO_PERMITIDO:
            assert [funcion for _, funcion in usos] == [FUNCION_PERMITIDA], f"{relativo}: {usos}"
        else:
            assert usos == [], f"{relativo} usa desbloquear fuera del camino permitido: {usos}"
