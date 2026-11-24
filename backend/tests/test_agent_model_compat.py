"""Ajuste del historial de los agentes para proveedores que rechazan respuestas vacías."""

import asyncio
import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest
from camel.models import ModelFactory

from app.utils.agent_model_compat import drop_empty_replies_from, install, without_empty_assistant_replies
from app.utils.openai_chat_compat import create_chat_completion

SYSTEM = {"role": "system", "content": "Eres un vendedor de una feria."}
USER = {"role": "user", "content": "¿Qué hago?"}
TOOL_CALL = {"id": "call_1", "type": "function", "function": {"name": "buscar", "arguments": "{}"}}


@pytest.mark.parametrize("empty", ["", None, "   ", "\n\t"])
def test_empty_assistant_replies_are_dropped_and_everything_else_is_kept(empty):
    reply = {"role": "assistant", "content": "Me parece mal"}
    history = [
        SYSTEM,
        USER,
        {"role": "assistant", "content": empty},
        {"role": "user", "content": "¿Y ahora?"},
        {"role": "assistant", "content": ""},
        reply,
        {"role": "assistant", "content": empty},
    ]

    cleaned = without_empty_assistant_replies(history)

    assert cleaned == [SYSTEM, USER, {"role": "user", "content": "¿Y ahora?"}, reply]
    assert history[2]["content"] == empty  # el historial original no se modifica


def test_assistant_replies_that_only_call_tools_are_kept():
    # Son parte de una secuencia de herramientas: quitarlas rompería la conversación.
    tool_call = {"role": "assistant", "content": "", "tool_calls": [TOOL_CALL]}
    tool_result = {"role": "tool", "tool_call_id": "call_1", "content": "{}"}
    legacy_call = {"role": "assistant", "content": None, "function_call": {"name": "buscar", "arguments": "{}"}}

    assert without_empty_assistant_replies([SYSTEM, USER, tool_call, tool_result, {"role": "assistant", "content": ""}]) == [
        SYSTEM,
        USER,
        tool_call,
        tool_result,
    ]
    assert without_empty_assistant_replies([legacy_call]) == [legacy_call]


def test_other_roles_and_text_parts_are_never_dropped():
    parts = {"role": "assistant", "content": [{"type": "text", "text": "hola"}]}
    history = [parts, {"role": "tool", "content": ""}, {"role": "user", "content": ""}, {"role": "system", "content": None}]

    assert without_empty_assistant_replies(history) == history


class Completions:
    """Recurso ``chat.completions`` de mentira, síncrono o asíncrono."""

    def __init__(self, asynchronous: bool) -> None:
        self.calls: list[dict] = []
        if asynchronous:

            async def create(**kwargs):
                self.calls.append(kwargs)
                return "async"

        else:

            def create(**kwargs):
                self.calls.append(kwargs)
                return "sync"

        self.create = create


async def send(completions, **kwargs):
    """Llama al recurso y espera el resultado cuando es asíncrono."""

    result = completions.create(**kwargs)
    if inspect.isawaitable(result):
        result = await result
    return result


@pytest.mark.parametrize("asynchronous", [False, True])
def test_clients_send_the_cleaned_history_and_are_wrapped_once(asynchronous):
    completions = Completions(asynchronous)
    drop_empty_replies_from(completions)
    drop_empty_replies_from(completions)

    result = asyncio.run(send(completions, model="m", messages=[{"role": "assistant", "content": ""}, USER]))

    assert result == ("async" if asynchronous else "sync")
    assert completions.calls == [{"model": "m", "messages": [USER]}]


def test_install_wraps_every_model_the_simulation_creates(monkeypatch):
    created = []

    def fake_create(*args, **kwargs):
        model = SimpleNamespace(
            _client=SimpleNamespace(chat=SimpleNamespace(completions=Completions(False))),
            _async_client=SimpleNamespace(chat=SimpleNamespace(completions=Completions(True))),
        )
        created.append(model)
        return model

    monkeypatch.setattr(ModelFactory, "create", staticmethod(fake_create))
    install()
    install()

    model = ModelFactory.create(model_platform="openai")

    assert len(created) == 1

    async def scenario():
        for completions in (model._client.chat.completions, model._async_client.chat.completions):
            await send(completions, messages=[{"role": "assistant", "content": ""}, USER])
            assert completions.calls == [{"messages": [USER]}]

    asyncio.run(scenario())
    assert isinstance(inspect.getattr_static(ModelFactory, "create"), staticmethod)


def test_install_leaves_models_without_chat_clients_alone(monkeypatch):
    monkeypatch.setattr(ModelFactory, "create", staticmethod(lambda **kwargs: object()))
    install()

    assert isinstance(ModelFactory.create(model_platform="openai"), object)


def test_the_application_own_calls_do_not_send_empty_replies_either():
    class Recorder:
        def __init__(self):
            self.calls = []

        def create(self, **kwargs):
            self.calls.append(kwargs)

    recorder = Recorder()
    client = SimpleNamespace(chat=SimpleNamespace(completions=recorder))

    create_chat_completion(client, model="m", messages=[USER, {"role": "assistant", "content": ""}, {"role": "user", "content": "¿Y?"}])

    assert recorder.calls[0]["messages"] == [USER, {"role": "user", "content": "¿Y?"}]


@pytest.mark.parametrize(
    "name", ["run_parallel_simulation.py", "run_twitter_simulation.py", "run_reddit_simulation.py"]
)
def test_simulation_scripts_install_it_before_creating_a_model(name):
    text = (Path(__file__).resolve().parents[1] / "scripts" / name).read_text(encoding="utf-8")

    assert "from app.utils.agent_model_compat import install" in text
    assert text.index("\ninstall()") < text.index("ModelFactory.create")
