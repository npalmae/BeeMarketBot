"""Motor asíncrono de memoria en grafos sobre Graphiti.

Cada grafo del proyecto equivale a un ``group_id`` de Graphiti. El motor guarda
junto a cada grafo su ontología (tipos de entidad y de relación) para aplicarla
en cada ingesta, también durante la simulación.

Dónde vive cada cosa:

- FalkorDB guarda cada grafo en un espacio propio. El motor usa para cada grafo
  un controlador y una instancia de Graphiti direccionados a ese espacio, así
  que nadie tiene que clonar controladores por dentro. Neo4j guarda todos los
  grafos en una misma base y los separa por la propiedad ``group_id``.
- El registro de la aplicación (qué grafos existen, su ontología y a qué grafo
  pertenece cada nodo, relación o episodio) vive en la base principal con las
  etiquetas ``BeeGraph`` y ``BeeRef``, fuera del alcance de las consultas de
  Graphiti.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid as uuid_lib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from ..utils.logger import get_logger
from .errors import AuthError, BadRequestError, GraphMemoryError, RateLimitError, ServerError
from .objects import Bag, edge, episode, graph, node, search_result
from .settings import GraphMemorySettings
from .types import model_from_definition

logger = get_logger("mirofish.graph_memory")

REF_LABEL = "BeeRef"
GRAPH_LABEL = "BeeGraph"
MAX_SEARCH_LIMIT = 50
# En el reordenado semántico: peso de la semejanza con la consulta frente a la
# variedad entre resultados. El resto penaliza los hechos casi repetidos.
SEARCH_RERANK_LAMBDA = 0.85

EPISODE_SUCCEEDED = "succeeded"
EPISODE_FAILED = "failed"

_REGISTRY_INDEXES = (
    f"FOR (g:{GRAPH_LABEL}) ON (g.group_id)",
    f"FOR (r:{REF_LABEL}) ON (r.uuid)",
)


@dataclass
class EpisodeSpec:
    """Un episodio de texto que se va a agregar a un grafo."""

    uuid: str
    data: str
    name: str | None = None
    source_description: str | None = None
    reference_time: datetime | None = None
    metadata: dict[str, Any] | None = None


@dataclass
class EpisodeResult:
    """Estado de un episodio tras la ingesta."""

    uuid: str
    status: str
    error: str | None = None


@dataclass
class _GroupScope:
    """Controlador y motor de Graphiti direccionados al espacio de un grafo."""

    driver: Any
    graphiti: Any


_gated_driver_class: Any = None


def gated_falkordb_driver() -> Any:
    """Clase del controlador de FalkorDB que limita las consultas simultáneas.

    El servidor responde ``Max pending queries exceeded`` cuando se acumulan más
    consultas de las que puede atender con sus hilos. El límite se comparte entre
    la base principal y todos los grafos.
    """

    global _gated_driver_class
    if _gated_driver_class is None:
        from graphiti_core.driver.falkordb_driver import FalkorDriver

        class GatedFalkorDriver(FalkorDriver):
            _gate: asyncio.Semaphore | None = None

            async def execute_query(self, cypher_query_: str, **kwargs: Any) -> Any:
                gate = self._gate
                if gate is None:
                    return await super().execute_query(cypher_query_, **kwargs)
                async with gate:
                    return await super().execute_query(cypher_query_, **kwargs)

        GatedFalkorDriver.__name__ = FalkorDriver.__name__
        GatedFalkorDriver.__qualname__ = FalkorDriver.__qualname__
        _gated_driver_class = GatedFalkorDriver
    return _gated_driver_class


def semantic_recipe(recipe: Any, limit: int) -> Any:
    """Prepara una receta de búsqueda para ordenar por semejanza semántica con la consulta.

    La fusión por rangos que viene por defecto deja ganar a las coincidencias de
    palabras clave: en un grafo de prueba, el hecho esperado aparecía primero en
    1 de cada 6 consultas; ordenando por semejanza, en 5 de 6. El reordenado
    aprovecha los vectores ya guardados y no cuesta llamadas adicionales.
    """

    from graphiti_core.search.search_config import EdgeReranker, NodeReranker

    changes: dict[str, Any] = {"limit": limit, "reranker_min_score": -1.0}
    if recipe.edge_config is not None:
        changes["edge_config"] = recipe.edge_config.model_copy(
            update={"reranker": EdgeReranker.mmr, "mmr_lambda": SEARCH_RERANK_LAMBDA}
        )
    if recipe.node_config is not None:
        changes["node_config"] = recipe.node_config.model_copy(
            update={"reranker": NodeReranker.mmr, "mmr_lambda": SEARCH_RERANK_LAMBDA}
        )
    return recipe.model_copy(update=changes)


class OmitQueryText(logging.Filter):
    """Deja de cada fallo solo la primera línea: el error, sin la consulta ni sus parámetros.

    El controlador de FalkorDB registra la consulta completa junto con sus
    parámetros, que pueden contener texto del usuario. El fallo ya se informa,
    traducido, a quien hizo la consulta.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = record.getMessage().split("\n", 1)[0]
        record.args = ()
        return True


