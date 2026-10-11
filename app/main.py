"""Crea la app FastAPI y monta los routers. Sin lógica."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api import (
    routes_alertas,
    routes_laboratorio,
    routes_notificaciones,
    routes_operador,
    routes_panel,
    salud,
    websocket,
)
from app.config import get_settings
from app.db.database import iniciar_bd
from app.services import recuperacion


@asynccontextmanager
async def ciclo_vida(_app: FastAPI):
    ajustes = get_settings()
    ajustes.verificar_secretos()  # sin AGENT_TOKEN, OPERADOR_TOKEN y AUDITORIA_CLAVE no se arranca
    iniciar_bd(ajustes.DB_URL)
    recuperacion.recuperar_al_arrancar()
    yield


app = FastAPI(title="SDN-Agent v2", version=salud.VERSION, lifespan=ciclo_vida)
app.include_router(salud.router)
app.include_router(routes_alertas.router)
app.include_router(routes_notificaciones.router)
app.include_router(routes_operador.router)
app.include_router(routes_panel.router)
app.include_router(routes_laboratorio.router)
app.include_router(routes_panel.router_pagina)
app.include_router(websocket.router)
app.mount("/static", StaticFiles(directory=routes_panel.DIRECTORIO_WEB), name="static")
