"""Clientes de modelo y de embeddings para el motor de memoria en grafos.

Graphiti se importa de forma diferida para poder fijar su configuración de
entorno (telemetría y concurrencia) antes de que se cargue por primera vez.
"""

from __future__ import annotations

from typing import Any

from .settings import EmbeddingSettings, LLMSettings

# Graphiti lee estas variables al importarse; se fijan antes del primer import.
ENVIRONMENT_DEFAULTS = {
    "GRAPHITI_TELEMETRY_ENABLED": "false",
}


def prepare_environment(settings: Any) -> None:
    """Fija la configuración de entorno de Graphiti antes de importarlo."""

    import os

    for name, value in ENVIRONMENT_DEFAULTS.items():
        os.environ.setdefault(name, value)
    concurrency = getattr(settings, "llm_concurrency", None)
    if concurrency:
        os.environ.setdefault("SEMAPHORE_LIMIT", str(int(concurrency)))


def build_noop_cross_encoder() -> Any:
    """Reclasificador vacío.

    El que Graphiti trae por defecto depende de funciones exclusivas de OpenAI.
    Sin él, la búsqueda híbrida combina resultados por fusión de rangos, que es
    lo que ya usa el proyecto.
    """

    from graphiti_core.cross_encoder.client import CrossEncoderClient

    class _NoopCrossEncoder(CrossEncoderClient):
        async def rank(self, query: str, passages: list[str]) -> list[tuple[str, float]]:
            return [(passage, 1.0) for passage in passages]

        async def score(self, query: str, passages: list[str]) -> list[float]:
            return [1.0] * len(passages)

    return _NoopCrossEncoder()


class _Completions:
    def __init__(self, real: Any, extra_body: dict[str, Any]) -> None:
        self._real = real
        self._extra_body = extra_body

    async def create(self, **kwargs: Any) -> Any:
        kwargs.setdefault("extra_body", {}).update(self._extra_body)
        return await self._real.create(**kwargs)


class _Chat:
    def __init__(self, completions: Any, extra_body: dict[str, Any]) -> None:
        self.completions = _Completions(completions, extra_body)


class ClientWithExtraBody:
    """Cliente OpenAI que agrega parámetros fijos a cada llamada de chat.

    ``extra_body`` es un parámetro admitido por el SDK de OpenAI, así que el
    cuerpo extra viaja con cada petición sin tocar Graphiti.
    """

    def __init__(self, client: Any, extra_body: dict[str, Any]) -> None:
        self._client = client
        self._extra_body = extra_body
        self.chat = _Chat(client.chat.completions, extra_body)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)


def build_llm_client(settings: LLMSettings) -> Any:
    """Cliente de modelo compatible con cualquier API en formato OpenAI."""

    from openai import AsyncOpenAI
    from graphiti_core.llm_client.config import LLMConfig
    from graphiti_core.llm_client.openai_generic_client import OpenAIGenericClient

    client: Any = AsyncOpenAI(api_key=settings.api_key, base_url=settings.base_url)
    thinking = settings.resolved_thinking()
    if thinking in ("disabled", "enabled"):
        client = ClientWithExtraBody(client, {"thinking": {"type": thinking}})

    config = LLMConfig(
        api_key=settings.api_key,
        model=settings.model,
        small_model=settings.model,
        base_url=settings.base_url,
        temperature=settings.temperature,
    )
    return OpenAIGenericClient(
        config=config,
        client=client,
        structured_output_mode=settings.structured_output,
    )


def build_embedder(settings: EmbeddingSettings) -> Any:
    """Cliente de embeddings compatible con cualquier API en formato OpenAI."""

    from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig

    return OpenAIEmbedder(
        config=OpenAIEmbedderConfig(
            api_key=settings.api_key,
            base_url=settings.base_url,
            embedding_model=settings.model,
            embedding_dim=settings.dimensions,
        )
    )
