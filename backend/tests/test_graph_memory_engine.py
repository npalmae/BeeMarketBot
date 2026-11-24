"""Motor de grafos: clasificación de errores, ciclo de vida y tipos de la ontología.

Lo que necesita una base o un modelo se cubre con la base real en
``test_graph_memory_integration.py`` y con el motor falso en las pruebas del cliente.
"""

import asyncio
import dataclasses
import logging
from types import SimpleNamespace
from typing import Optional

import httpx
import openai
import pytest
import redis.exceptions
from neo4j.exceptions import ServiceUnavailable
from pydantic import Field
from graphiti_core.errors import EntityTypeValidationError, GroupIdValidationError
from graphiti_core.nodes import EntityNode

from app.graph_memory import EdgeModel, EntityModel, EntityText
from app.graph_memory.engine import (
    GraphitiEngine,
    OmitQueryText,
    _as_attributes,
    _as_datetime,
    _uuids_of,
    SEARCH_RERANK_LAMBDA,
    gated_falkordb_driver,
    semantic_recipe,
)
from app.graph_memory.errors import (
    AuthError,
    BadRequestError,
    ConfigError,
    GraphMemoryError,
    NotFoundError,
    RateLimitError,
    ServerError,
)
from app.graph_memory.types import (
    RESERVED_FIELD_NAMES,
    class_name,
    edge_definition,
    entity_definition,
    field_name,
    model_from_definition,
)
from app.utils.zep import is_retryable_zep_error

REQUEST = httpx.Request("POST", "https://proveedor.example/v1/chat/completions")


def api_error(cls, status, message="falló", headers=None):
    return cls(message, response=httpx.Response(status, request=REQUEST, headers=headers or {}), body=None)


# ---------------------------------------------------------------- clasificación de errores


def test_rate_limit_keeps_the_waiting_time_asked_by_the_provider():
    error = GraphitiEngine._translate(api_error(openai.RateLimitError, 429, headers={"Retry-After": "12"}))

    assert type(error) is RateLimitError
    assert (error.status_code, error.retry_after) == (429, 12.0)


def test_rate_limit_without_a_waiting_time():
    error = GraphitiEngine._translate(api_error(openai.RateLimitError, 429, headers={"retry-after": "lueguito"}))

    assert error.retry_after is None
    assert error.status_code == 429


@pytest.mark.parametrize(
    "failure, expected, status",
    [
        (lambda: api_error(openai.AuthenticationError, 401), AuthError, 401),
        (lambda: api_error(openai.PermissionDeniedError, 403), AuthError, 401),
        (lambda: api_error(openai.BadRequestError, 400), BadRequestError, 400),
        (lambda: GroupIdValidationError("grafo con espacios"), BadRequestError, 400),
        (lambda: EntityTypeValidationError("Persona", "name"), BadRequestError, 400),
        (lambda: openai.APIConnectionError(message="sin red", request=REQUEST), ServerError, 503),
        (lambda: openai.APITimeoutError(request=REQUEST), ServerError, 503),
        (lambda: redis.exceptions.ConnectionError("Connection closed by server."), ServerError, 503),
        (lambda: redis.exceptions.TimeoutError("timeout"), ServerError, 503),
        (lambda: ServiceUnavailable("no se pudo conectar"), ServerError, 503),
        (lambda: ConnectionError("connection reset"), ServerError, 503),
        (lambda: TimeoutError("timed out"), ServerError, 503),
        (lambda: RuntimeError("Error executing FalkorDB query: boom"), ServerError, 503),
        (lambda: api_error(openai.InternalServerError, 500), GraphMemoryError, 500),
        (lambda: api_error(openai.NotFoundError, 404), GraphMemoryError, 404),
        (lambda: ValueError("algo inesperado"), GraphMemoryError, None),
    ],
)
def test_errors_are_classified_with_an_equivalent_http_status(failure, expected, status):
    error = GraphitiEngine._translate(failure(), "ingesta")

    assert type(error) is expected
    assert error.status_code == status
    # La acción describe dónde ocurrió, salvo en los errores que ya traen su propio texto.
    assert str(error)


def test_engine_errors_pass_through_unchanged():
    original = NotFoundError("falta", status_code=404)

    assert GraphitiEngine._translate(original) is original


