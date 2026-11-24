"""Accesorios compartidos de las pruebas del motor de memoria en grafos."""

from __future__ import annotations

import asyncio
import copy
import os
import threading
from typing import Any

import pytest

# Graphiti envía telemetría de uso a menos que se indique lo contrario; las
# pruebas nunca deben hacerlo. Se fija antes de importar cualquier módulo suyo.
os.environ.setdefault("GRAPHITI_TELEMETRY_ENABLED", "false")

from app.graph_memory.client import GraphMemoryClient
from app.graph_memory.engine import EPISODE_FAILED, EPISODE_SUCCEEDED, EpisodeResult
from app.graph_memory.errors import GraphMemoryError
from app.graph_memory.objects import edge, episode, graph, node, search_result
from app.graph_memory.settings import (
    DatabaseSettings,
    EmbeddingSettings,
    GraphMemorySettings,
    LLMSettings,
)

TEST_SETTINGS = GraphMemorySettings(
    llm=LLMSettings(
        api_key="sk-test-key-000000000000",
        base_url="https://example.invalid/v1",
        model="modelo-de-prueba",
    ),
    embedding=EmbeddingSettings(
        api_key="sk-test-key-000000000000",
        base_url="https://example.invalid/v1",
        model="embed-de-prueba",
        dimensions=8,
    ),
    database=DatabaseSettings(kind="falkordb", host="127.0.0.1", port=6379, database="pruebas"),
    ingestion_window=2,
    llm_concurrency=2,
)


