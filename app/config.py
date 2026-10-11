"""Configuración del agente leída desde .env. Único lugar que lee variables de entorno."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    AGENT_TOKEN: str = ""
    OPERADOR_TOKEN: str = ""
    RYU_URL: str = "http://localhost:8080"
    OPENROUTER_API_KEY: str = ""
    LLM_MODELO: str = "meta-llama/llama-3.1-8b-instruct"
    LLM_TIMEOUT: float = 20
    LLM_HABILITADO: bool = True
    LLM_CUPO_DIARIO: int = 1000
    APPSCRIPT_URL: str = ""
    APPSCRIPT_TIMEOUT: float = 30  # segundos totales por intento (arranque en frío + MailApp)
    PANEL_URL: str = "http://localhost:8081"
    DEDUP_SEGUNDOS: int = 60
    RATE_LIMIT_POR_MIN: int = 60
    DB_URL: str = "sqlite:///./sdn_agent.db"
    AUDITORIA_CLAVE: str = ""  # clave del HMAC de la cadena de auditoría

    def verificar_secretos(self) -> None:
        """Se llama al arrancar el agente: sin estos secretos no se arranca (los scripts no lo exigen)."""
        faltan = [n for n in ("AGENT_TOKEN", "OPERADOR_TOKEN", "AUDITORIA_CLAVE") if not getattr(self, n).strip()]
        if faltan:
            raise RuntimeError(f"Faltan en .env (no pueden estar vacíos): {', '.join(faltan)}")


@lru_cache
def get_settings() -> Settings:
    return Settings()
