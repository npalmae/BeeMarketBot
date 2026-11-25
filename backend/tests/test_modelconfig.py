"""Configuración de los servicios de IA desde la interfaz: guardado, aplicación
en caliente, enmascarado de claves y API de administración."""

from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from app import create_app
from app import modelconfig
from app.auth.roles import ADMIN, EDITOR
from app.config import Config

PASSWORD = "una-clave-valida"


@pytest.fixture
def models_file(tmp_path, monkeypatch):
    """La configuración de la interfaz se guarda en una carpeta desechable."""

    monkeypatch.setattr(Config, "UPLOAD_FOLDER", str(tmp_path))
    return tmp_path / "config" / "models.json"


@pytest.fixture
def app(models_file):
    application = create_app()
    application.config.update(TESTING=True)
    return application


def _sign_in(app, role):
    with app.app_context():
        app.extensions["auth"].create_user(f"user-{role}", PASSWORD, role)
    client = app.test_client()
    token = client.get("/api/auth/session").get_json()["data"]["csrf"]
    response = client.post(
        "/api/auth/login",
        json={"username": f"user-{role}", "password": PASSWORD},
        headers={"X-CSRF-Token": token},
    )
    assert response.status_code == 200, response.get_json()
    client.csrf = response.get_json()["data"]["csrf"]
    return client


def _put(client, body):
    return client.put("/api/config/models", json=body, headers={"X-CSRF-Token": client.csrf})


# ------------------------------------------------------------------ módulo


class TestStore:
    def test_empty_by_default(self, models_file):
        cfg = modelconfig.load()
        assert cfg["llm"]["api_key"] == ""
        assert cfg["embedding"]["dimensions"] == ""

    def test_roundtrip(self, models_file):
        modelconfig.save({"llm": {"base_url": "https://mi-gpu.local/v1", "model": "qwen"}})
        cfg = modelconfig.load()
        assert cfg["llm"]["base_url"] == "https://mi-gpu.local/v1"
        assert cfg["llm"]["model"] == "qwen"
        assert cfg["llm"]["api_key"] == ""

    def test_file_is_private(self, models_file):
        modelconfig.save({"llm": {"api_key": "sk-secreta-1234"}})
        mode = os.stat(models_file).st_mode & 0o777
        assert mode == 0o600

    def test_rejects_bad_url(self, models_file):
        with pytest.raises(ValueError, match="base_url"):
            modelconfig.save({"llm": {"base_url": "sin-esquema.local/v1"}})

    def test_rejects_bad_dimensions(self, models_file):
        with pytest.raises(ValueError, match="dimensions"):
            modelconfig.save({"embedding": {"dimensions": "mil"}})

    def test_effective_env_overrides(self, models_file, monkeypatch):
        monkeypatch.setenv("LLM_BASE_URL", "https://del-servidor/v1")
        modelconfig.save({"llm": {"base_url": "https://de-la-interfaz/v1"}})
        assert modelconfig.effective_env()["LLM_BASE_URL"] == "https://de-la-interfaz/v1"
        # Un campo vacío hereda el valor del servidor.
        modelconfig.save({"llm": {"base_url": ""}})
        assert modelconfig.effective_env()["LLM_BASE_URL"] == "https://del-servidor/v1"

    def test_apply_updates_config(self, models_file, monkeypatch):
        monkeypatch.setenv("LLM_API_KEY", "sk-del-servidor-9999")
        Config.LLM_API_KEY = "sk-del-servidor-9999"
        modelconfig.save({"llm": {"api_key": "sk-de-la-interfaz-1234"}})
        with patch("app.graph_memory.client.reset_shared_client"):
            modelconfig.apply()
        assert os.environ["LLM_API_KEY"] == "sk-de-la-interfaz-1234"
        assert Config.LLM_API_KEY == "sk-de-la-interfaz-1234"

    def test_masked_never_exposes_full_key(self, models_file, monkeypatch):
        monkeypatch.setenv("LLM_API_KEY", "sk-servidor-abcdef1234")
        modelconfig.save({"llm": {"api_key": "sk-interfaz-xyz9876"}})
        view = modelconfig.masked()
        assert view["llm"]["api_key"]["value"] == ""
        assert view["llm"]["api_key"]["preview"] == "…9876"
        assert view["llm"]["api_key"]["source"] == "interface"
        # La clave completa no aparece en ninguna parte de la vista.
        assert "xyz9876" not in json.dumps(view)
        assert "abcdef1234" not in json.dumps(view)


