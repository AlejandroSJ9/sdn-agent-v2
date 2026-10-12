"""LLM_MODELO: flujo único .env → config → resto del proyecto. Sin modelo fijo en el código."""

import pytest

from app import config
from app.config import ARCHIVO_ENV, RAIZ_PROYECTO, Settings


def test_env_se_carga_desde_la_raiz_del_proyecto_sin_importar_el_directorio(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # como si uvicorn se lanzara desde otra carpeta
    assert Settings.model_config["env_file"] == ARCHIVO_ENV == RAIZ_PROYECTO / ".env"
    assert ARCHIVO_ENV.is_absolute() and (RAIZ_PROYECTO / "app" / "config.py").is_file()


def test_el_modelo_sale_del_archivo_env(tmp_path, monkeypatch):
    monkeypatch.delenv("LLM_MODELO", raising=False)
    archivo = tmp_path / ".env"
    archivo.write_text("LLM_MODELO=proveedor/modelo-del-env\n", encoding="utf-8")
    assert Settings(_env_file=archivo).LLM_MODELO == "proveedor/modelo-del-env"
    assert config._definidas_en_env(archivo) == {"LLM_MODELO"}


def test_el_entorno_del_proceso_tiene_prioridad_y_se_informa_el_origen(tmp_path, monkeypatch):
    archivo = tmp_path / ".env"
    archivo.write_text("LLM_MODELO=proveedor/modelo-del-env\n", encoding="utf-8")
    monkeypatch.setenv("LLM_MODELO", "proveedor/modelo-del-entorno")
    assert Settings(_env_file=archivo).LLM_MODELO == "proveedor/modelo-del-entorno"
    assert config.origen("LLM_MODELO") == "entorno del proceso"


def test_sin_modelo_no_hay_valor_por_defecto_y_el_agente_no_arranca(tmp_path, monkeypatch):
    monkeypatch.delenv("LLM_MODELO", raising=False)
    monkeypatch.setattr(config, "ARCHIVO_ENV", tmp_path / "no_existe.env")
    ajustes = Settings(_env_file=None)
    assert ajustes.LLM_MODELO == ""  # nunca un modelo fijo (ni Llama ni otro)
    assert config.origen("LLM_MODELO") == "sin definir"
    with pytest.raises(RuntimeError, match="LLM_MODELO"):
        ajustes.verificar_secretos()


def test_origen_env_sin_variable_de_entorno(tmp_path, monkeypatch):
    monkeypatch.delenv("LLM_MODELO", raising=False)
    archivo = tmp_path / ".env"
    archivo.write_text("# comentario\nLLM_MODELO=proveedor/x\nOTRA=\n", encoding="utf-8")
    monkeypatch.setattr(config, "ARCHIVO_ENV", archivo)
    assert config.origen("LLM_MODELO") == ".env" and config.origen("OTRA") == "sin definir"
