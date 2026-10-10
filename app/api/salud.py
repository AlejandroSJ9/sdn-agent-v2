"""Endpoint de salud del agente."""

from fastapi import APIRouter

router = APIRouter(prefix="/api")

VERSION = "0.1.0"


@router.get("/salud")
def salud() -> dict:
    return {"ok": True, "version": VERSION}
