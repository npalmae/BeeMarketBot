#!/usr/bin/env python3
"""Comprueba de punta a punta la memoria en grafos con la configuración real.

Crea un grafo temporal, le aplica una ontología pequeña, ingiere textos por la
misma vía que usa la aplicación (lotes), busca, ejercita la ruta de la simulación
(episodios en cola) y borra el grafo. Hace llamadas reales al modelo y a la base
de grafos: es una herramienta manual, fuera de las pruebas automáticas.

Uso (desde ``backend/``):
    python scripts/validate_graph_memory.py             # comprobación completa
    python scripts/validate_graph_memory.py --dry-run   # solo revisa la configuración
    python scripts/validate_graph_memory.py --keep-graph

Las claves nunca se imprimen. Código de salida: 0 si todo funciona, 1 si falla
una comprobación y 2 si la configuración está incompleta.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pydantic import Field  # noqa: E402
from typing import Optional  # noqa: E402

from app.graph_memory import (  # noqa: E402
    BatchAddItem,
    EdgeModel,
    EntityEdgeSourceTarget,
    EntityModel,
    EntityText,
    GraphMemoryError,
)
from app.graph_memory.client import GraphMemoryClient  # noqa: E402
from app.graph_memory.settings import GraphMemorySettings  # noqa: E402
from app.utils.zep_paging import fetch_all_edges, fetch_all_nodes  # noqa: E402

TERMINAL = {"succeeded", "partial", "failed", "invalid", "canceled"}


class Persona(EntityModel):
    """Persona individual mencionada en el texto."""

    occupation: Optional[EntityText] = Field(None, description="Ocupación o rol")


class Organizacion(EntityModel):
    """Institución, empresa, organismo o agrupación."""


class WORKS_AT(EdgeModel):
    """Una persona trabaja o colabora en una organización."""


ENTITIES = {"Persona": Persona, "Organizacion": Organizacion}
EDGES = {"WORKS_AT": (WORKS_AT, [EntityEdgeSourceTarget(source="Persona", target="Organizacion")])}

TEXTS = [
    "Ana Torres es urbanista de la Municipalidad de Santiago y trabaja en el plan de "
    "ciclovías de la comuna.",
    "La Cámara de Comercio de Santiago agrupa a los comerciantes del centro. Su gerente "
    "es Raúl Fuentes.",
]
ACTIVITY = "[2026-11-23T20:00:00] Ana Torres anunció que la Municipalidad de Santiago ampliará el plan de ciclovías."
QUERY = "¿Quién trabaja en la Municipalidad de Santiago?"


def wait_until(
    description: str,
    read: Callable[[], Any],
    is_done: Callable[[Any], bool],
    *,
    timeout: float,
    sleep: Callable[[float], None] = time.sleep,
    interval: float = 3.0,
) -> Any:
    """Consulta repetidamente hasta que la condición se cumpla o venza el plazo."""

    deadline = time.monotonic() + timeout
    while True:
        value = read()
        if is_done(value):
            return value
        if time.monotonic() >= deadline:
            raise TimeoutError(f"{description} no terminó en {int(timeout)} s")
        sleep(interval)


def batch_items(client: Any, batch_id: str) -> list[Any]:
    items: list[Any] = []
    cursor = None
    while True:
        page = client.batch.list_items(batch_id=batch_id, limit=100, cursor=cursor)
        items.extend(page.items)
        if page.next_cursor is None:
            return items
        cursor = page.next_cursor


def run(
    client: Any,
    *,
    timeout: float = 600.0,
    keep_graph: bool = False,
    sleep: Callable[[float], None] = time.sleep,
    log: Callable[[str], None] = lambda message: print(message, file=sys.stderr, flush=True),
) -> dict[str, Any]:
    """Ejecuta todas las comprobaciones y devuelve un informe."""

    graph_id = f"beemarketbot_check_{uuid.uuid4().hex[:10]}"
    report: dict[str, Any] = {"graph_id": graph_id, "steps": {}}

    def passed(step: str, **details: Any) -> None:
        report["steps"][step] = {"ok": True, **details}
        log(f"  ok   {step}")

    try:
        report["engine"] = client.ping()
        passed("conexión", base=report["engine"]["kind"])

        client.graph.create(graph_id=graph_id, name="Comprobación de BeeMarketBot", description="Grafo temporal de validación")
        client.graph.set_ontology(graph_ids=[graph_id], entities=ENTITIES, edges=EDGES)
        passed("grafo y ontología", entity_types=sorted(ENTITIES), edge_types=sorted(EDGES))

        batch = client.batch.create(metadata={"purpose": "beemarketbot-validation", "graph_id": graph_id})
        client.batch.add(
            batch_id=batch.batch_id,
            items=[
                BatchAddItem(
                    type="graph_episode",
                    graph_id=graph_id,
                    data=text,
                    data_type="text",
                    source_description="texto de validación",
                )
                for text in TEXTS
            ],
        )
        client.batch.process(batch_id=batch.batch_id)

        def read_batch() -> Any:
            return client.batch.get(batch_id=batch.batch_id)

        summary = wait_until(
            "la ingesta del lote", read_batch, lambda value: value.status in TERMINAL, timeout=timeout, sleep=sleep
        )
        items = batch_items(client, batch.batch_id)
        failed = [item for item in items if item.status != "succeeded"]
        if summary.status != "succeeded" or failed:
            raise AssertionError(
                f"el lote terminó como {summary.status}; fallos={len(failed)}; "
                f"primer error={getattr(failed[0], 'error', None) if failed else None}"
            )
        passed(
            "ingesta por lotes",
            episodes=len(items),
            percent=summary.progress.percent_complete,
        )

        nodes = fetch_all_nodes(client, graph_id, page_size=2)
        edges = fetch_all_edges(client, graph_id, page_size=2)
        labels = {label for item in nodes for label in item.labels} - {"Entity", "Node"}
        if not nodes or not edges:
            raise AssertionError(f"el grafo quedó vacío: nodos={len(nodes)} relaciones={len(edges)}")
        unknown = sorted(labels - set(ENTITIES))
        passed("lectura paginada", nodes=len(nodes), edges=len(edges), entity_types=sorted(labels), fuera_de_ontologia=unknown)

        found = client.graph.search(graph_id=graph_id, query=QUERY, limit=5, scope="edges")
        if not found.edges:
            raise AssertionError("la búsqueda no devolvió relaciones")
        passed("búsqueda", edges=len(found.edges), ejemplo=found.edges[0].fact[:90])

        episode = client.graph.add(
            graph_id=graph_id,
            type="text",
            data=ACTIVITY,
            source_description="actividad de validación",
        )
        stored = wait_until(
            "el episodio de simulación",
            lambda: client.graph.episode.get(uuid_=episode.uuid_),
            lambda value: value.processed or getattr(value, "failed", False),
            timeout=timeout,
            sleep=sleep,
        )
        if not stored.processed:
            raise AssertionError(f"el episodio de simulación falló: {getattr(stored, 'error', None)}")
        passed("episodio de simulación", processed=stored.processed)

        detail = client.graph.node.get(uuid_=nodes[0].uuid_)
        related = client.graph.node.get_edges(node_uuid=detail.uuid_)
        passed("detalle de entidad", name=detail.name, labels=detail.labels, related_edges=len(related))
    except Exception as error:  # noqa: BLE001 - la herramienta informa cualquier fallo
        report["error"] = f"{type(error).__name__}: {error}"
        log(f"  FALLO {report['error']}")
    finally:
        if keep_graph:
            report["cleanup"] = "grafo conservado"
            log(f"  --    grafo conservado: {graph_id}")
        else:
            try:
                client.graph.delete(graph_id=graph_id)
                report["cleanup"] = "grafo borrado"
                log("  ok   limpieza")
            except Exception as error:  # noqa: BLE001
                report["cleanup"] = f"no se pudo borrar el grafo: {error}"
                log(f"  FALLO limpieza: {error}")
                report.setdefault("error", report["cleanup"])

    report["ok"] = "error" not in report
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Valida la memoria en grafos con la configuración real.")
    parser.add_argument("--dry-run", action="store_true", help="solo revisa y muestra la configuración, sin conectar")
    parser.add_argument("--keep-graph", action="store_true", help="no borra el grafo temporal al terminar")
    parser.add_argument("--timeout", type=float, default=600.0, help="segundos de espera máxima de cada ingesta")
    args = parser.parse_args(argv)

    try:
        settings = GraphMemorySettings.from_env()
        problems = settings.validate()
    except GraphMemoryError as error:
        print(f"Configuración inválida: {error}", file=sys.stderr)
        return 2
    if problems:
        print("Configuración incompleta:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 2
    print(f"Configuración: {settings.redacted_json()}")
    if args.dry_run:
        return 0

    with tempfile.TemporaryDirectory(prefix="beemarketbot-check-") as state_dir:
        client = GraphMemoryClient(settings, state_dir=state_dir)
        try:
            report = run(client, timeout=args.timeout, keep_graph=args.keep_graph)
        finally:
            client.close()
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
