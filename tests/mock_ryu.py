"""Mock en memoria de la API REST de Ryu-IA, idéntico a C2 (docs/contratos.md) y a la especificación A3/A5/A6.

Uso manual:  AGENT_TOKEN=... python tests/mock_ryu.py   → http://127.0.0.1:8080
Con MOCK_CUARENTENA=1 simula un Ryu con A6 (/cuarentena, /bloqueo_total y el campo "modo");
sin ella, esas rutas no existen (404), como un Ryu sin A6.
Las pruebas automáticas NO levantan este servidor: usan crear_app() en memoria.
"""

import ipaddress
import os
import sys
from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends, FastAPI, Header, HTTPException

DPID = "0x0000000000000001"
HOSTS_INICIALES = ("10.0.0.3", "10.0.0.5")
# Valores por defecto de RYU_IA v2 (docs/referencia/ryu_controller_actual.py), con los nombres de A5/C2.
UMBRALES_INICIALES = {
    "M1": 20, "M2": 8, "M3_DURACION_S": 5, "M3_BYTES": 512000, "M4": 200, "M5": 5,
    "SCORE_SOSPECHOSO": 30, "SCORE_CRITICO": 60,
}
PESOS = {"M5": 40, "M3": 35, "M1": 25, "M2": 20, "M4": 15}


def _validar_ip(ip: str) -> None:
    try:
        ipaddress.IPv4Address(ip)
    except ValueError:
        raise HTTPException(422, "ip inválida") from None


def crear_app(token: str | None = None, cuarentena: bool | None = None) -> FastAPI:
    """App nueva con su propio estado (cada prueba arranca con los 2 hosts bloqueados de ejemplo).

    cuarentena=None lee MOCK_CUARENTENA del entorno ("1" = Ryu con A6).
    """
    token = os.environ.get("AGENT_TOKEN", "") if token is None else token
    cuarentena = os.environ.get("MOCK_CUARENTENA") == "1" if cuarentena is None else cuarentena
    desde = datetime.now(timezone.utc).isoformat()

    def registro(ip: str, desde: str) -> dict:
        return {"ip": ip, "dpid": DPID, "desde": desde, **({"modo": "drop_total"} if cuarentena else {})}

    bloqueados = {ip: registro(ip, desde) for ip in HOSTS_INICIALES}
    umbrales = dict(UMBRALES_INICIALES)

    def validar_token(x_agent_token: str | None = Header(default=None)) -> None:
        if not token or x_agent_token != token:
            raise HTTPException(401, "token inválido")

    rutas = APIRouter(dependencies=[Depends(validar_token)])

    @rutas.get("/ryu/estado")
    def estado() -> dict:
        return {"switches": [DPID], "hosts_bloqueados": len(bloqueados), "umbrales": umbrales, "pesos": PESOS,
                "version": "2.0"}

    @rutas.get("/ryu/hosts/bloqueados")
    def listar_bloqueados() -> list[dict]:
        return list(bloqueados.values())

    @rutas.post("/ryu/hosts/{ip}/desbloquear")
    def desbloquear(ip: str) -> dict:
        _validar_ip(ip)
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
            raise HTTPException(422, "umbrales inválidos")
        umbrales.update(cambios)
        return umbrales

    if cuarentena:  # A6: sin ella estas rutas no existen y responden 404

        def cambiar_modo(ip: str, modo: str) -> dict:
            _validar_ip(ip)
            if ip not in bloqueados:
                raise HTTPException(404, "host no bloqueado")
            bloqueados[ip]["modo"] = modo
            return {"ok": True, "ip": ip, "modo": modo}

        @rutas.post("/ryu/hosts/{ip}/cuarentena")
        def poner_en_cuarentena(ip: str) -> dict:
            return cambiar_modo(ip, "cuarentena")

        @rutas.post("/ryu/hosts/{ip}/bloqueo_total")
        def bloqueo_total(ip: str) -> dict:
            return cambiar_modo(ip, "drop_total")

    @rutas.post("/mock/bloquear/{ip}")  # solo del mock, no es parte de C2
    def bloquear(ip: str) -> dict:
        _validar_ip(ip)
        bloqueados[ip] = registro(ip, datetime.now(timezone.utc).isoformat())
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
