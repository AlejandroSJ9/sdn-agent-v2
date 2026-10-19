"""Cliente de la API REST de Ryu-IA (C2 en docs/contratos.md). Solo habla HTTP."""

import json
import time

import httpx

from app.integrations._tope import TopeSuperado, ejecutar_con_tope, solicitar_hasta

# Claves de umbral que expone Ryu-IA (C2). Ryu rechaza con 422 cualquier otra o un valor que no sea entero > 0.
CLAVES_UMBRALES = ("M1", "M2", "M3_DURACION_S", "M3_BYTES", "M4", "M5", "SCORE_SOSPECHOSO", "SCORE_CRITICO")

# Capacidades de la escalera (C2, Fase 9b): A6 cuarentena, A7 limitar_tasa, A8 cuarentena_vlan.
CAPACIDADES = ("cuarentena", "limitar_tasa", "cuarentena_vlan")
SIN_CAPACIDADES = dict.fromkeys(CAPACIDADES, False)

# Capacidades detectadas, por URL de Ryu. El cliente se crea en cada llamada, por eso la caché es del módulo.
_capacidades: dict[str, dict[str, bool]] = {}


class RyuError(Exception):
    """Ryu respondió con un código que no es 2xx (codigo) o no se pudo hablar con él (codigo None)."""

    def __init__(self, codigo: int | None, detalle: str = "") -> None:
        super().__init__(detalle or f"http_{codigo}")
        self.codigo = codigo


class RyuNoDisponible(RyuError):
    """Error de red o timeout: Ryu no responde."""


class HostNoBloqueado(RyuError):
    """Ryu respondió 404 al desbloquear: el host no estaba bloqueado."""


API_NO_DISPONIBLE = "api_no_disponible (falta A5)"


class ApiNoDisponible(RyuError):
    """Ryu responde pero sus rutas /ryu/* dan 404: no tiene la API REST (A5)."""


class UmbralesNoValidos(ValueError):
    """Cambio de umbrales que Ryu rechazaría (clave desconocida o valor que no es entero > 0). No se envía."""


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
        """Cada host: ip, dpid, desde y, si Ryu tiene A6/A7/A8, "modo" (drop_total | cuarentena | limitado |
        vlan_cuarentena), con "kbps" en limitado y "vlan" en vlan_cuarentena."""
        return self._pedir("GET", "/ryu/hosts/bloqueados")

    def capacidades(self, refrescar: bool = False) -> dict[str, bool]:
        """{"cuarentena", "limitar_tasa", "cuarentena_vlan"}: lo que Ryu declara en GET /ryu/estado (Fase 9b).

        Ryu anterior a la 9b (sin el campo): A6 se infiere porque envía "modo" en cada host bloqueado; A7 y A8, no.
        Se detecta una vez y queda en caché (refrescar=True vuelve a consultar). Ryu anterior con la lista vacía
        no se puede saber: devuelve todo False y NO lo guarda, para detectarlo en la próxima llamada.
        """
        if not refrescar and self._base_url in _capacidades:
            return dict(_capacidades[self._base_url])
        declaradas = self.estado().get("capacidades")
        if isinstance(declaradas, list):
            _capacidades[self._base_url] = {c: c in declaradas for c in CAPACIDADES}
            return dict(_capacidades[self._base_url])
        lista = self.bloqueados()
        if not lista:
            return dict(SIN_CAPACIDADES)
        _capacidades[self._base_url] = {**SIN_CAPACIDADES, "cuarentena": all("modo" in host for host in lista)}
        return dict(_capacidades[self._base_url])

    def desbloquear(self, ip: str) -> dict:
        try:
            return self._pedir("POST", f"/ryu/hosts/{ip}/desbloquear")
        except RyuError as error:
            if error.codigo != 404:
                raise
        # Un 404 solo significa "no bloqueado" si la API existe; si /bloqueados también da 404, falta A5.
        try:
            self.bloqueados()
        except RyuError as error:
            if error.codigo == 404:
                raise ApiNoDisponible(404, API_NO_DISPONIBLE) from error
            raise
        raise HostNoBloqueado(404, "host_no_bloqueado")

    # A6/A7/A8 (C2): solo services/acciones.py puede llamarlas (tests/unit/test_unico_camino_desbloqueo.py).
    def cuarentena(self, ip: str) -> dict:
        """{"ok", "ip", "modo": "cuarentena"}. 404 = Ryu sin A6 o host no bloqueado → RyuError(404)."""
        return self._pedir("POST", f"/ryu/hosts/{ip}/cuarentena")

    def bloqueo_total(self, ip: str) -> dict:
        """Desde cualquier modo: {"ok", "ip", "modo": "drop_total"}. 404 = Ryu sin A6/A7/A8 o host no bloqueado."""
        return self._pedir("POST", f"/ryu/hosts/{ip}/bloqueo_total")

    def limitar_tasa(self, ip: str, kbps: int) -> dict:
        """A7: {"ok", "ip", "modo": "limitado", "kbps"}. 404 = Ryu sin A7 o host no bloqueado; 422 kbps inválido."""
        return self._pedir("POST", f"/ryu/hosts/{ip}/limitar_tasa", {"kbps": kbps})

    def cuarentena_vlan(self, ip: str) -> dict:
        """A8: {"ok", "ip", "modo": "vlan_cuarentena", "vlan"}. 404 = Ryu sin A8 o host no bloqueado."""
        return self._pedir("POST", f"/ryu/hosts/{ip}/cuarentena_vlan")

    def umbrales(self) -> dict[str, int]:
        return self._pedir("GET", "/ryu/umbrales")

    def actualizar_umbrales(self, cambios: dict[str, int]) -> dict[str, int]:
        """Valida localmente con las mismas reglas que Ryu antes de enviar."""
        desconocidas = sorted(set(cambios) - set(CLAVES_UMBRALES))
        invalidos = sorted(k for k, v in cambios.items() if type(v) is not int or v <= 0)
        if not cambios or desconocidas or invalidos:
            raise UmbralesNoValidos(f"claves desconocidas: {desconocidas}; valores inválidos: {invalidos}")
        return self._pedir("PUT", "/ryu/umbrales", cambios)