def test_only_transient_errors_are_worth_retrying():
    def classified(failure):
        return GraphitiEngine._translate(failure)

    assert is_retryable_zep_error(classified(api_error(openai.RateLimitError, 429)))
    assert is_retryable_zep_error(classified(redis.exceptions.ConnectionError("Connection closed by server.")))
    assert is_retryable_zep_error(classified(ServiceUnavailable("sin conexión")))
    assert not is_retryable_zep_error(classified(api_error(openai.AuthenticationError, 401)))
    assert not is_retryable_zep_error(classified(api_error(openai.BadRequestError, 400)))
    assert not is_retryable_zep_error(classified(ValueError("inesperado")))
    assert not is_retryable_zep_error(NotFoundError("falta", status_code=404))


# ---------------------------------------------------------------- ciclo de vida


def test_start_failure_is_reported_with_its_cause_and_leaves_nothing_open(graph_settings, monkeypatch):
    engine = GraphitiEngine(graph_settings)

    def unavailable():
        raise ConnectionError("connection refused")

    monkeypatch.setattr(engine, "_build_driver", unavailable)

    with pytest.raises(ServerError) as error:
        asyncio.run(engine.start())

    assert error.value.status_code == 503
    assert "connection refused" in str(error.value)
    assert (engine._started, engine._driver, engine._graphiti) == (False, None, None)


def test_operations_before_start_are_rejected(graph_settings):
    engine = GraphitiEngine(graph_settings)

    async def attempts():
        with pytest.raises(ServerError) as error:
            await engine.list_nodes("g1")
        assert error.value.status_code == 503
        with pytest.raises(ServerError):
            await engine.get_node("n1")

    asyncio.run(attempts())


def test_settle_waits_for_the_scheduled_index_build():
    class Driver:
        def __init__(self):
            self.built: list[str] = []
            self.foreground = False

        async def build_indices_and_constraints(self):
            self.foreground = True

    async def scenario():
        scheduled = Driver()

        async def build():
            scheduled.built.append("tarea en segundo plano")

        scheduled._init_task = asyncio.create_task(build())
        await GraphitiEngine._settle(scheduled)
        assert (scheduled.built, scheduled.foreground) == (["tarea en segundo plano"], False)

        bare = Driver()
        await GraphitiEngine._settle(bare)
        assert bare.foreground

    asyncio.run(scenario())


# ---------------------------------------------------------------- límites de simultaneidad


def test_falkordb_queries_never_exceed_the_shared_limit(monkeypatch):
    driver_class = gated_falkordb_driver()
    assert gated_falkordb_driver() is driver_class
    running = {"now": 0, "max": 0}

    async def fake_query(_self, cypher, **kwargs):
        running["now"] += 1
        running["max"] = max(running["max"], running["now"])
        await asyncio.sleep(0.01)
        running["now"] -= 1
        return [cypher]

    monkeypatch.setattr(driver_class.__bases__[0], "execute_query", fake_query)

    async def scenario():
        driver = driver_class.__new__(driver_class)
        shared = asyncio.Semaphore(2)
        driver._gate = shared
        results = await asyncio.gather(*(driver.execute_query(f"q{index}") for index in range(12)))
        assert [rows[0] for rows in results] == [f"q{index}" for index in range(12)]
        assert running["max"] == 2

        # Dos controladores con el mismo límite (base principal y grafo) lo comparten de verdad.
        running.update(now=0, max=0)
        other = driver_class.__new__(driver_class)
        other._gate = shared
        await asyncio.gather(*(driver.execute_query("a") for _ in range(6)), *(other.execute_query("b") for _ in range(6)))
        assert running["max"] == 2

    asyncio.run(scenario())


def test_query_limit_follows_the_server_threads_unless_configured(graph_settings):
    async def threads_of(_name):
        return 4

    automatic = GraphitiEngine(graph_settings)
    automatic._driver = SimpleNamespace(client=SimpleNamespace(config_get=threads_of))
    assert asyncio.run(automatic._query_concurrency()) == 16

    configured = engine_with_query_limit(graph_settings, 3)
    assert asyncio.run(configured._query_concurrency()) == 3

    huge = engine_with_query_limit(graph_settings, 1000)
    assert asyncio.run(huge._query_concurrency()) == 64


def engine_with_query_limit(settings, limit):
    limited = dataclasses.replace(settings.database, max_concurrent_queries=limit)
    engine = GraphitiEngine(dataclasses.replace(settings, database=limited))
    engine._driver = SimpleNamespace()
    return engine


def test_query_limit_assumes_a_small_server_when_it_cannot_be_read(graph_settings):
    async def broken(_name):
        raise RuntimeError("comando no admitido")

    engine = GraphitiEngine(graph_settings)
    engine._driver = SimpleNamespace(client=SimpleNamespace(config_get=broken))

    assert asyncio.run(engine._query_concurrency()) == 4


