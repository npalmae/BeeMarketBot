"""Cliente síncrono del motor de grafos sobre un motor falso: sin red, con hilos reales."""

from __future__ import annotations

import dataclasses
import threading
import time
from typing import Optional

import pytest
from pydantic import Field

from app.graph_memory import (
    AuthError,
    BatchAddItem,
    BadRequestError,
    EdgeModel,
    EntityEdgeSourceTarget,
    EntityModel,
    EntityText,
    GraphMemoryError,
    NotFoundError,
)
from app.graph_memory.client import GraphMemoryClient
from app.graph_memory.engine import GraphitiEngine
from app.graph_memory.errors import ConfigError
from app.graph_memory.objects import node as make_node
from app.graph_memory.types import model_from_definition
from app.utils import zep_paging

TERMINAL = {"succeeded", "partial", "failed", "invalid", "canceled"}


def wait_until(read, done, timeout: float = 10.0, message: str = "no se cumplió a tiempo"):
    """Espera una condición que cumple un hilo en segundo plano y devuelve lo leído."""

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = read()
        if done(value):
            return value
        time.sleep(0.01)
    raise AssertionError(message)


def wait_batch(client, batch_id, timeout: float = 10.0):
    return wait_until(
        lambda: client.batch.get(batch_id=batch_id),
        lambda summary: summary.status in TERMINAL,
        timeout,
        f"el lote {batch_id} no llegó a un estado terminal",
    )


def wait_episode(client, uuid_, timeout: float = 10.0):
    return wait_until(
        lambda: client.graph.episode.get(uuid_=uuid_),
        lambda episode: episode.processed or episode.failed,
        timeout,
        f"el episodio {uuid_} no llegó a un estado terminal",
    )


def wait_no_active_batches(client, timeout: float = 10.0):
    """Espera a que el hilo de ingesta termine: marca el lote como acabado un instante antes de salir."""

    return wait_until(
        lambda: client.stats()["active_batches"],
        lambda active: active == [],
        timeout,
        "el hilo de ingesta sigue vivo después de terminar el lote",
    )


def items(texts):
    return [
        BatchAddItem(
            type="graph_episode",
            graph_id="g1",
            data=text,
            data_type="text",
            source_description="documento de prueba",
        )
        for text in texts
    ]


def processed_batch(client, texts):
    """Crea, ingiere y termina un lote; devuelve los uuids de sus episodios."""

    batch = client.batch.create()
    details = client.batch.add(batch_id=batch.batch_id, items=items(texts))
    client.batch.process(batch_id=batch.batch_id)
    assert wait_batch(client, batch.batch_id).status == "succeeded"
    return [detail.episode_uuid for detail in details]


class Speaker(EntityModel):
    """Ponente de una charla."""

    role: Optional[EntityText] = Field(None, description="Rol del ponente")


class QUOTES(EdgeModel):
    """Un ponente cita a otro."""


# ---------------------------------------------------------------- grafos


def test_graph_lifecycle(make_graph_client):
    client, engine = make_graph_client()

    created = client.graph.create(graph_id="mirofish_abc-1", name="Mi grafo", description="prueba")
    assert (created.graph_id, created.name, created.description) == ("mirofish_abc-1", "Mi grafo", "prueba")
    assert client.graph.get(graph_id="mirofish_abc-1").name == "Mi grafo"

    with pytest.raises(GraphMemoryError) as error:
        client.graph.create(graph_id="mirofish_abc-1", name="Otra vez")
    assert error.value.status_code == 409

    with pytest.raises(NotFoundError) as error:
        client.graph.get(graph_id="falta")
    assert error.value.status_code == 404

    with pytest.raises(NotFoundError):
        client.graph.delete(graph_id="falta")

    client.graph.delete(graph_id="mirofish_abc-1")
    assert engine.graphs == {}
    # Tras borrar, el grafo deja de darse por conocido.
    with pytest.raises(NotFoundError):
        client.graph.add(graph_id="mirofish_abc-1", data="texto")


