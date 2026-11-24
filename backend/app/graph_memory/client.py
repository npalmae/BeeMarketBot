"""Cliente síncrono de memoria en grafos.

El backend de BeeMarketBot es síncrono (hilos de Flask y de simulación) y
Graphiti es asíncrono. Este cliente puentea ambos: mantiene un bucle de eventos
propio en un hilo dedicado y expone métodos que bloquean al llamador hasta
obtener el resultado.

Conserva la forma del cliente anterior (``client.graph.*`` y ``client.batch.*``)
para que los servicios del backend no cambien. Dos operaciones son asíncronas a
propósito, porque el backend las consulta después:

- ``graph.add`` deja el episodio en cola; ``graph.episode.get`` informa cuándo
  se procesó. Las actividades de la simulación se agrupan para ingestarlas juntas.
- ``batch.process`` inicia la ingesta en segundo plano; ``batch.get`` informa el
  avance.
"""

from __future__ import annotations

import asyncio
import os
import re
import threading
from concurrent.futures import Future, TimeoutError as FutureTimeoutError
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from ..config import Config
from ..utils.logger import get_logger
from .batches import TERMINAL_STATUSES, BatchRecord, BatchRegistry, ItemRecord, paginate
from .engine import (
    EPISODE_FAILED,
    EPISODE_SUCCEEDED,
    EpisodeResult,
    EpisodeSpec,
    GraphitiEngine,
    new_uuid,
)
from .errors import BadRequestError, ConfigError, GraphMemoryError, NotFoundError, ServerError
from .objects import Bag, batch, batch_item, episode, page, raw_response
from .settings import GraphMemorySettings
from .types import BatchAddItem, EntityEdgeSourceTarget, edge_definition, entity_definition

logger = get_logger("mirofish.graph_memory.client")

# Estados de un episodio en cola (actividades de la simulación).
STATE_PENDING = "pending"
STATE_PROCESSING = "processing"
STATE_PROCESSED = "processed"
STATE_FAILED = "failed"

NEXT_CURSOR_HEADER = "zep-next-cursor"  # Nombre histórico que lee la paginación.
DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 100
MAX_SEARCH_LIMIT = 50
STARTUP_TIMEOUT_SECONDS = 120.0
# Cortesía al cerrar: tiempo máximo de espera de las operaciones en curso.
SHUTDOWN_GRACE_SECONDS = 30.0
# El motor solo admite letras, dígitos, guiones y guiones bajos en el
# identificador del grafo; el nombre sirve además de espacio en la base de grafos.
_GRAPH_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
# Los tipos de la ontología se usan como etiquetas de la base de grafos.
_TYPE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _check_graph_id(graph_id: Any) -> str:
    if not isinstance(graph_id, str) or not _GRAPH_ID.match(graph_id):
        raise BadRequestError(
            "El identificador del grafo debe tener entre 1 y 128 caracteres, "
            "solo letras, dígitos, '-' y '_'"
        )
    return graph_id


def _check_type_names(names: Any) -> None:
    for name in names or ():
        if not _TYPE_NAME.match(str(name)):
            raise BadRequestError(
                f"El tipo de ontología '{name}' no es un nombre válido: use letras, dígitos "
                "o '_', sin empezar por dígito (p. ej. Persona, WORKS_AT)"
            )


def _as_source_target(pair: Any, edge_name: str) -> EntityEdgeSourceTarget:
    """Acepta el par origen→destino como objeto, diccionario o secuencia de dos valores."""

    if isinstance(pair, EntityEdgeSourceTarget):
        return pair
    values = [pair.get("source", "Entity"), pair.get("target", "Entity")] if isinstance(pair, dict) else (
        list(pair) if isinstance(pair, (list, tuple)) else None
    )
    if values is None or len(values) != 2:
        raise BadRequestError(f"La relación {edge_name} tiene un par origen→destino inválido: {pair!r}")
    return EntityEdgeSourceTarget(source=str(values[0] or "Entity"), target=str(values[1] or "Entity"))


def _parse_timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value.strip():
        try:
            return _parse_timestamp(datetime.fromisoformat(value.strip().replace("Z", "+00:00")))
        except ValueError:
            return None
    return None


