"""Rutas del laboratorio (operador): escenarios, corridas, pytest desde el panel y exportaciones."""

from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response, status

from app.api.routes_alertas import verificar_token_operador
from app.schemas.laboratorio import CorridaEntrada, PytestEntrada
from app.services import comparacion, laboratorio, pytest_panel, resultados_lab

router = APIRouter(prefix="/api", dependencies=[Depends(verificar_token_operador)])


def _csv(contenido: str, nombre: str) -> Response:
    return Response(
        contenido,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{nombre}"'},
    )


def _corrida_o_404(corrida_id: int) -> dict:
    corrida = laboratorio.obtener_corrida(corrida_id)
    if corrida is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Corrida no encontrada")
    return corrida


@router.get("/laboratorio/escenarios")
def escenarios() -> list[dict]:
    return laboratorio.listar_escenarios()


@router.post("/laboratorio/corridas", status_code=status.HTTP_202_ACCEPTED)
def iniciar_corrida(entrada: CorridaEntrada, tareas: BackgroundTasks) -> dict:
    try:
        return laboratorio.iniciar_corrida(
            entrada.casos, entrada.conjunto, entrada.usar_llm, entrada.repeticiones, entrada.enviar_correos,
            tareas.add_task, variante=entrada.variante, modo_decision=entrada.modo_decision,
            umbral_confianza=entrada.umbral_confianza,
        )
    except (laboratorio.CasoDesconocido, laboratorio.ModoRetirado) as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(error)) from error
    except laboratorio.CorridaRechazada as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error
    except laboratorio.CorridaEnCurso as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error


@router.get("/laboratorio/corridas")
def listar_corridas() -> list[dict]:
    return laboratorio.listar_corridas()


def _ids_corridas(corridas: Annotated[str, Query(max_length=100, description="ids separados por coma")]) -> list[int]:
    try:
        return [int(parte) for parte in corridas.split(",") if parte.strip()]
    except ValueError:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "corridas debe ser una lista de ids: 1,2,3") from None


def _comparar(funcion, ids: list[int]):
    try:
        return funcion(ids)
    except comparacion.ComparacionInvalida as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(error)) from error


@router.get("/laboratorio/comparar")
def comparar_corridas(ids: Annotated[list[int], Depends(_ids_corridas)]) -> dict:
    return _comparar(comparacion.comparar, ids)


@router.get("/export/comparar.json")
def exportar_comparacion_json(ids: Annotated[list[int], Depends(_ids_corridas)]) -> dict:
    """Incluye el campo "hipotesis" (H1–H3 y la limitación) para que queden junto a los resultados."""
    return _comparar(comparacion.exportar_json, ids)


@router.get("/export/comparar.csv")
def exportar_comparacion_csv(ids: Annotated[list[int], Depends(_ids_corridas)]) -> Response:
    return _csv(_comparar(comparacion.exportar_csv, ids), "comparacion.csv")


@router.get("/laboratorio/corridas/{corrida_id}")
def obtener_corrida(corrida_id: int) -> dict:
    return _corrida_o_404(corrida_id)


@router.post("/laboratorio/pytest", status_code=status.HTTP_202_ACCEPTED)
def ejecutar_pytest(entrada: PytestEntrada, tareas: BackgroundTasks) -> dict:
    try:
        return {"ejecucion_id": pytest_panel.lanzar(entrada.carpeta, tareas.add_task)}
    except pytest_panel.PytestEnCurso as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error


@router.get("/laboratorio/pytest/ultimo")
def ultimo_pytest() -> dict | None:
    return pytest_panel.ultimo()


@router.get("/export/incidentes.csv")
def exportar_incidentes(origen: Literal["real", "prueba", "todos"] = "real") -> Response:
    return _csv(resultados_lab.csv_incidentes(origen), f"incidentes_{origen}.csv")


@router.get("/export/corrida/{corrida_id}.json")
def exportar_corrida_json(corrida_id: int) -> dict:
    corrida = _corrida_o_404(corrida_id)
    return {"corrida": {k: v for k, v in corrida.items() if k != "resultados"},
            "resumen": corrida["resumen"] or resultados_lab.resumen_corrida(corrida_id),
            "resultados": corrida["resultados"]}


@router.get("/export/corrida/{corrida_id}.csv")
def exportar_corrida_csv(corrida_id: int) -> Response:
    _corrida_o_404(corrida_id)
    return _csv(resultados_lab.csv_corrida(corrida_id), f"corrida_{corrida_id}.csv")
