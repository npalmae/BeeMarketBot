"""Motor de memoria en grafos de BeeMarketBot.

Reemplaza a Zep Cloud por un motor propio sobre Graphiti (código abierto), que
construye y consulta el grafo de conocimiento usando cualquier API de modelos
compatible con OpenAI y cualquier API de embeddings del mismo formato.

Módulos:

- ``settings``: configuración leída de variables de entorno.
- ``errors``: errores con código de estado comparable al HTTP.
- ``types``: tipos de datos de la ontología y de los lotes.
- ``objects``: forma de los datos que devuelve el cliente.
- ``llm``: clientes de modelo y de embeddings, y el reclasificador vacío.
- ``engine``: motor asíncrono sobre Graphiti.
- ``client``: cliente síncrono con la interfaz que usa el resto del backend.
"""

from .errors import (
    ApiError,
    AuthError,
    BadRequestError,
    ConfigError,
    GraphMemoryError,
    NotFoundError,
    RateLimitError,
    ServerError,
)
from .types import (
    BatchAddItem,
    EdgeModel,
    EntityEdgeSourceTarget,
    EntityModel,
    EntityText,
    SearchFilters,
)

__all__ = [
    "ApiError",
    "AuthError",
    "BadRequestError",
    "BatchAddItem",
    "ConfigError",
    "EdgeModel",
    "EntityEdgeSourceTarget",
    "EntityModel",
    "EntityText",
    "GraphMemoryError",
    "NotFoundError",
    "RateLimitError",
    "SearchFilters",
    "ServerError",
]
