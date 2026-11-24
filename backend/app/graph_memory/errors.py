"""Errores del motor de memoria en grafos.

Llevan un ``status_code`` equivalente al HTTP para que la política de reintentos
del backend pueda decidir si un fallo de lectura es transitorio.
"""

from __future__ import annotations

from typing import Any


class GraphMemoryError(Exception):
    """Error genérico del motor de memoria en grafos."""

    status_code: int | None = None

    def __init__(
        self,
        message: str | None = None,
        *,
        status_code: int | None = None,
        body: dict[str, Any] | None = None,
        retry_after: float | None = None,
    ) -> None:
        if message is None:
            message = str((body or {}).get("message") or self.__class__.__name__)
        super().__init__(message)
        if status_code is not None:
            self.status_code = status_code
        self.body = body or {}
        self.retry_after = retry_after


class ConfigError(GraphMemoryError):
    """Configuración incompleta o inválida. No tiene sentido reintentar."""

    status_code = None


class BadRequestError(GraphMemoryError):
    """Petición inválida."""

    status_code = 400


class AuthError(GraphMemoryError):
    """Credenciales o permisos insuficientes."""

    status_code = 401


class NotFoundError(GraphMemoryError):
    """El grafo, nodo, arista o episodio solicitado no existe."""

    status_code = 404


class RateLimitError(GraphMemoryError):
    """El proveedor de modelos o la base de datos limitó la tasa de peticiones."""

    status_code = 429


class ServerError(GraphMemoryError):
    """Fallo del motor o de la base de datos. Reintenta solo en lecturas."""

    status_code = 503


# Nombre histórico: el backend clasificaba los errores por esta clase.
ApiError = GraphMemoryError
