"""Script manual de validación: misma lógica, sobre un motor falso."""

import json
import time

import pytest

from scripts import validate_graph_memory as validation

STEP_NAMES = [
    "conexión",
    "grafo y ontología",
    "ingesta por lotes",
    "lectura paginada",
    "búsqueda",
    "episodio de simulación",
    "detalle de entidad",
]


def quick(_seconds):
    time.sleep(0.01)


def test_run_checks_every_step_and_deletes_the_temporary_graph(make_graph_client):
    client, engine = make_graph_client()
    lines = []

    report = validation.run(client, sleep=quick, log=lines.append)

    assert report["ok"] is True
    assert "error" not in report
    assert list(report["steps"]) == STEP_NAMES
    assert report["steps"]["ingesta por lotes"]["episodes"] == 2
    assert (report["steps"]["lectura paginada"]["nodes"], report["steps"]["lectura paginada"]["edges"]) == (2, 1)
    assert report["steps"]["lectura paginada"]["fuera_de_ontologia"] == ["Cosa"]
    assert report["steps"]["episodio de simulación"]["processed"] is True
    assert report["cleanup"] == "grafo borrado"
    assert engine.graphs == {}
    # La herramienta informa el avance paso a paso por el canal de progreso.
    assert [line.strip() for line in lines] == [f"ok   {step}" for step in STEP_NAMES] + ["ok   limpieza"]
    assert report["graph_id"].startswith("beemarketbot_check_")


def test_run_keeps_the_graph_when_asked(make_graph_client):
    client, engine = make_graph_client()

    report = validation.run(client, keep_graph=True, sleep=quick, log=lambda _message: None)

    assert report["ok"] is True
    assert report["cleanup"] == "grafo conservado"
    assert list(engine.graphs) == [report["graph_id"]]


def test_a_failed_check_is_reported_and_the_graph_is_still_deleted(make_graph_client):
    client, engine = make_graph_client()
    engine.search_empty = True

    report = validation.run(client, sleep=quick, log=lambda _message: None)

    assert report["ok"] is False
    assert "búsqueda" in report["error"]
    assert list(report["steps"]) == STEP_NAMES[:4]
    assert report["cleanup"] == "grafo borrado"
    assert engine.graphs == {}


def test_a_failed_episode_reported_by_the_engine_fails_the_check(make_graph_client):
    client, engine = make_graph_client()
    engine.failing_text = "anunció"

    report = validation.run(client, sleep=quick, log=lambda _message: None)

    assert report["ok"] is False
    assert "episodio de simulación" in report["error"]
    assert report["cleanup"] == "grafo borrado"


def test_wait_until_gives_up_with_a_clear_message():
    readings = []

    with pytest.raises(TimeoutError, match="no terminó"):
        validation.wait_until(
            "la ingesta",
            read=lambda: readings.append(1) or {"status": "processing"},
            is_done=lambda value: value["status"] == "succeeded",
            timeout=0.05,
            sleep=quick,
            interval=0.01,
        )

    assert len(readings) >= 2


def test_dry_run_prints_the_configuration_without_secrets(monkeypatch, capsys):
    secret = "sk-secreto-de-prueba-1234567890"
    for name in list(__import__("os").environ):
        if name.startswith(("LLM_", "GRAPH_", "EMBEDDING_")):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LLM_API_KEY", secret)
    monkeypatch.setenv("LLM_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("LLM_MODEL_NAME", "gpt-prueba")
    monkeypatch.setenv("EMBEDDING_MODEL", "vectores-prueba")
    monkeypatch.setenv("GRAPH_DB_URL", "neo4j://usuario:clave-muy-secreta@db.example.com:7687/app")

    assert validation.main(["--dry-run"]) == 0

    output = capsys.readouterr().out
    for secret_part in ("secreto", "1234567890", "muy-secreta", "clave"):
        assert secret_part not in output, secret_part
    shown = json.loads(output.split("Configuración: ", 1)[1])
    assert shown["database"] == {
        "kind": "neo4j",
        "host": "db.example.com",
        "port": 7687,
        "database": "app",
        "username": "usuario",
        "password": f"clav…({len('clave-muy-secreta')})",
        "max_concurrent_queries": 0,
    }
    assert shown["llm"]["api_key"] == f"sk-s…({len(secret)})"
    assert shown["llm"]["model"] == "gpt-prueba"


def test_incomplete_configuration_exits_with_code_two(monkeypatch, capsys):
    for name in list(__import__("os").environ):
        if name.startswith(("LLM_", "GRAPH_", "EMBEDDING_")):
            monkeypatch.delenv(name, raising=False)

    assert validation.main(["--dry-run"]) == 2

    errors = capsys.readouterr().err
    assert "Configuración incompleta" in errors
    assert "GRAPH_LLM_API_KEY" in errors


def test_unreadable_configuration_exits_with_code_two(monkeypatch, capsys):
    monkeypatch.setenv("GRAPH_DB_URL", "mysql://db")

    assert validation.main(["--dry-run"]) == 2

    assert "Configuración inválida" in capsys.readouterr().err
