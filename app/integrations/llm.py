"""Cliente de OpenRouter (chat completions) con httpx. Sin reintentos: si falla, decide el pipeline."""

import json
import time
from dataclasses import dataclass
from typing import Protocol

import httpx

from app.integrations._tope import TopeSuperado, ejecutar_con_tope, post_hasta

URL_OPENROUTER = "https://openrouter.ai/api/v1/chat/completions"


class LLMError(Exception):
    """Fallo del LLM. motivo: "timeout" | "error_llm" | "json_invalido"; peticiones: HTTP enviadas."""

    def __init__(self, motivo: str, detalle: str = "", peticiones: int = 0) -> None:
        super().__init__(f"{motivo}: {detalle}" if detalle else motivo)
        self.motivo = motivo
        self.peticiones = peticiones


@dataclass
class RespuestaLLM:
    texto: str
    tokens_entrada: int
    tokens_salida: int
    latencia_ms: int
    peticiones: int


class ClienteLLM(Protocol):
    def completar(self, system: str, usuario: str) -> RespuestaLLM: ...


def extraer_primer_json(texto: str) -> str:
    """Devuelve el primer objeto JSON que aparezca en el texto."""
    decodificador = json.JSONDecoder()
    for inicio, caracter in enumerate(texto):
        if caracter != "{":
            continue
        try:
            objeto, fin = decodificador.raw_decode(texto, inicio)
        except json.JSONDecodeError:
            continue
        if isinstance(objeto, dict):
            return texto[inicio:fin]
    raise LLMError("json_invalido", "la respuesta no contiene un objeto JSON")


class ClienteOpenRouter:
    def __init__(
        self, api_key: str, modelo: str, timeout: float, transporte: httpx.BaseTransport | None = None
    ) -> None:
        self._api_key = api_key
        self._modelo = modelo
        self._timeout = timeout
        self._transporte = transporte

    def _intercambio(self, cuerpo: dict, estado: dict, limite: float) -> tuple[int, bytes]:
        cabeceras = {"Authorization": f"Bearer {self._api_key}"}
        with httpx.Client(transport=self._transporte) as http:
            estado["peticiones"] = 1
            codigo, contenido = post_hasta(http, URL_OPENROUTER, cuerpo, cabeceras, limite)
            if codigo in (400, 422):  # el proveedor no acepta response_format: UNA repetición sin él
                del cuerpo["response_format"]
                estado["peticiones"] = 2
                codigo, contenido = post_hasta(http, URL_OPENROUTER, cuerpo, cabeceras, limite)
        return codigo, contenido

    def completar(self, system: str, usuario: str) -> RespuestaLLM:
        cuerpo = {
            "model": self._modelo,
            "temperature": 0,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": usuario}],
            "response_format": {"type": "json_object"},
        }
        # Tope TOTAL de `timeout` segundos para toda la llamada, incluido el reintento (ver _tope.py).
        estado: dict = {"peticiones": 0}
        limite = time.monotonic() + self._timeout
        inicio = time.perf_counter()
        try:
            codigo, contenido_http = ejecutar_con_tope(
                lambda: self._intercambio(cuerpo, estado, limite), self._timeout, "llm-openrouter"
            )
        except TopeSuperado as error:
            raise LLMError("timeout", str(error), max(estado["peticiones"], 1)) from error
        except httpx.TimeoutException as error:
            raise LLMError("timeout", type(error).__name__, max(estado["peticiones"], 1)) from error
        except httpx.HTTPError as error:
            raise LLMError("error_llm", type(error).__name__, max(estado["peticiones"], 1)) from error
        peticiones = max(estado["peticiones"], 1)
        latencia_ms = int((time.perf_counter() - inicio) * 1000)

        if codigo != 200:
            raise LLMError("error_llm", f"HTTP {codigo}", peticiones)
        try:
            datos = json.loads(contenido_http)
            contenido = datos["choices"][0]["message"]["content"]
            uso = datos.get("usage") or {}
            texto = extraer_primer_json(contenido)
        except LLMError as error:
            error.peticiones = peticiones
            raise
        except (ValueError, KeyError, IndexError, TypeError) as error:
            raise LLMError("error_llm", "respuesta con formato inesperado", peticiones) from error
        return RespuestaLLM(
            texto=texto,
            tokens_entrada=int(uso.get("prompt_tokens") or 0),
            tokens_salida=int(uso.get("completion_tokens") or 0),
            latencia_ms=latencia_ms,
            peticiones=peticiones,
        )
