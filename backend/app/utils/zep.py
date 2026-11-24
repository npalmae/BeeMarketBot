"""Cliente compartido del motor de memoria en grafos y política de reintentos.

Nombre histórico: este módulo se llamaba así cuando el proyecto usaba Zep
Cloud. Conserva sus nombres para no reescribir los servicios que lo importan,
pero ahora entrega el cliente de ``app.graph_memory`` (motor propio sobre
Graphiti) y ya no requiere ``ZEP_API_KEY`` ni habla con ningún servicio externo
de memoria. La conexión con el modelo y los embeddings se configura con las
variables ``GRAPH_LLM_*`` y ``EMBEDDING_*``.
"""

from __future__ import annotations

import time
from typing import Callable, TypeVar

from ..graph_memory.client import get_shared_client, reset_shared_client
from ..graph_memory.errors import GraphMemoryError
from .logger import get_logger

logger = get_logger("mirofish.graph_client")

T = TypeVar("T")

# Espera máxima, en segundos, de una ingesta (un documento grande puede tardar).
ZEP_INGESTION_WAIT_TIMEOUT_SECONDS = 600
MAX_ZEP_SEARCH_QUERY_CHARS = 400
MAX_ZEP_SEARCH_RESULTS = 50
# Se conserva por compatibilidad: el cliente ya no fija un tiempo por petición HTTP.
ZEP_HTTP_REQUEST_TIMEOUT_SECONDS = 60.0


def normalize_zep_search_query(query) -> str:
    """Devuelve una consulta no vacía dentro del límite de longitud del motor."""

    if not isinstance(query, str):
        raise ValueError("La consulta de búsqueda debe ser texto")
    normalized = query.strip()
    if not normalized:
        raise ValueError("La consulta de búsqueda no puede estar vacía")
    return normalized[:MAX_ZEP_SEARCH_QUERY_CHARS]


def normalize_zep_search_limit(limit) -> int:
    """Ajusta el límite de resultados al rango que acepta el motor."""

    try:
        normalized = int(limit)
    except (TypeError, ValueError) as exc:
        raise ValueError("El límite de búsqueda debe ser un entero") from exc
    if normalized < 1:
        raise ValueError("El límite de búsqueda debe ser al menos 1")
    return min(normalized, MAX_ZEP_SEARCH_RESULTS)


def get_zep_client(api_key: str | None = None, timeout: float | None = None):
    """Devuelve el cliente compartido del motor de grafos, creado la primera vez.

    ``api_key`` y ``timeout`` se aceptan por compatibilidad con el código que
    usaba Zep Cloud y se ignoran: la configuración viene del entorno.
    """

    del api_key, timeout
    return get_shared_client()


def clear_zep_client_cache() -> None:
    """Cierra y olvida el cliente compartido. Para pruebas y reconfiguración."""

    reset_shared_client()


def is_retryable_zep_error(error: BaseException) -> bool:
    """Indica si falló una lectura que vale la pena reintentar."""

    if isinstance(error, (ConnectionError, TimeoutError, OSError)):
        return True
    if isinstance(error, GraphMemoryError):
        status_code = error.status_code
        return status_code in {408, 429} or (
            status_code is not None and 500 <= status_code <= 599
        )
    return False


def _retry_after_seconds(error: BaseException) -> float | None:
    value = getattr(error, "retry_after", None)
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return None


def call_zep_read_with_retry(
    operation: Callable[[], T],
    *,
    operation_name: str,
    max_attempts: int = 3,
    initial_delay: float = 2.0,
    max_delay: float = 60.0,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Reintenta una lectura segura ante fallos de transporte, 408, 429 o 5xx.

    Las escrituras no se reintentan aquí: repetir una ingesta puede duplicar
    hechos en el grafo.
    """

    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")

    for attempt in range(1, max_attempts + 1):
        try:
            return operation()
        except Exception as error:
            if attempt == max_attempts or not is_retryable_zep_error(error):
                raise

            retry_after = _retry_after_seconds(error)
            delay = min(
                retry_after if retry_after is not None else initial_delay * (2 ** (attempt - 1)),
                max_delay,
            )
            logger.warning(
                "Graph memory %s attempt %s/%s failed (%s); retrying in %.1fs",
                operation_name,
                attempt,
                max_attempts,
                type(error).__name__,
                delay,
            )
            sleep(delay)

    raise AssertionError("unreachable")
