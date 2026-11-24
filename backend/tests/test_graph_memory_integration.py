"""Pruebas contra una base de grafos real. No llaman a ningún modelo.

Desactivadas por defecto. Se activan con ``GRAPH_MEMORY_TEST_URL`` y usan una
base principal propia (``beemarketbot_pytest``) para no tocar datos reales::

    GRAPH_MEMORY_TEST_URL=falkordb://127.0.0.1:6379 python -m pytest tests/test_graph_memory_integration.py
    GRAPH_MEMORY_TEST_URL=neo4j://localhost:7687 python -m pytest tests/test_graph_memory_integration.py
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest

from app.graph_memory.engine import GraphitiEngine, new_uuid
from app.graph_memory.settings import GraphMemorySettings

URL = os.environ.get("GRAPH_MEMORY_TEST_URL", "")
pytestmark = pytest.mark.skipif(not URL, reason="se requiere GRAPH_MEMORY_TEST_URL con una base de grafos de prueba")

UNUSED_KEY = "sk-sin-uso-" + "0" * 20


def new_graph_id() -> str:
    return f"pytest_{uuid.uuid4().hex[:10]}"


@pytest.fixture(scope="module")
def loop():
    """Un único bucle de eventos para todo el módulo."""

    with asyncio.Runner() as runner:
        yield runner.run


@pytest.fixture(scope="module")
def settings() -> GraphMemorySettings:
    return GraphMemorySettings.from_env(
        {
            "GRAPH_LLM_API_KEY": UNUSED_KEY,
            "GRAPH_LLM_MODEL": "sin-uso",
            "GRAPH_LLM_BASE_URL": "https://example.invalid/v1",
            "EMBEDDING_DIMENSIONS": "8",
            "GRAPH_DB_URL": URL,
            "GRAPH_DB_NAME": "beemarketbot_pytest",
        }
    )


@pytest.fixture(scope="module")
def engine(loop, settings):
    engine = GraphitiEngine(settings)
    loop(engine.start())
    yield engine
    loop(engine.close())


def leftovers(engine, graph_id):
    """Corrutina: cuántos datos quedan en la base con el identificador de un grafo borrado."""

    async def count():
        if engine.settings.database.is_falkordb:
            # En FalkorDB el grafo era un espacio propio: debe haber desaparecido entero.
            return int(graph_id in await engine._driver.client.list_graphs())
        rows = await engine._query("MATCH (n) WHERE n.group_id = $graph_id RETURN count(n) AS total", graph_id=graph_id)
        return rows[0]["total"]

    return count()


def test_engine_starts_and_reports_its_configuration(engine, loop):
    info = loop(engine.ping())

    assert info["kind"] == engine.settings.database.kind
    assert info["model"] == "sin-uso"


def test_graph_lifecycle_leaves_no_data_behind(engine, loop):
    graph_id = new_graph_id()
    node_uuid, edge_uuid, episode_uuid = new_uuid(), new_uuid(), new_uuid()

    async def create_and_remember():
        created = await engine.create_graph(graph_id, "Prueba", "de integración")
        assert created.graph_id == graph_id
        await engine._remember(graph_id, "node", [node_uuid])
        await engine._remember(graph_id, "edge", [edge_uuid])
        await engine._remember(graph_id, "episode", [episode_uuid])
        assert await engine.graph_of("node", node_uuid) == graph_id
        assert await engine.graph_of("edge", edge_uuid) == graph_id
        assert await engine.graph_of("episode", episode_uuid) == graph_id
        assert await engine.list_nodes(graph_id) == []
        assert await engine.list_edges(graph_id) == []
        # Crear dos veces es un conflicto, no un duplicado.
        with pytest.raises(Exception, match="ya existe"):
            await engine.create_graph(graph_id, "Otra vez")

    loop(create_and_remember())

    async def delete():
        assert await engine.delete_graph(graph_id) is True
        assert await engine.graph_exists(graph_id) is False
        assert await engine.graph_of("node", node_uuid) is None
        assert graph_id not in engine._scopes

    loop(delete())
    assert loop(leftovers(engine, graph_id)) == 0

    # Recrear con el mismo nombre empieza limpio, sin ontología ni datos viejos.
    async def recreate():
        await engine.create_graph(graph_id, "Otra vez")
        assert await engine.get_ontology(graph_id) is None
        assert await engine.list_nodes(graph_id) == []
        return await engine.delete_graph(graph_id)

    assert loop(recreate()) is True
    assert loop(engine.delete_graph("pytest_inexistente")) is False


def test_ontology_is_stored_with_the_graph(engine, loop):
    graph_id = new_graph_id()
    ontology = {
        "entity_types": [
            {"name": "Persona", "description": "Persona individual.", "attributes": {"rol": "Ocupación", "activo": "Sí o no"}}
        ],
        "edge_types": [
            {
                "name": "TRABAJA_EN",
                "description": "Una persona trabaja en una organización.",
                "attributes": {},
                "source_targets": [["Persona", "Organizacion"]],
            }
        ],
    }

    async def scenario():
        await engine.create_graph(graph_id, "Prueba")
        await engine.set_ontology(graph_id, ontology)
        assert await engine.get_ontology(graph_id) == ontology
        return await engine.delete_graph(graph_id)

    assert loop(scenario()) is True


def test_many_parallel_registry_writes_and_reads_are_safe(engine, loop):
    graph_id = new_graph_id()
    uuids = [new_uuid() for _ in range(60)]

    async def scenario():
        await engine.create_graph(graph_id, "Concurrencia")
        await asyncio.gather(*(engine._remember(graph_id, "node", [value]) for value in uuids))
        found = await asyncio.gather(*(engine.graph_of("node", value) for value in uuids))
        assert found == [graph_id] * len(uuids)
        return await engine.delete_graph(graph_id)

    assert loop(scenario()) is True


def test_graphs_survive_an_engine_restart(engine, loop):
    graph_id = new_graph_id()

    async def scenario():
        await engine.create_graph(graph_id, "Persistente", "sobrevive")
        await engine._remember(graph_id, "node", ["n-1"])
        restarted = GraphitiEngine(engine.settings)
        try:
            await restarted.start()
            assert await restarted.graph_exists(graph_id) is True
            assert (await restarted.get_graph(graph_id)).name == "Persistente"
            assert await restarted.graph_of("node", "n-1") == graph_id
            assert await restarted.delete_graph(graph_id) is True
        finally:
            await restarted.close()
        # El motor original ve el borrado: no hay estado propio desincronizado.
        assert await engine.graph_exists(graph_id) is False

    loop(scenario())
