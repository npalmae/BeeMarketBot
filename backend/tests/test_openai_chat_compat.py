from types import SimpleNamespace

import pytest

from app.utils.openai_chat_compat import (
    create_chat_completion,
    extract_chat_completion_text,
    is_gpt5_family,
)


class CompletionRecorder:
    def __init__(self, result=None, error=None):
        self.result = result or object()
        self.error = error
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.result


def client_for(recorder):
    return SimpleNamespace(chat=SimpleNamespace(completions=recorder))


def test_gpt5_uses_completion_token_limit_without_temperature():
    recorder = CompletionRecorder()
    messages = [{"role": "user", "content": "hello"}]

    result = create_chat_completion(
        client_for(recorder),
        model="gpt-5-2025-08-07",
        messages=messages,
        temperature=0.2,
        max_tokens=123,
        response_format={"type": "json_object"},
    )

    assert result is recorder.result
    assert recorder.calls == [
        {
            "model": "gpt-5-2025-08-07",
            "messages": messages,
            "max_completion_tokens": 123,
            "response_format": {"type": "json_object"},
        }
    ]


def test_legacy_model_preserves_original_request_shape():
    recorder = CompletionRecorder()
    messages = [{"role": "user", "content": "hello"}]

    create_chat_completion(
        client_for(recorder),
        model="third-party-chat-model",
        messages=messages,
        temperature=0.7,
        max_tokens=456,
        response_format={"type": "json_object"},
    )

    assert recorder.calls == [
        {
            "model": "third-party-chat-model",
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 456,
            "response_format": {"type": "json_object"},
        }
    ]


def test_provider_error_is_propagated_without_guessing_or_retrying():
    provider_error = RuntimeError("unsupported max_tokens due to a server outage")
    recorder = CompletionRecorder(error=provider_error)

    with pytest.raises(RuntimeError) as captured:
        create_chat_completion(
            client_for(recorder),
            model="legacy-model",
            messages=[],
            max_tokens=10,
        )

    assert captured.value is provider_error
    assert len(recorder.calls) == 1


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("gpt-5", True),
        (" GPT-5.1-mini ", True),
        ("gpt-4.1", False),
        ("my-gpt-5-proxy", False),
        (None, False),
    ],
)
def test_gpt5_family_detection(model, expected):
    assert is_gpt5_family(model) is expected


def test_extracts_text_from_supported_content_shapes():
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=[
                        {"text": {"value": "first"}},
                        {"content": " second"},
                        SimpleNamespace(text=" third"),
                    ]
                )
            )
        ]
    )

    assert extract_chat_completion_text(response) == "first second third"
    assert extract_chat_completion_text(SimpleNamespace(choices=[])) == ""


# ---------------------------------------------------------------- provider compatibility settings

MESSAGES = [{"role": "user", "content": "hello"}]
JSON = {"type": "json_object"}


def test_a_configured_temperature_replaces_every_requested_one(monkeypatch):
    monkeypatch.setenv("LLM_TEMPERATURE", "0.6")
    recorder = CompletionRecorder()
    client = client_for(recorder)

    create_chat_completion(client, model="kimi-for-coding", messages=MESSAGES, temperature=0.3)
    create_chat_completion(client, model="kimi-for-coding", messages=MESSAGES, temperature=0.7, response_format=JSON)

    assert [call["temperature"] for call in recorder.calls] == [0.6, 0.6]
    assert all("extra_body" not in call for call in recorder.calls)


def test_without_a_configured_temperature_each_call_keeps_its_own(monkeypatch):
    recorder = CompletionRecorder()
    client = client_for(recorder)

    create_chat_completion(client, model="kimi-for-coding", messages=MESSAGES, temperature=0.7)
    create_chat_completion(client, model="kimi-for-coding", messages=MESSAGES, response_format=JSON)

    assert [call.get("temperature") for call in recorder.calls] == [0.7, None]


def test_zero_is_a_valid_configured_temperature(monkeypatch):
    monkeypatch.setenv("LLM_TEMPERATURE", "0")
    recorder = CompletionRecorder()
    client = client_for(recorder)

    create_chat_completion(client, model="kimi-for-coding", messages=MESSAGES, temperature=0.7)

    assert recorder.calls[0]["temperature"] == 0.0


def test_gpt5_still_sends_no_temperature_even_when_one_is_configured(monkeypatch):
    monkeypatch.setenv("LLM_TEMPERATURE", "0.6")
    recorder = CompletionRecorder()
    client = client_for(recorder)

    create_chat_completion(client, model="gpt-5", messages=MESSAGES, temperature=0.3, response_format=JSON)

    assert "temperature" not in recorder.calls[0]


def test_extra_body_is_sent_with_every_request(monkeypatch):
    monkeypatch.setenv("LLM_EXTRA_BODY", '{"thinking": {"type": "disabled"}, "top_k": 5}')
    recorder = CompletionRecorder()
    client = client_for(recorder)

    create_chat_completion(client, model="kimi-for-coding", messages=MESSAGES, temperature=0.7)
    create_chat_completion(client, model="kimi-for-coding", messages=MESSAGES, temperature=0.7, response_format=JSON)

    assert [call["extra_body"] for call in recorder.calls] == [{"thinking": {"type": "disabled"}, "top_k": 5}] * 2
    # Van dentro de extra_body, no sueltos en la petición.
    assert "thinking" not in recorder.calls[0]


@pytest.mark.parametrize(
    "value, message",
    [("{no json", "JSON object"), ("[1, 2]", "JSON object"), ('"texto"', "JSON object")],
)
def test_an_unreadable_extra_body_is_reported_clearly(monkeypatch, value, message):
    monkeypatch.setenv("LLM_EXTRA_BODY", value)

    with pytest.raises(ValueError, match=message):
        create_chat_completion(client_for(CompletionRecorder()), model="m", messages=MESSAGES)


def test_an_unreadable_temperature_is_reported_clearly(monkeypatch):
    monkeypatch.setenv("LLM_TEMPERATURE", "tibia")

    with pytest.raises(ValueError, match="LLM_TEMPERATURE must be a number"):
        create_chat_completion(client_for(CompletionRecorder()), model="m", messages=MESSAGES)


def test_startup_validation_reports_unreadable_compatibility_settings(monkeypatch):
    from app.config import Config

    monkeypatch.setenv("LLM_EXTRA_BODY", "{no")
    assert any("LLM_EXTRA_BODY" in problem for problem in Config.validate())

    monkeypatch.setenv("LLM_EXTRA_BODY", "")
    monkeypatch.setenv("LLM_TEMPERATURE", "no")
    assert [problem for problem in Config.llm_compatibility_errors() if "LLM_TEMPERATURE" in problem]

    monkeypatch.setenv("LLM_TEMPERATURE", "")
    assert Config.llm_compatibility_errors() == []
