"""Parser ReAct del agente de informes: formatos aceptados y recordatorio.

El bucle ReAct fallaba cuando el modelo describía su intención de buscar sin
emitir la llamada: ahora el parser acepta más formatos y el recordatorio de
la iteración muestra la sintaxis exacta con un ejemplo literal.
"""

import json

import pytest

from app.services.report_agent import (
    REACT_NO_TOOL_CALL_MSG,
    SECTION_USER_PROMPT_TEMPLATE,
    ReportAgent,
)

OPEN = chr(60) + "tool_call" + chr(62)
CLOSE = chr(60) + chr(47) + "tool_call" + chr(62)


def _agent() -> ReportAgent:
    return ReportAgent.__new__(ReportAgent)


def _std_call(query="evento principal"):
    return OPEN + "\n" + json.dumps(
        {"name": "quick_search", "parameters": {"query": query}}
    ) + "\n" + CLOSE


def test_parse_standard_tag_format():
    response = "Thought: busco datos\n" + _std_call("apertura CandyLand")
    calls = _agent()._parse_tool_calls(response)
    assert len(calls) == 1
    assert calls[0]["name"] == "quick_search"
    assert calls[0]["parameters"]["query"] == "apertura CandyLand"


def test_parse_fenced_json_block():
    response = (
        "Thought: voy a consultar el grafo\n"
        "```json\n"
        '{"name": "panorama_search", "parameters": {"query": "reaccion del barrio"}}\n'
        "```"
    )
    calls = _agent()._parse_tool_calls(response)
    assert len(calls) == 1
    assert calls[0]["name"] == "panorama_search"


def test_parse_standalone_line_json():
    response = (
        "Thought: necesito buscar las reacciones de las familias.\n"
        '{"name": "quick_search", "parameters": {"query": "familias con ninos"}}'
    )
    calls = _agent()._parse_tool_calls(response)
    assert len(calls) == 1
    assert calls[0]["name"] == "quick_search"


def test_parse_ignores_prose_without_valid_tool():
    response = (
        "Thought: describo que voy a buscar, pero no llamo a nada.\n"
        "Las familias reaccionaron bien segun el informe previo."
    )
    assert _agent()._parse_tool_calls(response) == []


def test_parse_ignores_json_with_unknown_tool():
    response = '{"name": "herramienta_inexistente", "parameters": {"q": "x"}}'
    assert _agent()._parse_tool_calls(response) == []


def test_parse_accepts_tool_and_params_alias():
    response = OPEN + "\n" + '{"tool": "quick_search", "params": {"query": "prueba"}}' + "\n" + CLOSE
    calls = _agent()._parse_tool_calls(response)
    assert calls[0]["name"] == "quick_search"
    assert calls[0]["parameters"]["query"] == "prueba"


def test_reminder_message_contains_exact_format():
    assert OPEN in REACT_NO_TOOL_CALL_MSG
    assert CLOSE in REACT_NO_TOOL_CALL_MSG
    assert '"name": "quick_search"' in REACT_NO_TOOL_CALL_MSG
    assert "insight_forge" in REACT_NO_TOOL_CALL_MSG


def test_section_prompt_contains_literal_example():
    rendered = SECTION_USER_PROMPT_TEMPLATE.format(
        previous_content="",
        section_title="Seccion de prueba",
    )
    assert OPEN in rendered
    assert CLOSE in rendered
    assert "panorama_search" in rendered
