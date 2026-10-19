"""Mock en memoria de la API REST de Ryu-IA, idéntico a C2 (docs/contratos.md) y a la especificación A3/A5–A8.

Uso manual:  AGENT_TOKEN=... python tests/mock_ryu.py   → http://127.0.0.1:8080
Capacidades (Fase 9b), cada una con su variable; sin ella, su ruta no existe (404), como un Ryu sin esa capacidad:
  MOCK_CUARENTENA=1       A6: /cuarentena (modo "cuarentena")
  MOCK_LIMITAR_TASA=1     A7: /limitar_tasa {"kbps"} (modo "limitado")
  MOCK_CUARENTENA_VLAN=1  A8: /cuarentena_vlan (modo "vlan_cuarentena")
Con cualquiera de ellas existen /bloqueo_total (desde cualquier modo) y el campo "modo"; /ryu/estado declara
"capacidades" con las activas. Las pruebas automáticas NO levantan este servidor: usan crear_app() en memoria.
"""

import ipaddress
import os
import sys
import time
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
KBPS_POR_DEFECTO = 1000
VLAN_CUARENTENA = 999


def _validar_ip(ip: str) -> None:
    try:
        ipaddress.IPv4Address(ip)
    except ValueError:
        raise HTTPException(422, "ip inválida") from None


def _activa(valor: bool | None, variable: str) -> bool:
    return os.environ.get(variable) == "1" if valor is None else valor


def crear_app(token: str | None = None, cuarentena: bool | None = None, limitar_tasa: bool | None = None,
              cuarentena_vlan: bool | None = None, fallas: dict[str, str] | None = None) -> FastAPI:
    """App nueva con su propio estado (cada prueba arranca con los 2 hosts bloqueados de ejemplo).

    Cada capacidad en None lee su variable del entorno. `fallas` (solo pruebas, no es C2): ruta → "error" (500 y
    sin cambio, como exige C2), "no_aplica" (200 pero el modo no cambia), "aplica_y_error" (cambia el modo y aun así
    responde 500: un Ryu que viola la transacción) o "lento" (cambia el modo y responde 1 s después: timeout ambiguo).
    """
    token = os.environ.get("AGENT_TOKEN", "") if token is None else token
    capacidades = [nombre for nombre, valor, variable in (
        ("cuarentena", cuarentena, "MOCK_CUARENTENA"), ("limitar_tasa", limitar_tasa, "MOCK_LIMITAR_TASA"),
        ("cuarentena_vlan", cuarentena_vlan, "MOCK_CUARENTENA_VLAN")) if _activa(valor, variable)]
    con_modo = bool(capacidades)
    fallas = fallas if fallas is not None else {}
    desde = datetime.now(timezone.utc).isoformat()

    def registro(ip: str, desde: str) -> dict:
        return {"ip": ip, "dpid": DPID, "desde": desde, **({"modo": "drop_total"} if con_modo else {})}

    bloqueados = {ip: registro(ip, desde) for ip in HOSTS_INICIALES}
    umbrales = dict(UMBRALES_INICIALES)

    def validar_token(x_agent_token: str | None = Header(default=None)) -> None:
        if not token or x_agent_token != token:
            raise HTTPException(401, "token inválido")

    rutas = APIRouter(dependencies=[Depends(validar_token)])

    @rutas.get("/ryu/estado")
    def estado() -> dict:
        return {"switches": [DPID], "hosts_bloqueados": len(bloqueados), "umbrales": umbrales, "pesos": PESOS,
                "version": "2.0", "capacidades": list(capacidades)}

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

    def cambiar_modo(ruta: str, ip: str, modo: str, **extra) -> dict:
        _validar_ip(ip)
        if ip not in bloqueados:
            raise HTTPException(404, "host no bloqueado")
        falla = fallas.get(ruta)
        if falla == "error":  # transaccional (C2): no aplica nada y deja el modo como estaba
            raise HTTPException(500, "no se pudo aplicar en todos los switches")
        if falla != "no_aplica":
            host = bloqueados[ip]
            host.pop("kbps", None)
            host.pop("vlan", None)
            host.update(modo=modo, **extra)
        if falla == "aplica_y_error":
            raise HTTPException(500, "error después de aplicar")
        if falla == "lento":
            time.sleep(1)
        return {"ok": True, "ip": ip, "modo": modo, **extra}

    if con_modo:  # bloqueo_total existe con A6, A7 o A8 y funciona desde cualquier modo
        @rutas.post("/ryu/hosts/{ip}/bloqueo_total")
        def bloqueo_total(ip: str) -> dict:
            return cambiar_modo("bloqueo_total", ip, "drop_total")

    if "cuarentena" in capacidades:  # A6
        @rutas.post("/ryu/hosts/{ip}/cuarentena")
        def poner_en_cuarentena(ip: str) -> dict:
            return cambiar_modo("cuarentena", ip, "cuarentena")

    if "limitar_tasa" in capacidades:  # A7
        @rutas.post("/ryu/hosts/{ip}/limitar_tasa")
        def limitar(ip: str, cuerpo: dict | None = Body(default=None)) -> dict:
            kbps = (cuerpo or {}).get("kbps", KBPS_POR_DEFECTO)
            if type(kbps) is not int or kbps <= 0:
                raise HTTPException(422, "kbps inválido")
            return cambiar_modo("limitar_tasa", ip, "limitado", kbps=kbps)

    if "cuarentena_vlan" in capacidades:  # A8
        @rutas.post("/ryu/hosts/{ip}/cuarentena_vlan")
        def a_vlan(ip: str) -> dict:
            return cambiar_modo("cuarentena_vlan", ip, "vlan_cuarentena", vlan=VLAN_CUARENTENA)

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
