"""Registro de lotes de ingesta, guardado en disco.

Un lote reúne los fragmentos de un documento que se envían a un grafo. Su estado
se guarda como JSON para que la aplicación pueda retomar la ingesta después de
un reinicio, igual que hacía con los lotes de Zep Cloud.
"""

from __future__ import annotations

import json
import os
import re
import threading
import uuid as uuid_lib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .errors import BadRequestError, NotFoundError

# Estados que la aplicación considera definitivos.
TERMINAL_STATUSES = frozenset({"succeeded", "partial", "failed", "invalid", "canceled"})
# Un lote en estos estados se estaba ingiriendo: si el proceso murió así, se reanuda.
IN_PROGRESS_STATUSES = frozenset({"queued", "processing"})
ITEM_DONE_STATUSES = frozenset({"succeeded", "skipped"})
DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 100

_VALID_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@dataclass
class ItemRecord:
    """Un fragmento de texto del lote."""

    sequence_index: int
    graph_id: str
    data: str
    source_description: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    status: str = "pending"
    episode_uuid: str | None = None
    error: str | None = None


@dataclass
class BatchRecord:
    """Un lote de ingesta."""

    batch_id: str
    status: str = "draft"
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    items: list[ItemRecord] = field(default_factory=list)

    @property
    def counts(self) -> tuple[int, int]:
        succeeded = sum(1 for item in self.items if item.status in ITEM_DONE_STATUSES)
        failed = sum(1 for item in self.items if item.status == "failed")
        return succeeded, failed


def paginate(items: list[Any], limit: Any, cursor: Any) -> tuple[list[Any], str | None]:
    """Recorta una lista ordenada según límite y cursor (un desfase como texto)."""

    try:
        size = int(limit) if limit is not None else DEFAULT_PAGE_SIZE
    except (TypeError, ValueError) as error:
        raise BadRequestError("El límite de paginación debe ser un entero") from error
    size = max(1, min(size, MAX_PAGE_SIZE))
    if cursor in (None, ""):
        offset = 0
    else:
        try:
            offset = int(str(cursor))
        except (TypeError, ValueError) as error:
            raise BadRequestError("El cursor de paginación no es válido") from error
        if offset < 0 or (items and offset >= len(items)):
            raise BadRequestError("El cursor de paginación está fuera de rango")
    end = min(offset + size, len(items))
    return items[offset:end], (str(end) if end < len(items) else None)


class BatchRegistry:
    """Guarda y actualiza lotes; seguro para usar desde varios hilos."""

    def __init__(self, directory: str | os.PathLike[str]) -> None:
        self._dir = Path(directory)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._cache: dict[str, BatchRecord] = {}

    def _path(self, batch_id: str) -> Path:
        if not _VALID_ID.match(str(batch_id or "")):
            raise BadRequestError("Identificador de lote no válido")
        return self._dir / f"{batch_id}.json"

    def _load(self, batch_id: str) -> BatchRecord | None:
        if batch_id in self._cache:
            return self._cache[batch_id]
        path = self._path(batch_id)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            items = [ItemRecord(**item) for item in payload.pop("items", [])]
            record = BatchRecord(items=items, **payload)
        except (OSError, TypeError, ValueError) as error:
            raise NotFoundError(f"Lote ilegible: {batch_id} ({error})", status_code=404) from error
        self._cache[batch_id] = record
        return record

    def _save(self, record: BatchRecord) -> None:
        path = self._path(record.batch_id)
        temporary = path.with_suffix(".json.tmp")
        payload = asdict(record)
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        # Reemplazo atómico: un corte de energía no deja un lote a medias.
        os.replace(temporary, path)
        self._cache[record.batch_id] = record

    def create(self, metadata: dict[str, Any] | None = None) -> BatchRecord:
        from datetime import datetime, timezone

        record = BatchRecord(
            batch_id=uuid_lib.uuid4().hex,
            status="draft",
            metadata=dict(metadata or {}),
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        with self._lock:
            self._save(record)
        return record

    def get(self, batch_id: str) -> BatchRecord | None:
        with self._lock:
            return self._load(batch_id)

    def require(self, batch_id: str) -> BatchRecord:
        record = self.get(batch_id)
        if record is None:
            raise NotFoundError(f"Lote no encontrado: {batch_id}", status_code=404)
        return record

    def add_items(self, batch_id: str, items: list[ItemRecord]) -> list[ItemRecord]:
        with self._lock:
            record = self.require(batch_id)
            if record.status != "draft":
                raise BadRequestError(
                    f"No se pueden agregar elementos a un lote en estado {record.status}",
                    status_code=409,
                )
            for item in items:
                item.sequence_index = len(record.items)
                record.items.append(item)
            self._save(record)
            return list(items)

    def set_status(self, batch_id: str, status: str) -> BatchRecord:
        with self._lock:
            record = self.require(batch_id)
            record.status = status
            self._save(record)
            return record

    def update_items(self, batch_id: str, updates: dict[int, dict[str, Any]]) -> BatchRecord:
        with self._lock:
            record = self.require(batch_id)
            for sequence_index, changes in updates.items():
                if 0 <= sequence_index < len(record.items):
                    for key, value in changes.items():
                        setattr(record.items[sequence_index], key, value)
            self._save(record)
            return record

    def list(self, limit: int | None = None, cursor: Any = None) -> tuple[list[BatchRecord], str | None]:
        with self._lock:
            records = [record for record in (self._load(path.stem) for path in self._dir.glob("*.json")) if record]
            # Del más reciente al más antiguo.
            records.sort(key=lambda record: (record.created_at, record.batch_id), reverse=True)
            page, next_cursor = paginate(records, limit, cursor)
            return list(page), next_cursor

    def list_items(self, batch_id: str, limit: int | None = None, cursor: Any = None) -> tuple[list[ItemRecord], str | None]:
        with self._lock:
            record = self.require(batch_id)
            items = sorted(record.items, key=lambda item: item.sequence_index)
            return paginate(items, limit, cursor)

    def interrupted(self) -> list[BatchRecord]:
        """Lotes cuya ingesta quedó a medias (p. ej. tras un reinicio).

        Un borrador no cuenta: nunca se envió a ingestar, y quien lo creó necesita
        encontrarlo intacto para seguirle agregando elementos.
        """

        with self._lock:
            records = [record for record in (self._load(path.stem) for path in self._dir.glob("*.json")) if record]
            return [record for record in records if record.status in IN_PROGRESS_STATUSES]

    def reset_for_resume(self, batch_id: str) -> BatchRecord:
        """Deja el lote listo para reanudar: solo se reprocesan los elementos pendientes.

        Cada elemento conserva su uuid de episodio, así que repetir su ingesta
        actualiza el mismo episodio en vez de duplicarlo.
        """

        with self._lock:
            record = self.require(batch_id)
            for item in record.items:
                if item.status not in ITEM_DONE_STATUSES:
                    item.status = "pending"
                    item.error = None
            record.status = "queued"
            self._save(record)
            return record
