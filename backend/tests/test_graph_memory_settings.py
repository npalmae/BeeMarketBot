"""Configuración del motor de memoria en grafos: qué se lee, cómo se hereda y qué se rechaza."""

import dataclasses
import json

import pytest

from app.graph_memory.errors import ConfigError
from app.graph_memory.settings import (
    DEFAULT_DATABASE,
    DEFAULT_EMBEDDING_DIMENSIONS,
    DatabaseSettings,
    GraphMemorySettings,
    resolve_thinking,
)

GENERAL = {
    "LLM_API_KEY": "sk-general",
    "LLM_BASE_URL": "https://api.openai.com/v1",
    "LLM_MODEL_NAME": "gpt-prueba",
    "EMBEDDING_MODEL": "vectores-prueba",
}


def from_env(**extra):
    return GraphMemorySettings.from_env({**GENERAL, **extra})


# ---------------------------------------------------------------- herencia y proveedores


def test_settings_inherit_from_the_general_llm_configuration():
    settings = from_env(GRAPH_DB_URL="falkordb://falkordb:6379")

    assert settings.llm.api_key == "sk-general"
    assert settings.llm.base_url == "https://api.openai.com/v1"
    assert settings.llm.model == "gpt-prueba"
    # Sin un proveedor de embeddings propio, se usa el mismo.
    assert (settings.embedding.api_key, settings.embedding.base_url) == (
        "sk-general",
        "https://api.openai.com/v1",
    )
    assert (settings.embedding.model, settings.embedding.dimensions) == (
        "vectores-prueba",
        DEFAULT_EMBEDDING_DIMENSIONS,
    )
    assert settings.database == DatabaseSettings(
        kind="falkordb", host="falkordb", port=6379, username=None, password=None, database=DEFAULT_DATABASE
    )
    assert settings.validate() == []


def test_the_extraction_model_and_the_embeddings_can_use_different_providers():
    settings = from_env(
        GRAPH_LLM_API_KEY="sk-extrae",
        GRAPH_LLM_BASE_URL="https://extrae.example/v1",
        GRAPH_LLM_MODEL="modelo-extractor",
        EMBEDDING_API_KEY="sk-vectores",
        EMBEDDING_BASE_URL="https://vectores.example/v1",
        EMBEDDING_MODEL="text-embedding-3-small",
        EMBEDDING_DIMENSIONS="1536",
    )

    assert (settings.llm.api_key, settings.llm.base_url, settings.llm.model) == (
        "sk-extrae",
        "https://extrae.example/v1",
        "modelo-extractor",
    )
    assert (settings.embedding.api_key, settings.embedding.base_url, settings.embedding.model) == (
        "sk-vectores",
        "https://vectores.example/v1",
        "text-embedding-3-small",
    )
    assert settings.embedding.dimensions == 1536
    assert settings.validate() == []


def test_blank_values_fall_back_to_the_general_configuration():
    settings = from_env(GRAPH_DB_URL="falkordb://falkordb:6379", GRAPH_LLM_API_KEY="   ", GRAPH_LLM_MODEL="")

    assert settings.llm.api_key == "sk-general"
    assert settings.llm.model == "gpt-prueba"


def test_the_embedding_model_is_never_assumed():
    # Cada proveedor usa nombres propios: adivinar uno produce un "modelo no
    # encontrado" en mitad de una ingesta. Sin EMBEDDING_MODEL, la configuración
    # no es válida y el problema se explica.
    settings = from_env(GRAPH_DB_URL="falkordb://falkordb:6379", EMBEDDING_MODEL="   ")

    assert settings.embedding.model == ""
    assert any("EMBEDDING_MODEL" in problem and "bge_m3_embed" in problem for problem in settings.validate())


def test_values_are_trimmed():
    settings = from_env(GRAPH_DB_URL="  falkordb://falkordb:6379  ", LLM_API_KEY="  sk-recortada  ")

    assert settings.llm.api_key == "sk-recortada"
    assert settings.database.host == "falkordb"


# ---------------------------------------------------------------- base de grafos