def keep_query_text_out_of_logs() -> None:
    """Aplica el filtro al registrador del controlador de FalkorDB, una sola vez."""

    driver_logger = logging.getLogger("graphiti_core.driver.falkordb_driver")
    if not any(isinstance(item, OmitQueryText) for item in driver_logger.filters):
        driver_logger.addFilter(OmitQueryText())


def new_uuid() -> str:
    return str(uuid_lib.uuid4())


def _as_datetime(value: Any) -> Any:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value:
        try:
            return _as_datetime(datetime.fromisoformat(value.replace("Z", "+00:00")))
        except ValueError:
            return value
    return value


def _as_attributes(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return dict(value) if isinstance(value, dict) else {}


def _uuids_of(items: Any) -> list[str]:
    """Uuids no vacíos de una lista de nodos o relaciones, sin repeticiones."""

    return sorted({str(getattr(item, "uuid", "") or "") for item in (items or [])} - {""})


class GraphitiEngine:
    """Operaciones asíncronas del motor. Todas corren en un mismo bucle de eventos."""

    def __init__(self, settings: GraphMemorySettings) -> None:
        self.settings = settings
        self._driver: Any = None
        self._graphiti: Any = None
        self._llm_client: Any = None
        self._embedder: Any = None
        self._cross_encoder: Any = None
        self._http_clients: list[Any] = []
        self._clear_data: Any = None
        self._scopes: dict[str, _GroupScope] = {}
        self._group_locks: dict[str, asyncio.Lock] = {}
        # Límites de simultaneidad; se crean en start(), dentro del bucle de eventos.
        self._query_gate: asyncio.Semaphore | None = None
        self._search_gate: asyncio.Semaphore | None = None
        self._started = False

    # ------------------------------------------------------------ ciclo de vida
    async def start(self) -> None:
        """Conecta la base de grafos y prepara los índices y los clientes del modelo."""

        from graphiti_core.utils.maintenance.graph_data_operations import clear_data

        from .llm import (
            build_embedder,
            build_llm_client,
            build_noop_cross_encoder,
            prepare_environment,
        )

        prepare_environment(self.settings)
        self._clear_data = clear_data
        self._started = False
        try:
            self._driver = self._build_driver()
            await self._settle(self._driver)
            self._search_gate = asyncio.Semaphore(self.settings.llm_concurrency)
            if self.settings.database.is_falkordb:
                # El límite depende de los hilos del servidor: se consulta ya conectado.
                self._query_gate = asyncio.Semaphore(await self._query_concurrency())
                self._driver._gate = self._query_gate
            self._llm_client = build_llm_client(self.settings.llm)
            self._embedder = build_embedder(self.settings.embedding)
            self._cross_encoder = build_noop_cross_encoder()
            self._http_clients = [
                client
                for client in (
                    getattr(self._llm_client, "client", None),
                    getattr(self._embedder, "client", None),
                )
                if client is not None
            ]
            self._graphiti = self._new_graphiti(self._driver)
            await self._build_registry_indexes()
        except Exception as error:
            await self._release()
            raise self._translate(error, "iniciando el motor de grafos") from error
        self._started = True
        logger.info(
            "Motor de grafos listo: base=%s://%s:%s/%s modelo=%s embeddings=%s",
            self.settings.database.kind,
            self.settings.database.host,
            self.settings.database.port,
            self.settings.database.database,
            self.settings.llm.model,
            self.settings.embedding.model,
        )

    async def close(self) -> None:
        self._started = False
        await self._release()

    async def _release(self) -> None:
        # Los controladores por grafo comparten la conexión del principal: no se cierran solos.
        self._scopes.clear()
        driver, self._driver = self._driver, None
        clients, self._http_clients = self._http_clients, []
        if driver is not None:
            try:
                await driver.close()
            except Exception as error:  # Cerrar nunca debe enmascarar el fallo original.
                logger.warning("Error al cerrar la conexión con la base de grafos: %s", error)
        for client in clients:
            try:
                await client.close()
            except Exception as error:
                logger.warning("Error al cerrar un cliente del modelo: %s", error)

    async def _query_concurrency(self) -> int:
        """Consultas simultáneas: lo configurado, o cuatro por hilo del servidor.

        Con cuatro hilos, el servidor de prueba rechazaba consultas a partir de
        unas cuarenta; dieciséis deja margen para las escrituras internas del motor.
        """

        configured = self.settings.database.max_concurrent_queries
        if configured:
            return min(configured, 64)
        try:
            threads = int(await self._driver.client.config_get("THREAD_COUNT"))
        except Exception as error:
            logger.warning("No se pudo leer THREAD_COUNT de FalkorDB; se supone un servidor pequeño: %s", error)
            threads = 1
        return max(4, min(32, threads * 4))

    def _build_driver(self) -> Any:
        database = self.settings.database
        if database.is_falkordb:
            keep_query_text_out_of_logs()
            return gated_falkordb_driver()(
                host=database.host,
                port=database.port,
                username=database.username,
                password=database.password,
                database=database.database,
            )
        from graphiti_core.driver.neo4j_driver import Neo4jDriver

        return Neo4jDriver(
            uri=f"neo4j://{database.host}:{database.port}",
            user=database.username,
            password=database.password,
            database=database.database,
        )

    def _new_graphiti(self, driver: Any) -> Any:
        """Instancia de Graphiti sobre un controlador. No hace ninguna conexión nueva."""

        from graphiti_core import Graphiti

        return Graphiti(
            graph_driver=driver,
            llm_client=self._llm_client,
            embedder=self._embedder,
            cross_encoder=self._cross_encoder,
            max_coroutines=self.settings.llm_concurrency,
        )

    @staticmethod
    async def _settle(driver: Any) -> None:
        """Espera a que el controlador termine de crear los índices.

        Construido dentro de un bucle de eventos, el controlador agenda esa
        creación en segundo plano. Dejarla corriendo sin esperarla provoca dos
        problemas comprobados: una tarea cortada a mitad falla con errores de
        conexión al apagar, y una tarea tardía vuelve a crear un espacio que ya
        se borró.
        """

        task = getattr(driver, "_init_task", None)
        if task is None:
            await driver.build_indices_and_constraints()
        else:
            await task

    async def _build_registry_indexes(self) -> None:
        # FalkorDB no admite IF NOT EXISTS; su controlador ignora el error de "ya indexado".
        guard = "" if self.settings.database.is_falkordb else "IF NOT EXISTS "
        for target in _REGISTRY_INDEXES:
            try:
                await self._execute(self._driver, f"CREATE INDEX {guard}{target}")
            except Exception as error:
                # Sin índice el registro sigue funcionando, solo más lento al crecer.
                logger.warning("No se pudo crear un índice del registro de grafos: %s", error)

    async def ping(self) -> dict[str, Any]:
        """Comprueba la conexión a la base de grafos y devuelve la configuración activa."""

        await self._query("RETURN 1 AS ok")
        database = self.settings.database
        return {
            "kind": database.kind,
            "host": database.host,
            "port": database.port,
            "database": database.database,
            "model": self.settings.llm.model,
            "embedding_model": self.settings.embedding.model,
            "embedding_dimensions": self.settings.embedding.dimensions,
        }

    # ------------------------------------------------------------ utilidades
    def _require_started(self) -> Any:
        if not self._started or self._driver is None:
            raise ServerError("El motor de grafos no está iniciado", status_code=503)
        return self._driver

    @staticmethod
    async def _execute(driver: Any, cypher: str, **params: Any) -> list[dict[str, Any]]:
        """Ejecuta una consulta propia y devuelve sus filas como diccionarios.

        Cada controlador recibe los parámetros a su manera: FalkorDB como
        argumentos sueltos y Neo4j dentro de ``params``.
        """

        from graphiti_core.driver.driver import GraphProvider

        if getattr(driver, "provider", None) is GraphProvider.FALKORDB:
            result = await driver.execute_query(cypher, **params)
        else:
            result = await driver.execute_query(cypher, params=params)
        records = result[0] if result else []
        return [dict(record) for record in (records or [])]

    async def _scope(self, graph_id: str) -> _GroupScope:
        """Controlador y motor del espacio de un grafo, creados una sola vez por grafo."""

        driver = self._require_started()
        if not self.settings.database.is_falkordb:
            return _GroupScope(driver, self._graphiti)
        scope = self._scopes.get(graph_id)
        if scope is None:
            scoped = gated_falkordb_driver()(falkor_db=driver.client, database=graph_id)
            scoped._gate = self._query_gate
            try:
                await self._settle(scoped)
                scope = self._scopes[graph_id] = _GroupScope(scoped, self._new_graphiti(scoped))
            except Exception as error:
                raise self._translate(error, f"preparando el grafo {graph_id}") from error
        return scope

    async def _query(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        """Consulta la base principal, donde vive el registro de grafos."""

        driver = self._require_started()
        try:
            return await self._execute(driver, cypher, **params)
        except Exception as error:
            raise self._translate(error, "consultando la base de grafos") from error

    def _group_lock(self, graph_id: str) -> asyncio.Lock:
        lock = self._group_locks.get(graph_id)
        if lock is None:
            lock = self._group_locks[graph_id] = asyncio.Lock()
        return lock

    @staticmethod
    def _retry_after(error: BaseException) -> float | None:
        """Segundos de espera que pide el proveedor en la cabecera Retry-After."""

        headers = getattr(getattr(error, "response", None), "headers", None)
        try:
            value = next(
                (
                    item
                    for name, item in dict(headers or {}).items()
                    if str(name).lower() == "retry-after"
                ),
                None,
            )
            return max(0.0, float(value))
        except (AttributeError, TypeError, ValueError):
            return None

    @classmethod
    def _translate(cls, error: BaseException, action: str = "operación de grafos") -> GraphMemoryError:
        """Convierte cualquier excepción en un error del motor con su código de estado."""

        if isinstance(error, GraphMemoryError):
            return error
        import openai
        import redis.exceptions
        from graphiti_core.errors import (
            EntityTypeValidationError,
            GroupIdValidationError,
            NodeLabelValidationError,
        )
        from neo4j.exceptions import ServiceUnavailable, SessionExpired, TransientError

        text = str(error) or type(error).__name__
        if isinstance(error, openai.RateLimitError):
            return RateLimitError(text, status_code=429, retry_after=cls._retry_after(error))
        if isinstance(error, (openai.AuthenticationError, openai.PermissionDeniedError)):
            return AuthError(text, status_code=401)
        if isinstance(error, (openai.BadRequestError, GroupIdValidationError)):
            return BadRequestError(text, status_code=400)
        if isinstance(error, (EntityTypeValidationError, NodeLabelValidationError)):
            return BadRequestError(f"{action}: {text}", status_code=400)
        if isinstance(
            error,
            (
                openai.APIConnectionError,
                openai.APITimeoutError,
                redis.exceptions.ConnectionError,
                redis.exceptions.TimeoutError,
                ServiceUnavailable,
                SessionExpired,
                TransientError,
                ConnectionError,
                TimeoutError,
            ),
        ):
            return ServerError(f"{action}: {text}", status_code=503)
        if isinstance(error, openai.APIStatusError):
            return GraphMemoryError(f"{action}: {text}", status_code=error.status_code)
        lowered = text.lower()
        if any(
            marker in lowered
            for marker in (
                "connection closed",
                "connection refused",
                "connection error",
                "connection reset",
                "timed out",
                "service unavailable",
                "error executing falkordb query",
                "error executing neo4j query",
                "max pending queries exceeded",
            )
        ):
            return ServerError(f"{action}: {text}", status_code=503)
        return GraphMemoryError(f"{action}: {text}")

    @staticmethod
    def _node_to_bag(item: Any, graph_id: str | None = None) -> Bag:
        return node(
            uuid=str(getattr(item, "uuid", "") or ""),
            name=getattr(item, "name", "") or "",
            labels=list(getattr(item, "labels", None) or []),
            summary=getattr(item, "summary", "") or "",
            attributes=_as_attributes(getattr(item, "attributes", None)),
            created_at=_as_datetime(getattr(item, "created_at", None)),
            graph_id=graph_id,
        )

    @staticmethod
    def _edge_to_bag(item: Any, graph_id: str | None = None) -> Bag:
        return edge(
            uuid=str(getattr(item, "uuid", "") or ""),
            name=getattr(item, "name", "") or "",
            fact=getattr(item, "fact", "") or "",
            source_node_uuid=getattr(item, "source_node_uuid", "") or "",
            target_node_uuid=getattr(item, "target_node_uuid", "") or "",
            attributes=_as_attributes(getattr(item, "attributes", None)),
            created_at=_as_datetime(getattr(item, "created_at", None)),
            valid_at=_as_datetime(getattr(item, "valid_at", None)),
            invalid_at=_as_datetime(getattr(item, "invalid_at", None)),
            expired_at=_as_datetime(getattr(item, "expired_at", None)),
            episodes=[str(value) for value in (getattr(item, "episodes", None) or [])],
            graph_id=graph_id,
        )

    # ------------------------------------------------------------ registro
    async def _remember(self, graph_id: str, kind: str, uuids: list[str]) -> None:
        """Anota a qué grafo pertenece cada elemento para poder buscarlo por uuid."""

        unique = sorted({value for value in uuids if value})
        if not unique:
            return
        try:
            await self._query(
                f"UNWIND $uuids AS item\n"
                f"MERGE (r:{REF_LABEL} {{uuid: item}})\n"
                "SET r.kind = $kind, r.group_id = $graph_id",
                uuids=unique,
                kind=kind,
                graph_id=graph_id,
            )
        except GraphMemoryError as error:
            # El índice de referencias es una ayuda: su fallo no invalida la ingesta.
            logger.warning(
                "No se pudo actualizar el índice de referencias del grafo %s: %s", graph_id, error
            )

    async def graph_of(self, kind: str, uuid: str) -> str | None:
        """Grafo al que pertenece un nodo, una relación o un episodio."""

        rows = await self._query(
            f"MATCH (r:{REF_LABEL}) WHERE r.uuid = $uuid AND r.kind = $kind "
            "RETURN r.group_id AS graph_id LIMIT 1",
            uuid=uuid,
            kind=kind,
        )
        value = rows[0].get("graph_id") if rows else None
        return str(value) if value else None

    async def _forget(self, graph_id: str) -> None:
        await self._query(
            f"MATCH (r:{REF_LABEL}) WHERE r.group_id = $graph_id DETACH DELETE r", graph_id=graph_id
        )

    # ------------------------------------------------------------ grafos
    async def graph_exists(self, graph_id: str) -> bool:
        rows = await self._query(
            f"MATCH (g:{GRAPH_LABEL}) WHERE g.group_id = $graph_id RETURN g.group_id AS graph_id",
            graph_id=graph_id,
        )
        return bool(rows)

    async def create_graph(self, graph_id: str, name: str, description: str = "") -> Bag:
        if await self.graph_exists(graph_id):
            raise GraphMemoryError(f"El grafo {graph_id} ya existe", status_code=409)
        created_at = datetime.now(timezone.utc)
        try:
            if self.settings.database.is_falkordb:
                # El espacio puede traer restos de un grafo anterior con el mismo
                # nombre; se vacía para que el nuevo empiece limpio.
                scope = await self._scope(graph_id)
                await scope.driver.graph_ops.clear_data(scope.driver)
            await self._query(
                f"MERGE (g:{GRAPH_LABEL} {{group_id: $graph_id}})\n"
                "SET g.name = $name, g.description = $description, g.created_at = $created_at",
                graph_id=graph_id,
                name=name or graph_id,
                description=description or "",
                created_at=created_at.isoformat(),
            )
        except Exception as error:
            raise self._translate(error, f"creando el grafo {graph_id}") from error
        return graph(graph_id, name or graph_id, description or "", created_at)

    async def get_graph(self, graph_id: str) -> Bag | None:
        rows = await self._query(
            f"MATCH (g:{GRAPH_LABEL}) WHERE g.group_id = $graph_id\n"
            "RETURN g.name AS name, g.description AS description, g.created_at AS created_at",
            graph_id=graph_id,
        )
        if not rows:
            return None
        row = rows[0]
        return graph(
            graph_id,
            row.get("name") or graph_id,
            row.get("description") or "",
            _as_datetime(row.get("created_at")),
        )

    async def delete_graph(self, graph_id: str) -> bool:
        """Borra el grafo y sus datos. Devuelve False si no existía."""

        if not await self.graph_exists(graph_id):
            return False
        try:
            if self.settings.database.is_falkordb:
                scope = await self._scope(graph_id)
                await scope.driver.graph_ops.clear_data(scope.driver)
                # Sin datos, se elimina el espacio entero para no dejar índices huérfanos.
                await scope.driver.client.select_graph(graph_id).delete()
            else:
                await self._clear_data(self._driver, [graph_id])
            await self._query(
                f"MATCH (g:{GRAPH_LABEL}) WHERE g.group_id = $graph_id DETACH DELETE g",
                graph_id=graph_id,
            )
            await self._forget(graph_id)
        except Exception as error:
            raise self._translate(error, f"borrando el grafo {graph_id}") from error
        self._scopes.pop(graph_id, None)
        self._group_locks.pop(graph_id, None)
        return True

    # ------------------------------------------------------------ ontología
    async def set_ontology(self, graph_id: str, ontology: dict[str, Any]) -> None:
        payload = {
            "entity_types": [item for item in ontology.get("entity_types", []) if isinstance(item, dict)],
            "edge_types": [item for item in ontology.get("edge_types", []) if isinstance(item, dict)],
        }
        await self._query(
            f"MATCH (g:{GRAPH_LABEL}) WHERE g.group_id = $graph_id SET g.ontology = $ontology",
            graph_id=graph_id,
            ontology=json.dumps(payload, ensure_ascii=False),
        )

    async def get_ontology(self, graph_id: str) -> dict[str, Any] | None:
        rows = await self._query(
            f"MATCH (g:{GRAPH_LABEL}) WHERE g.group_id = $graph_id RETURN g.ontology AS ontology",
            graph_id=graph_id,
        )
        raw = rows[0].get("ontology") if rows else None
        if not raw:
            return None
        try:
            value = json.loads(raw) if isinstance(raw, str) else dict(raw)
        except ValueError:
            return None
        return value if isinstance(value, dict) else None

    @staticmethod
    def _ingestion_options(ontology: dict[str, Any]) -> dict[str, Any]:
        """Opciones de ingesta a partir de la ontología guardada del grafo."""

        entity_types: dict[str, Any] = {}
        for definition in ontology.get("entity_types", []):
            name = str(definition.get("name") or "")
            if name:
                entity_types[name] = model_from_definition(definition)

        edge_types: dict[str, Any] = {}
        edge_type_map: dict[tuple[str, str], list[str]] = {}
        for definition in ontology.get("edge_types", []):
            name = str(definition.get("name") or "")
            if not name:
                continue
            edge_types[name] = model_from_definition(definition)
            for pair in definition.get("source_targets", []):
                if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                    continue
                source, target = str(pair[0]), str(pair[1])
                # Una firma solo aporta si ambos tipos existen en la ontología.
                if source in entity_types and target in entity_types:
                    edge_type_map.setdefault((source, target), []).append(name)

        options: dict[str, Any] = {}
        if entity_types:
            options["entity_types"] = entity_types
        if edge_types:
            options["edge_types"] = edge_types
        if edge_type_map:
            options["edge_type_map"] = edge_type_map
        return options

    # ------------------------------------------------------------ ingesta
    async def add_episodes(self, graph_id: str, specs: list[EpisodeSpec]) -> list[EpisodeResult]:
        """Agrega episodios a un grafo y devuelve el estado de cada uno.

        Se procesan en bloque para aprovechar el paralelismo del motor. Si el
        bloque falla, se reintenta episodio por episodio para aislar el problema.
        """

        if not specs:
            return []
        from graphiti_core.nodes import EpisodeType, EpisodicNode
        from graphiti_core.utils.bulk_utils import RawEpisode

        self._require_started()
        async with self._group_lock(graph_id):
            scope = await self._scope(graph_id)
            options: dict[str, Any] = {"group_id": graph_id}
            options.update(self._ingestion_options(await self.get_ontology(graph_id) or {}))

            now = datetime.now(timezone.utc)
            raw: list[Any] = []
            for spec in specs:
                reference_time = spec.reference_time or now
                raw.append(
                    RawEpisode(
                        name=spec.name or f"episode_{spec.uuid[:8]}",
                        uuid=spec.uuid,
                        content=spec.data,
                        source_description=spec.source_description or "text",
                        source=EpisodeType.text,
                        reference_time=reference_time,
                    )
                )

            # Para Graphiti, un episodio con uuid es uno que ya existe y hay que
            # actualizar, así que se guardan primero con su uuid. Repetir una
            # ingesta con el mismo uuid no duplica el episodio.
            stored = [
                EpisodicNode(
                    uuid=item.uuid,
                    name=item.name,
                    group_id=graph_id,
                    labels=[],
                    source=item.source,
                    content=item.content,
                    source_description=item.source_description,
                    created_at=now,
                    valid_at=item.reference_time,
                )
                for item in raw
            ]
            try:
                await asyncio.gather(*(episode_node.save(scope.driver) for episode_node in stored))
            except Exception as error:
                raise self._translate(error, f"guardando episodios en el grafo {graph_id}") from error

            results: list[EpisodeResult] = []
            touched_nodes: list[str] = []
            touched_edges: list[str] = []
            try:
                bulk = await scope.graphiti.add_episode_bulk(bulk_episodes=raw, **options)
                results = [EpisodeResult(spec.uuid, EPISODE_SUCCEEDED) for spec in specs]
                touched_nodes = _uuids_of(getattr(bulk, "nodes", None))
                touched_edges = _uuids_of(getattr(bulk, "edges", None))
            except Exception as error:
                failure = self._translate(error, "ingesta en bloque")
                if isinstance(failure, AuthError):
                    # Sin credenciales válidas cada episodio fallaría igual.
                    raise failure from error
                logger.warning(
                    "La ingesta en bloque del grafo %s falló (%s); se procesa episodio por episodio",
                    graph_id,
                    failure,
                )
                for item in raw:
                    try:
                        single = await scope.graphiti.add_episode(
                            name=item.name,
                            episode_body=item.content,
                            uuid=item.uuid,
                            source_description=item.source_description,
                            source=EpisodeType.text,
                            reference_time=item.reference_time,
                            **options,
                        )
                        results.append(EpisodeResult(item.uuid, EPISODE_SUCCEEDED))
                        touched_nodes.extend(_uuids_of(getattr(single, "nodes", None)))
                        touched_edges.extend(_uuids_of(getattr(single, "edges", None)))
                    except Exception as episode_error:
                        translated = self._translate(episode_error, "ingesta del episodio")
                        logger.warning(
                            "El episodio %s del grafo %s no se pudo procesar: %s",
                            item.uuid,
                            graph_id,
                            translated,
                        )
                        results.append(EpisodeResult(item.uuid, EPISODE_FAILED, str(translated)))

            await self._remember(
                graph_id,
                "episode",
                [result.uuid for result in results if result.status == EPISODE_SUCCEEDED],
            )
            await self._remember(graph_id, "node", touched_nodes)
            await self._remember(graph_id, "edge", touched_edges)
            return results

    # ------------------------------------------------------------ lecturas
    async def list_nodes(self, graph_id: str) -> list[Bag]:
        self._require_started()
        try:
            scope = await self._scope(graph_id)
            items = await scope.driver.entity_node_ops.get_by_group_ids(scope.driver, [graph_id])
        except Exception as error:
            raise self._translate(error, f"leyendo los nodos del grafo {graph_id}") from error
        return [self._node_to_bag(item, graph_id) for item in (items or [])]

    async def list_edges(self, graph_id: str) -> list[Bag]:
        self._require_started()
        try:
            scope = await self._scope(graph_id)
            items = await scope.driver.entity_edge_ops.get_by_group_ids(scope.driver, [graph_id])
        except Exception as error:
            raise self._translate(error, f"leyendo las relaciones del grafo {graph_id}") from error
        return [self._edge_to_bag(item, graph_id) for item in (items or [])]

    async def get_node(self, uuid: str) -> Bag | None:
        from graphiti_core.errors import NodeNotFoundError
        from graphiti_core.nodes import EntityNode

        graph_id = await self.graph_of("node", uuid)
        if not graph_id:
            return None
        try:
            item = await EntityNode.get_by_uuid((await self._scope(graph_id)).driver, uuid)
        except NodeNotFoundError:
            return None
        except Exception as error:
            raise self._translate(error, f"leyendo el nodo {uuid}") from error
        return self._node_to_bag(item, graph_id)

    async def get_edge(self, uuid: str) -> Bag | None:
        from graphiti_core.edges import EntityEdge
        from graphiti_core.errors import EdgeNotFoundError

        graph_id = await self.graph_of("edge", uuid)
        if not graph_id:
            return None
        try:
            item = await EntityEdge.get_by_uuid((await self._scope(graph_id)).driver, uuid)
        except EdgeNotFoundError:
            return None
        except Exception as error:
            raise self._translate(error, f"leyendo la relación {uuid}") from error
        return self._edge_to_bag(item, graph_id)

    async def get_node_edges(self, graph_id: str, node_uuid: str) -> list[Bag]:
        from graphiti_core.edges import EntityEdge

        try:
            items = await EntityEdge.get_by_node_uuid((await self._scope(graph_id)).driver, node_uuid)
        except Exception as error:
            raise self._translate(error, f"leyendo las relaciones del nodo {node_uuid}") from error
        return [self._edge_to_bag(item, graph_id) for item in (items or [])]

    async def get_episode(self, uuid: str) -> Bag | None:
        """Devuelve un episodio ya almacenado; None si no existe."""

        from graphiti_core.errors import NodeNotFoundError
        from graphiti_core.nodes import EpisodicNode

        graph_id = await self.graph_of("episode", uuid)
        if not graph_id:
            return None
        try:
            item = await EpisodicNode.get_by_uuid(
                (await self._scope(graph_id)).driver, uuid, group_id=graph_id
            )
        except NodeNotFoundError:
            return None
        except Exception as error:
            raise self._translate(error, f"leyendo el episodio {uuid}") from error
        return episode(
            uuid,
            item.name or "",
            _as_datetime(item.created_at),
            processed=True,
            content=item.content,
            source_description=item.source_description,
        )

    async def search(self, graph_id: str, query: str, scope: str = "edges", limit: int = 10) -> Bag:
        """Búsqueda híbrida (significado + palabras clave + recorrido del grafo)."""

        from graphiti_core.search import search_config_recipes as recipes

        self._require_started()
        recipes_by_scope = {
            "edges": recipes.EDGE_HYBRID_SEARCH_RRF,
            "nodes": recipes.NODE_HYBRID_SEARCH_RRF,
        }
        recipe = recipes_by_scope.get(scope, recipes.COMBINED_HYBRID_SEARCH_RRF)
        bounded = max(1, min(int(limit or 10), MAX_SEARCH_LIMIT))
        config = semantic_recipe(recipe, bounded)
        try:
            target = await self._scope(graph_id)
            # Cada búsqueda consume una llamada de embeddings: se acotan las simultáneas
            # para que un estallido de consultas no agote el límite del proveedor.
            async with self._search_gate:
                result = await target.graphiti.search_(
                    query, config, group_ids=[graph_id], driver=target.driver
                )
        except Exception as error:
            raise self._translate(error, f"buscando en el grafo {graph_id}") from error
        return search_result(
            [self._edge_to_bag(item, graph_id) for item in (getattr(result, "edges", None) or [])],
            [self._node_to_bag(item, graph_id) for item in (getattr(result, "nodes", None) or [])],
            query,
        )