@pytest.mark.parametrize(
    "call",
    [
        lambda client: client.graph.create(graph_id="con espacios", name="x"),
        lambda client: client.graph.create(graph_id=None, name="x"),
        lambda client: client.graph.get(graph_id="g" * 129),
        lambda client: client.graph.add(graph_id="a.b", data="texto"),
        lambda client: client.graph.search(graph_id="grafo/ajeno", query="¿quién?"),
        lambda client: client.graph.node.with_raw_response.get_by_graph_id(""),
        lambda client: client.batch.add(batch_id="lote", items=[BatchAddItem(graph_id="grafo malo", data="texto")]),
        lambda client: client.graph.set_ontology(graph_ids=["grafo malo"], entities={"Speaker": Speaker}),
    ],
)
def test_invalid_graph_ids_are_rejected_before_reaching_the_engine(make_graph_client, call):
    client, engine = make_graph_client()
    client.graph.create(graph_id="g1", name="g1")

    with pytest.raises(BadRequestError) as error:
        call(client)

    assert error.value.status_code == 400
    assert engine.calls == []


def test_argument_errors_are_bad_requests_and_unknown_ids_are_not_found(make_graph_client):
    client, _engine = make_graph_client()
    client.graph.create(graph_id="g1", name="g1")
    batch = client.batch.create()

    bad_request = [
        lambda: client.graph.search(graph_id="g1", query="   "),
        lambda: client.graph.search(graph_id="g1", query="¿quién?", limit=0),
        lambda: client.graph.search(graph_id="g1", query="¿quién?", limit="muchos"),
        lambda: client.graph.add(graph_id="g1", data="   "),
        lambda: client.graph.add(graph_id="g1", type="json", data="{}"),
        lambda: client.graph.edge.get(uuid_=""),
        lambda: client.graph.node.get_edges(node_uuid=None),
        lambda: client.graph.episode.get(uuid_=None),
        lambda: client.batch.process(batch_id=""),
        lambda: client.batch.add(batch_id=batch.batch_id, items=[]),
    ]
    for call in bad_request:
        with pytest.raises(BadRequestError):
            call()

    not_found = [
        lambda: client.graph.node.get(uuid_="desconocido"),
        lambda: client.graph.edge.get(uuid_="desconocido"),
        lambda: client.graph.episode.get(uuid_="desconocido"),
        lambda: client.batch.get(batch_id="desconocido"),
        lambda: client.batch.list_items(batch_id="desconocido"),
    ]
    for call in not_found:
        with pytest.raises(NotFoundError) as error:
            call()
        assert error.value.status_code == 404


# ---------------------------------------------------------------- ontología


def test_ontology_is_serialized_and_rebuilt_for_ingestion(make_graph_client):
    client, engine = make_graph_client()
    client.graph.create(graph_id="g1", name="g1")

    client.graph.set_ontology(
        graph_ids=["g1"],
        entities={"Speaker": Speaker},
        edges={"QUOTES": (QUOTES, [("Speaker", "Speaker")])},
    )

    stored = engine.ontologies["g1"]
    assert stored["entity_types"] == [
        {"name": "Speaker", "description": "Ponente de una charla.", "attributes": {"role": "Rol del ponente"}}
    ]
    assert stored["edge_types"] == [
        {
            "name": "QUOTES",
            "description": "Un ponente cita a otro.",
            "attributes": {},
            "source_targets": [["Speaker", "Speaker"]],
        }
    ]

    options = GraphitiEngine._ingestion_options(stored)
    assert list(options["entity_types"]) == ["Speaker"]
    assert list(options["edge_types"]) == ["QUOTES"]
    assert options["edge_type_map"] == {("Speaker", "Speaker"): ["QUOTES"]}
    # Un tipo sin atributos declarados obtiene el campo genérico "details".
    assert set(options["edge_types"]["QUOTES"].model_fields) == {"details"}

    # Sin ontología, la ingesta usa la extracción genérica del motor.
    assert GraphitiEngine._ingestion_options({}) == {}


def test_edge_signatures_only_count_when_both_types_exist(make_graph_client):
    options = GraphitiEngine._ingestion_options(
        {
            "entity_types": [{"name": "Speaker"}],
            "edge_types": [
                {"name": "QUOTES", "source_targets": [["Speaker", "Speaker"], ["Speaker", "Fantasma"], "roto"]},
                {"name": ""},
            ],
        }
    )
    assert options["edge_type_map"] == {("Speaker", "Speaker"): ["QUOTES"]}
    assert list(options["edge_types"]) == ["QUOTES"]


