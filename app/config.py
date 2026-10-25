"""Configuración del agente leída desde .env. Único lugar que lee variables de entorno.

Flujo único: .env (en la RAÍZ del proyecto, sin importar desde qué carpeta se lance uvicorn) → Settings →
el resto del código. Una variable del entorno del proceso tiene prioridad sobre .env (pydantic-settings).
"""

import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

RAIZ_PROYECTO = Path(__file__).resolve().parents[1]
ARCHIVO_ENV = RAIZ_PROYECTO / ".env"
OBLIGATORIAS = ("AGENT_TOKEN", "OPERADOR_TOKEN", "AUDITORIA_CLAVE", "LLM_MODELO")


def _definidas_en_env(ruta: Path | None = None) -> set[str]:
    """Claves con valor no vacío en el archivo .env (solo para informar el origen; no devuelve valores)."""
    ruta = ruta or ARCHIVO_ENV
    if not ruta.is_file():
        return set()
    claves = set()
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        clave, igual, valor = linea.strip().partition("=")
        if igual and not clave.startswith("#") and valor.strip():
            claves.add(clave.strip())
    return claves


def origen(clave: str) -> str:
    """De dónde sale el valor efectivo: "entorno del proceso", ".env" o "sin definir"."""
    if os.environ.get(clave):
        return "entorno del proceso"
    return ".env" if clave in _definidas_en_env() else "sin definir"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ARCHIVO_ENV, env_file_encoding="utf-8", extra="ignore")

    AGENT_TOKEN: str = ""
    OPERADOR_TOKEN: str = ""
    RYU_URL: str = "http://localhost:8080"
    OPENROUTER_API_KEY: str = ""
    LLM_MODELO: str = ""  # OBLIGATORIO: se define en .env; no hay modelo fijo en el código
    LLM_TIMEOUT: float = 20
    LLM_HABILITADO: bool = True
    LLM_CUPO_DIARIO: int = 1000
    APPSCRIPT_URL: str = ""
    APPSCRIPT_TIMEOUT: float = 30  # segundos totales por intento (arranque en frío + MailApp)
    PANEL_URL: str = "http://localhost:8081"
    DEDUP_SEGUNDOS: int = 60
    CONTEXTO_HABILITADO: bool = False  # C6: historial del host en el prompt (editable en caliente)
    PROMPT_V4: bool = True  # Fase D3.1: v4.0 / v4.1 por defecto; false → v3.0 / v3.1 (repetir la comparación)
    HISTORIAL_DIAS: int = 30
    CAMPANA_MINUTOS: int = 30
    # C7: motor de políticas (editables en caliente)
    MODO_DECISION: str = "recomendar"  # solo_explicar | recomendar (autonomo_acotado retirado en la D1 → recomendar)
    ESCALON_HITL: float = 0.70  # C7 (Fase 9a): debajo, decide el humano
    ESCALON_VLAN: float = 0.95  # C7: confirmado por encima → cuarentena_vlan
    LIMITE_TASA_KBPS: int = 1000  # A7 (Fase 9b): kbps de limitar_tasa; solo .env, sin edición en caliente
    RECOMENDACION_TTL_MIN: int = 60
    RATE_LIMIT_POR_MIN: int = 60
    ALERTA_MAX_BYTES: int = 65536  # tope del cuerpo de POST /api/alertas (413 si lo supera)
    DB_URL: str = "sqlite:///./sdn_agent.db"
    AUDITORIA_CLAVE: str = ""  # clave del HMAC de la cadena de auditoría

    def verificar_secretos(self) -> None:
        """Se llama al arrancar el agente: sin estos valores no se arranca (los scripts no lo exigen)."""
        faltan = [n for n in OBLIGATORIAS if not getattr(self, n).strip()]
        if faltan:
            raise RuntimeError(f"Faltan en .env ({ARCHIVO_ENV}; no pueden estar vacíos): {', '.join(faltan)}")


@lru_cache
def get_settings() -> Settings:
    return Settings()
