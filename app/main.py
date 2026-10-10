"""Crea la app FastAPI y monta los routers. Sin lógica."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import routes_alertas, salud
from app.config import get_settings
from app.db.database import iniciar_bd


@asynccontextmanager
async def ciclo_vida(_app: FastAPI):
    iniciar_bd(get_settings().DB_URL)
    yield


app = FastAPI(title="SDN-Agent v2", version=salud.VERSION, lifespan=ciclo_vida)
app.include_router(salud.router)
app.include_router(routes_alertas.router)