class FakeEngine:
    """Sustituto del motor: misma interfaz asíncrona, sin base de datos ni modelos.

    Cada episodio ingerido crea un nodo y lo une con el anterior mediante una
    relación, para que las lecturas tengan algo que devolver.
    """

    def __init__(self) -> None:
        self.graphs: dict[str, dict[str, Any]] = {}
        self.ontologies: dict[str, dict[str, Any]] = {}
        self.episodes: dict[str, dict[str, Any]] = {}
        self.nodes: dict[str, list[Any]] = {}
        self.edges: dict[str, list[Any]] = {}
        self.owners: dict[tuple[str, str], str] = {}
        self.calls: list[tuple[str, ...]] = []
        self.ingested: list[str] = []
        # Control de fallos: episodios cuyo texto contiene `failing_text` fallan, o
        # una excepción para toda la ingesta.
        self.failing_text: str | None = None
        self.failure: BaseException | None = None
        self.search_hits: list[str] = []
        self.search_empty = False
        # Si está definido, la ingesta espera hasta que se suelte: mantiene un lote
        # "en curso" sin depender del tiempo del hilo.
        self.gate: threading.Event | None = None

    async def ping(self) -> dict[str, Any]:
        return {"kind": "fake"}

    # -- grafos y ontología
    async def graph_exists(self, graph_id: str) -> bool:
        return graph_id in self.graphs

    async def create_graph(self, graph_id: str, name: str, description: str = "") -> Any:
        if graph_id in self.graphs:
            raise GraphMemoryError(f"El grafo {graph_id} ya existe", status_code=409)
        self.graphs[graph_id] = {"name": name, "description": description}
        self.nodes.setdefault(graph_id, [])
        self.edges.setdefault(graph_id, [])
        return graph(graph_id, name, description)

    async def get_graph(self, graph_id: str) -> Any:
        if graph_id not in self.graphs:
            return None
        return graph(graph_id, self.graphs[graph_id]["name"], self.graphs[graph_id]["description"])

    async def delete_graph(self, graph_id: str) -> bool:
        if graph_id not in self.graphs:
            return False
        for owner in [key for key, value in self.owners.items() if value == graph_id]:
            del self.owners[owner]
        for store in (self.graphs, self.ontologies, self.nodes, self.edges):
            store.pop(graph_id, None)
        return True

    async def set_ontology(self, graph_id: str, ontology: dict[str, Any]) -> None:
        self.ontologies[graph_id] = ontology

    async def get_ontology(self, graph_id: str) -> dict[str, Any] | None:
        return self.ontologies.get(graph_id)

    # -- ingesta
    async def add_episodes(self, graph_id: str, specs: list[Any]) -> list[EpisodeResult]:
        self.calls.append(("add_episodes", graph_id, len(specs)))
        if self.gate is not None:
            self.gate.wait()
        if self.failure is not None:
            raise self.failure
        results = []
        for spec in specs:
            if self.failing_text is not None and self.failing_text in spec.data:
                results.append(EpisodeResult(spec.uuid, EPISODE_FAILED, "el modelo rechazó el episodio"))
                continue
            self.ingested.append(spec.uuid)
            index = len(self.nodes[graph_id])
            created = node(f"node-{graph_id}-{index}", f"Nodo {index}", ["Entity", "Cosa"], "resumen", {"orden": index})
            self.nodes[graph_id].append(created)
            self.owners[("node", created.uuid)] = graph_id
            if index:
                previous = self.nodes[graph_id][index - 1]
                linked = edge(f"edge-{graph_id}-{index}", "SIGUE_A", f"Nodo {index - 1} sigue a Nodo {index}", previous.uuid, created.uuid)
                self.edges[graph_id].append(linked)
                self.owners[("edge", linked.uuid)] = graph_id
            self.episodes[spec.uuid] = {
                "graph_id": graph_id,
                "name": spec.name or "",
                "content": spec.data,
                "source_description": spec.source_description or "text",
            }
            self.owners[("episode", spec.uuid)] = graph_id
            results.append(EpisodeResult(spec.uuid, EPISODE_SUCCEEDED))
        return results

    # -- lecturas
    async def list_nodes(self, graph_id: str) -> list[Any]:
        return list(self.nodes.get(graph_id, []))

    async def list_edges(self, graph_id: str) -> list[Any]:
        return list(self.edges.get(graph_id, []))

    async def graph_of(self, kind: str, uuid: str) -> str | None:
        return self.owners.get((kind, uuid))

    async def get_node(self, uuid: str) -> Any:
        return self._owned("node", self.nodes, uuid)

    async def get_edge(self, uuid: str) -> Any:
        return self._owned("edge", self.edges, uuid)

    def _owned(self, kind: str, store: dict[str, list[Any]], uuid: str) -> Any:
        """Elemento por uuid, anotando a qué grafo pertenece (como hace el motor real)."""

        owner = self.owners.get((kind, uuid))
        found = next((item for item in store.get(owner, []) if item.uuid == uuid), None)
        if found is None:
            return None
        found = copy.copy(found)
        found.graph_id = owner
        return found

    async def get_node_edges(self, graph_id: str, node_uuid: str) -> list[Any]:
        return [
            item
            for item in self.edges.get(graph_id, [])
            if node_uuid in (item.source_node_uuid, item.target_node_uuid)
        ]

    async def get_episode(self, uuid: str) -> Any:
        record = self.episodes.get(uuid)
        if record is None:
            return None
        return episode(uuid, record["name"], processed=True, content=record["content"])

    async def search(self, graph_id: str, query: str, scope: str = "edges", limit: int = 10) -> Any:
        self.calls.append(("search", graph_id, query, scope, limit))
        edges = [] if self.search_empty else list(self.edges.get(graph_id, []))[:limit]
        nodes = [item for item in self.nodes.get(graph_id, []) if item.name in self.search_hits][:limit]
        return search_result(edges, nodes, query)


@pytest.fixture(autouse=True)
def no_provider_compatibility_settings(monkeypatch):
    """Ninguna prueba depende de la compatibilidad de proveedor que haya en el entorno.

    En un contenedor configurado estas variables existen; cada prueba que las
    necesita las define explícitamente.
    """

    for name in ("LLM_TEMPERATURE", "LLM_TEMPERATURE_TEXT", "LLM_TEMPERATURE_JSON", "LLM_EXTRA_BODY"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def graph_settings() -> GraphMemorySettings:
    """Configuración completa de ejemplo, sin servicios reales detrás."""

    return TEST_SETTINGS


@pytest.fixture
def make_graph_client(tmp_path):
    """Fábrica de clientes reales del motor conectados a un motor falso.

    Cada cliente guarda sus lotes en una carpeta temporal propia, para probar
    también lo que sobrevive a un reinicio.
    """

    created: list[GraphMemoryClient] = []

    def make(engine: FakeEngine | None = None, settings: GraphMemorySettings | None = None, name: str = "estado") -> tuple[GraphMemoryClient, FakeEngine]:
        engine = engine or FakeEngine()
        client = GraphMemoryClient(settings or TEST_SETTINGS, state_dir=tmp_path / name)
        client._run = lambda operation, timeout=None: asyncio.run(operation(engine))
        created.append(client)
        return client, engine

    yield make
    for client in created:
        client.close()