def test_ontology_source_targets_accept_objects_dicts_and_pairs(make_graph_client):
    client, engine = make_graph_client()
    client.graph.create(graph_id="g1", name="g1")

    client.graph.set_ontology(
        graph_ids=["g1"],
        entities={"Speaker": Speaker},
        edges={
            "QUOTES": (
                QUOTES,
                [
                    EntityEdgeSourceTarget(source="Speaker", target="Speaker"),
                    {"source": "Speaker", "target": "Speaker"},
                ],
            )
        },
    )
    assert engine.ontologies["g1"]["edge_types"][0]["source_targets"] == [["Speaker", "Speaker"]] * 2

    with pytest.raises(BadRequestError, match="origen"):
        client.graph.set_ontology(
            graph_ids=["g1"],
            entities={"Speaker": Speaker},
            edges={"QUOTES": (QUOTES, [("Solo uno",)])},
        )
    with pytest.raises(BadRequestError, match="Definición de relación"):
        client.graph.set_ontology(graph_ids=["g1"], entities={}, edges={"QUOTES": QUOTES})


def test_ontology_type_names_must_be_safe_identifiers(make_graph_client):
    client, _engine = make_graph_client()
    client.graph.create(graph_id="g1", name="g1")

    for names in ({"Tipo Invalido": Speaker}, {"1EmpiezaConDigito": Speaker}, {"Con-Acentós": Speaker}):
        with pytest.raises(BadRequestError, match="nombre válido"):
            client.graph.set_ontology(graph_ids=["g1"], entities=names)
    with pytest.raises(BadRequestError, match="nombre válido"):
        client.graph.set_ontology(
            graph_ids=["g1"], entities={"Speaker": Speaker}, edges={"con espacios": (QUOTES, [])}
        )
    with pytest.raises(BadRequestError):
        client.graph.set_ontology(graph_ids=[], entities={"Speaker": Speaker})


# ---------------------------------------------------------------- episodios en cola


def test_added_activities_are_gathered_and_ingested_in_the_background(make_graph_client):
    client, engine = make_graph_client()
    client.graph.create(graph_id="g1", name="g1")

    first = client.graph.add(graph_id="g1", data="Ana trabaja en la municipalidad", source_description="actividad")
    second = client.graph.add(
        graph_id="g1",
        type="message",
        data="Ana apoya el plan",
        created_at="2026-11-23T20:00:00Z",
        metadata={"simulation_id": "sim-1"},
    )

    assert first.processed is False
    assert first.name.startswith("activity_")
    assert first.source_description == "actividad"
    assert client.graph.episode.get(uuid_=first.uuid_).processed is False
    assert client.stats()["pending_episodes"] == 2

    assert wait_episode(client, second.uuid_).processed is True
    stored = client.graph.episode.get(uuid_=first.uuid_)
    assert (stored.processed, stored.failed, stored.error) == (True, False, None)
    assert client.graph.episode.get(uuid_=second.uuid_).metadata == {"simulation_id": "sim-1"}

    # Llegaron juntas y se ingirieron en una sola llamada.
    assert [call for call in engine.calls if call[0] == "add_episodes"] == [("add_episodes", "g1", 2)]
    assert sorted(engine.ingested) == sorted([first.uuid_, second.uuid_])
    assert client.stats()["pending_episodes"] == 0


def test_engine_failure_is_reported_on_each_pending_episode(make_graph_client):
    client, engine = make_graph_client()
    client.graph.create(graph_id="g1", name="g1")
    engine.failure = RuntimeError("base de grafos caída")

    episode = client.graph.add(graph_id="g1", data="sin procesar")

    stored = wait_episode(client, episode.uuid_)
    assert (stored.processed, stored.failed) == (False, True)
    assert "base de grafos caída" in stored.error
    assert engine.ingested == []


def test_deleting_a_graph_fails_its_pending_activities(make_graph_client):
    client, engine = make_graph_client()
    client.graph.create(graph_id="g1", name="g1")
    episode = client.graph.add(graph_id="g1", data="aún en cola")

    client.graph.delete(graph_id="g1")

    stored = client.graph.episode.get(uuid_=episode.uuid_)
    assert (stored.processed, stored.failed) == (False, True)
    assert stored.error == "El grafo se eliminó antes de procesar el episodio"
    # El procesador ya no tiene nada pendiente y no intenta ingerir en un grafo borrado.
    time.sleep(1.3)
    assert engine.ingested == []
    assert client.stats()["pending_episodes"] == 0


