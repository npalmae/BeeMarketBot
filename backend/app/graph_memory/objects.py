"""Forma de los datos que devuelve el cliente de memoria en grafos.

Son objetos simples de atributos. Reproducen los campos que el resto del backend
leía antes de Zep Cloud, incluido ``uuid_`` junto a ``uuid``, para que los
servicios y las pruebas existentes sigan funcionando sin cambios.
"""

from __future__ import annotations

from typing import Any


class Bag:
    """Contenedor de atributos con una representación compacta."""

    def __init__(self, **attributes: Any) -> None:
        self.__dict__.update(attributes)

    def __repr__(self) -> str:
        shown = ", ".join(
            f"{key}={value!r}"
            for key, value in self.__dict__.items()
            if not key.startswith("_")
        )
        return f"{type(self).__name__}({shown})"


def node(
    uuid: str,
    name: str,
    labels: list[str] | None,
    summary: str,
    attributes: dict[str, Any] | None,
    created_at: Any = None,
    graph_id: str | None = None,
) -> Bag:
    return Bag(
        uuid_=uuid,
        uuid=uuid,
        name=name or "",
        labels=list(labels or []),
        summary=summary or "",
        attributes=dict(attributes or {}),
        created_at=created_at,
        graph_id=graph_id,
    )


def edge(
    uuid: str,
    name: str,
    fact: str,
    source_node_uuid: str,
    target_node_uuid: str,
    attributes: dict[str, Any] | None = None,
    created_at: Any = None,
    valid_at: Any = None,
    invalid_at: Any = None,
    expired_at: Any = None,
    episodes: list[str] | None = None,
    graph_id: str | None = None,
) -> Bag:
    return Bag(
        uuid_=uuid,
        uuid=uuid,
        name=name or "",
        fact=fact or "",
        fact_type=name or "",
        source_node_uuid=source_node_uuid or "",
        target_node_uuid=target_node_uuid or "",
        attributes=dict(attributes or {}),
        created_at=created_at,
        valid_at=valid_at,
        invalid_at=invalid_at,
        expired_at=expired_at,
        episodes=[str(item) for item in (episodes or [])],
        graph_id=graph_id,
    )


def episode(
    uuid: str,
    name: str,
    created_at: Any = None,
    *,
    processed: bool = True,
    content: str | None = None,
    source_description: str | None = None,
    metadata: dict[str, Any] | None = None,
    failed: bool = False,
    error: str | None = None,
) -> Bag:
    return Bag(
        uuid_=uuid,
        uuid=uuid,
        name=name or "",
        created_at=created_at,
        processed=processed,
        content=content,
        source_description=source_description,
        metadata=dict(metadata or {}),
        failed=failed,
        error=error,
    )


def graph(graph_id: str, name: str, description: str, created_at: Any = None) -> Bag:
    return Bag(
        graph_id=graph_id,
        name=name or "",
        description=description or "",
        created_at=created_at,
    )


def search_result(
    edges: list[Any] | None,
    nodes: list[Any] | None,
    query: str | None = None,
) -> Bag:
    return Bag(edges=list(edges or []), nodes=list(nodes or []), query=query)


def raw_response(data: list[Any], headers: dict[str, str] | None = None) -> Bag:
    """Respuesta "sin procesar": los datos y las cabeceras de paginación."""

    return Bag(data=list(data), headers=dict(headers or {}))


def progress(
    *,
    total_items: int,
    succeeded_items: int = 0,
    failed_items: int = 0,
) -> Bag:
    done = succeeded_items + failed_items
    percent = (done / total_items * 100.0) if total_items else 0.0
    return Bag(
        percent_complete=percent,
        succeeded_items=succeeded_items,
        failed_items=failed_items,
        total_items=total_items,
        pending_items=max(total_items - done, 0),
    )


def batch(
    batch_id: str,
    status: str,
    metadata: dict[str, Any] | None,
    *,
    total_items: int,
    succeeded_items: int = 0,
    failed_items: int = 0,
    created_at: Any = None,
) -> Bag:
    return Bag(
        batch_id=batch_id,
        status=status,
        metadata=dict(metadata or {}),
        created_at=created_at,
        progress=progress(
            total_items=total_items,
            succeeded_items=succeeded_items,
            failed_items=failed_items,
        ),
    )


def batch_item(
    sequence_index: int,
    *,
    status: str = "pending",
    episode_uuid: str | None = None,
    source_uuid: str | None = None,
    error: str | None = None,
    data: Any = None,
) -> Bag:
    return Bag(
        sequence_index=sequence_index,
        status=status,
        episode_uuid=episode_uuid,
        source_uuid=source_uuid or episode_uuid,
        error=error,
        data=data,
    )


def page(items: list[Any], next_cursor: str | None, *, items_field: str = "items") -> Bag:
    return Bag(**{items_field: list(items), "next_cursor": next_cursor})
