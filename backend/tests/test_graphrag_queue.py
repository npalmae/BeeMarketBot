from app.config import Config
from app.services.graphrag_queue import GraphRAGQueue


def _use_queue(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "UPLOAD_FOLDER", str(tmp_path))
    monkeypatch.delenv("GRAPHRAG_QUEUE_DIR", raising=False)


def _activity(content="hola"):
    return {
        "agent_id": 7,
        "agent_name": "Ada",
        "action_type": "CREATE_POST",
        "action_args": {"content": content},
        "round": 1,
        "timestamp": "2026-01-02T03:04:05+00:00",
        "success": True,
    }


def test_build_job_survives_worker_restart(monkeypatch, tmp_path):
    _use_queue(monkeypatch, tmp_path)
    GraphRAGQueue.enqueue_build(
        task_id="task-1",
        project_id="proj-1",
        graph_name="Prueba",
        chunk_size=500,
        chunk_overlap=50,
        locale="es",
    )

    first_claim = GraphRAGQueue.claim_build()
    assert first_claim["job_id"] == "task-1"
    assert first_claim["attempts"] == 1

    assert GraphRAGQueue.recover_builds() == 1
    recovered = GraphRAGQueue.claim_build()
    assert recovered["job_id"] == "task-1"
    assert recovered["attempts"] == 2

    GraphRAGQueue.finish_build(
        "task-1",
        success=True,
        result={"graph_id": "graph-1"},
    )
    assert GraphRAGQueue.claim_build() is None


def test_activity_is_deduplicated_and_session_completes(monkeypatch, tmp_path):
    _use_queue(monkeypatch, tmp_path)
    session_id = GraphRAGQueue.create_session(
        simulation_id="sim-1",
        graph_id="graph-1",
        locale="es",
    )
    assert GraphRAGQueue.enqueue_activity(
        session_id=session_id,
        platform="twitter",
        data=_activity(),
        source_event_id="twitter:0",
    )
    assert not GraphRAGQueue.enqueue_activity(
        session_id=session_id,
        platform="twitter",
        data=_activity(),
        source_event_id="twitter:0",
    )

    GraphRAGQueue.close_session(session_id)
    batch = GraphRAGQueue.claim_activity_batch(batch_size=5)
    GraphRAGQueue.mark_batch_sending(session_id, batch["batch_id"])
    GraphRAGQueue.complete_activity_batch(
        session_id,
        batch["batch_id"],
        result={"episode_count": 1},
    )
    assert GraphRAGQueue.finalize_closed_sessions() == 1

    snapshot = GraphRAGQueue.session_snapshot(session_id)
    assert snapshot["status"] == "completed"
    assert snapshot["event_counts"] == {
        "pending": 0,
        "processing": 0,
        "completed": 1,
        "failed": 0,
    }


def test_claimed_activity_is_safely_requeued_after_restart(monkeypatch, tmp_path):
    _use_queue(monkeypatch, tmp_path)
    session_id = GraphRAGQueue.create_session(
        simulation_id="sim-1",
        graph_id="graph-1",
        locale="es",
    )
    GraphRAGQueue.enqueue_activity(
        session_id=session_id,
        platform="reddit",
        data=_activity(),
        source_event_id="reddit:12",
    )
    GraphRAGQueue.claim_activity_batch(batch_size=5)

    recovery = GraphRAGQueue.recover_sessions()

    assert recovery == {"recovered_events": 1, "ambiguous_events": 0}
    snapshot = GraphRAGQueue.session_snapshot(session_id)
    assert snapshot["status"] == "queued"
    assert snapshot["event_counts"]["pending"] == 1


def test_sending_activity_is_not_replayed_after_restart(monkeypatch, tmp_path):
    _use_queue(monkeypatch, tmp_path)
    session_id = GraphRAGQueue.create_session(
        simulation_id="sim-1",
        graph_id="graph-1",
        locale="es",
    )
    GraphRAGQueue.enqueue_activity(
        session_id=session_id,
        platform="twitter",
        data=_activity(),
        source_event_id="twitter:99",
    )
    batch = GraphRAGQueue.claim_activity_batch(batch_size=5)
    GraphRAGQueue.mark_batch_sending(session_id, batch["batch_id"])

    recovery = GraphRAGQueue.recover_sessions()

    assert recovery == {"recovered_events": 0, "ambiguous_events": 1}
    snapshot = GraphRAGQueue.session_snapshot(session_id)
    assert snapshot["status"] == "failed"
    assert "no se volvió a enviar" in snapshot["error"]
    assert snapshot["event_counts"]["failed"] == 1