# ---------------------------------------------------------------- lecturas


def test_nodes_and_edges_are_found_by_uuid_through_the_registry(make_graph_client):
    client, _engine = make_graph_client()
    client.graph.create(graph_id="g1", name="g1")
    processed_batch(client, ["uno", "dos", "tres"])
    nodes = client.graph.node.with_raw_response.get_by_graph_id("g1").data

    assert [item.name for item in nodes] == ["Nodo 0", "Nodo 1", "Nodo 2"]
    detail = client.graph.node.get(uuid_=nodes[1].uuid_)
    assert detail.labels == ["Entity", "Cosa"]
    assert detail.attributes == {"orden": 1}
    assert detail.graph_id == "g1"

    related = client.graph.node.get_edges(node_uuid=detail.uuid_)
    assert len(related) == 2
    assert {item.name for item in related} == {"SIGUE_A"}
    assert {item.fact_type for item in related} == {"SIGUE_A"}

    first_edge = client.graph.edge.get(uuid_=related[0].uuid_)
    assert first_edge.fact == "Nodo 0 sigue a Nodo 1"
    assert (first_edge.source_node_uuid, first_edge.target_node_uuid) == (nodes[0].uuid_, nodes[1].uuid_)

    # El extremo suelto del grafo solo tiene una relación.
    assert len(client.graph.node.get_edges(node_uuid=nodes[2].uuid_)) == 1
    assert client.graph.node.get_edges(node_uuid="desconocido") == []


def test_engine_failures_keep_their_status_code(make_graph_client):
    client, engine = make_graph_client()
    client.graph.create(graph_id="g1", name="g1")

    async def unauthorized(_graph_id):
        raise AuthError("unauthorized", status_code=401)

    engine.list_nodes = unauthorized
    with pytest.raises(AuthError) as error:
        client.graph.node.with_raw_response.get_by_graph_id("g1")
    assert error.value.status_code == 401


def test_raw_reader_paginates_with_an_opaque_cursor(make_graph_client):
    client, engine = make_graph_client()
    client.graph.create(graph_id="g1", name="g1")
    engine.nodes["g1"].extend(
        make_node(f"n{index}", f"Nodo {index}", ["Entity", "Cosa"], "", {}, created_at=None) for index in range(5)
    )

    page = client.graph.node.with_raw_response.get_by_graph_id("g1", limit=2)
    assert [item.name for item in page.data] == ["Nodo 0", "Nodo 1"]
    assert page.headers == {"zep-next-cursor": "2"}

    following = client.graph.node.with_raw_response.get_by_graph_id("g1", limit=2, cursor="2")
    assert [item.name for item in following.data] == ["Nodo 2", "Nodo 3"]
    assert following.headers == {"zep-next-cursor": "4"}

    last = client.graph.node.with_raw_response.get_by_graph_id("g1", limit=2, cursor="4")
    assert [item.name for item in last.data] == ["Nodo 4"]
    assert last.headers == {}

    everything = zep_paging.fetch_all_nodes(client, "g1", page_size=2)
    assert [item.name for item in everything] == [f"Nodo {index}" for index in range(5)]
    assert [item.name for item in zep_paging.fetch_all_nodes(client, "g1", max_items=3)] == ["Nodo 0", "Nodo 1", "Nodo 2"]
    assert zep_paging.fetch_all_edges(client, "g1") == []
    assert len(client.graph.node.get_by_graph_id("g1")) == 5


def test_raw_reader_rejects_a_cursor_out_of_range(make_graph_client):
    client, _engine = make_graph_client()
    client.graph.create(graph_id="g1", name="g1")
    processed_batch(client, ["uno"])

    with pytest.raises(BadRequestError, match="cursor"):
        client.graph.node.with_raw_response.get_by_graph_id("g1", limit=5, cursor="9")


# ---------------------------------------------------------------- búsqueda


