"""Mock en memoria de la API REST de Ryu-IA (C2 / docs/ajustes.md A3), para desarrollo.

Uso manual:  AGENT_TOKEN=... python tests/mock_ryu.py   → http://127.0.0.1:8080
Las pruebas automáticas NO levantan este servidor: usan crear_app() en memoria.
"""

import os
import sys
from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends, FastAPI, Header, HTTPException

DPID = "0x0000000000000001"
HOSTS_INICIALES = ("10.0.0.3", "10.0.0.5")
UMBRALES_INICIALES = {"M1": 20, "M2": 5, "M4": 60, "M5": 3, "CRITICAL_THRESHOLD": 2}


def crear_app(token: str | None = None) -> FastAPI:
    """App nueva con su propio estado (cada prueba arranca con los 2 hosts bloqueados de ejemplo)."""
    token = os.environ.get("AGENT_TOKEN", "") if token is None else token
    desde = datetime.now(timezone.utc).isoformat()
    bloqueados = {ip: {"ip": ip, "dpid": DPID, "desde": desde} for ip in HOSTS_INICIALES}
    umbrales = dict(UMBRALES_INICIALES)

    def validar_token(x_agent_token: str | None = Header(default=None)) -> None:
        if not token or x_agent_token != token:
            raise HTTPException(401, "token inválido")

    rutas = APIRouter(dependencies=[Depends(validar_token)])

    @rutas.get("/ryu/estado")
    def estado() -> dict:
        return {"switches": [DPID], "hosts_bloqueados": len(bloqueados), "umbrales": umbrales, "version": "mock-1"}

    @rutas.get("/ryu/hosts/bloqueados")
    def listar_bloqueados() -> list[dict]:
        return list(bloqueados.values())

    @rutas.post("/ryu/hosts/{ip}/desbloquear")
    def desbloquear(ip: str) -> dict:
        if ip not in bloqueados:
            raise HTTPException(404, "host no bloqueado")
        del bloqueados[ip]
        return {"ok": True, "ip": ip, "reglas_borradas": 1}

    @rutas.get("/ryu/umbrales")
    def leer_umbrales() -> dict:
        return umbrales

    @rutas.put("/ryu/umbrales")
    def cambiar_umbrales(cambios: dict = Body(...)) -> dict:
        if any(k not in umbrales or type(v) is not int or v <= 0 for k, v in cambios.items()):
            raise HTTPException(400, "umbrales inválidos")
        umbrales.update(cambios)
        return umbrales

    @rutas.post("/mock/bloquear/{ip}")  # solo del mock, no es parte de C2
    def bloquear(ip: str) -> dict:
        bloqueados[ip] = {"ip": ip, "dpid": DPID, "desde": datetime.now(timezone.utc).isoformat()}
        return {"ok": True, "ip": ip}

    app = FastAPI(title="Mock Ryu-IA")
    app.include_router(rutas)
    app.state.bloqueados = bloqueados  # para inspección en pruebas
    return app


if __name__ == "__main__":
    import uvicorn

    if not os.environ.get("AGENT_TOKEN"):
        sys.exit("Define AGENT_TOKEN (el mismo que usa el agente) antes de arrancar el mock.")
    uvicorn.run(crear_app(), host="127.0.0.1", port=8080)
