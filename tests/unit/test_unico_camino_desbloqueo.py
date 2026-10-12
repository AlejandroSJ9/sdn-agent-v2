"""El ÚNICO camino del código que desbloquea en Ryu es services/operador.py:desbloquear_host.

Se analiza el código con ast (no con búsqueda de texto): cualquier uso cuyo nombre final sea
"desbloquear" (x.desbloquear, desbloquear(...) importada o con alias) cuenta; las definiciones no.
"""

import ast
from pathlib import Path

import pytest

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


# ---- Fase 6.7: el contexto nunca bloquea, desbloquea ni cambia el estado de un incidente ----

ESCRITURAS_SQL = {"begin", "add", "merge", "delete", "update", "insert"}
ACCIONES_PROHIBIDAS_CONTEXTO = {"desbloquear", "desbloquear_host", "bloquear", "marcar_desbloqueados",
                                "guardar_explicacion", "crear_sembrado", "crear_incidente", "registrar", "notificar"}


def _nombres_usados(nodo: ast.AST) -> set[str]:
    nombres = set()
    for hijo in ast.walk(nodo):
        if isinstance(hijo, ast.Attribute):
            nombres.add(hijo.attr)
        elif isinstance(hijo, ast.Name):
            nombres.add(hijo.id)
    return nombres


def test_servicio_de_contexto_solo_lee():
    arbol = ast.parse((RAIZ_APP / "services/contexto.py").read_text(encoding="utf-8"))
    importados = {(n.module, a.name) for n in ast.walk(arbol) if isinstance(n, ast.ImportFrom) for a in n.names}
    assert {m for m, _ in importados} <= {"datetime", "typing", "app.db", "app.services", "app.services.prompt"}
    assert ("app.db", "repository_contexto") in importados and not any(m == "app.integrations.ryu" for m, _ in importados)
    usados = _nombres_usados(arbol)
    assert not usados & ACCIONES_PROHIBIDAS_CONTEXTO, usados & ACCIONES_PROHIBIDAS_CONTEXTO
    assert not usados & ESCRITURAS_SQL


def test_consultas_de_contexto_solo_leen():
    """En repository_contexto solo crear_sembrado (exclusivo del laboratorio) escribe."""
    arbol = ast.parse((RAIZ_APP / "db/repository_contexto.py").read_text(encoding="utf-8"))
    funciones = [n for n in arbol.body if isinstance(n, ast.FunctionDef)]
    assert {f.name for f in funciones} >= {"referencia", "incidentes_en_ventana", "crear_sembrado"}
    for funcion in funciones:
        if funcion.name != "crear_sembrado":
            assert not _nombres_usados(funcion) & ESCRITURAS_SQL, funcion.name


def test_el_detector_de_escrituras_funciona():
    codigo = "def f(sesion):\n    with SesionLocal.begin() as s:\n        s.add(x)\n"
    assert _nombres_usados(ast.parse(codigo)) & ESCRITURAS_SQL == {"begin", "add"}


# ---- Fase 6.8: caminos autorizados hacia Ryu ----

ACCIONES_A6 = {"cuarentena", "bloqueo_total"}
ARCHIVO_ACCIONES = Path("services/acciones.py")
# Módulos de LLM, prompt, contexto y decisión pura: jamás importan el cliente de Ryu.
SIN_RYU = ("integrations/llm.py", "services/prompt.py", "services/contexto.py", "services/explicacion_reglas.py",
           "services/politica.py", "db/repository_contexto.py")


def usos_de_acciones_a6(codigo: str) -> list[tuple[int, str | None]]:
    """(línea, función) de cada llamada x.cuarentena(...) o x.bloqueo_total(...)."""
    usos: list[tuple[int, str | None]] = []
    funciones: list[str] = []

    class Visitante(ast.NodeVisitor):
        def visit_FunctionDef(self, nodo: ast.FunctionDef) -> None:
            funciones.append(nodo.name)
            self.generic_visit(nodo)
            funciones.pop()

        def visit_Call(self, nodo: ast.Call) -> None:
            if isinstance(nodo.func, ast.Attribute) and nodo.func.attr in ACCIONES_A6:
                usos.append((nodo.lineno, funciones[-1] if funciones else None))
            self.generic_visit(nodo)

    Visitante().visit(ast.parse(codigo))
    return usos


def test_detector_de_acciones_a6():
    codigo = "def f(c):\n    c.cuarentena('10.0.0.3')\n    return c.bloqueo_total('10.0.0.3')\n"
    assert usos_de_acciones_a6(codigo) == [(2, "f"), (3, "f")]


def test_cuarentena_y_bloqueo_total_solo_desde_acciones_ejecutar():
    for archivo in sorted(RAIZ_APP.rglob("*.py")):
        relativo = archivo.relative_to(RAIZ_APP)
        if relativo == Path("integrations/ryu.py"):
            continue  # ahí se DEFINEN (C2)
        usos = usos_de_acciones_a6(archivo.read_text(encoding="utf-8"))
        if relativo == ARCHIVO_ACCIONES:
            assert {funcion for _, funcion in usos} == {"ejecutar"}, usos
        else:
            assert usos == [], f"{relativo} llama a cuarentena/bloqueo_total fuera de acciones.py: {usos}"


@pytest.mark.parametrize("relativo", SIN_RYU)
def test_llm_prompt_y_contexto_no_importan_el_cliente_de_ryu(relativo):
    arbol = ast.parse((RAIZ_APP / relativo).read_text(encoding="utf-8"))
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.ImportFrom):
            nombres = {a.name for a in nodo.names}
            assert nodo.module != "app.integrations.ryu", relativo
            assert not (nodo.module == "app.integrations" and "ryu" in nombres), relativo
            assert not (nodo.module == "app.services" and nombres & {"operador", "acciones"}), relativo
        if isinstance(nodo, ast.Import):
            assert not any(a.name.startswith("app.integrations.ryu") for a in nodo.names), relativo