def test_search_passes_scope_and_caps_the_limit(make_graph_client):
    client, engine = make_graph_client()
    client.graph.create(graph_id="g1", name="g1")

    client.graph.search(graph_id="g1", query="  ¿Quién sabe?  ", limit=500, scope="nodes", reranker="cross_encoder")
    client.graph.search(graph_id="g1", query="¿Quién sabe?", limit="4", scope="combined")

    assert [call for call in engine.calls if call[0] == "search"] == [
        ("search", "g1", "¿Quién sabe?", "nodes", 50),
        ("search", "g1", "¿Quién sabe?", "combined", 4),
    ]


# ---------------------------------------------------------------- lotes


def test_batch_flow_persists_identity_and_reports_each_item(make_graph_client):
    client, engine = make_graph_client()
    client.graph.create(graph_id="g1", name="g1")

    created = client.batch.create(metadata={"mirofish_operation_id": "op-1", "graph_id": "g1"})
    assert created.status == "draft"
    assert created.metadata == {"mirofish_operation_id": "op-1", "graph_id": "g1"}
    assert (created.progress.percent_complete, created.progress.total_items) == (0, 0)

    details = client.batch.add(batch_id=created.batch_id, items=items(["uno", "dos", "tres"]))
    assert [detail.sequence_index for detail in details] == [0, 1, 2]
    assert len({detail.episode_uuid for detail in details}) == 3
    assert all(detail.source_uuid == detail.episode_uuid for detail in details)
    assert all(detail.status == "pending" for detail in details)
    # Los detalles se leen igual desde el estado guardado.
    assert [detail.episode_uuid for detail in client.batch.list_items(batch_id=created.batch_id).items] == [
        detail.episode_uuid for detail in details
    ]

    client.batch.process(batch_id=created.batch_id)
    assert client.batch.get(batch_id=created.batch_id).status in {"queued", "processing", "succeeded"}

    final = wait_batch(client, created.batch_id)
    assert final.status == "succeeded"
    assert (final.progress.percent_complete, final.progress.succeeded_items, final.progress.failed_items) == (100, 3, 0)

    page = client.batch.list_items(batch_id=created.batch_id, limit=2)
    assert [(item.sequence_index, item.status) for item in page.items] == [(0, "succeeded"), (1, "succeeded")]
    assert page.next_cursor == "2"
    rest = client.batch.list_items(batch_id=created.batch_id, limit=2, cursor=page.next_cursor)
    assert [(item.sequence_index, item.status) for item in rest.items] == [(2, "succeeded")]
    assert rest.next_cursor is None

    # Tres elementos con ventana de ingesta dos: una llamada de dos y otra de uno.
    assert [call for call in engine.calls if call[0] == "add_episodes"] == [
        ("add_episodes", "g1", 2),
        ("add_episodes", "g1", 1),
    ]
    assert sorted(engine.ingested) == sorted(detail.episode_uuid for detail in details)
    # Cada episodio ingerido queda registrado y se puede consultar por su uuid.
    assert client.graph.episode.get(uuid_=details[0].episode_uuid).processed is True

    listed = client.batch.list(limit=10)
    assert [item.batch_id for item in listed.batches] == [created.batch_id]
    assert listed.next_cursor is None
    assert [item.name for item in client.graph.node.with_raw_response.get_by_graph_id("g1").data] == [
        "Nodo 0",
        "Nodo 1",
        "Nodo 2",
    ]

    # Un lote terminado no admite más trabajo.
    with pytest.raises(BadRequestError) as error:
        client.batch.process(batch_id=created.batch_id)
    assert error.value.status_code == 409
    with pytest.raises(BadRequestError):
        client.batch.add(batch_id=created.batch_id, items=items(["tarde"]))


def test_batch_item_validation(make_graph_client):
    client, _engine = make_graph_client()
    batch = client.batch.create()

    with pytest.raises(BadRequestError, match="elemento 1 .*grafo"):
        client.batch.add(
            batch_id=batch.batch_id,
            items=items(["uno"])[:1] + [BatchAddItem(graph_id=None, data="dos")],
        )
    with pytest.raises(BadRequestError, match="elemento 0 .*texto"):
        client.batch.add(batch_id=batch.batch_id, items=[BatchAddItem(graph_id="g1", data="   ")])
    with pytest.raises(BadRequestError, match="al menos un elemento"):
        client.batch.add(batch_id=batch.batch_id, items=[])
    with pytest.raises(BadRequestError):
        client.batch.process(batch_id=batch.batch_id)
    # Nada de lo rechazado se guardó.
    assert client.batch.get(batch_id=batch.batch_id).progress.total_items == 0


