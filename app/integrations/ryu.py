"""Cliente de la API REST de Ryu-IA (C2 / docs/ajustes.md A3). Solo habla HTTP."""

import json
import time

import httpx

from app.integrations._tope import TopeSuperado, ejecutar_con_tope, solicitar_hasta


class RyuError(Exception):
    """Ryu respondió con un código que no es 2xx (codigo) o no se pudo hablar con él (codigo None)."""

    def __init__(self, codigo: int | None, detalle: str = "") -> None:
        super().__init__(detalle or f"http_{codigo}")
        self.codigo = codigo


class RyuNoDisponible(RyuError):
    """Error de red o timeout: Ryu no responde."""


class HostNoBloqueado(RyuError):
    """Ryu respondió 404 al desbloquear: el host no estaba bloqueado."""


class ClienteRyu:
    def __init__(self, base_url: str, token: str, transporte: httpx.BaseTransport | None = None, tope: float = 3.0):
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._transporte = transporte
        self._tope = tope

    def _pedir(self, metodo: str, ruta: str, cuerpo: dict | None = None):
        """Una petición con tope TOTAL de `tope` segundos (ver _tope.py)."""
        limite = time.monotonic() + self._tope
        cabeceras = {"X-Agent-Token": self._token}

        def hacer() -> tuple[int, bytes]:
            with httpx.Client(transport=self._transporte) as http:
                return solicitar_hasta(http, metodo, self._base_url + ruta, limite, cuerpo, cabeceras)

        try:
            codigo, contenido = ejecutar_con_tope(hacer, self._tope, "ryu")
        except (TopeSuperado, httpx.TimeoutException) as error:
            raise RyuNoDisponible(None, "timeout") from error
        except httpx.HTTPError as error:
            raise RyuNoDisponible(None, f"red_{type(error).__name__}") from error
        if not 200 <= codigo < 300:
            raise RyuError(codigo)
        try:
            return json.loads(contenido)
        except ValueError as error:
            raise RyuError(codigo, "respuesta_no_json") from error

    def estado(self) -> dict:
        return self._pedir("GET", "/ryu/estado")

    def bloqueados(self) -> list[dict]:
        return self._pedir("GET", "/ryu/hosts/bloqueados")

    def desbloquear(self, ip: str) -> dict:
        try:
            return self._pedir("POST", f"/ryu/hosts/{ip}/desbloquear")
        except RyuError as error:
            if error.codigo == 404:
                raise HostNoBloqueado(404, "host_no_bloqueado") from error
            raise

    def umbrales(self) -> dict[str, int]:
        return self._pedir("GET", "/ryu/umbrales")

    def actualizar_umbrales(self, cambios: dict[str, int]) -> dict[str, int]:
        return self._pedir("PUT", "/ryu/umbrales", cambios)
