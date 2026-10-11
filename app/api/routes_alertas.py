"""Rutas de alertas (Ryu → agente), incidentes y verificación de auditoría (operador)."""

import hmac
import threading
import time
from collections import deque
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query, status

from app.config import get_settings
from app.schemas.alerta import AlertaEntrada, AlertaRespuesta
from app.schemas.notificacion import IncidenteDetalle
from app.services import alertas, auditoria

router = APIRouter(prefix="/api")


class LimiteTasa:
    """Ventana deslizante de 60 s en memoria.

    Es GLOBAL a propósito (no por IP): el único emisor legítimo de alertas es Ryu, y el límite se
    evalúa después del token, así que un cliente sin token no consume cupo.
    """

    VENTANA_SEGUNDOS = 60.0

    def __init__(self) -> None:
        self._marcas: deque[float] = deque()
        self._candado = threading.Lock()

    def permitir(self, limite: int) -> bool:
        ahora = time.monotonic()
        with self._candado:
            while self._marcas and ahora - self._marcas[0] >= self.VENTANA_SEGUNDOS:
                self._marcas.popleft()
            if len(self._marcas) >= limite:
                return False
            self._marcas.append(ahora)
            return True

    def reiniciar(self) -> None:
        with self._candado:
            self._marcas.clear()


limite_tasa = LimiteTasa()


def _token_valido(recibido: str | None, esperado: str) -> bool:
    """Falla cerrado: si no hay token configurado nadie pasa. Comparación en tiempo constante."""
    if not esperado or not recibido:
        return False
    return hmac.compare_digest(recibido.encode(), esperado.encode())


def verificar_token_agente(x_agent_token: Annotated[str | None, Header(alias="X-Agent-Token")] = None) -> None:
    if not _token_valido(x_agent_token, get_settings().AGENT_TOKEN):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token de agente inválido")


def verificar_token_operador(
    x_operador_token: Annotated[str | None, Header(alias="X-Operador-Token")] = None,
) -> None:
    if not _token_valido(x_operador_token, get_settings().OPERADOR_TOKEN):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token de operador inválido")


def limitar_tasa() -> None:
    if not limite_tasa.permitir(get_settings().RATE_LIMIT_POR_MIN):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Límite de tasa excedido")


# Orden de las dependencias: token (401) → límite de tasa (429); el cuerpo se valida después (422).
@router.post(
    "/alertas",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(verificar_token_agente), Depends(limitar_tasa)],
)
def recibir_alerta(alerta: AlertaEntrada, tareas: BackgroundTasks) -> AlertaRespuesta:
    """Responde 202 al instante; el pipeline corre en segundo plano."""
    return AlertaRespuesta(incidente_id=alertas.recibir(alerta, tareas.add_task))


@router.get("/incidentes", dependencies=[Depends(verificar_token_operador)])
def listar_incidentes(
    limite: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    origen: Literal["real", "prueba", "todos"] = "real",
) -> list[IncidenteDetalle]:
    return alertas.listar_incidentes(limite, offset, origen)


@router.get("/incidentes/{incidente_id}", dependencies=[Depends(verificar_token_operador)])
def obtener_incidente(incidente_id: int) -> IncidenteDetalle:
    incidente = alertas.obtener_incidente(incidente_id)
    if incidente is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Incidente no encontrado")
    return incidente


@router.get("/auditoria/verificar", dependencies=[Depends(verificar_token_operador)])
def verificar_auditoria() -> dict:
    return auditoria.verificar_cadena()