def test_searches_run_at_most_as_many_at_a_time_as_the_model_allows(graph_settings, monkeypatch):
    engine = GraphitiEngine(graph_settings)
    assert graph_settings.llm_concurrency == 2
    running = {"now": 0, "max": 0}

    class Graphiti:
        async def search_(self, query, config, group_ids, driver):
            running["now"] += 1
            running["max"] = max(running["max"], running["now"])
            await asyncio.sleep(0.01)
            running["now"] -= 1
            return SimpleNamespace(edges=[], nodes=[])

    async def scope(_graph_id):
        return SimpleNamespace(driver=None, graphiti=Graphiti())

    async def scenario():
        engine._started = True
        engine._driver = object()
        engine._search_gate = asyncio.Semaphore(engine.settings.llm_concurrency)
        monkeypatch.setattr(engine, "_scope", scope)
        await asyncio.gather(*(engine.search("g1", f"pregunta {index}") for index in range(9)))
        assert running == {"now": 0, "max": 2}

    asyncio.run(scenario())


def test_log_filter_keeps_the_error_and_drops_the_query_and_its_parameters():
    record = logging.LogRecord(
        "graphiti_core.driver.falkordb_driver",
        logging.ERROR,
        __file__,
        1,
        "Error executing FalkorDB query: boom\nMERGE (x {id: $id})\n{'id': 'texto del usuario'}",
        None,
        None,
    )

    assert OmitQueryText().filter(record) is True

    assert record.getMessage() == "Error executing FalkorDB query: boom"


@pytest.mark.parametrize("scope", ["edges", "nodes", "combined"])
def test_search_recipes_rank_by_meaning_and_set_aside_near_duplicates(scope):
    from graphiti_core.search import search_config_recipes as recipes

    recipe = {
        "edges": recipes.EDGE_HYBRID_SEARCH_RRF,
        "nodes": recipes.NODE_HYBRID_SEARCH_RRF,
        "combined": recipes.COMBINED_HYBRID_SEARCH_RRF,
    }[scope]
    original_edge = (recipe.edge_config.reranker, recipe.edge_config.mmr_lambda) if recipe.edge_config else None

    config = semantic_recipe(recipe, 7)

    for part in (config.edge_config, config.node_config):
        if part is not None:
            assert part.reranker.name == "mmr"
            assert part.mmr_lambda == SEARCH_RERANK_LAMBDA
    assert config.limit == 7
    # El reordenado descarta por debajo de este mínimo: debe dejar pasar todo.
    assert config.reranker_min_score < 0
    if recipe.edge_config is not None:
        # La receta compartida no se modifica.
        assert (recipe.edge_config.reranker, recipe.edge_config.mmr_lambda) == original_edge


def test_an_overloaded_server_is_reported_as_a_transient_failure():
    error = GraphitiEngine._translate(RuntimeError("Max pending queries exceeded"), "buscando")

    assert (type(error), error.status_code) == (ServerError, 503)
    assert is_retryable_zep_error(error)


def test_settle_surfaces_a_failed_index_build():
    class Driver:
        pass

    async def scenario():
        async def boom():
            raise RuntimeError("no se pudo crear el índice")

        driver = Driver()
        driver._init_task = asyncio.create_task(boom())
        with pytest.raises(RuntimeError, match="índice"):
            await GraphitiEngine._settle(driver)

    asyncio.run(scenario())


# ---------------------------------------------------------------- conversión de datos


def test_uuids_are_deduplicated_and_empty_ones_dropped():
    items = [SimpleNamespace(uuid="b"), SimpleNamespace(uuid="a"), SimpleNamespace(uuid="b"), SimpleNamespace(uuid=None)]

    assert _uuids_of(items) == ["a", "b"]
    assert _uuids_of(None) == []


def test_attributes_accept_dicts_and_serialized_dicts_only():
    assert _as_attributes('{"rol": "jefa"}') == {"rol": "jefa"}
    assert _as_attributes({"rol": "jefa"}) == {"rol": "jefa"}
    assert _as_attributes("no es json") == {}
    assert _as_attributes(["lista"]) == {}
    assert _as_attributes(None) == {}


def test_datetimes_get_a_timezone_and_unreadable_values_are_kept():
    naive = _as_datetime("2026-11-23T20:00:00")
    aware = _as_datetime("2026-11-23T20:00:00Z")

    assert naive.utcoffset().total_seconds() == 0
    assert aware.year == 2026 and aware.month == 11
    assert _as_datetime("mañana") == "mañana"
    assert _as_datetime(None) is None


