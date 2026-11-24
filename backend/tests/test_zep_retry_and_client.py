import os
from pathlib import Path

import pytest

from app.graph_memory.errors import (
    AuthError,
    BadRequestError,
    ConfigError,
    GraphMemoryError,
    NotFoundError,
    RateLimitError,
    ServerError,
)
from app.graph_memory.settings import GraphMemorySettings
from app.utils import zep

NO_SLEEP = lambda _seconds: None  # noqa: E731


def test_permanent_errors_fail_without_retry():
    calls = []

    def operation():
        calls.append(True)
        raise BadRequestError("bad query", status_code=400)

    with pytest.raises(GraphMemoryError) as error:
        zep.call_zep_read_with_retry(
            operation,
            operation_name="permanent failure",
            sleep=NO_SLEEP,
        )

    assert error.value.status_code == 400
    assert len(calls) == 1


@pytest.mark.parametrize(
    "failure",
    [
        AuthError("unauthorized", status_code=401),
        NotFoundError("missing", status_code=404),
        GraphMemoryError("sin código de estado"),
    ],
)
def test_non_transient_errors_are_not_retried(failure):
    calls = []

    def operation():
        calls.append(True)
        raise failure

    with pytest.raises(GraphMemoryError):
        zep.call_zep_read_with_retry(operation, operation_name="fatal", sleep=NO_SLEEP)

    assert len(calls) == 1


def test_rate_limit_retry_respects_retry_after():
    calls = []
    sleeps = []

    def operation():
        calls.append(True)
        if len(calls) == 1:
            raise RateLimitError("slow down", status_code=429, retry_after=7.0)
        return "ok"

    result = zep.call_zep_read_with_retry(
        operation,
        operation_name="rate limited read",
        sleep=sleeps.append,
    )

    assert result == "ok"
    assert len(calls) == 2
    assert sleeps == [7.0]


def test_server_errors_retry_with_backoff_up_to_the_cap():
    calls = []
    sleeps = []

    def operation():
        calls.append(True)
        if len(calls) <= 3:
            raise ServerError("unavailable", status_code=503)
        return "ok"

    result = zep.call_zep_read_with_retry(
        operation,
        operation_name="unavailable read",
        max_attempts=4,
        initial_delay=10.0,
        max_delay=15.0,
        sleep=sleeps.append,
    )

    assert result == "ok"
    assert sleeps == [10.0, 15.0, 15.0]


@pytest.mark.parametrize("failure", [ConnectionError("reset"), TimeoutError("timed out"), OSError("broken pipe")])
def test_transport_errors_are_retried(failure):
    calls = []

    def operation():
        calls.append(True)
        if len(calls) == 1:
            raise failure
        return "ok"

    assert zep.call_zep_read_with_retry(operation, operation_name="flaky", sleep=NO_SLEEP) == "ok"
    assert len(calls) == 2


def test_retry_gives_up_after_max_attempts():
    calls = []

    def operation():
        calls.append(True)
        raise ServerError("down", status_code=503)

    with pytest.raises(ServerError, match="down"):
        zep.call_zep_read_with_retry(
            operation, operation_name="down", max_attempts=3, sleep=NO_SLEEP
        )

    assert len(calls) == 3


def test_retry_policy_rejects_a_non_positive_attempt_count():
    with pytest.raises(ValueError, match="max_attempts"):
        zep.call_zep_read_with_retry(lambda: None, operation_name="x", max_attempts=0)


def test_search_limits_are_normalized():
    assert zep.normalize_zep_search_query("  ¿quién sabe?  ") == "¿quién sabe?"
    assert len(zep.normalize_zep_search_query("q" * 500)) == zep.MAX_ZEP_SEARCH_QUERY_CHARS
    assert zep.normalize_zep_search_limit(999) == zep.MAX_ZEP_SEARCH_RESULTS
    assert zep.normalize_zep_search_limit("7") == 7
    with pytest.raises(ValueError):
        zep.normalize_zep_search_query("   ")
    with pytest.raises(ValueError):
        zep.normalize_zep_search_limit(0)
    with pytest.raises(ValueError):
        zep.normalize_zep_search_limit("many")


def test_zep_client_is_shared_and_ignores_the_legacy_arguments(monkeypatch):
    shared = object()
    monkeypatch.setattr(zep, "get_shared_client", lambda: shared)

    assert zep.get_zep_client(" test-key ", timeout=12) is shared
    assert zep.get_zep_client() is shared


def test_invalid_graph_database_url_is_rejected_at_configuration_time():
    with pytest.raises(ConfigError, match="GRAPH_DB_URL"):
        GraphMemorySettings.from_env({"GRAPH_DB_URL": "https://example.invalid"})


def test_zep_client_requires_a_model_key(monkeypatch):
    for name in list(os.environ):
        if name.startswith(("LLM_", "GRAPH_", "EMBEDDING_")):
            monkeypatch.delenv(name, raising=False)

    with pytest.raises(ConfigError, match="API_KEY"):
        zep.get_zep_client()


def test_timeout_policy_is_not_exposed_in_env_example():
    env_example = Path(__file__).resolve().parents[2] / ".env.example"
    contents = env_example.read_text(encoding="utf-8")

    assert "ZEP_" not in contents
    assert "REQUEST_TIMEOUT" not in contents
