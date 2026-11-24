"""
OpenAI Chat Completions compatibility helpers.

This module keeps existing behavior for legacy models/providers while
gracefully adapting request parameters for GPT-5 family models.

Providers that only accept certain temperatures, or that expect extra
parameters, are handled through two optional settings, read from the environment
on every call:

- ``LLM_TEMPERATURE``: replaces the temperature requested by each call.
- ``LLM_EXTRA_BODY``: a JSON object merged into every request.

Unset, they change nothing.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional


def configured_temperature() -> Optional[float]:
    """Return the temperature fixed by configuration, if the provider needs one."""

    raw = os.environ.get("LLM_TEMPERATURE", "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        raise ValueError("LLM_TEMPERATURE must be a number") from None


def configured_extra_body() -> Dict[str, Any]:
    """Return the extra parameters the provider expects in every request."""

    raw = os.environ.get("LLM_EXTRA_BODY", "").strip()
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except ValueError as error:
        raise ValueError("LLM_EXTRA_BODY must be a valid JSON object") from error
    if not isinstance(value, dict):
        raise ValueError("LLM_EXTRA_BODY must be a JSON object, not a list or a single value")
    return value


def without_empty_assistant_replies(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Omit assistant replies that have neither text nor tool calls.

    camel-ai keeps such replies in each agent's memory. OpenAI accepts them;
    Kimi rejects the whole request and, while the empty reply stays in the
    history, every later turn of that agent.
    """

    return [message for message in messages if not _is_empty_assistant_reply(message)]


def _is_empty_assistant_reply(message: Any) -> bool:
    if not isinstance(message, dict) or message.get("role") != "assistant":
        return False
    if message.get("tool_calls") or message.get("function_call"):
        return False
    return not str(message.get("content") or "").strip()


def is_gpt5_family(model: Optional[str]) -> bool:
    """Return True when model belongs to GPT-5 family aliases/snapshots."""
    if not model:
        return False
    return model.strip().lower().startswith("gpt-5")


def create_chat_completion(
    client: Any,
    *,
    model: str,
    messages: List[Dict[str, Any]],
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    response_format: Optional[Dict[str, Any]] = None,
) -> Any:
    """
    Create a chat completion with model-specific request parameters.

    Compatibility strategy:
    - For GPT-5 family, avoid sending temperature by default.
    - For token limit, use `max_completion_tokens` on GPT-5, `max_tokens` otherwise.
    - Let configuration replace the temperature, and add provider-specific
      parameters, when those settings are present.
    - Preserve the legacy request shape for every other model/provider.
    - Propagate provider errors unchanged instead of guessing from message text.
    """
    requested = temperature
    temperature = configured_temperature()
    if temperature is None:
        temperature = requested

    kwargs: Dict[str, Any] = {
        "model": model,
        "messages": without_empty_assistant_replies(messages),
    }

    if response_format is not None:
        kwargs["response_format"] = response_format

    gpt5_family = is_gpt5_family(model)

    if temperature is not None and not gpt5_family:
        kwargs["temperature"] = temperature

    if max_tokens is not None:
        if gpt5_family:
            kwargs["max_completion_tokens"] = max_tokens
        else:
            kwargs["max_tokens"] = max_tokens

    extra = configured_extra_body()
    if extra:
        kwargs["extra_body"] = extra

    return client.chat.completions.create(**kwargs)


def extract_chat_completion_text(response: Any) -> str:
    """Extract plain text from chat completion response across SDK content shapes."""
    choices = getattr(response, "choices", None) or []
    if not choices:
        return ""

    message = getattr(choices[0], "message", None)
    if message is None:
        return ""

    content = getattr(message, "content", "")

    if isinstance(content, str):
        return content

    if isinstance(content, list):
        chunks: List[str] = []
        for item in content:
            if isinstance(item, dict):
                text_obj = item.get("text")
                if isinstance(text_obj, dict):
                    text_obj = text_obj.get("value")
                if isinstance(text_obj, str):
                    chunks.append(text_obj)
                elif isinstance(item.get("content"), str):
                    chunks.append(item["content"])
                continue

            text_obj = getattr(item, "text", None)
            if isinstance(text_obj, dict):
                text_obj = text_obj.get("value")
            if isinstance(text_obj, str):
                chunks.append(text_obj)
                continue

            content_obj = getattr(item, "content", None)
            if isinstance(content_obj, str):
                chunks.append(content_obj)

        return "".join(chunks).strip()

    return str(content or "")