@pytest.mark.parametrize(
    "url, expected",
    [
        ("falkordb://localhost:6379", ("falkordb", "localhost", 6379, None, None, DEFAULT_DATABASE)),
        ("falkordb://falkordb", ("falkordb", "falkordb", 6379, None, None, DEFAULT_DATABASE)),
        ("cache:6380", ("falkordb", "cache", 6380, None, None, DEFAULT_DATABASE)),
        ("redis://cache:6379", ("falkordb", "cache", 6379, None, None, DEFAULT_DATABASE)),
        ("falkordb://cache:6379/espacio", ("falkordb", "cache", 6379, None, None, "espacio")),
        ("neo4j://db.example.com", ("neo4j", "db.example.com", 7687, None, None, DEFAULT_DATABASE)),
        ("bolt+s://db.example.com:7688", ("neo4j", "db.example.com", 7688, None, None, DEFAULT_DATABASE)),
        (
            "neo4j://usuario:clave@db.example.com:7687/mi_grafo",
            ("neo4j", "db.example.com", 7687, "usuario", "clave", "mi_grafo"),
        ),
        ("neo4j://usuario:p%40ss@db.example.com", ("neo4j", "db.example.com", 7687, "usuario", "p@ss", DEFAULT_DATABASE)),
    ],
)
def test_database_url_forms(url, expected):
    database = from_env(GRAPH_DB_URL=url).database

    assert (database.kind, database.host, database.port, database.username, database.password, database.database) == expected
    assert database.is_falkordb == (expected[0] == "falkordb")


def test_database_credentials_and_name_can_be_given_separately():
    database = from_env(
        GRAPH_DB_URL="neo4j://db.example.com",
        GRAPH_DB_NAME="otro",
        GRAPH_DB_USERNAME="neo4j",
        GRAPH_DB_PASSWORD="secreta",
    ).database

    assert (database.database, database.username, database.password) == ("otro", "neo4j", "secreta")


@pytest.mark.parametrize(
    "url",
    [
        "mysql://db",
        "https://example.invalid",
        "falkordb://db:puerto",
        "falkordb://db:99999",
        "://nada",
        "falkordb://:6379",
    ],
)
def test_unreadable_database_urls_are_rejected(url):
    with pytest.raises(ConfigError, match="GRAPH_DB_URL"):
        from_env(GRAPH_DB_URL=url)


# ---------------------------------------------------------------- modelo


@pytest.mark.parametrize(
    "base_url, expected",
    [
        ("https://api.kimi.ai/coding/v1", "disabled"),
        ("https://api.moonshot.cn/v1", "disabled"),
        ("https://api.z.ai/api/paas/v4", "disabled"),
        ("https://api.openai.com/v1", "omit"),
        ("https://proveedor.example/v1", "omit"),
        (None, "omit"),
    ],
)
def test_thinking_is_disabled_automatically_only_for_providers_that_accept_it(base_url, expected):
    assert resolve_thinking("auto", base_url) == expected


@pytest.mark.parametrize("mode", ["enabled", "disabled", "omit"])
def test_an_explicit_thinking_mode_applies_to_any_provider(mode):
    assert resolve_thinking(mode, "https://api.openai.com/v1") == mode


def test_an_unknown_thinking_mode_is_rejected():
    with pytest.raises(ConfigError, match="GRAPH_LLM_THINKING"):
        resolve_thinking("tal vez", None)
    with pytest.raises(ConfigError, match="GRAPH_LLM_THINKING"):
        from_env(GRAPH_DB_URL="falkordb://db", GRAPH_LLM_THINKING="tal vez")


def test_thinking_resolution_follows_the_effective_base_url():
    settings = from_env(GRAPH_DB_URL="falkordb://db", GRAPH_LLM_BASE_URL="https://api.kimi.com/coding/v1")
    assert settings.llm.thinking == "auto"
    assert settings.llm.resolved_thinking() == "disabled"


def test_tuning_options_and_their_bounds():
    settings = from_env(
        GRAPH_DB_URL="falkordb://db",
        GRAPH_INGESTION_WINDOW="500",
        GRAPH_LLM_CONCURRENCY="0",
        GRAPH_LLM_TEMPERATURE="0.2",
        GRAPH_LLM_STRUCTURED_OUTPUT="json_object",
        GRAPH_REQUEST_TIMEOUT="120",
    )

    assert (settings.ingestion_window, settings.llm_concurrency) == (32, 1)
    assert settings.llm.temperature == 0.2
    assert settings.llm.structured_output == "json_object"
    assert settings.request_timeout == 120

    defaults = from_env(GRAPH_DB_URL="falkordb://db")
    assert defaults.ingestion_window > 1
    assert defaults.llm.temperature is not None
    assert defaults.llm.structured_output == "json_schema"


