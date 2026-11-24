"""Historial de los agentes aceptado por proveedores estrictos.

La librería de agentes (camel-ai) conserva en la memoria de cada agente las
respuestas vacías del asistente: sin texto y sin llamadas a herramientas. OpenAI
las admite, pero Kimi las rechaza con un error 400 ("the message at position N
with role 'assistant' must not be empty") y, mientras el mensaje vacío siga en
el historial, rechaza todos los turnos siguientes de ese agente, que deja de
actuar sin que nadie lo avise.

``install()`` ajusta cada modelo que crea la simulación para omitir esas
respuestas antes de enviar el historial. Es inofensivo para los proveedores que
las admiten: un mensaje vacío no aporta información.
"""

from __future__ import annotations

import functools
import inspect
from typing import Any

from .openai_chat_compat import without_empty_assistant_replies

__all__ = ["install", "drop_empty_replies_from", "without_empty_assistant_replies"]


def drop_empty_replies_from(completions: Any) -> None:
    """Hace que un recurso ``chat.completions`` limpie los mensajes que recibe.

    Acepta el recurso de un cliente síncrono o asíncrono, y no lo envuelve dos
    veces.
    """

    original = completions.create
    if getattr(original, "_drops_empty_assistant_replies", False):
        return

    def clean(kwargs: dict[str, Any]) -> dict[str, Any]:
        if "messages" in kwargs:
            kwargs["messages"] = without_empty_assistant_replies(kwargs["messages"])
        return kwargs

    if inspect.iscoroutinefunction(original):

        @functools.wraps(original)
        async def create(*args: Any, **kwargs: Any) -> Any:
            return await original(*args, **clean(kwargs))

    else:

        @functools.wraps(original)
        def create(*args: Any, **kwargs: Any) -> Any:
            return original(*args, **clean(kwargs))

    create._drops_empty_assistant_replies = True
    completions.create = create


def install() -> None:
    """Aplica el ajuste a todos los modelos que cree la simulación, una sola vez.

    Los procesos de simulación deben llamarlo antes de crear el primer modelo.
    """

    from camel.models import ModelFactory

    if getattr(ModelFactory.create, "_drops_empty_assistant_replies", False):
        return
    original = ModelFactory.create

    @functools.wraps(original)
    def create(*args: Any, **kwargs: Any) -> Any:
        model = original(*args, **kwargs)
        for attribute in ("_client", "_async_client"):
            chat = getattr(getattr(model, attribute, None), "chat", None)
            completions = getattr(chat, "completions", None)
            if completions is not None:
                drop_empty_replies_from(completions)
        return model

    create._drops_empty_assistant_replies = True
    ModelFactory.create = staticmethod(create)
