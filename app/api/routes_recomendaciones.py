"""Rutas del operador para la decisión acotada (C7): recomendaciones y acciones manuales sobre un host (Fase D5)."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status

from app.api.routes_alertas import verificar_token_operador
from app.schemas.alerta import IPv4
from app.schemas.operador import RechazoEntrada
from app.services import acciones, acciones_operador, recomendaciones

router = APIRouter(prefix="/api", dependencies=[Depends(verificar_token_operador)])

Estado = Literal["pendiente", "aprobada", "rechazada", "ejecutada", "expirada", "fallida"]


def _resolver(funcion, *argumentos) -> dict:
    try:
        return funcion(*argumentos)
    except recomendaciones.RecomendacionNoEncontrada as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    except recomendaciones.RecomendacionYaResuelta as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error


@router.get("/recomendaciones")
def listar(estado: Estado | None = None, limite: Annotated[int, Query(ge=1, le=200)] = 100) -> list[dict]:
    return recomendaciones.listar(estado, limite)


@router.post("/recomendaciones/{recomendacion_id}/aprobar")
def aprobar(recomendacion_id: int) -> dict:
    """Ejecuta la acción por services/acciones.py con origen "operador"."""
    return _resolver(recomendaciones.aprobar, recomendacion_id)


@router.post("/recomendaciones/{recomendacion_id}/rechazar")
def rechazar(recomendacion_id: int, entrada: RechazoEntrada) -> dict:
    return _resolver(recomendaciones.rechazar, recomendacion_id, entrada.motivo)


def _accion_operador(ip: str, accion: str) -> dict:
    try:
        resultado = acciones_operador.ejecutar(ip, accion)
    except (acciones.AccionNoPermitida, recomendaciones.RecomendacionYaResuelta) as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    except acciones_operador.RyuError as error:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"Ryu no responde ({error})") from error
    if resultado["estado"] == "fallida":
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Ryu no aplicó la acción ({resultado['error']})")
    return resultado


@router.post("/hosts/{ip}/cuarentena")
def pasar_a_cuarentena(ip: Annotated[IPv4, Path()]) -> dict:
    return _accion_operador(ip, "cuarentena_puertos")


@router.post("/hosts/{ip}/bloqueo_total")
def volver_a_bloqueo_total(ip: Annotated[IPv4, Path()]) -> dict:
    return _accion_operador(ip, "bloqueo_total")


@router.post("/hosts/{ip}/limitar_tasa")
def limitar_tasa(ip: Annotated[IPv4, Path()]) -> dict:
    return _accion_operador(ip, "limitar_tasa")


@router.post("/hosts/{ip}/cuarentena_vlan")
def pasar_a_cuarentena_vlan(ip: Annotated[IPv4, Path()]) -> dict:
    return _accion_operador(ip, "cuarentena_vlan")


@router.get("/hosts/{ip}/acciones")
def acciones_del_host(ip: Annotated[IPv4, Path()]) -> dict:
    """Lo que el operador puede hacer ahora con el host (C7). Lee Ryu en cada llamada."""
    try:
        return acciones_operador.listar(ip)
    except acciones_operador.RyuError as error:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"Ryu no responde ({error})") from error