class TestConnectionTest:
    def test_llm_probe(self, models_file, monkeypatch):
        monkeypatch.setenv("LLM_BASE_URL", "https://api.ejemplo/v1")
        monkeypatch.setenv("LLM_API_KEY", "sk-x")
        monkeypatch.setenv("LLM_MODEL_NAME", "modelo")
        client = MagicMock()
        with patch("openai.OpenAI", return_value=client):
            result = modelconfig.test_connection("llm")
        assert result["ok"] is True
        client.chat.completions.create.assert_called_once()

    def test_embedding_probe(self, models_file, monkeypatch):
        monkeypatch.setenv("EMBEDDING_BASE_URL", "https://api.ejemplo/v1")
        monkeypatch.setenv("EMBEDDING_API_KEY", "sk-x")
        monkeypatch.setenv("EMBEDDING_MODEL", "bge-m3")
        client = MagicMock()
        with patch("openai.OpenAI", return_value=client):
            result = modelconfig.test_connection("embedding")
        assert result["ok"] is True
        client.embeddings.create.assert_called_once()

    def test_probe_failure_reports_error(self, models_file, monkeypatch):
        monkeypatch.setenv("LLM_BASE_URL", "https://api.ejemplo/v1")
        monkeypatch.setenv("LLM_API_KEY", "sk-x")
        monkeypatch.setenv("LLM_MODEL_NAME", "modelo")
        client = MagicMock()
        client.chat.completions.create.side_effect = RuntimeError("rechazada")
        with patch("openai.OpenAI", return_value=client):
            result = modelconfig.test_connection("llm")
        assert result["ok"] is False
        assert "rechazada" in result["error"]

    def test_probe_requires_complete_config(self, models_file, monkeypatch):
        monkeypatch.delenv("EMBEDDING_MODEL", raising=False)
        result = modelconfig.test_connection("embedding")
        assert result["ok"] is False
        assert "faltan" in result["error"]

    def test_embedding_inherits_llm_endpoint(self, models_file, monkeypatch):
        """Sin configuración propia, los embeddings usan la URL y la clave del LLM."""
        monkeypatch.setenv("LLM_BASE_URL", "https://api.ejemplo/v1")
        monkeypatch.setenv("LLM_API_KEY", "sk-del-llm")
        monkeypatch.setenv("EMBEDDING_MODEL", "bge-m3")
        monkeypatch.delenv("EMBEDDING_BASE_URL", raising=False)
        monkeypatch.delenv("EMBEDDING_API_KEY", raising=False)
        env = modelconfig.effective_env()
        assert env["EMBEDDING_BASE_URL"] == "https://api.ejemplo/v1"
        assert env["EMBEDDING_API_KEY"] == "sk-del-llm"
        # La prueba de conexión se beneficia igual.
        client = MagicMock()
        with patch("openai.OpenAI", return_value=client) as factory:
            result = modelconfig.test_connection("embedding")
        assert result["ok"] is True
        assert factory.call_args.kwargs["base_url"] == "https://api.ejemplo/v1"

    def test_probe_uses_unsaved_overrides(self, models_file, monkeypatch):
        monkeypatch.delenv("LLM_BASE_URL", raising=False)
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        monkeypatch.delenv("LLM_MODEL_NAME", raising=False)
        client = MagicMock()
        with patch("openai.OpenAI", return_value=client) as factory:
            result = modelconfig.test_connection(
                "llm",
                {"base_url": "https://nueva-gpu/v1", "api_key": "sk-nueva", "model": "qwen"},
            )
        assert result["ok"] is True
        assert factory.call_args.kwargs["base_url"] == "https://nueva-gpu/v1"


# ---------------------------------------------------------------------- API


class TestApi:
    def test_requires_admin(self, app):
        viewer_client = _sign_in(app, EDITOR)
        assert viewer_client.get("/api/config/models").status_code == 403
        assert _put(viewer_client, {}).status_code == 403

    def test_requires_login(self, app):
        client = app.test_client()
        assert client.get("/api/config/models").status_code == 401

    def test_get_masks_keys(self, app, models_file, monkeypatch):
        monkeypatch.setenv("LLM_API_KEY", "sk-servidor-0000abcd")
        admin = _sign_in(app, ADMIN)
        response = admin.get("/api/config/models")
        assert response.status_code == 200
        data = response.get_json()["data"]
        assert data["llm"]["api_key"]["preview"] == "…abcd"
        assert data["llm"]["api_key"]["source"] == "server"
        assert "0000abcd" not in json.dumps(data)

    def test_put_saves_applies_and_keeps_saved_key(self, app, models_file, monkeypatch):
        monkeypatch.setenv("LLM_API_KEY", "sk-del-servidor-9999")
        with patch("app.graph_memory.client.reset_shared_client"):
            admin = _sign_in(app, ADMIN)
            # Primero se guarda una clave…
            response = _put(admin, {"llm": {"api_key": "sk-guardada-5555"}})
            assert response.status_code == 200
            # …y después una URL nueva sin reenviar la clave: se conserva.
            response = _put(admin, {"llm": {"base_url": "https://mi-gpu/v1", "api_key": None}})
            assert response.status_code == 200
            cfg = modelconfig.load()
            assert cfg["llm"]["base_url"] == "https://mi-gpu/v1"
            assert cfg["llm"]["api_key"] == "sk-guardada-5555"
            assert os.environ["LLM_BASE_URL"] == "https://mi-gpu/v1"

    def test_put_validates(self, app, models_file, monkeypatch):
        monkeypatch.setenv("LLM_API_KEY", "sk-del-servidor-9999")
        with patch("app.graph_memory.client.reset_shared_client"):
            admin = _sign_in(app, ADMIN)
            response = _put(admin, {"embedding": {"dimensions": "no-numero"}})
            assert response.status_code == 400
            assert "dimensions" in response.get_json()["error"]

    def test_test_endpoint(self, app, models_file, monkeypatch):
        monkeypatch.setenv("LLM_API_KEY", "sk-del-servidor-9999")
        with patch("app.graph_memory.client.reset_shared_client"):
            admin = _sign_in(app, ADMIN)
            client = MagicMock()
            with patch("openai.OpenAI", return_value=client):
                response = admin.post(
                    "/api/config/models/test",
                    json={"kind": "llm"},
                    headers={"X-CSRF-Token": admin.csrf},
                )
            assert response.status_code == 200
            assert response.get_json()["data"]["ok"] is True
