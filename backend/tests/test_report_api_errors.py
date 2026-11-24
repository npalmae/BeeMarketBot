"""Las rutas del informe responden 400, y sin traza interna, ante un cuerpo JSON ilegible."""

import pytest
from flask import Flask

from app.api import report as report_api
from app.utils.locale import t


@pytest.mark.parametrize(
    "view, expected_error",
    [
        ("generate_report", "api.requireSimulationId"),
        ("get_generate_status", "api.requireTaskOrSimId"),
        ("chat_with_report_agent", "api.requireSimulationId"),
        ("search_graph_tool", "api.requireGraphIdAndQuery"),
        ("get_graph_statistics_tool", "api.requireGraphId"),
    ],
)
def test_an_unreadable_json_body_is_a_client_error(view, expected_error):
    app = Flask(__name__)
    app.config["JSON_AS_ASCII"] = False
    view_function = getattr(report_api, view)

    with app.test_request_context(method="POST", data="{no es json", content_type="application/json"):
        response, status = view_function()

    body = response.get_json()
    assert status == 400
    assert body["success"] is False
    assert body["error"] == t(expected_error)
    assert "traceback" not in body
