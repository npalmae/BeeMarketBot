"""Configuración del motor de memoria en grafos.

Se lee de variables de entorno para poder conectar cualquier proveedor:

- ``LLM_*``: API del modelo de lenguaje (formato OpenAI). El motor reutiliza
  estos valores salvo que se definan los específicos ``GRAPH_LLM_*``.
- ``EMBEDDING_*``: API de embeddings (formato OpenAI). Puede ser la misma
  cuenta que el modelo u otra distinta.
- ``GRAPH_DB_*``: base de datos de grafos (FalkorDB o Neo4j).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import unquote, urlsplit

from .errors import ConfigError

# Proveedores que aceptan el parámetro ``thinking`` para activar o desactivar
# el razonamiento. Con ``GRAPH_LLM_THINKING=auto`` solo se envía a estos hosts.
_THINKING_HOSTS = (
    "api.kimi.ai",
    "api.kimi.com",
    "api.moonshot.ai",
    "api.moonshot.cn",
    "api.z.ai",
    "open.bigmodel.cn",
)

THINKING_MODES = ("auto", "disabled", "enabled", "omit")
STRUCTURED_OUTPUT_MODES = ("json_schema", "json_object")
DATABASE_KINDS = ("falkordb", "neo4j")

# Tamaño de los vectores cuando no se indica: el de bge_m3 (1024 dimensiones).
# Los modelos de embeddings no se adivinan: cada proveedor usa nombres propios.
DEFAULT_EMBEDDING_DIMENSIONS = 1024
DEFAULT_GRAPH_LLM_TEMPERATURE = 0.6
DEFAULT_DATABASE = "beemarketbot"


def _env(env: Mapping[str, str] | None, name: str, default: str | None = None) -> str | None:
    value = (env if env is not None else os.environ).get(name)
    if value is None or not value.strip():
        return default
    return value.strip()


def _int_env(env: Mapping[str, str] | None, name: str, default: int) -> int:
    raw = _env(env, name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as error:
        raise ConfigError(f"{name} debe ser un número entero") from error


def _float_env(env: Mapping[str, str] | None, name: str, default: float | None) -> float | None:
    raw = _env(env, name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError as error:
        raise ConfigError(f"{name} debe ser un número") from error


def _host_of(base_url: str | None) -> str:
    if not base_url:
        return ""
    parts = urlsplit(base_url if "://" in base_url else f"//{base_url}")
    return (parts.hostname or "").lower()


def resolve_thinking(mode: str, base_url: str | None) -> str:
    """Devuelve ``disabled``, ``enabled`` u ``omit`` según el modo y el proveedor."""

    if mode not in THINKING_MODES:
        raise ConfigError(
            f"GRAPH_LLM_THINKING debe ser uno de: {', '.join(THINKING_MODES)}"
        )
    if mode != "auto":
        return mode
    return "disabled" if _host_of(base_url) in _THINKING_HOSTS else "omit"


@dataclass(frozen=True)
class LLMSettings:
    """API del modelo que extrae entidades y relaciones."""

    api_key: str
    base_url: str | None
    model: str
    temperature: float | None = DEFAULT_GRAPH_LLM_TEMPERATURE
    thinking: str = "auto"
    structured_output: str = "json_schema"

    def resolved_thinking(self) -> str:
        return resolve_thinking(self.thinking, self.base_url)


@dataclass(frozen=True)
class EmbeddingSettings:
    """API de embeddings que vectoriza el grafo y las consultas."""

    api_key: str
    base_url: str | None
    model: str
    dimensions: int = DEFAULT_EMBEDDING_DIMENSIONS


@dataclass(frozen=True)
class DatabaseSettings:
    """Base de datos de grafos."""

    kind: str
    host: str
    port: int
    username: str | None = None
    password: str | None = None
    database: str = DEFAULT_DATABASE
    # Consultas simultáneas permitidas; 0 las calcula a partir de los hilos del servidor.
    max_concurrent_queries: int = 0

    @property
    def is_falkordb(self) -> bool:
        return self.kind == "falkordb"


def _parse_database_url(
    url: str, env: Mapping[str, str] | None
) -> tuple[str, str, int, str | None, str | None, str | None]:
    """Devuelve (tipo, host, puerto, usuario, contraseña, nombre) de una URL."""

    if "://" not in url:
        url = f"falkordb://{url}"
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme in ("falkordb", "redis", "rediss"):
        kind, default_port = "falkordb", 6379
    elif scheme in ("neo4j", "neo4j+s", "neo4j+ssc", "bolt", "bolt+s"):
        kind, default_port = "neo4j", 7687
    else:
        raise ConfigError(
            f"GRAPH_DB_URL: esquema no reconocido '{scheme}'. "
            "Use falkordb://host:puerto o neo4j://usuario:clave@host:puerto"
        )
    try:
        port = parts.port or default_port
    except ValueError as error:
        raise ConfigError(f"GRAPH_DB_URL: puerto inválido en {url}") from error
    if not parts.hostname:
        # No se adivina "localhost": en un despliegue con contenedores apuntaría a otro sitio.
        raise ConfigError(f"GRAPH_DB_URL: falta el host de la base de datos en {url}")
    username = unquote(parts.username) if parts.username else None
    password = unquote(parts.password) if parts.password else None
    name = parts.path.strip("/") or _env(env, "GRAPH_DB_NAME", DEFAULT_DATABASE)
    return kind, parts.hostname, port, username, password, name


@dataclass(frozen=True)
class GraphMemorySettings:
    """Configuración completa del motor de memoria en grafos."""

    llm: LLMSettings
    embedding: EmbeddingSettings
    database: DatabaseSettings
    # Cuántos episodios se procesan juntos en una ventana de ingesta.
    ingestion_window: int = 8
    # Llamadas simultáneas al modelo durante la ingesta, y búsquedas a la vez
    # (cada búsqueda consume una llamada de embeddings).
    llm_concurrency: int = 5
    # Espera máxima, en segundos, de una operación iniciada desde un hilo del backend.
    request_timeout: float = 600.0

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "GraphMemorySettings":
        llm_api_key = _env(env, "GRAPH_LLM_API_KEY") or _env(env, "LLM_API_KEY")
        llm_base_url = _env(env, "GRAPH_LLM_BASE_URL") or _env(env, "LLM_BASE_URL")
        llm_model = _env(env, "GRAPH_LLM_MODEL") or _env(env, "LLM_MODEL_NAME")
        thinking = (_env(env, "GRAPH_LLM_THINKING", "auto") or "auto").lower()
        # Valida el modo explícito aquí para fallar pronto con un valor inválido.
        if thinking != "auto":
            resolve_thinking(thinking, llm_base_url)
        structured = (
            _env(env, "GRAPH_LLM_STRUCTURED_OUTPUT", "json_schema") or "json_schema"
        ).lower()
        if structured not in STRUCTURED_OUTPUT_MODES:
            raise ConfigError(
                "GRAPH_LLM_STRUCTURED_OUTPUT debe ser uno de: "
                + ", ".join(STRUCTURED_OUTPUT_MODES)
            )
        llm = LLMSettings(
            api_key=(llm_api_key or "").strip(),
            base_url=llm_base_url,
            model=(llm_model or "").strip(),
            temperature=_float_env(
                env, "GRAPH_LLM_TEMPERATURE", DEFAULT_GRAPH_LLM_TEMPERATURE
            ),
            thinking=thinking,
            structured_output=structured,
        )

        embedding = EmbeddingSettings(
            api_key=(_env(env, "EMBEDDING_API_KEY") or llm.api_key or "").strip(),
            base_url=_env(env, "EMBEDDING_BASE_URL") or llm.base_url,
            model=(_env(env, "EMBEDDING_MODEL") or "").strip(),
            dimensions=_int_env(env, "EMBEDDING_DIMENSIONS", DEFAULT_EMBEDDING_DIMENSIONS),
        )

        url = _env(env, "GRAPH_DB_URL", "falkordb://localhost:6379") or "falkordb://localhost:6379"
        kind, host, port, username, password, name = _parse_database_url(url, env)
        database = DatabaseSettings(
            kind=kind,
            host=host,
            port=port,
            username=username or _env(env, "GRAPH_DB_USERNAME"),
            password=password or _env(env, "GRAPH_DB_PASSWORD"),
            database=name or DEFAULT_DATABASE,
            max_concurrent_queries=max(0, _int_env(env, "GRAPH_DB_MAX_CONCURRENT_QUERIES", 0)),
        )

        window = _int_env(env, "GRAPH_INGESTION_WINDOW", 8)
        concurrency = _int_env(env, "GRAPH_LLM_CONCURRENCY", 5)
        timeout = _float_env(env, "GRAPH_REQUEST_TIMEOUT", 600.0)
        return cls(
            llm=llm,
            embedding=embedding,
            database=database,
            ingestion_window=max(1, min(window, 32)),
            llm_concurrency=max(1, min(concurrency, 32)),
            request_timeout=timeout if timeout and timeout > 0 else 600.0,
        )

    def validate(self) -> list[str]:
        """Devuelve la lista de problemas de configuración; vacía si todo sirve."""

        errors: list[str] = []
        if not self.llm.api_key:
            errors.append("GRAPH_LLM_API_KEY (o LLM_API_KEY) no está configurada")
        if not self.llm.model:
            errors.append("GRAPH_LLM_MODEL (o LLM_MODEL_NAME) no está configurado")
        if not self.embedding.api_key:
            errors.append("EMBEDDING_API_KEY (o LLM_API_KEY) no está configurada")
        if not self.embedding.model:
            errors.append(
                "EMBEDDING_MODEL no está configurado: indica el modelo de embeddings del proveedor "
                "(p. ej. bge_m3_embed para Kimi, text-embedding-3-small para OpenAI)"
            )
        if self.embedding.dimensions < 1:
            errors.append("EMBEDDING_DIMENSIONS debe ser mayor que 0")
        if self.database.kind not in DATABASE_KINDS:
            errors.append(
                f"GRAPH_DB_URL: base de datos no admitida '{self.database.kind}'. "
                f"Use una de: {', '.join(DATABASE_KINDS)}"
            )
        if not self.database.host:
            errors.append("GRAPH_DB_URL: falta el host de la base de datos")
        if not self.database.database:
            errors.append("GRAPH_DB_NAME no puede estar vacío")
        if self.llm.structured_output not in STRUCTURED_OUTPUT_MODES:
            errors.append(
                "GRAPH_LLM_STRUCTURED_OUTPUT debe ser uno de: "
                + ", ".join(STRUCTURED_OUTPUT_MODES)
            )
        return errors

    def redacted(self) -> dict[str, Any]:
        """Resumen para logs, sin claves ni contraseñas."""

        def masked(value: str | None) -> str | None:
            if not value:
                return value
            return f"{value[:4]}…({len(value)})" if len(value) > 8 else "…"

        llm_thinking = self.llm.thinking
        database = self.database
        return {
            "llm": {
                "base_url": self.llm.base_url,
                "model": self.llm.model,
                "temperature": self.llm.temperature,
                "thinking": llm_thinking,
                "structured_output": self.llm.structured_output,
                "api_key": masked(self.llm.api_key),
            },
            "embedding": {
                "base_url": self.embedding.base_url,
                "model": self.embedding.model,
                "dimensions": self.embedding.dimensions,
                "api_key": masked(self.embedding.api_key),
            },
            "database": {
                "kind": database.kind,
                "host": database.host,
                "port": database.port,
                "database": database.database,
                "username": database.username,
                "password": masked(database.password),
                "max_concurrent_queries": database.max_concurrent_queries,
            },
            "ingestion_window": self.ingestion_window,
            "llm_concurrency": self.llm_concurrency,
        }

    def redacted_json(self) -> str:
        return json.dumps(self.redacted(), ensure_ascii=False)
