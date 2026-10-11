"""Panel del operador: página principal y lecturas que solo usa el panel (auditoría reciente y resumen)."""

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse

from app.api.routes_alertas import verificar_token_operador
from app.services import alertas, auditoria

DIRECTORIO_WEB = Path(__file__).resolve().parent.parent / "web"

router_pagina = APIRouter()
router = APIRouter(prefix="/api", dependencies=[Depends(verificar_token_operador)])


@router_pagina.get("/", include_in_schema=False)
def pagina_principal() -> FileResponse:
    return FileResponse(DIRECTORIO_WEB / "index.html")


@router.get("/auditoria")
def auditoria_reciente(limite: Annotated[int, Query(ge=1, le=200)] = 50) -> list[dict]:
    return auditoria.ultimos(limite)


@router.get("/resumen")
def resumen() -> dict:
    return alertas.resumen()
