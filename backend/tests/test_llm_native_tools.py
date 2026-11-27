"""Function calling nativo: normalización de respuestas y esquemas de herramientas."""

import json
from types import SimpleNamespace

from app.services.report_agent import ReportAgent
from app.utils.llm_client import LLMClient


def _client_with(response):
    client = LLMClient(api_key="test-key", base_url="http://test.local/v1", model="test-model")
    recorder = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **kwargs: response
            )
        )
    )
    client.client = recorder
    return client


def _tool_call_response(content="", name="quick_search", arguments=None, call_id="call_1"):
    function = SimpleNamespace(name=name, arguments=json.dumps(arguments or {"query": "prueba"}))
    call = SimpleNamespace(id=call_id, function=function)
    message = SimpleNamespace(content=content, tool_calls=[call])
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def test_chat_with_tools_normalizes_native_calls():
    response = _tool_call_response(content="Voy a buscar", arguments={"query": "reaccion barrio"})
    step = _client_with(response).chat_with_tools(
        messages=[{"role": "user", "content": "hola"}],
        tools=[{"type": "function", "function": {"name": "quick_search"}}],
    )

    assert step["content"] == "Voy a buscar"
    assert len(step["tool_calls"]) == 1
    assert step["tool_calls"][0]["name"] == "quick_search"
    assert step["tool_calls"][0]["id"] == "call_1"
    assert step["tool_calls"][0]["arguments"] == {"query": "reaccion barrio"}


def test_chat_with_tools_without_calls_returns_content_only():
    message = SimpleNamespace(content="Respuesta final", tool_calls=None)
    response = SimpleNamespace(choices=[SimpleNamespace(message=message)])

    step = _client_with(response).chat_with_tools(
        messages=[{"role": "user", "content": "hola"}], tools=[]
    )

    assert step == {"content": "Respuesta final", "tool_calls": []}


def test_chat_with_tools_uses_reasoning_when_content_empty():
    response = _tool_call_response(content="", arguments={"query": "x"})
    response.choices[0].message.reasoning_content = "razonamiento util"
    response.choices[0].message.content = ""

    step = _client_with(response).chat_with_tools(
        messages=[{"role": "user", "content": "hola"}], tools=[]
    )

    assert step["content"] == "razonamiento util"


def test_chat_with_tools_tolerates_invalid_arguments():
    response = _tool_call_response(arguments=None)
    response.choices[0].message.tool_calls[0].function.arguments = "{no es json"

    step = _client_with(response).chat_with_tools(
        messages=[{"role": "user", "content": "hola"}], tools=[]
    )

    assert step["tool_calls"][0]["arguments"] == {}


def test_tool_schemas_cover_the_four_report_tools():
    agent = ReportAgent.__new__(ReportAgent)
    schemas = agent._get_tool_schemas()

    names = {s["function"]["name"] for s in schemas}
    assert names == {"insight_forge", "panorama_search", "quick_search", "interview_agents"}
    for schema in schemas:
        assert schema["type"] == "function"
        params = schema["function"]["parameters"]
        assert params["type"] == "object"
        assert isinstance(params["properties"], dict) and params["properties"]
        for prop in params["properties"].values():
            assert prop["type"] == "string"


def test_quick_search_schema_requires_query():
    agent = ReportAgent.__new__(ReportAgent)
    schemas = {s["function"]["name"]: s for s in agent._get_tool_schemas()}

    assert "query" in schemas["quick_search"]["function"]["parameters"]["required"]
    assert "interview_topic" in schemas["interview_agents"]["function"]["parameters"]["required"]