@pytest.mark.parametrize(
    "name, value",
    [
        ("GRAPH_INGESTION_WINDOW", "abc"),
        ("GRAPH_LLM_CONCURRENCY", "1.5"),
        ("GRAPH_LLM_TEMPERATURE", "caliente"),
        ("GRAPH_LLM_STRUCTURED_OUTPUT", "xml"),
        ("GRAPH_REQUEST_TIMEOUT", "lento"),
        ("EMBEDDING_DIMENSIONS", "muchas"),
    ],
)
def test_unreadable_tuning_values_are_rejected(name, value):
    with pytest.raises(ConfigError, match=name):
        from_env(GRAPH_DB_URL="falkordb://db", **{name: value})


def test_query_limit_can_be_configured_or_left_to_the_server():
    automatic = from_env(GRAPH_DB_URL="falkordb://db")
    assert automatic.database.max_concurrent_queries == 0
    assert automatic.redacted()["database"]["max_concurrent_queries"] == 0

    explicit = from_env(GRAPH_DB_URL="falkordb://db", GRAPH_DB_MAX_CONCURRENT_QUERIES="6")
    assert explicit.database.max_concurrent_queries == 6

    assert from_env(GRAPH_DB_URL="falkordb://db", GRAPH_DB_MAX_CONCURRENT_QUERIES="-3").database.max_concurrent_queries == 0
    with pytest.raises(ConfigError, match="GRAPH_DB_MAX_CONCURRENT_QUERIES"):
        from_env(GRAPH_DB_URL="falkordb://db", GRAPH_DB_MAX_CONCURRENT_QUERIES="pocos")


# ---------------------------------------------------------------- validación y resumen


def test_validate_reports_each_missing_piece():
    problems = GraphMemorySettings.from_env({}).validate()

    for fragment in ("GRAPH_LLM_API_KEY", "GRAPH_LLM_MODEL", "EMBEDDING_API_KEY", "EMBEDDING_MODEL"):
        assert any(fragment in problem for problem in problems), fragment


def test_validate_reports_an_unusable_database():
    settings = dataclasses.replace(
        GraphMemorySettings.from_env(GENERAL),
        database=DatabaseSettings(kind="", host="", port=1, database=""),
    )

    problems = settings.validate()

    for fragment in ("base de datos no admitida", "host", "GRAPH_DB_NAME"):
        assert any(fragment in problem for problem in problems), fragment


def test_validate_reports_an_unusable_embedding_size_and_database():
    settings = from_env(
        GRAPH_DB_URL="falkordb://db/espacio",
        EMBEDDING_DIMENSIONS="0",
    )
    assert any("EMBEDDING_DIMENSIONS" in problem for problem in settings.validate())

    empty_name = from_env(GRAPH_DB_URL="falkordb://db", GRAPH_DB_NAME="")
    assert empty_name.database.database == DEFAULT_DATABASE

    unsupported = dataclasses.replace(
        from_env(GRAPH_DB_URL="falkordb://db"),
        database=DatabaseSettings(kind="sqlite", host="db", port=1, database=DEFAULT_DATABASE),
    )
    assert any("base de datos no admitida" in problem for problem in unsupported.validate())


def test_redacted_summary_shows_the_shape_of_a_secret_but_never_the_secret():
    llm_key, embedding_key, password = "sk-secreto-1234567890", "sk-otro-0987654321", "clave-super-secreta"
    settings = from_env(
        GRAPH_LLM_API_KEY=llm_key,
        EMBEDDING_API_KEY=embedding_key,
        GRAPH_DB_URL=f"neo4j://usuario:{password}@db.example.com:7687/app",
    )

    summary = settings.redacted_json()
    shown = json.loads(summary)

    for secret in ("secreto", "1234567890", "0987654321", "clave", "super"):
        assert secret not in summary, secret
    assert shown["llm"]["api_key"] == f"sk-s…({len(llm_key)})"
    assert shown["embedding"]["api_key"] == f"sk-o…({len(embedding_key)})"
    assert shown["database"]["password"] == f"clav…({len(password)})"
    # Lo que sí sirve para diagnosticar queda a la vista.
    assert shown["llm"]["model"] == "gpt-prueba"
    assert shown["database"]["kind"] == "neo4j"
    assert shown["database"]["host"] == "db.example.com"
    assert shown["database"]["database"] == "app"
    assert shown["embedding"]["dimensions"] == DEFAULT_EMBEDDING_DIMENSIONS


def test_short_secrets_are_not_revealed_either():
    settings = from_env(GRAPH_DB_URL="neo4j://u:xyz@db", LLM_API_KEY="k1")

    summary = settings.redacted_json()

    assert "xyz" not in summary
    assert "k1" not in summary
    assert json.loads(summary)["llm"]["api_key"] == "…"
