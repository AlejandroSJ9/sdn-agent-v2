"""Rutas de notificación por correo (operador): prueba y reenvío."""

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.routes_alertas import verificar_token_operador
from app.schemas.notificacion import ResultadoNotificacion
from app.services import notificacion

router = APIRouter(prefix="/api", dependencies=[Depends(verificar_token_operador)])


@router.post("/notificaciones/prueba")
def enviar_prueba() -> ResultadoNotificacion:
    return notificacion.notificar(None, "prueba")


@router.post("/incidentes/{incidente_id}/reenviar")
def reenviar(incidente_id: int) -> ResultadoNotificacion:
    resultado = notificacion.notificar(incidente_id, "incidente")
    if resultado is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Incidente no encontrado")
    return resultado
