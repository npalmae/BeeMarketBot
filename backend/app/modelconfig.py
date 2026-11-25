"""Conexión con los servicios de IA, configurable desde la interfaz.

El servidor define los valores por defecto con variables de entorno
(``LLM_*``, ``EMBEDDING_*``). Un administrador puede cambiarlos en caliente
desde la sección Modelos: se guardan en ``uploads/config/models.json``
(persiste con los datos de la aplicación, entra en las copias de seguridad) y
se aplican al entorno y a ``Config``, así que cada operación nueva usa los
valores vigentes sin reiniciar.

Un campo vacío significa "usar el valor del servidor". Las claves nunca salen
completas por la API: se devuelven enmascaradas.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Mapping

from .config import Config

# Dónde se guarda la configuración hecha desde la interfaz.
def _path() -> str:
    return os.path.join(Config.UPLOAD_FOLDER, "config", "models.json")


# Campos de cada servicio y la variable de entorno que pisan.
FIELDS = {
    "llm": {
        "base_url": "LLM_BASE_URL",
        "api_key": "LLM_API_KEY",
        "model": "LLM_MODEL_NAME",
    },
    "embedding": {
        "base_url": "EMBEDDING_BASE_URL",
        "api_key": "EMBEDDING_API_KEY",
        "model": "EMBEDDING_MODEL",
        "dimensions": "EMBEDDING_DIMENSIONS",
    },
}

SECRET_FIELDS = {"api_key"}


def _empty() -> dict[str, dict[str, str]]:
    return {kind: {field: "" for field in FIELDS[kind]} for kind in FIELDS}


def load() -> dict[str, dict[str, str]]:
    """La configuración guardada desde la interfaz; vacía si nunca se ha usado."""
    cfg = _empty()
    try:
        with open(_path(), encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return cfg
    for kind in FIELDS:
        if isinstance(raw.get(kind), dict):
            for field in FIELDS[kind]:
                value = raw[kind].get(field)
                if value is not None:
                    cfg[kind][field] = str(value).strip()
    return cfg


def save(cfg: Mapping[str, Mapping[str, Any]]) -> None:
    """Valida y guarda. Lanza ValueError con el problema si algo no sirve."""
    cleaned = _empty()
    errors: list[str] = []
    for kind in FIELDS:
        incoming = cfg.get(kind) or {}
        if not isinstance(incoming, Mapping):
            errors.append(f"{kind}: se esperaba un objeto")
            continue
        for field in FIELDS[kind]:
            value = str(incoming.get(field) or "").strip()
            if field == "base_url" and value and not value.startswith(("http://", "https://")):
                errors.append(f"{kind}.base_url debe partir con http:// o https://")
            if field == "dimensions" and value:
                if not value.isdigit() or int(value) <= 0:
                    errors.append(f"{kind}.dimensions debe ser un entero positivo")
            cleaned[kind][field] = value
    if errors:
        raise ValueError("; ".join(errors))
    os.makedirs(os.path.dirname(_path()), exist_ok=True)
    # La clave queda legible solo para el proceso de la aplicación.
    fd = os.open(_path(), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(cleaned, handle, indent=2, ensure_ascii=False)


def _mask(value: str) -> str:
    """Vista segura de una clave: solo los últimos 4 caracteres."""
    if not value:
        return ""
    return "…" + value[-4:] if len(value) > 4 else "…"


def masked(cfg: Mapping[str, Mapping[str, str]] | None = None) -> dict[str, Any]:
    """La configuración guardada, lista para mostrar: claves enmascaradas y, por
    campo, de dónde sale el valor vigente (interfaz o servidor)."""
    cfg = cfg if cfg is not None else load()
    env = effective_env(cfg)
    out: dict[str, Any] = {}
    for kind in FIELDS:
        section: dict[str, Any] = {}
        for field, env_key in FIELDS[kind].items():
            override = cfg[kind].get(field, "")
            effective = env.get(env_key, "")
            entry: dict[str, Any] = {
                "source": "interface" if override else "server",
                "set": bool(effective),
            }
            if field in SECRET_FIELDS:
                entry["value"] = ""
                entry["preview"] = _mask(effective)
            else:
                entry["value"] = override
                entry["effective"] = effective
            section[field] = entry
        out[kind] = section
    return out


def effective_env(cfg: Mapping[str, Mapping[str, str]] | None = None) -> dict[str, str]:
    """Entorno del proceso con los campos configurados en la interfaz encima."""
    merged = dict(os.environ)
    cfg = cfg if cfg is not None else load()
    for kind in FIELDS:
        for field, env_key in FIELDS[kind].items():
            value = cfg[kind].get(field, "")
            if value:
                merged[env_key] = value
    # Como en GraphMemorySettings: los embeddings heredan la URL y la clave del
    # modelo de lenguaje cuando no tienen las suyas.
    if not merged.get("EMBEDDING_BASE_URL") and merged.get("LLM_BASE_URL"):
        merged["EMBEDDING_BASE_URL"] = merged["LLM_BASE_URL"]
    if not merged.get("EMBEDDING_API_KEY") and merged.get("LLM_API_KEY"):
        merged["EMBEDDING_API_KEY"] = merged["LLM_API_KEY"]
    return merged


def apply(cfg: Mapping[str, Mapping[str, str]] | None = None) -> None:
    """Aplica la configuración guardada: entorno y Config quedan al día, así que
    los servicios (que leen Config al crearse) usan los valores vigentes."""
    cfg = cfg if cfg is not None else load()
    for kind in FIELDS:
        for field, env_key in FIELDS[kind].items():
            value = cfg[kind].get(field, "")
            if value:
                os.environ[env_key] = value
    Config.LLM_API_KEY = os.environ.get("LLM_API_KEY")
    Config.LLM_BASE_URL = os.environ.get("LLM_BASE_URL", Config.LLM_BASE_URL)
    Config.LLM_MODEL_NAME = os.environ.get("LLM_MODEL_NAME", Config.LLM_MODEL_NAME)
    # El cliente de grafos compartido guarda la configuración con que se creó:
    # se olvida para que la próxima operación use los valores vigentes.
    from .graph_memory.client import reset_shared_client
    reset_shared_client()


def test_connection(kind: str, overrides: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Prueba mínima contra un servicio: 1 token para el modelo, un embedding
    corto para embeddings. `overrides` permite probar valores sin guardarlos."""
    if kind not in FIELDS:
        return {"ok": False, "error": f"servicio desconocido: {kind}"}
    cfg = load()
    for field in FIELDS[kind]:
        value = (overrides or {}).get(field)
        if value:
            cfg[kind][field] = str(value).strip()
    env = effective_env(cfg)
    names = FIELDS[kind]
    base_url = env.get(names["base_url"], "")
    api_key = env.get(names["api_key"], "")
    model = env.get(names["model"], "")
    if not base_url or not api_key or not model:
        return {"ok": False, "error": "faltan datos: URL base, clave o modelo"}
    from openai import OpenAI

    client = OpenAI(api_key=api_key, base_url=base_url, timeout=20.0, max_retries=0)
    started = time.monotonic()
    try:
        if kind == "llm":
            client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=1,
            )
        else:
            client.embeddings.create(model=model, input="ping")
    except Exception as error:  # la API del SDK envuelve los errores HTTP
        return {"ok": False, "error": str(error)[:300]}
    latency_ms = round((time.monotonic() - started) * 1000)
    return {"ok": True, "latency_ms": latency_ms, "model": model, "base_url": base_url}