def test_node_and_edge_bags_expose_the_fields_the_app_reads():
    node_bag = GraphitiEngine._node_to_bag(
        SimpleNamespace(
            uuid="n1",
            name="Ana",
            labels=["Entity", "Persona"],
            summary="urbanista",
            attributes='{"rol": "jefa"}',
            created_at="2026-11-23T20:00:00Z",
        ),
        "g1",
    )
    assert (node_bag.uuid, node_bag.uuid_) == ("n1", "n1")
    assert (node_bag.name, node_bag.labels, node_bag.summary) == ("Ana", ["Entity", "Persona"], "urbanista")
    assert node_bag.attributes == {"rol": "jefa"}
    assert node_bag.graph_id == "g1"
    assert node_bag.created_at.year == 2026

    edge_bag = GraphitiEngine._edge_to_bag(
        SimpleNamespace(
            uuid="e1",
            name="WORKS_AT",
            fact="Ana trabaja en la municipalidad",
            source_node_uuid="n1",
            target_node_uuid="n2",
            attributes={"desde": "2020"},
            created_at=None,
            valid_at=None,
            invalid_at=None,
            expired_at=None,
            episodes=["ep-1"],
        ),
        "g1",
    )
    assert (edge_bag.uuid_, edge_bag.name, edge_bag.fact_type) == ("e1", "WORKS_AT", "WORKS_AT")
    assert (edge_bag.source_node_uuid, edge_bag.target_node_uuid) == ("n1", "n2")
    assert (edge_bag.attributes, edge_bag.episodes) == ({"desde": "2020"}, ["ep-1"])
    assert edge_bag.created_at is None and edge_bag.invalid_at is None


# ---------------------------------------------------------------- ontología


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("occupation", "occupation"),
        ("2do_piso", "attr_2do_piso"),
        ("a-b c", "a_b_c"),
        ("árbol", "_rbol"),
        ("name", "name_"),
        ("summary", "summary_"),
        ("labels", "labels_"),
        ("attributes", "attributes_"),
        ("class", "class_"),
        ("", "details"),
        (None, "details"),
    ],
)
def test_field_name_makes_a_valid_unique_attribute(raw, expected):
    assert field_name(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("persona natural", "PersonaNatural"),
        ("WORKS_AT", "WORKSAT"),
        ("Persona", "Persona"),
        ("9tipo", "T9tipo"),
        ("", "EntityType"),
    ],
)
def test_class_name(raw, expected):
    assert class_name(raw) == expected


def test_reserved_names_match_the_engine_node_fields():
    # Si el motor añade un campo interno, esta prueba obliga a ampliar la lista.
    assert set(EntityNode.model_fields) <= RESERVED_FIELD_NAMES


class Speaker(EntityModel):
    """Ponente de una charla."""

    occupation: Optional[EntityText] = Field(None, description="Rol del ponente")
    summary: Optional[EntityText] = Field(None, description="Se omite: choca con un campo interno")


class QUOTES(EdgeModel):
    """Un ponente cita a otro."""


def test_definitions_are_serializable_and_rebuildable():
    entity = entity_definition("Speaker", Speaker)
    edge = edge_definition("QUOTES", QUOTES, [])

    assert jsonable(entity) == {
        "name": "Speaker",
        "description": "Ponente de una charla.",
        "attributes": {"occupation": "Rol del ponente"},
    }
    assert jsonable(edge) == {
        "name": "QUOTES",
        "description": "Un ponente cita a otro.",
        "attributes": {},
        "source_targets": [],
    }

    rebuilt = model_from_definition(entity)
    assert rebuilt.__name__ == "Speaker"
    assert rebuilt.__doc__ == "Ponente de una charla."
    assert rebuilt.model_fields["occupation"].description == "Rol del ponente"
    assert rebuilt(occupation="alcalde").occupation == "alcalde"

    # Sin atributos declarados, el tipo conserva uno genérico para no perder detalles.
    assert set(model_from_definition(edge).model_fields) == {"details"}


def test_a_definition_with_repeated_or_reserved_names_keeps_every_attribute():
    rebuilt = model_from_definition(
        {"name": "X", "attributes": {"name": "1", "name_": "2", "a-b": "3", "a_b": "4"}}
    )

    assert sorted(rebuilt.model_fields) == ["a_b", "a_b_", "name_", "name__"]


def jsonable(value):
    import json

    return json.loads(json.dumps(value, ensure_ascii=False))


# ---------------------------------------------------------------- cliente bien formado


def test_configuration_errors_are_distinct_from_runtime_errors():
    assert issubclass(ConfigError, GraphMemoryError)

    with pytest.raises(ConfigError):
        raise ConfigError("falta la clave")

    error = ConfigError("falta la clave")
    assert error.status_code is None
    assert not is_retryable_zep_error(error)