class _AsyncRuntime:
    """Bucle de eventos propio que posee el motor, arrancado la primera vez que se usa."""

    def __init__(self, settings: GraphMemorySettings) -> None:
        self._settings = settings
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._engine: GraphitiEngine | None = None
        self._ready = threading.Event()
        self._error: BaseException | None = None
        self._stop: asyncio.Event | None = None
        # Operaciones en curso. Solo lo tocan corrutinas del bucle propio.
        self._inflight = 0

    @property
    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        """Arranca el bucle si hace falta y espera a que el motor esté listo.

        Si el arranque anterior falló (p. ej. la base de grafos estaba apagada),
        se intenta de nuevo en cada llamada.
        """

        with self._lock:
            if not self.is_alive:
                self._ready.clear()
                self._error = None
                self._thread = threading.Thread(target=self._run, name="graph-memory-engine", daemon=True)
                self._thread.start()
        if not self._ready.wait(STARTUP_TIMEOUT_SECONDS):
            raise ServerError("El motor de grafos no respondió al arrancar", status_code=503)
        if self._error is not None:
            raise self._error

    def _run(self) -> None:
        async def main() -> None:
            self._loop = asyncio.get_running_loop()
            self._stop = asyncio.Event()
            engine = GraphitiEngine(self._settings)
            self._engine = engine
            try:
                await engine.start()
            except BaseException as error:
                self._error = error
                self._ready.set()
                return
            self._ready.set()
            await self._stop.wait()
            await self._drain()
            await engine.close()

        asyncio.run(main())

    async def _drain(self) -> None:
        """Espera, hasta el límite de cortesía, las operaciones que aún corren."""

        assert self._loop is not None
        deadline = self._loop.time() + SHUTDOWN_GRACE_SECONDS
        while self._inflight > 0 and self._loop.time() < deadline:
            await asyncio.sleep(0.05)
        if self._inflight:
            logger.warning(
                "Se cierra el motor de grafos con %d operaciones en curso", self._inflight
            )

    def submit(self, operation: Callable[[GraphitiEngine], Awaitable[Any]]) -> Future:
        self.start()
        loop, engine = self._loop, self._engine
        if loop is None or engine is None or loop.is_closed():
            raise ServerError("El motor de grafos no está disponible", status_code=503)

        async def tracked() -> Any:
            self._inflight += 1
            try:
                return await operation(engine)
            finally:
                self._inflight -= 1

        return asyncio.run_coroutine_threadsafe(tracked(), loop)

    def stop(self, timeout: float = 45.0) -> None:
        loop, thread = self._loop, self._thread
        if loop is not None and thread is not None and thread.is_alive():
            loop.call_soon_threadsafe(self._stop.set)
            thread.join(timeout=timeout)
        self._thread = None
        self._loop = None
        self._engine = None