def test_batch_reports_partial_and_total_failure_per_item(make_graph_client):
    client, engine = make_graph_client()
    client.graph.create(graph_id="g1", name="g1")
    engine.failing_text = "roto"

    batch = client.batch.create()
    client.batch.add(batch_id=batch.batch_id, items=items(["bueno", "roto", "bueno también"]))
    client.batch.process(batch_id=batch.batch_id)
    final = wait_batch(client, batch.batch_id)

    assert final.status == "partial"
    assert (final.progress.succeeded_items, final.progress.failed_items) == (2, 1)
    failed = client.batch.list_items(batch_id=batch.batch_id).items[1]
    assert (failed.status, failed.error) == ("failed", "el modelo rechazó el episodio")
    assert failed.episode_uuid  # conserva su identidad para un reintento

    other = client.batch.create()
    client.batch.add(batch_id=other.batch_id, items=items(["roto", "roto también"]))
    client.batch.process(batch_id=other.batch_id)
    assert wait_batch(client, other.batch_id).status == "failed"


def test_interrupted_batch_resumes_after_a_restart_without_reingesting_done_items(make_graph_client):
    client, engine = make_graph_client(name="reinicio")
    client.graph.create(graph_id="g1", name="g1")
    batch = client.batch.create(metadata={"graph_id": "g1"})
    details = client.batch.add(batch_id=batch.batch_id, items=items(["cero", "uno", "dos", "tres"]))

    # Simula un corte: el primer elemento terminó, el segundo quedó a medias.
    client._batches.set_status(batch.batch_id, "processing")
    client._batches.update_items(
        batch.batch_id,
        {0: {"status": "succeeded"}, 1: {"status": "failed", "error": "proceso interrumpido"}},
    )
    client.close()

    # Un proceso nuevo lee el mismo estado del disco. La ingesta reanudada espera
    # en el cerrojo, así que el estado intermedio se puede comprobar sin prisa.
    engine.gate = threading.Event()
    restarted, _same_engine = make_graph_client(engine=engine, name="reinicio")
    try:
        summary = restarted.batch.get(batch_id=batch.batch_id)
        assert summary.status in {"queued", "processing"}
        assert summary.metadata == {"graph_id": "g1"}
    finally:
        engine.gate.set()

    final = wait_batch(restarted, batch.batch_id)
    assert (final.status, final.progress.succeeded_items) == ("succeeded", 4)
    # Solo se reingieren los que no habían terminado, y con su uuid original.
    assert sorted(engine.ingested) == sorted(detail.episode_uuid for detail in details[1:])
    assert [item.status for item in restarted.batch.list_items(batch_id=batch.batch_id).items] == ["succeeded"] * 4


def test_an_interrupted_batch_is_resumed_once_and_then_stays_finished(make_graph_client):
    client, engine = make_graph_client(name="reinicio")
    client.graph.create(graph_id="g1", name="g1")
    batch = client.batch.create()
    client.batch.add(batch_id=batch.batch_id, items=items(["uno", "dos"]))
    client._batches.set_status(batch.batch_id, "processing")
    client.close()

    restarted, _engine = make_graph_client(engine=engine, name="reinicio")
    restarted.batch.get(batch_id=batch.batch_id)  # la primera lectura reanuda la ingesta
    restarted.batch.get(batch_id=batch.batch_id)  # las siguientes no arrancan nada más

    assert wait_batch(restarted, batch.batch_id).status == "succeeded"
    assert len(engine.ingested) == 2
    wait_no_active_batches(restarted)
    # Un lote terminado no admite más trabajo, aunque se pida de nuevo.
    with pytest.raises(BadRequestError) as error:
        restarted.batch.process(batch_id=batch.batch_id)
    assert error.value.status_code == 409


