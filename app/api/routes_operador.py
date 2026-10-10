"""Rutas del operador: Ryu (estado, bloqueados, desbloqueo, umbrales), configuración y cupo del LLM."""

from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, Path, status

from app.api.routes_alertas import verificar_token_operador
from app.schemas.alerta import IPv4
from app.schemas.operador import ConfigActualizar, DesbloqueoEntrada
from app.services import configuracion, operador, presupuesto

router = APIRouter(prefix="/api", dependencies=[Depends(verificar_token_operador)])


def _error_ryu(error: operador.RyuError) -> HTTPException:
    if isinstance(error, operador.HostNoBloqueado):
        return HTTPException(status.HTTP_404_NOT_FOUND, "El host no está bloqueado en Ryu")
    if isinstance(error, operador.RyuNoDisponible):
        return HTTPException(status.HTTP_502_BAD_GATEWAY, f"Ryu no responde ({error})")
    return HTTPException(status.HTTP_502_BAD_GATEWAY, f"Ryu respondió con error ({error})")


@router.get("/ryu/estado")
def estado_ryu() -> dict:
    return operador.estado_ryu()


@router.get("/ryu/bloqueados")
def bloqueados() -> list[dict]:
    try:
        return operador.bloqueados_enriquecidos()
    except operador.RyuError as error:
        raise _error_ryu(error) from error


@router.post("/hosts/{ip}/desbloquear")
def desbloquear_host(ip: Annotated[IPv4, Path()], entrada: DesbloqueoEntrada, tareas: BackgroundTasks) -> dict:
    try:
        return operador.desbloquear_host(ip, entrada.confirmacion, entrada.motivo, tareas.add_task)
    except operador.ConfirmacionInvalida as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error
    except operador.RyuError as error:
        raise _error_ryu(error) from error


@router.get("/ryu/umbrales")
def obtener_umbrales() -> dict:
    try:
        return operador.obtener_umbrales()
    except operador.RyuError as error:
        raise _error_ryu(error) from error


@router.put("/ryu/umbrales")
def actualizar_umbrales(cambios: Annotated[dict[str, Any], Body()]) -> dict:
    try:
        return operador.actualizar_umbrales(cambios)
    except operador.UmbralesInvalidos as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(error)) from error
    except operador.RyuError as error:
        raise _error_ryu(error) from error


@router.get("/config")
def obtener_config() -> dict:
    return configuracion.obtener()


@router.put("/config")
def actualizar_config(cambios: ConfigActualizar) -> dict:
    return configuracion.actualizar(cambios.model_dump(exclude_none=True))


@router.get("/llm/cupo")
def cupo_llm() -> dict:
    return presupuesto.estado_cupo()