class _EpisodeRegistry:
    """Estado en memoria de los episodios en cola (actividades de la simulación)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: dict[str, dict[str, Any]] = {}

    def add(self, record: dict[str, Any]) -> None:
        with self._lock:
            self._records[record["uuid"]] = record

    def get(self, uuid: str) -> dict[str, Any] | None:
        with self._lock:
            return self._records.get(uuid)

    def oldest_graph(self) -> str | None:
        with self._lock:
            pending = [r for r in self._records.values() if r["status"] == STATE_PENDING]
            if not pending:
                return None
            return min(pending, key=lambda r: r["created_at"])["graph_id"]

    def take(self, graph_id: str, limit: int) -> list[dict[str, Any]]:
        with self._lock:
            pending = sorted(
                (r for r in self._records.values() if r["status"] == STATE_PENDING and r["graph_id"] == graph_id),
                key=lambda r: r["created_at"],
            )[: max(1, limit)]
            for record in pending:
                record["status"] = STATE_PROCESSING
            return list(pending)

    def mark(self, uuid: str, status: str, error: str | None = None) -> None:
        with self._lock:
            record = self._records.get(uuid)
            if record is not None:
                record["status"] = status
                record["error"] = error

    def forget_graph(self, graph_id: str) -> None:
        """Marca como fallidos los episodios de un grafo que se eliminó."""

        with self._lock:
            for record in self._records.values():
                if record["graph_id"] == graph_id and record["status"] in (STATE_PENDING, STATE_PROCESSING):
                    record["status"] = STATE_FAILED
                    record["error"] = "El grafo se eliminó antes de procesar el episodio"

    def pending_count(self) -> int:
        with self._lock:
            return sum(1 for r in self._records.values() if r["status"] in (STATE_PENDING, STATE_PROCESSING))


class _IngestionWorker:
    """Procesa en segundo plano los episodios dejados en cola por ``graph.add``.

    Espera bloqueado hasta que llega una actividad; en lugar de sondear. Cuando
    llega, aguarda un instante para reunir varias del mismo grafo y las ingiere
    juntas, que es varias veces más rápido que una por una.
    """

    GATHER_SECONDS = 1.0

    def __init__(self, client: "GraphMemoryClient") -> None:
        self._client = client
        self._wakeup = threading.Event()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def wake(self) -> None:
        """Avisa de actividades nuevas, arrancando el hilo si hace falta."""

        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                if self._stop.is_set():
                    return
                self._thread = threading.Thread(target=self._run, name="graph-memory-ingestion", daemon=True)
                self._thread.start()
        # Se marca después de agregar la actividad: el hilo no puede perder el aviso.
        self._wakeup.set()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._wakeup.set()
        with self._lock:
            thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)

    def _run(self) -> None:
        registry = self._client._episodes
        window = self._client.settings.ingestion_window
        while not self._stop.is_set():
            self._wakeup.wait()
            self._wakeup.clear()
            if self._stop.is_set():
                break
            graph_id = registry.oldest_graph()
            if graph_id is None:
                continue
            # Dar tiempo a que lleguen más actividades y procesarlas en bloque.
            self._wakeup.wait(self.GATHER_SECONDS)
            records = registry.take(graph_id, window)
            if not records:
                continue
            specs = [
                EpisodeSpec(
                    uuid=record["uuid"],
                    data=record["data"],
                    name=record["name"],
                    source_description=record.get("source_description"),
                    reference_time=record.get("reference_time"),
                    metadata=record.get("metadata"),
                )
                for record in records
            ]
            for result in self._client.ingest(graph_id, specs):
                registry.mark(
                    result.uuid,
                    STATE_PROCESSED if result.status == EPISODE_SUCCEEDED else STATE_FAILED,
                    result.error,
                )


class _GraphNamespace:
    def __init__(self, client: "GraphMemoryClient") -> None:
        self._client = client
        self._known_graphs: set[str] = set()
        self._known_lock = threading.Lock()
        self.node = _NodeNamespace(client)
        self.edge = _EdgeNamespace(client)
        self.episode = _EpisodeNamespace(client)

    # -- grafos
    def create(
        self,
        graph_id: str | None = None,
        name: str | None = None,
        description: str | None = None,
    ) -> Bag:
        _check_graph_id(graph_id)
        created = self._client._run(lambda engine: engine.create_graph(graph_id, name or graph_id, description or ""))
        with self._known_lock:
            self._known_graphs.add(graph_id)
        return created

    def get(self, graph_id: str | None = None) -> Bag:
        _check_graph_id(graph_id)
        found = self._client._run(lambda engine: engine.get_graph(graph_id))
        if found is None:
            raise NotFoundError(f"Graph not found: {graph_id}", status_code=404)
        with self._known_lock:
            self._known_graphs.add(graph_id)
        return found

    def delete(self, graph_id: str | None = None) -> None:
        _check_graph_id(graph_id)
        deleted = self._client._run(lambda engine: engine.delete_graph(graph_id))
        self._client._episodes.forget_graph(graph_id)
        with self._known_lock:
            self._known_graphs.discard(graph_id)
        if not deleted:
            raise NotFoundError(f"Graph not found: {graph_id}", status_code=404)

    def _require_graph(self, graph_id: str) -> None:
        """Comprueba que el grafo existe una sola vez por proceso."""

        with self._known_lock:
            if graph_id in self._known_graphs:
                return
        if not self._client._run(lambda engine: engine.graph_exists(graph_id)):
            raise NotFoundError(f"Graph not found: {graph_id}", status_code=404)
        with self._known_lock:
            self._known_graphs.add(graph_id)

    # -- ontología
    def set_ontology(
        self,
        graph_ids: list[str] | None = None,
        entities: dict[str, type] | None = None,
        edges: dict[str, tuple[type, list[EntityEdgeSourceTarget]]] | None = None,
    ) -> None:
        graph_ids = [_check_graph_id(graph_id) for graph_id in (graph_ids or [])]
        if not graph_ids:
            raise BadRequestError("graph_ids es obligatorio")
        _check_type_names(list(entities or {}) + list(edges or {}))
        ontology: dict[str, Any] = {"entity_types": [], "edge_types": []}
        for name, model in (entities or {}).items():
            ontology["entity_types"].append(entity_definition(str(name), model))
        for name, definition in (edges or {}).items():
            if not isinstance(definition, tuple) or len(definition) != 2:
                raise BadRequestError(f"Definición de relación inválida: {name}")
            model, source_targets = definition
            pairs = [_as_source_target(pair, str(name)) for pair in (source_targets or [])]
            ontology["edge_types"].append(edge_definition(str(name), model, pairs))
        for graph_id in graph_ids:
            self._client._run(lambda engine, gid=graph_id: engine.set_ontology(gid, ontology))

    # -- episodios
    def add(
        self,
        graph_id: str | None = None,
        type: str = "text",  # noqa: A002 - nombre de la interfaz anterior
        data: str | None = None,
        created_at: Any = None,
        source_description: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Bag:
        """Deja un episodio en cola y vuelve de inmediato."""

        _check_graph_id(graph_id)
        if not isinstance(data, str) or not data.strip():
            raise BadRequestError("data debe ser un texto no vacío")
        if type not in ("text", "message"):
            raise BadRequestError("type debe ser 'text' o 'message'")
        self._require_graph(graph_id)

        episode_uuid = new_uuid()
        now = datetime.now(timezone.utc)
        self._client._episodes.add(
            {
                "uuid": episode_uuid,
                "graph_id": graph_id,
                "data": data,
                "name": f"activity_{episode_uuid[:8]}",
                "source_description": source_description or "text",
                "metadata": dict(metadata or {}),
                "reference_time": _parse_timestamp(created_at),
                "created_at": now.timestamp(),
                "status": STATE_PENDING,
                "error": None,
            }
        )
        self._client._worker.wake()
        return episode(
            episode_uuid,
            f"activity_{episode_uuid[:8]}",
            now,
            processed=False,
            source_description=source_description,
            metadata=metadata,
        )

    # -- búsqueda
    def search(
        self,
        graph_id: str | None = None,
        query: str | None = None,
        limit: int = 10,
        scope: str = "edges",
        reranker: str | None = None,
    ) -> Bag:
        _check_graph_id(graph_id)
        if not isinstance(query, str) or not query.strip():
            raise BadRequestError("La consulta de búsqueda no puede estar vacía")
        try:
            bounded = int(limit)
        except (TypeError, ValueError) as error:
            raise BadRequestError("El límite de búsqueda debe ser un entero") from error
        if bounded < 1:
            raise BadRequestError("El límite de búsqueda debe ser al menos 1")
        # ``reranker`` se acepta por compatibilidad: la búsqueda híbrida del motor
        # combina resultados por fusión de rangos y no necesita un reclasificador.
        del reranker
        return self._client._run(
            lambda engine: engine.search(graph_id, query.strip(), scope=scope, limit=min(bounded, MAX_SEARCH_LIMIT))
        )


class _EpisodeNamespace:
    def __init__(self, client: "GraphMemoryClient") -> None:
        self._client = client

    def get(self, uuid_: str | None = None, **kwargs: Any) -> Bag:
        if kwargs.get("uuid"):
            uuid_ = kwargs["uuid"]
        if not uuid_:
            raise BadRequestError("El identificador del episodio es obligatorio")
        record = self._client._episodes.get(str(uuid_))
        if record is not None:
            return episode(
                record["uuid"],
                record["name"],
                datetime.fromtimestamp(record["created_at"], tz=timezone.utc),
                processed=record["status"] == STATE_PROCESSED,
                source_description=record.get("source_description"),
                metadata=record.get("metadata"),
                failed=record["status"] == STATE_FAILED,
                error=record.get("error"),
            )
        stored = self._client._run(lambda engine: engine.get_episode(str(uuid_)))
        if stored is None:
            raise NotFoundError(f"Episode not found: {uuid_}", status_code=404)
        return stored


class _NodeNamespace:
    def __init__(self, client: "GraphMemoryClient") -> None:
        self._client = client
        self.with_raw_response = _RawReader(client, "node")

    def get(self, uuid_: str | None = None, **kwargs: Any) -> Bag:
        if kwargs.get("uuid"):
            uuid_ = kwargs["uuid"]
        if not uuid_:
            raise BadRequestError("El identificador del nodo es obligatorio")
        found = self._client._run(lambda engine: engine.get_node(str(uuid_)))
        if found is None:
            raise NotFoundError(f"Node not found: {uuid_}", status_code=404)
        return found

    def get_edges(self, node_uuid: str | None = None, **kwargs: Any) -> list[Bag]:
        if not node_uuid:
            raise BadRequestError("El identificador del nodo es obligatorio")
        graph_id = self._client._run(lambda engine: engine.graph_of("node", str(node_uuid)))
        if not graph_id:
            return []
        return self._client._run(lambda engine: engine.get_node_edges(graph_id, str(node_uuid)))

    def get_by_graph_id(self, graph_id: str, limit: int | None = None, cursor: str | None = None) -> Bag:
        return self.with_raw_response.get_by_graph_id(graph_id, limit=limit, cursor=cursor).data


class _EdgeNamespace:
    def __init__(self, client: "GraphMemoryClient") -> None:
        self._client = client
        self.with_raw_response = _RawReader(client, "edge")

    def get(self, uuid_: str | None = None, **kwargs: Any) -> Bag:
        if kwargs.get("uuid"):
            uuid_ = kwargs["uuid"]
        if not uuid_:
            raise BadRequestError("El identificador de la relación es obligatorio")
        found = self._client._run(lambda engine: engine.get_edge(str(uuid_)))
        if found is None:
            raise NotFoundError(f"Edge not found: {uuid_}", status_code=404)
        return found


class _RawReader:
    """Listado paginado de nodos o de relaciones de un grafo."""

    def __init__(self, client: "GraphMemoryClient", kind: str) -> None:
        self._client = client
        self._kind = kind

    def get_by_graph_id(self, graph_id: str, limit: int | None = None, cursor: str | None = None) -> Bag:
        _check_graph_id(graph_id)
        if self._kind == "node":
            items = self._client._run(lambda engine: engine.list_nodes(graph_id))
        else:
            items = self._client._run(lambda engine: engine.list_edges(graph_id))
        items.sort(key=lambda item: (str(item.created_at or ""), item.uuid))
        slice_, next_cursor = paginate(items, limit, cursor)
        headers = {NEXT_CURSOR_HEADER: next_cursor} if next_cursor is not None else {}
        return raw_response(slice_, headers)


class _BatchNamespace:
    def __init__(self, client: "GraphMemoryClient") -> None:
        self._client = client

    def create(self, metadata: dict[str, Any] | None = None) -> Bag:
        self._client.ensure_resumed()
        return self._client._batch_view(self._client._batches.create(metadata))

    def add(self, batch_id: str | None = None, items: list[BatchAddItem] | None = None) -> list[Bag]:
        if not batch_id:
            raise BadRequestError("batch_id es obligatorio")
        if not items:
            raise BadRequestError("El lote necesita al menos un elemento")
        records: list[ItemRecord] = []
        for position, item in enumerate(items or []):
            try:
                graph_id = _check_graph_id(getattr(item, "graph_id", None))
            except BadRequestError as error:
                raise BadRequestError(f"El elemento {position} del lote no tiene un grafo válido: {error}") from error
            data = getattr(item, "data", None)
            if not isinstance(data, str) or not data.strip():
                raise BadRequestError(f"El elemento {position} del lote no tiene texto")
            records.append(
                ItemRecord(
                    sequence_index=0,
                    graph_id=graph_id,
                    episode_uuid=new_uuid(),
                    data=data,
                    source_description=getattr(item, "source_description", None),
                    metadata=dict(getattr(item, "metadata", None) or {}),
                )
            )
        # El uuid de cada episodio se fija aquí: permite conciliar un envío cuya
        # respuesta se perdió y reanudar sin duplicar episodios.
        return [
            batch_item(
                record.sequence_index,
                status=record.status,
                episode_uuid=record.episode_uuid,
            )
            for record in self._client._batches.add_items(batch_id, records)
        ]

    def process(self, batch_id: str | None = None) -> None:
        """Inicia la ingesta del lote en segundo plano."""

        if not batch_id:
            raise BadRequestError("batch_id es obligatorio")
        record = self._client._batches.require(batch_id)
        if not record.items:
            raise BadRequestError("El lote no tiene elementos que procesar")
        if record.status in TERMINAL_STATUSES:
            raise BadRequestError(f"El lote {batch_id} ya terminó como {record.status}", status_code=409)
        # Si ya hay una ingesta en curso el método no hace nada; si el proceso se
        # reinició, arranca otra vez con los elementos que quedaron pendientes.
        self._client.start_batch(batch_id)

    def get(self, batch_id: str | None = None) -> Bag:
        if not batch_id:
            raise BadRequestError("batch_id es obligatorio")
        self._client.ensure_resumed()
        record = self._client._batches.get(batch_id)
        if record is None:
            raise NotFoundError(f"Batch not found: {batch_id}", status_code=404)
        return self._client._batch_view(record)

    def list(self, limit: int | None = None, cursor: Any = None) -> Bag:
        self._client.ensure_resumed()
        records, next_cursor = self._client._batches.list(limit, cursor)
        return Bag(batches=[self._client._batch_view(r) for r in records], next_cursor=next_cursor)

    def list_items(self, batch_id: str | None = None, limit: int | None = None, cursor: Any = None) -> Bag:
        if not batch_id:
            raise BadRequestError("batch_id es obligatorio")
        items, next_cursor = self._client._batches.list_items(batch_id, limit, cursor)
        return page(
            [
                batch_item(
                    item.sequence_index,
                    status=item.status,
                    episode_uuid=item.episode_uuid,
                    error=item.error,
                )
                for item in items
            ],
            next_cursor,
        )


class GraphMemoryClient:
    """Cliente síncrono de memoria en grafos con la interfaz que usa el backend."""

    def __init__(
        self,
        settings: GraphMemorySettings | None = None,
        *,
        state_dir: str | os.PathLike[str] | None = None,
    ) -> None:
        self.settings = settings or GraphMemorySettings.from_env()
        problems = self.settings.validate()
        if problems:
            raise ConfigError("Configuración del motor de grafos inválida: " + "; ".join(problems))
        self._runtime = _AsyncRuntime(self.settings)
        self._episodes = _EpisodeRegistry()
        directory = state_dir or os.environ.get("GRAPH_STATE_DIR") or os.path.join(
            Config.UPLOAD_FOLDER, "graph_memory", "batches"
        )
        self._batches = BatchRegistry(directory)
        self._batch_workers: dict[str, threading.Thread] = {}
        # Reentrante: los métodos de lotes se llaman entre sí con el candado tomado.
        self._batch_lock = threading.RLock()
        self._resume_lock = threading.Lock()
        self._resumed = False
        self._worker = _IngestionWorker(self)
        self.graph = _GraphNamespace(self)
        self.batch = _BatchNamespace(self)

    # ------------------------------------------------------------ infraestructura
    def _run(
        self,
        operation: Callable[[GraphitiEngine], Awaitable[Any]],
        timeout: float | None = None,
    ) -> Any:
        """Ejecuta una operación del motor y espera su resultado."""

        try:
            future = self._runtime.submit(operation)
            wait = self.settings.request_timeout if timeout is None else timeout
            return future.result(wait)
        except FutureTimeoutError as error:
            future.cancel()
            raise ServerError("El motor de grafos no respondió a tiempo", status_code=503) from error
        except GraphMemoryError:
            raise
        except BaseException as error:
            raise GraphitiEngine._translate(error) from error

    def ingest(self, graph_id: str, specs: list[EpisodeSpec]) -> list[EpisodeResult]:
        """Ingiere episodios sin límite de espera; un fallo marca cada episodio."""

        try:
            return self._run(
                lambda engine: engine.add_episodes(graph_id, specs),
                timeout=None,
            )
        except BaseException as error:
            translated = GraphitiEngine._translate(error, "ingesta de episodios")
            logger.error("Falló la ingesta de %d episodios del grafo %s: %s", len(specs), graph_id, translated)
            return [EpisodeResult(spec.uuid, EPISODE_FAILED, str(translated)) for spec in specs]

    def ping(self) -> dict[str, Any]:
        return self._run(lambda engine: engine.ping(), timeout=30.0)

    def stats(self) -> dict[str, Any]:
        return {
            "pending_episodes": self._episodes.pending_count(),
            "active_batches": [key for key, thread in self._batch_workers.items() if thread.is_alive()],
            "settings": self.settings.redacted(),
        }

    def close(self) -> None:
        self._worker.stop()
        self._runtime.stop()

    # ------------------------------------------------------------ lotes
    def _batch_view(self, record: BatchRecord) -> Bag:
        succeeded, failed = record.counts
        return batch(
            record.batch_id,
            record.status,
            record.metadata,
            total_items=len(record.items),
            succeeded_items=succeeded,
            failed_items=failed,
            created_at=record.created_at,
        )

    def ensure_resumed(self) -> None:
        """Reanuda la ingesta de los lotes que quedaron a medias por un reinicio."""

        with self._resume_lock:
            if self._resumed:
                return
            self._resumed = True
            for record in self._batches.interrupted():
                try:
                    self._batches.reset_for_resume(record.batch_id)
                    self.start_batch(record.batch_id)
                    logger.info("Lote de ingesta reanudado tras el reinicio: %s", record.batch_id)
                except GraphMemoryError as error:
                    logger.warning("No se pudo reanudar el lote %s: %s", record.batch_id, error)

    def start_batch(self, batch_id: str) -> None:
        """Arranca la ingesta de un lote en segundo plano, si no está ya en curso."""

        with self._batch_lock:
            thread = self._batch_workers.get(batch_id)
            if thread is not None and thread.is_alive():
                return
            # El estado se fija aquí y no en el hilo: quien concilie un fallo de
            # `process` con una lectura no debe encontrar un lote "draft" ambiguo.
            if self._batches.require(batch_id).status == "draft":
                self._batches.set_status(batch_id, "queued")
            thread = threading.Thread(
                target=self._process_batch,
                args=(batch_id,),
                name=f"graph-memory-batch-{batch_id[:8]}",
                daemon=True,
            )
            self._batch_workers[batch_id] = thread
            thread.start()

    def _process_batch(self, batch_id: str) -> None:
        window = self.settings.ingestion_window
        try:
            self._batches.set_status(batch_id, "processing")
            record = self._batches.require(batch_id)
            groups: dict[str, list[ItemRecord]] = {}
            for item in record.items:
                if item.status == "pending":
                    groups.setdefault(item.graph_id, []).append(item)

            for graph_id, items in groups.items():
                for start in range(0, len(items), window):
                    chunk = items[start : start + window]
                    specs = [
                        EpisodeSpec(
                            uuid=item.episode_uuid or new_uuid(),
                            data=item.data,
                            name=f"chunk_{item.sequence_index}",
                            source_description=item.source_description or "document chunk",
                            metadata=item.metadata,
                        )
                        for item in chunk
                    ]
                    results = self.ingest(graph_id, specs)
                    updates = {
                        item.sequence_index: {
                            "episode_uuid": spec.uuid,
                            "status": "succeeded" if result.status == EPISODE_SUCCEEDED else "failed",
                            "error": result.error,
                        }
                        for item, spec, result in zip(chunk, specs, results)
                    }
                    self._batches.update_items(batch_id, updates)

            record = self._batches.require(batch_id)
            succeeded, failed = record.counts
            if failed == 0 and succeeded == len(record.items):
                final = "succeeded"
            elif succeeded:
                final = "partial"
            else:
                final = "failed"
            self._batches.set_status(batch_id, final)
            logger.info("Lote %s terminó como %s: %d procesados, %d fallos", batch_id, final, succeeded, failed)
        except BaseException as error:
            logger.error("Error inesperado al procesar el lote %s: %s", batch_id, error)
            try:
                self._batches.set_status(batch_id, "failed")
            except GraphMemoryError:
                pass
        finally:
            with self._batch_lock:
                self._batch_workers.pop(batch_id, None)


_shared_lock = threading.Lock()
_shared_client: GraphMemoryClient | None = None


def get_shared_client() -> GraphMemoryClient:
    """Cliente único por proceso, creado con la configuración del entorno."""

    global _shared_client
    with _shared_lock:
        if _shared_client is None:
            _shared_client = GraphMemoryClient()
        return _shared_client


def reset_shared_client() -> None:
    """Cierra y olvida el cliente compartido. Para pruebas y reconfiguración."""

    global _shared_client
    with _shared_lock:
        client, _shared_client = _shared_client, None
    if client is not None:
        client.close()