def test_finished_and_unsent_batches_are_left_alone_after_a_restart(make_graph_client):
    client, engine = make_graph_client(name="reinicio")
    client.graph.create(graph_id="g1", name="g1")
    finished = client.batch.create().batch_id
    client.batch.add(batch_id=finished, items=items(["uno"]))
    client.batch.process(batch_id=finished)
    assert wait_batch(client, finished).status == "succeeded"
    # Un borrador que nunca se envió: el constructor de grafos lo necesita intacto
    # para seguirle agregando fragmentos tras conciliar su creación.
    draft = client.batch.create(metadata={"mirofish_operation_id": "op-pendiente"}).batch_id
    ingested = list(engine.ingested)
    client.close()

    restarted, _engine = make_graph_client(engine=engine, name="reinicio")
    restarted.batch.get(batch_id=finished)
    assert restarted.stats()["active_batches"] == []
    assert restarted.batch.get(batch_id=draft).status == "draft"
    restarted.batch.add(batch_id=draft, items=items(["dos", "tres"]))
    restarted.batch.process(batch_id=draft)
    assert wait_batch(restarted, draft).status == "succeeded"
    assert restarted.batch.list(limit=10).batches[0].metadata == {"mirofish_operation_id": "op-pendiente"}
    # Lo único ingerido tras el reinicio son los dos fragmentos del borrador.
    draft_episodes = [item.episode_uuid for item in restarted.batch.list_items(batch_id=draft).items]
    assert len(draft_episodes) == 2
    assert sorted(engine.ingested) == sorted(ingested + draft_episodes)


def test_processing_twice_while_running_keeps_a_single_worker(make_graph_client):
    client, engine = make_graph_client()
    client.graph.create(graph_id="g1", name="g1")
    batch = client.batch.create()
    client.batch.add(batch_id=batch.batch_id, items=items(["uno", "dos", "tres"]))
    # La ingesta queda en curso hasta que se suelte: el resultado no depende del
    # tiempo del hilo.
    engine.gate = threading.Event()
    try:
        client.batch.process(batch_id=batch.batch_id)
        wait_until(
            lambda: client.batch.get(batch_id=batch.batch_id).status,
            lambda status: status == "processing",
            message="el lote no entró en curso",
        )
        client.batch.process(batch_id=batch.batch_id)
        client.batch.process(batch_id=batch.batch_id)
        assert client.stats()["active_batches"] == [batch.batch_id]
    finally:
        engine.gate.set()

    assert wait_batch(client, batch.batch_id).status == "succeeded"
    assert len(engine.ingested) == 3
    wait_no_active_batches(client)


def test_batch_state_can_live_in_a_custom_folder(make_graph_client, tmp_path):
    client, _engine = make_graph_client(name="otra-carpeta")
    client.graph.create(graph_id="g1", name="g1")
    batch_id = client.batch.create().batch_id

    saved = sorted(path.name for path in (tmp_path / "otra-carpeta").glob("*.json"))
    assert saved == [f"{batch_id}.json"]
    # No quedan archivos temporales de escritura a medias.
    assert list((tmp_path / "otra-carpeta").glob("*.tmp")) == []


# ---------------------------------------------------------------- configuración


def test_client_rejects_incomplete_settings(graph_settings, tmp_path):
    without_key = dataclasses.replace(graph_settings, llm=dataclasses.replace(graph_settings.llm, api_key=""))
    with pytest.raises(ConfigError, match="GRAPH_LLM_API_KEY"):
        GraphMemoryClient(without_key, state_dir=tmp_path)

    without_model = dataclasses.replace(graph_settings, llm=dataclasses.replace(graph_settings.llm, model=""))
    with pytest.raises(ConfigError, match="GRAPH_LLM_MODEL"):
        GraphMemoryClient(without_model, state_dir=tmp_path)


def test_model_from_definition_keeps_a_stable_shape():
    rebuilt = model_from_definition(
        {
            "name": "Speaker",
            "description": "Ponente.",
            "attributes": {"role": "Rol", "name": "Nombre", "2lugar": "Lugar", "resumen": "Resumen"},
        }
    )
    assert rebuilt.__name__ == "Speaker"
    assert rebuilt.__doc__ == "Ponente."
    assert set(rebuilt.model_fields) == {"role", "name_", "attr_2lugar", "resumen"}
    assert rebuilt().role is None
