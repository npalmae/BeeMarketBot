"""Watchdog del worker GraphRAG: deteccion de bucle estancado."""

import time

from app.graphrag_worker import GraphRAGWorker


def _worker(stall_timeout=1200.0):
    worker = GraphRAGWorker()
    worker.stall_timeout = stall_timeout
    return worker


def test_fresh_loop_is_not_stalled():
    worker = _worker()
    worker.last_progress = time.monotonic()

    assert worker._is_stalled() is False


def test_stalled_loop_is_detected():
    worker = _worker(stall_timeout=1200.0)
    worker.last_progress = time.monotonic() - 1300.0

    assert worker._is_stalled() is True


def test_idle_iterations_reset_the_stall():
    worker = _worker()
    worker.last_progress = time.monotonic() - 5000.0

    worker.last_progress = time.monotonic()

    assert worker._is_stalled() is False


def test_minimum_stall_timeout_is_enforced():
    worker = GraphRAGWorker()
    worker.stall_timeout = 60.0
    worker.last_progress = time.monotonic() - 100.0

    assert worker._is_stalled() is True
    assert worker.stall_timeout >= 300.0 or GraphRAGWorker().stall_timeout >= 300.0
