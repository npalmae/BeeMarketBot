"""Durable filesystem queue shared by the API and the GraphRAG worker.

The queue lives below ``uploads`` so both Docker containers see the same data.
Files are written atomically and jobs are claimed with ``os.replace``.  This is
intentionally small: BeeMarketBot runs one GraphRAG worker by default and does
not need a separate Redis/RabbitMQ service merely to hand work to it.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from ..config import Config


TERMINAL_SESSION_STATES = {"completed", "failed", "cancelled"}


class GraphRAGQueueError(RuntimeError):
    """Base error for durable queue operations."""


class GraphRAGSessionClosed(GraphRAGQueueError):
    """Raised when an activity arrives after its producer closed the session."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_identifier(value: str, label: str) -> str:
    value = str(value or "")
    if not value or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for char in value):
        raise GraphRAGQueueError(f"{label} inválido")
    return value


def _read_json(path: str) -> Dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as error:
        raise GraphRAGQueueError(f"No se pudo leer {path}: {error}") from error
    if not isinstance(data, dict):
        raise GraphRAGQueueError(f"El archivo {path} no contiene un objeto JSON")
    return data


def _write_json_atomic(path: str, data: Dict[str, Any]) -> None:
    """Replace ``path`` only after a complete, fsynced JSON file exists."""

    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        dir=directory,
        prefix=f".{os.path.basename(path)}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _write_json_once(path: str, data: Dict[str, Any]) -> bool:
    """Create a complete file without replacing an existing queue item."""

    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        dir=directory,
        prefix=f".{os.path.basename(path)}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        try:
            # A hard link publishes the fully written inode and fails
            # atomically if another producer already published this event.
            os.link(temporary, path)
        except FileExistsError:
            return False
        return True
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class GraphRAGQueue:
    """Filesystem-backed build queue and simulation activity outbox."""

    BUILD_STATES = ("pending", "processing", "completed", "failed")
    EVENT_STATES = ("pending", "processing", "completed", "failed")

    @classmethod
    def root(cls) -> str:
        return os.environ.get("GRAPHRAG_QUEUE_DIR") or os.path.join(
            Config.UPLOAD_FOLDER,
            "graphrag_queue",
        )

    @classmethod
    def _build_dir(cls, state: str) -> str:
        if state not in cls.BUILD_STATES:
            raise GraphRAGQueueError(f"Estado de trabajo desconocido: {state}")
        return os.path.join(cls.root(), "build", state)

    @classmethod
    def _build_path(cls, state: str, job_id: str) -> str:
        return os.path.join(
            cls._build_dir(state),
            f"{_safe_identifier(job_id, 'job_id')}.json",
        )

    @classmethod
    def _sessions_dir(cls) -> str:
        return os.path.join(cls.root(), "sessions")

    @classmethod
    def _session_dir(cls, session_id: str) -> str:
        return os.path.join(
            cls._sessions_dir(),
            _safe_identifier(session_id, "session_id"),
        )

    @classmethod
    def _session_path(cls, session_id: str, filename: str) -> str:
        return os.path.join(cls._session_dir(session_id), filename)

    @classmethod
    def _event_dir(cls, session_id: str, state: str) -> str:
        if state not in cls.EVENT_STATES:
            raise GraphRAGQueueError(f"Estado de evento desconocido: {state}")
        return os.path.join(cls._session_dir(session_id), "events", state)

    @classmethod
    def _batch_dir(cls, session_id: str) -> str:
        return os.path.join(cls._session_dir(session_id), "batches")

    @classmethod
    def ensure(cls) -> None:
        for state in cls.BUILD_STATES:
            os.makedirs(cls._build_dir(state), exist_ok=True)
        os.makedirs(cls._sessions_dir(), exist_ok=True)

    # ------------------------------------------------------------------ builds
    @classmethod
    def enqueue_build(
        cls,
        *,
        task_id: str,
        project_id: str,
        graph_name: str,
        chunk_size: int,
        chunk_overlap: int,
        locale: str,
    ) -> str:
        """Persist one graph-build request. ``task_id`` is its idempotency key."""

        cls.ensure()
        job_id = _safe_identifier(task_id, "task_id")
        job = {
            "schema_version": 1,
            "job_id": job_id,
            "job_type": "graph_build",
            "status": "pending",
            "created_at": _now(),
            "updated_at": _now(),
            "attempts": 0,
            "payload": {
                "task_id": task_id,
                "project_id": project_id,
                "graph_name": graph_name,
                "chunk_size": chunk_size,
                "chunk_overlap": chunk_overlap,
                "locale": locale,
            },
        }
        path = cls._build_path("pending", job_id)
        if not _write_json_once(path, job):
            existing = _read_json(path)
            if existing.get("payload") != job["payload"]:
                raise GraphRAGQueueError(
                    f"Ya existe un trabajo distinto con id {job_id}"
                )
        return job_id

    @classmethod
    def claim_build(cls) -> Optional[Dict[str, Any]]:
        """Atomically claim the oldest pending build job."""

        cls.ensure()
        for filename in sorted(os.listdir(cls._build_dir("pending"))):
            if not filename.endswith(".json"):
                continue
            source = os.path.join(cls._build_dir("pending"), filename)
            target = os.path.join(cls._build_dir("processing"), filename)
            try:
                os.replace(source, target)
            except FileNotFoundError:
                continue
            try:
                job = _read_json(target)
            except GraphRAGQueueError as error:
                malformed_id = filename[:-5]
                cls.finish_build(
                    malformed_id,
                    success=False,
                    error=str(error),
                )
                continue
            job["status"] = "processing"
            job["attempts"] = int(job.get("attempts", 0)) + 1
            job["started_at"] = _now()
            job["updated_at"] = _now()
            _write_json_atomic(target, job)
            return job
        return None

    @classmethod
    def finish_build(
        cls,
        job_id: str,
        *,
        success: bool,
        result: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> None:
        source = cls._build_path("processing", job_id)
        if os.path.exists(source):
            try:
                job = _read_json(source)
            except GraphRAGQueueError:
                job = {
                    "schema_version": 1,
                    "job_id": job_id,
                    "job_type": "graph_build",
                    "created_at": _now(),
                    "attempts": 1,
                    "payload": {},
                }
        else:
            # A malformed claimed file may not be readable. Preserve enough
            # information for diagnostics instead of losing the queue record.
            job = {
                "schema_version": 1,
                "job_id": job_id,
                "job_type": "graph_build",
                "created_at": _now(),
                "attempts": 1,
                "payload": {},
            }
        final_state = "completed" if success else "failed"
        job.update(
            {
                "status": final_state,
                "updated_at": _now(),
                "finished_at": _now(),
                "result": result if success else None,
                "error": None if success else (error or "Error desconocido"),
            }
        )
        _write_json_atomic(cls._build_path(final_state, job_id), job)
        try:
            os.remove(source)
        except FileNotFoundError:
            pass

    @classmethod
    def recover_builds(cls) -> int:
        """Return jobs left in ``processing`` by a stopped worker to pending."""

        cls.ensure()
        recovered = 0
        for filename in sorted(os.listdir(cls._build_dir("processing"))):
            if not filename.endswith(".json"):
                continue
            source = os.path.join(cls._build_dir("processing"), filename)
            target = os.path.join(cls._build_dir("pending"), filename)
            try:
                job = _read_json(source)
                job["status"] = "pending"
                job["updated_at"] = _now()
                job["recovered_at"] = _now()
                _write_json_atomic(target, job)
                os.remove(source)
                recovered += 1
            except FileNotFoundError:
                continue
            except GraphRAGQueueError as error:
                cls.finish_build(
                    filename[:-5],
                    success=False,
                    error=str(error),
                )
        return recovered

    # ------------------------------------------------------ simulation sessions
    @classmethod
    def create_session(
        cls,
        *,
        simulation_id: str,
        graph_id: str,
        locale: str,
    ) -> str:
        cls.ensure()
        session_id = f"grs_{uuid.uuid4().hex}"
        directory = cls._session_dir(session_id)
        os.makedirs(directory, exist_ok=False)
        for state in cls.EVENT_STATES:
            os.makedirs(cls._event_dir(session_id, state), exist_ok=True)
        os.makedirs(cls._batch_dir(session_id), exist_ok=True)
        _write_json_atomic(
            cls._session_path(session_id, "metadata.json"),
            {
                "schema_version": 1,
                "session_id": session_id,
                "simulation_id": simulation_id,
                "graph_id": graph_id,
                "locale": locale,
                "created_at": _now(),
            },
        )
        _write_json_atomic(
            cls._session_path(session_id, "state.json"),
            {
                "status": "queued",
                "created_at": _now(),
                "updated_at": _now(),
                "error": None,
            },
        )
        return session_id

    @classmethod
    def session_metadata(cls, session_id: str) -> Dict[str, Any]:
        return _read_json(cls._session_path(session_id, "metadata.json"))

    @classmethod
    def session_state(cls, session_id: str) -> Dict[str, Any]:
        path = cls._session_path(session_id, "state.json")
        if not os.path.exists(path):
            return {"status": "queued", "updated_at": None, "error": None}
        return _read_json(path)

    @classmethod
    def _save_session_state(
        cls,
        session_id: str,
        *,
        status: str,
        error: Optional[str] = None,
        **extra: Any,
    ) -> None:
        state = cls.session_state(session_id)
        state.update(extra)
        state.update(
            {
                "status": status,
                "updated_at": _now(),
                "error": error,
            }
        )
        _write_json_atomic(cls._session_path(session_id, "state.json"), state)

    @classmethod
    def enqueue_activity(
        cls,
        *,
        session_id: str,
        platform: str,
        data: Dict[str, Any],
        source_event_id: str,
    ) -> bool:
        """Append one deduplicated action to a simulation outbox.

        Returns ``False`` for filtered or already-persisted actions.
        """

        if "event_type" in data:
            return False
        if data.get("success") is False or data.get("action_type") == "DO_NOTHING":
            return False
        if os.path.exists(cls._session_path(session_id, "closed.json")):
            raise GraphRAGSessionClosed(
                f"La sesión GraphRAG {session_id} ya está cerrada"
            )
        state = cls.session_state(session_id)
        if state.get("status") in TERMINAL_SESSION_STATES:
            raise GraphRAGSessionClosed(
                f"La sesión GraphRAG {session_id} está en estado {state.get('status')}"
            )

        event_hash = hashlib.sha256(
            f"{session_id}\0{source_event_id}".encode("utf-8")
        ).hexdigest()
        try:
            source_order = max(0, int(str(source_event_id).rsplit(":", 1)[-1]))
        except (TypeError, ValueError):
            source_order = 0
        # The digest remains the deduplication key; the numeric prefix preserves
        # action-log order within each platform for temporal extraction.
        filename = f"{source_order:020d}_{event_hash}.json"
        for event_state in cls.EVENT_STATES:
            if os.path.exists(os.path.join(cls._event_dir(session_id, event_state), filename)):
                return False

        payload = {
            "schema_version": 1,
            "event_id": event_hash,
            "source_event_id": source_event_id,
            "session_id": session_id,
            "platform": str(platform).lower(),
            "created_at": _now(),
            "activity": data,
        }
        return _write_json_once(
            os.path.join(cls._event_dir(session_id, "pending"), filename),
            payload,
        )

    @classmethod
    def close_session(cls, session_id: str) -> None:
        """Publish the producer barrier after the final action-log tail."""

        cls.session_metadata(session_id)  # Validate that it exists first.
        _write_json_once(
            cls._session_path(session_id, "closed.json"),
            {"closed_at": _now()},
        )

    @classmethod
    def list_session_ids(cls) -> list[str]:
        directory = cls._sessions_dir()
        if not os.path.isdir(directory):
            return []
        return sorted(
            name
            for name in os.listdir(directory)
            if os.path.isdir(os.path.join(directory, name))
            and name.startswith("grs_")
        )

    @classmethod
    def session_snapshot(cls, session_id: str) -> Dict[str, Any]:
        metadata = cls.session_metadata(session_id)
        state = cls.session_state(session_id)
        counts = {}
        for event_state in cls.EVENT_STATES:
            directory = cls._event_dir(session_id, event_state)
            counts[event_state] = (
                len([name for name in os.listdir(directory) if name.endswith(".json")])
                if os.path.isdir(directory)
                else 0
            )
        batch_dir = cls._batch_dir(session_id)
        return {
            **metadata,
            **state,
            "closed": os.path.exists(cls._session_path(session_id, "closed.json")),
            "event_counts": counts,
            "batch_count": (
                len([name for name in os.listdir(batch_dir) if name.endswith(".json")])
                if os.path.isdir(batch_dir)
                else 0
            ),
        }

    @classmethod
    def active_sessions(
        cls,
        *,
        graph_id: Optional[str] = None,
        simulation_id: Optional[str] = None,
    ) -> list[Dict[str, Any]]:
        active = []
        for session_id in cls.list_session_ids():
            try:
                snapshot = cls.session_snapshot(session_id)
            except GraphRAGQueueError:
                continue
            if snapshot.get("status") in TERMINAL_SESSION_STATES:
                continue
            if graph_id is not None and snapshot.get("graph_id") != graph_id:
                continue
            if (
                simulation_id is not None
                and snapshot.get("simulation_id") != simulation_id
            ):
                continue
            active.append(snapshot)
        return active

    @classmethod
    def _pending_event_files(cls, session_id: str) -> list[str]:
        directory = cls._event_dir(session_id, "pending")
        if not os.path.isdir(directory):
            return []
        return sorted(
            name for name in os.listdir(directory) if name.endswith(".json")
        )

    @classmethod
    def claim_activity_batch(
        cls,
        *,
        batch_size: int,
    ) -> Optional[Dict[str, Any]]:
        """Claim one same-platform activity batch from the oldest session."""

        for session_id in cls.list_session_ids():
            state = cls.session_state(session_id)
            if state.get("status") in TERMINAL_SESSION_STATES:
                continue
            inflight_path = cls._session_path(session_id, "inflight.json")
            if os.path.exists(inflight_path):
                continue
            pending = cls._pending_event_files(session_id)
            if not pending:
                continue

            first = _read_json(
                os.path.join(cls._event_dir(session_id, "pending"), pending[0])
            )
            platform = first.get("platform") or "unknown"
            selected: list[str] = []
            payloads: list[Dict[str, Any]] = []
            for filename in pending:
                if len(selected) >= max(1, batch_size):
                    break
                source = os.path.join(cls._event_dir(session_id, "pending"), filename)
                try:
                    payload = _read_json(source)
                except GraphRAGQueueError as error:
                    cls._save_session_state(
                        session_id,
                        status="failed",
                        error=str(error),
                    )
                    return None
                if payload.get("platform") != platform:
                    continue
                target = os.path.join(
                    cls._event_dir(session_id, "processing"),
                    filename,
                )
                try:
                    os.replace(source, target)
                except FileNotFoundError:
                    continue
                selected.append(filename)
                payloads.append(payload)

            if not selected:
                continue
            batch_id = f"grb_{uuid.uuid4().hex}"
            inflight = {
                "schema_version": 1,
                "batch_id": batch_id,
                "session_id": session_id,
                "platform": platform,
                "event_files": selected,
                "event_count": len(selected),
                "status": "claimed",
                "created_at": _now(),
                "updated_at": _now(),
            }
            _write_json_atomic(inflight_path, inflight)
            cls._save_session_state(
                session_id,
                status="processing",
                current_batch_id=batch_id,
            )
            return {
                "batch_id": batch_id,
                "session_id": session_id,
                "platform": platform,
                "events": payloads,
                "metadata": cls.session_metadata(session_id),
            }
        return None

    @classmethod
    def mark_batch_sending(cls, session_id: str, batch_id: str) -> None:
        path = cls._session_path(session_id, "inflight.json")
        inflight = _read_json(path)
        if inflight.get("batch_id") != batch_id:
            raise GraphRAGQueueError("El lote activo cambió antes de enviarse")
        inflight["status"] = "sending"
        inflight["sending_at"] = _now()
        inflight["updated_at"] = _now()
        _write_json_atomic(path, inflight)

    @classmethod
    def complete_activity_batch(
        cls,
        session_id: str,
        batch_id: str,
        *,
        result: Optional[Dict[str, Any]] = None,
    ) -> None:
        path = cls._session_path(session_id, "inflight.json")
        inflight = _read_json(path)
        if inflight.get("batch_id") != batch_id:
            raise GraphRAGQueueError("El lote activo cambió antes de completarse")

        # Mark the network operation complete first. If the worker stops after
        # this point, recovery only finishes local file moves and never replays
        # the non-idempotent graph write.
        inflight.update(
            {
                "status": "completed",
                "completed_at": _now(),
                "updated_at": _now(),
                "result": result or {},
            }
        )
        _write_json_atomic(path, inflight)
        cls._finalize_completed_inflight(session_id, inflight)

    @classmethod
    def _finalize_completed_inflight(
        cls,
        session_id: str,
        inflight: Dict[str, Any],
    ) -> None:
        for filename in inflight.get("event_files") or []:
            source = os.path.join(
                cls._event_dir(session_id, "processing"),
                filename,
            )
            target = os.path.join(
                cls._event_dir(session_id, "completed"),
                filename,
            )
            try:
                os.replace(source, target)
            except FileNotFoundError:
                pass
        _write_json_atomic(
            os.path.join(
                cls._batch_dir(session_id),
                f"{inflight['batch_id']}.json",
            ),
            inflight,
        )
        try:
            os.remove(cls._session_path(session_id, "inflight.json"))
        except FileNotFoundError:
            pass
        cls._save_session_state(
            session_id,
            status="queued",
            current_batch_id=None,
        )

    @classmethod
    def fail_activity_batch(
        cls,
        session_id: str,
        batch_id: str,
        *,
        error: str,
    ) -> None:
        path = cls._session_path(session_id, "inflight.json")
        inflight = _read_json(path)
        if inflight.get("batch_id") != batch_id:
            raise GraphRAGQueueError("El lote activo cambió antes de fallar")
        inflight.update(
            {
                "status": "failed",
                "failed_at": _now(),
                "updated_at": _now(),
                "error": error,
            }
        )
        for filename in inflight.get("event_files") or []:
            source = os.path.join(
                cls._event_dir(session_id, "processing"),
                filename,
            )
            target = os.path.join(
                cls._event_dir(session_id, "failed"),
                filename,
            )
            try:
                os.replace(source, target)
            except FileNotFoundError:
                pass
        _write_json_atomic(
            os.path.join(
                cls._batch_dir(session_id),
                f"{batch_id}.json",
            ),
            inflight,
        )
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        cls._save_session_state(
            session_id,
            status="failed",
            error=error,
            current_batch_id=None,
        )

    @classmethod
    def finalize_closed_sessions(cls) -> int:
        """Complete closed sessions after all accepted events were processed."""

        completed = 0
        for session_id in cls.list_session_ids():
            state = cls.session_state(session_id)
            if state.get("status") in TERMINAL_SESSION_STATES:
                continue
            if not os.path.exists(cls._session_path(session_id, "closed.json")):
                continue
            if cls._pending_event_files(session_id):
                continue
            processing_dir = cls._event_dir(session_id, "processing")
            if any(name.endswith(".json") for name in os.listdir(processing_dir)):
                continue
            if os.path.exists(cls._session_path(session_id, "inflight.json")):
                continue
            cls._save_session_state(
                session_id,
                status="completed",
                error=None,
                completed_at=_now(),
                current_batch_id=None,
            )
            completed += 1
        return completed

    @classmethod
    def recover_sessions(cls) -> Dict[str, int]:
        """Recover local claims without replaying ambiguous graph mutations."""

        recovered = 0
        ambiguous = 0
        for session_id in cls.list_session_ids():
            inflight_path = cls._session_path(session_id, "inflight.json")
            if not os.path.exists(inflight_path):
                # A claim can be interrupted between the file moves and the
                # inflight journal. Those events are safe to return to pending.
                for filename in os.listdir(cls._event_dir(session_id, "processing")):
                    if not filename.endswith(".json"):
                        continue
                    os.replace(
                        os.path.join(
                            cls._event_dir(session_id, "processing"),
                            filename,
                        ),
                        os.path.join(
                            cls._event_dir(session_id, "pending"),
                            filename,
                        ),
                    )
                    recovered += 1
                continue

            inflight = _read_json(inflight_path)
            status = inflight.get("status")
            if status == "completed":
                cls._finalize_completed_inflight(session_id, inflight)
                recovered += int(inflight.get("event_count", 0))
            elif status == "claimed":
                for filename in inflight.get("event_files") or []:
                    source = os.path.join(
                        cls._event_dir(session_id, "processing"),
                        filename,
                    )
                    target = os.path.join(
                        cls._event_dir(session_id, "pending"),
                        filename,
                    )
                    try:
                        os.replace(source, target)
                    except FileNotFoundError:
                        pass
                os.remove(inflight_path)
                cls._save_session_state(
                    session_id,
                    status="queued",
                    error=None,
                    current_batch_id=None,
                )
                recovered += int(inflight.get("event_count", 0))
            else:
                # "sending" means the process may have stopped after FalkorDB
                # accepted the episode. Replaying could duplicate facts, so the
                # durable and honest outcome is an incomplete, failed session.
                cls.fail_activity_batch(
                    session_id,
                    str(inflight.get("batch_id")),
                    error=(
                        "El worker se reinició durante una escritura GraphRAG "
                        "no idempotente; el lote no se volvió a enviar"
                    ),
                )
                ambiguous += int(inflight.get("event_count", 0))
        return {"recovered_events": recovered, "ambiguous_events": ambiguous}

    # --------------------------------------------------------------- heartbeat
    @classmethod
    def write_heartbeat(cls, worker_id: str) -> None:
        cls.ensure()
        _write_json_atomic(
            os.path.join(cls.root(), "worker-heartbeat.json"),
            {
                "worker_id": worker_id,
                "pid": os.getpid(),
                "updated_at": _now(),
                "epoch": datetime.now(timezone.utc).timestamp(),
            },
        )

    @classmethod
    def heartbeat_age_seconds(cls) -> Optional[float]:
        path = os.path.join(cls.root(), "worker-heartbeat.json")
        if not os.path.exists(path):
            return None
        heartbeat = _read_json(path)
        try:
            return max(
                0.0,
                datetime.now(timezone.utc).timestamp() - float(heartbeat["epoch"]),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise GraphRAGQueueError("Heartbeat del worker inválido") from error
