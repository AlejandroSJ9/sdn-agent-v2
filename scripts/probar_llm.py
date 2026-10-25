"""Hace UNA llamada real al LLM con el ejemplo de C1 e imprime la salida validada, la latencia y los tokens.

Uso (desde la raíz del repo, con OPENROUTER_API_KEY en .env):  python scripts/probar_llm.py
No toca la base de datos: esta llamada NO queda contada en uso_llm.
Fase D4: usa el prompt activo sin contexto según PROMPT_V4 de .env (por defecto v4.0; un cambio hecho desde el panel
no se lee, porque vive en la base), el mensaje con evidencia (umbrales por defecto de docs/ajustes.md) y valida con
SalidaLLMv4, igual que el pipeline.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydantic import ValidationError  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.integrations.llm import ClienteOpenRouter, LLMError  # noqa: E402
from app.schemas.alerta import AlertaEntrada  # noqa: E402
from app.schemas.explicacion import SalidaLLMv4  # noqa: E402
from app.services import evidencia  # noqa: E402
from app.services.prompt import construir_mensaje_usuario, system_prompt, version  # noqa: E402

EJEMPLO_C1 = {
    "timestamp": "2026-10-10T20:31:05.123Z",
    "alerta_nivel": "CRITICO",
    "host_origen": "10.0.0.3",
    "dpid_switch": "0x0000000000000001",
    "contexto_topologico": {},
    "metricas": {
        "M1_nuevos_flujos_por_30s": 42,
        "M2_destinos_unicos_por_30s": 9,
        "M3_smb_anomalo": True,
        "M4_packet_in_por_min": 120.0,
        "M5_flujos_east_west": True,
        "M5_count": 7,
    },
    "umbrales_superados": ["M1", "M2", "M3"],
    "ioc_activados": ["..."],
    "accion_ryu_previa": "OFPFlowMod DROP prioridad 100 — 10.0.0.3 aislado",
    "solicitud_al_agente": "IGNORADO por el agente",
}


def main() -> int:
    ajustes = get_settings()
    if not ajustes.OPENROUTER_API_KEY:
        print("Falta OPENROUTER_API_KEY en .env")
        return 1
    alerta = AlertaEntrada.model_validate(EJEMPLO_C1)
    evaluadas = evidencia.evaluar(alerta.metricas, dict(evidencia.POR_DEFECTO))
    mensaje = construir_mensaje_usuario(alerta, 1, evidencia=evidencia.para_prompt(evaluadas))
    cliente = ClienteOpenRouter(ajustes.OPENROUTER_API_KEY, ajustes.LLM_MODELO, ajustes.LLM_TIMEOUT)
    print(f"Modelo: {ajustes.LLM_MODELO} · prompt {version('sin_contexto', ajustes.PROMPT_V4)}\nMensaje enviado:\n{mensaje}\n")
    try:
        respuesta = cliente.completar(system_prompt(False, ajustes.PROMPT_V4), mensaje)
    except LLMError as error:
        print(f"Error del LLM ({error.motivo}): {error} — peticiones: {error.peticiones}")
        return 1
    print(
        f"Latencia: {respuesta.latencia_ms} ms | tokens entrada/salida: "
        f"{respuesta.tokens_entrada}/{respuesta.tokens_salida} | peticiones: {respuesta.peticiones}"
    )
    try:
        explicacion = SalidaLLMv4.model_validate_json(respuesta.texto)
    except ValidationError as error:
        print(f"La salida NO valida contra C4 (SalidaLLMv4):\n{respuesta.texto}\n\n{error}")
        return 1
    print("Salida C4 válida (SalidaLLMv4):")
    print(json.dumps(explicacion.model_dump(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
