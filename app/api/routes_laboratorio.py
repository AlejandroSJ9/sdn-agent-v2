"""Rutas del laboratorio (operador): escenarios, corridas, pytest desde el panel y exportaciones."""

from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response, status

from app.api.routes_alertas import verificar_token_operador
from app.schemas.laboratorio import CorridaEntrada, PytestEntrada
from app.services import laboratorio, pytest_panel, resultados_lab

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
            tareas.add_task,
        )
    except laboratorio.CasoDesconocido as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(error)) from error
    except laboratorio.CorridaRechazada as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error
    except laboratorio.CorridaEnCurso as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error


@router.get("/laboratorio/corridas")
def listar_corridas() -> list[dict]:
    return laboratorio.listar_corridas()


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
