"""Standalone GraphRAG worker entry point.

Run with::

    cd backend && uv run python -m app.graphrag_worker

The API only persists build jobs and simulation activities.  This process owns
the expensive LLM/embedding writes into FalkorDB and publishes progress through
the shared ``uploads`` directory.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import signal
import socket
import threading
import time
import traceback
from dataclasses import dataclass
from typing import Any, Dict, Optional

from . import modelconfig
from .graph_memory import BatchAddItem, NotFoundError
from .models.project import ProjectManager, ProjectStatus
from .models.task import TaskManager, TaskStatus
from .services.graph_builder import BatchSubmission, GraphBuilderService
from .services.graphrag_queue import GraphRAGQueue, GraphRAGQueueError
from .services.text_processor import TextProcessor
from .services.zep_graph_memory_updater import AgentActivity, ZepGraphMemoryUpdater
from .utils.locale import set_locale, t
from .utils.logger import get_logger


logger = get_logger("mirofish.graphrag_worker")


class StaleGraphBuildJob(RuntimeError):
    """A newer task owns the project; this queue record must do nothing."""


class ModelConfigReloader:
    """Apply interface-managed model settings between worker operations."""

    def __init__(self) -> None:
        self._signature: Optional[tuple[int, int]] = None

    @staticmethod
    def _path() -> str:
        from .config import Config

        return os.path.join(Config.UPLOAD_FOLDER, "config", "models.json")

    def refresh(self, *, force: bool = False) -> None:
        try:
            stat = os.stat(self._path())
            signature = (stat.st_mtime_ns, stat.st_size)
        except FileNotFoundError:
            signature = (-1, 0)
        if force or signature != self._signature:
            modelconfig.apply()
            self._signature = signature
            logger.info("Configuración de modelos aplicada al worker GraphRAG")


@dataclass
class GraphBuildExecutor:
    task_manager: TaskManager

    @staticmethod
    def _submission_from_existing_batch(
        builder: GraphBuilderService,
        project: Any,
        *,
        graph_id: str,
        chunks: list[str],
    ) -> Optional[BatchSubmission]:
        operation_id = builder.build_operation_id(graph_id, chunks)
        persisted_operation = project.zep_batch_operation_id
        batch_id = project.zep_batch_id

        if persisted_operation and persisted_operation != operation_id:
            raise RuntimeError(
                "El lote persistido no corresponde al texto actual del proyecto"
            )

        if not batch_id and persisted_operation == operation_id:
            match = builder._find_batch_by_operation_id(graph_id, operation_id)
            if match is not None:
                batch_id = getattr(match, "batch_id", None)
                if batch_id:
                    project.zep_batch_id = batch_id
                    ProjectManager.save_project(project)

        if not batch_id:
            return None

        summary = builder.get_batch_summary(batch_id)
        metadata = getattr(summary, "metadata", None) or {}
        remote_operation = metadata.get("mirofish_operation_id")
        if remote_operation and remote_operation != operation_id:
            raise RuntimeError(
                "El identificador del lote GraphRAG no coincide con el proyecto"
            )
        if not project.zep_batch_operation_id:
            project.zep_batch_operation_id = operation_id
            ProjectManager.save_project(project)

        status = getattr(summary, "status", None)
        if status == "draft":
            items = builder._list_batch_items(batch_id)
            indexes = [
                getattr(item, "sequence_index", None)
                for item in items
            ]
            if (
                len(items) > len(chunks)
                or indexes != list(range(len(items)))
            ):
                raise RuntimeError(
                    "El lote GraphRAG parcial no tiene una secuencia recuperable"
                )
            # A draft batch has not started graph ingestion yet. Its persisted
            # operation ID binds it to this exact chunk list, so appending only
            # the missing suffix is safe and avoids rebuilding after a restart.
            for start in range(len(items), len(chunks), 350):
                missing = chunks[start : start + 350]
                added = builder.client.batch.add(
                    batch_id=batch_id,
                    items=[
                        BatchAddItem(
                            type="graph_episode",
                            graph_id=graph_id,
                            data=chunk,
                            data_type="text",
                            source_description=(
                                "MiroFish source document chunk"
                            ),
                            metadata={
                                "mirofish_operation_id": operation_id,
                                "chunk_index": start + offset,
                                "chunk_sha256": hashlib.sha256(
                                    chunk.encode("utf-8")
                                ).hexdigest(),
                            },
                        )
                        for offset, chunk in enumerate(missing)
                    ],
                )
                if len(added or []) != len(missing):
                    raise RuntimeError(
                        "La recuperación del lote GraphRAG no confirmó "
                        "todos los fragmentos pendientes"
                    )
            builder.client.batch.process(batch_id=batch_id)
            status = "queued"

        if status not in {"queued", "processing", "succeeded"}:
            raise RuntimeError(
                f"El lote GraphRAG persistido terminó en estado {status}"
            )
        return BatchSubmission(
            batch_id=batch_id,
            operation_id=operation_id,
            episode_uuids=[],
            item_count=len(chunks),
        )

    @staticmethod
    def _ensure_graph(
        builder: GraphBuilderService,
        project: Any,
        graph_name: str,
    ) -> str:
        if project.graph_id:
            try:
                builder.client.graph.get(project.graph_id)
                return project.graph_id
            except NotFoundError:
                # The ID was journaled before create; reusing it is safe when a
                # read confirms that the graph does not exist.
                return builder.create_graph(
                    graph_name,
                    graph_id=project.graph_id,
                    graph_id_callback=lambda graph_id: GraphBuildExecutor._remember_graph(
                        project,
                        graph_id,
                    ),
                )

        return builder.create_graph(
            graph_name,
            graph_id_callback=lambda graph_id: GraphBuildExecutor._remember_graph(
                project,
                graph_id,
            ),
        )

    @staticmethod
    def _remember_graph(project: Any, graph_id: str) -> None:
        project.graph_id = graph_id
        ProjectManager.save_project(project)

    @staticmethod
    def _remember_batch(
        project: Any,
        batch_id: Optional[str],
        operation_id: str,
    ) -> None:
        project.zep_batch_id = batch_id
        project.zep_batch_operation_id = operation_id
        ProjectManager.save_project(project)

    def execute(self, job: Dict[str, Any]) -> Dict[str, Any]:
        payload = job.get("payload") or {}
        task_id = str(payload["task_id"])
        project_id = str(payload["project_id"])
        graph_name = str(payload["graph_name"])
        chunk_size = int(payload["chunk_size"])
        chunk_overlap = int(payload["chunk_overlap"])
        set_locale(str(payload.get("locale") or "es"))

        project = ProjectManager.get_project(project_id)
        if project is None:
            raise StaleGraphBuildJob(f"El proyecto {project_id} ya no existe")
        if project.graph_build_task_id != task_id:
            raise StaleGraphBuildJob(
                f"El proyecto {project_id} pertenece a una tarea más reciente"
            )

        task = self.task_manager.get_task(task_id)
        if task is None:
            raise StaleGraphBuildJob(f"No existe la tarea {task_id}")

        if project.status == ProjectStatus.GRAPH_COMPLETED and project.graph_id:
            result = task.result or {
                "project_id": project_id,
                "graph_id": project.graph_id,
            }
            if task.status != TaskStatus.COMPLETED:
                self.task_manager.complete_task(task_id, result)
            return result
        if project.status != ProjectStatus.GRAPH_BUILDING:
            raise StaleGraphBuildJob(
                f"El proyecto {project_id} no está esperando una construcción"
            )

        self.task_manager.update_task(
            task_id,
            status=TaskStatus.PROCESSING,
            progress=1,
            message=t("progress.initGraphService"),
        )
        text = ProjectManager.get_extracted_text(project_id)
        if not text:
            raise RuntimeError("No se encontró el texto extraído del proyecto")
        if not project.ontology:
            raise RuntimeError("No se encontró la ontología del proyecto")

        builder = GraphBuilderService()
        self.task_manager.update_task(
            task_id,
            progress=5,
            message=t("progress.textChunking"),
        )
        chunks = TextProcessor.split_text(
            text,
            chunk_size=chunk_size,
            overlap=chunk_overlap,
        )
        builder.validate_batch_chunks(chunks, batch_size=350)
        total_chunks = len(chunks)

        graph_id: Optional[str] = project.graph_id
        submission: Optional[BatchSubmission] = None
        if graph_id:
            submission = self._submission_from_existing_batch(
                builder,
                project,
                graph_id=graph_id,
                chunks=chunks,
            )

        if submission is None:
            self.task_manager.update_task(
                task_id,
                progress=10,
                message=t("progress.creatingZepGraph"),
            )
            graph_id = self._ensure_graph(builder, project, graph_name)
            self.task_manager.update_task(
                task_id,
                progress=15,
                message=t("progress.settingOntology"),
            )
            builder.set_ontology(graph_id, project.ontology)

            def add_progress(message: str, ratio: float) -> None:
                self.task_manager.update_task(
                    task_id,
                    message=message,
                    progress=15 + int(ratio * 40),
                )

            self.task_manager.update_task(
                task_id,
                progress=15,
                message=t("progress.addingChunks", count=total_chunks),
            )
            submission = builder.add_text_batches(
                graph_id,
                chunks,
                batch_size=350,
                progress_callback=add_progress,
                batch_created_callback=lambda batch_id, operation_id: self._remember_batch(
                    project,
                    batch_id,
                    operation_id,
                ),
            )
        else:
            self.task_manager.update_task(
                task_id,
                progress=55,
                message=t("progress.waitingZepProcess"),
            )

        def wait_progress(message: str, ratio: float) -> None:
            self.task_manager.update_task(
                task_id,
                message=message,
                progress=55 + int(ratio * 35),
            )

        builder._wait_for_batch(submission, wait_progress)
        self.task_manager.update_task(
            task_id,
            progress=95,
            message=t("progress.fetchingGraphData"),
        )
        graph_data = builder.get_graph_data(graph_id)
        result = {
            "project_id": project_id,
            "graph_id": graph_id,
            "node_count": graph_data.get("node_count", 0),
            "edge_count": graph_data.get("edge_count", 0),
            "chunk_count": total_chunks,
            "zep_batch_id": submission.batch_id,
        }

        # Project first, task second: reset/delete continues to see an active
        # task until every project reference is durably published.
        project.status = ProjectStatus.GRAPH_COMPLETED
        project.error = None
        ProjectManager.save_project(project)
        self.task_manager.update_task(
            task_id,
            status=TaskStatus.COMPLETED,
            message=t("progress.graphBuildComplete"),
            progress=100,
            result=result,
        )
        logger.info(
            "Grafo construido: project_id=%s graph_id=%s nodos=%s aristas=%s",
            project_id,
            graph_id,
            result["node_count"],
            result["edge_count"],
        )
        return result

    def fail(self, job: Dict[str, Any], error: BaseException) -> None:
        payload = job.get("payload") or {}
        task_id = str(payload.get("task_id") or "")
        project_id = str(payload.get("project_id") or "")
        detail = "".join(
            traceback.format_exception(type(error), error, error.__traceback__)
        )
        if not isinstance(error, StaleGraphBuildJob) and project_id:
            project = ProjectManager.get_project(project_id)
            owns_project = bool(
                project is not None
                and project.graph_build_task_id == task_id
            )
            if owns_project:
                # Keep the task active until the terminal project state is
                # durable, mirroring the successful completion ordering.
                project.status = ProjectStatus.FAILED
                project.error = str(error)
                ProjectManager.save_project(project)
        if task_id:
            self.task_manager.update_task(
                task_id,
                status=TaskStatus.FAILED,
                message=t("progress.buildFailed", error=str(error)),
                error=detail,
            )


class GraphRAGWorker:
    def __init__(self) -> None:
        self.stop_event = threading.Event()
        self.worker_id = f"{socket.gethostname()}-{os.getpid()}"
        self.model_config = ModelConfigReloader()
        self.builds = GraphBuildExecutor(TaskManager())
        try:
            self.poll_interval = max(
                0.1,
                float(os.environ.get("GRAPHRAG_POLL_INTERVAL", "1")),
            )
        except ValueError:
            self.poll_interval = 1.0
        try:
            self.stall_timeout = max(
                300.0,
                float(os.environ.get("GRAPHRAG_STALL_TIMEOUT", "1200")),
            )
        except ValueError:
            self.stall_timeout = 1200.0
        self.last_progress = time.monotonic()

    def _is_stalled(self, now: Optional[float] = None) -> bool:
        """True cuando el bucle principal lleva demasiado sin avanzar."""

        if now is None:
            now = time.monotonic()
        return (now - self.last_progress) > self.stall_timeout

    def _watchdog_loop(self) -> None:
        """Reinicia el proceso si el bucle principal queda estancado.

        Docker vuelve a levantar el contenedor (restart: unless-stopped) y la
        cola durable recupera builds y sesiones al arrancar.
        """

        while not self.stop_event.is_set():
            self.stop_event.wait(60)
            if self.stop_event.is_set():
                break
            try:
                if self._is_stalled():
                    stalled_minutes = (
                        time.monotonic() - self.last_progress
                    ) / 60
                    logger.error(
                        "Bucle GraphRAG estancado %.0f minutos: el worker se "
                        "reinicia para recuperarse",
                        stalled_minutes,
                    )
                    os._exit(1)
            except Exception:
                logger.exception("Error del watchdog GraphRAG")

    def _heartbeat_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                GraphRAGQueue.write_heartbeat(self.worker_id)
            except Exception:
                logger.exception("No se pudo escribir el heartbeat GraphRAG")
            self.stop_event.wait(5)

    def _process_activity_batch(self, batch: Dict[str, Any]) -> None:
        session_id = batch["session_id"]
        batch_id = batch["batch_id"]
        metadata = batch["metadata"]
        platform = batch["platform"]
        set_locale(str(metadata.get("locale") or "es"))
        activities = []
        for event in batch["events"]:
            data = event.get("activity") or {}
            activities.append(
                AgentActivity(
                    platform=platform,
                    agent_id=data.get("agent_id", 0),
                    agent_name=data.get("agent_name", ""),
                    action_type=data.get("action_type", ""),
                    action_args=data.get("action_args") or {},
                    round_num=data.get("round", 0),
                    timestamp=data.get("timestamp") or time.strftime(
                        "%Y-%m-%dT%H:%M:%S%z"
                    ),
                )
            )

        # Client construction may fail due to configuration and has no remote
        # side effect. Journal "sending" only immediately before graph.add.
        updater = ZepGraphMemoryUpdater(
            str(metadata["graph_id"]),
            simulation_id=str(metadata["simulation_id"]),
        )
        GraphRAGQueue.mark_batch_sending(session_id, batch_id)
        result = updater.ingest_batch(activities, platform)
        GraphRAGQueue.complete_activity_batch(
            session_id,
            batch_id,
            result=result,
        )
        logger.info(
            "Lote de simulación procesado: session_id=%s actividades=%d",
            session_id,
            len(activities),
        )

    def _process_build(self, job: Dict[str, Any]) -> None:
        job_id = str(job["job_id"])
        try:
            result = self.builds.execute(job)
        except BaseException as error:
            self.builds.fail(job, error)
            GraphRAGQueue.finish_build(
                job_id,
                success=False,
                error=str(error),
            )
            logger.exception("Falló el trabajo GraphRAG %s", job_id)
        else:
            GraphRAGQueue.finish_build(
                job_id,
                success=True,
                result=result,
            )

    def run(self) -> None:
        self.model_config.refresh(force=True)
        recovered_builds = GraphRAGQueue.recover_builds()
        recovered_sessions = GraphRAGQueue.recover_sessions()
        logger.info(
            "Worker GraphRAG iniciado: id=%s builds_recuperados=%d "
            "eventos_recuperados=%d ambiguos=%d",
            self.worker_id,
            recovered_builds,
            recovered_sessions["recovered_events"],
            recovered_sessions["ambiguous_events"],
        )
        heartbeat = threading.Thread(
            target=self._heartbeat_loop,
            daemon=True,
            name="GraphRAGHeartbeat",
        )
        heartbeat.start()

        watchdog = threading.Thread(
            target=self._watchdog_loop,
            daemon=True,
            name="GraphRAGWatchdog",
        )
        watchdog.start()

        while not self.stop_event.is_set():
            did_work = False
            self.last_progress = time.monotonic()
            try:
                self.model_config.refresh()
                batch = GraphRAGQueue.claim_activity_batch(
                    batch_size=ZepGraphMemoryUpdater.BATCH_SIZE,
                )
                if batch is not None:
                    did_work = True
                    try:
                        self._process_activity_batch(batch)
                    except BaseException as error:
                        GraphRAGQueue.fail_activity_batch(
                            batch["session_id"],
                            batch["batch_id"],
                            error=str(error),
                        )
                        logger.exception(
                            "Falló una actualización GraphRAG de simulación"
                        )
                    GraphRAGQueue.finalize_closed_sessions()
                    continue

                if GraphRAGQueue.finalize_closed_sessions():
                    did_work = True

                job = GraphRAGQueue.claim_build()
                if job is not None:
                    did_work = True
                    self._process_build(job)
            except BaseException:
                # A malformed queue item must not terminate the container and
                # leave every unrelated project unprocessed.
                logger.exception("Error en el bucle principal GraphRAG")

            if not did_work:
                self.stop_event.wait(self.poll_interval)

        try:
            GraphRAGQueue.write_heartbeat(self.worker_id)
        except Exception:
            pass
        logger.info("Worker GraphRAG detenido")


def _healthcheck(max_age: float) -> int:
    try:
        age = GraphRAGQueue.heartbeat_age_seconds()
    except GraphRAGQueueError:
        return 1
    return 0 if age is not None and age <= max_age else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="BeeMarketBot GraphRAG worker")
    parser.add_argument("--healthcheck", action="store_true")
    parser.add_argument("--max-heartbeat-age", type=float, default=30.0)
    args = parser.parse_args()
    if args.healthcheck:
        return _healthcheck(args.max_heartbeat_age)

    worker = GraphRAGWorker()

    def stop(_signum: int, _frame: Any) -> None:
        worker.stop_event.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    worker.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
