from __future__ import annotations

import sqlite3

import pytest

from src.agent.persistence import SQLiteAgentStateStore, redact_sensitive
from src.agent.persistence.redaction import sanitize_bounded, sanitize_sensitive_text


def test_sqlite_store_round_trips_run_checkpoint_and_ordered_events(tmp_path):
    store = SQLiteAgentStateStore(tmp_path / "agent_state.sqlite3")
    store.start_run(
        {
            "trace_id": "trace-1",
            "status": "running",
            "skill_name": "comprehensive_evaluation",
            "query": "全面分析 CCO",
            "workflow_version": "1",
            "idempotency_key": "request-1",
        }
    )

    first_sequence = store.append_event(
        {
            "trace_id": "trace-1",
            "event": "task_started",
            "message": "started",
        }
    )
    second_sequence = store.append_event(
        {
            "trace_id": "trace-1",
            "event": "tool_started",
            "tool": "property_calculator",
            "message": "running",
        }
    )
    store.save_checkpoint(
        {
            "trace_id": "trace-1",
            "step_id": "properties",
            "workflow_version": "1",
            "status": "succeeded",
            "input_hash": "hash-1",
            "tool_name": "property_calculator",
            "tool_version": "1",
            "adapter_version": "1",
            "model_version": "",
            "output": {"molecular_weight": 46.07},
        }
    )

    assert (first_sequence, second_sequence) == (1, 2)
    assert store.get_run("trace-1")["skill_name"] == "comprehensive_evaluation"
    assert store.get_events("trace-1")[1]["tool"] == "property_calculator"
    checkpoint = store.latest_checkpoint("trace-1", "properties")
    assert checkpoint["status"] == "succeeded"
    assert checkpoint["output"] == {"molecular_weight": 46.07}

    reopened = SQLiteAgentStateStore(tmp_path / "agent_state.sqlite3")
    assert reopened.latest_checkpoint("trace-1", "properties")["input_hash"] == "hash-1"


def test_store_redacts_nested_secrets_before_persisting(tmp_path):
    store = SQLiteAgentStateStore(tmp_path / "agent_state.sqlite3")
    store.start_run(
        {
            "trace_id": "trace-secret",
            "status": "running",
            "query": "test",
            "metadata": {
                "api_key": "secret-value",
                "nested": {"authorization": "Bearer secret-token"},
            },
        }
    )

    run = store.get_run("trace-secret")

    assert run["metadata"]["api_key"] == "[REDACTED]"
    assert run["metadata"]["nested"]["authorization"] == "[REDACTED]"
    assert "secret-value" not in (tmp_path / "agent_state.sqlite3").read_bytes().decode(
        "latin1", errors="ignore"
    )


def test_bounded_sanitizer_uses_context_without_redacting_ordinary_short_words():
    text, changed = sanitize_sensitive_text(
        "token bucket is ordinary; api_key=abcd123456 password:hunter2",
        max_chars=256,
    )

    assert changed is True
    assert "token bucket is ordinary" in text
    assert text.count("[redacted]") == 2
    assert "abcd123456" not in text
    assert "hunter2" not in text

    sanitized, changed = sanitize_bounded(
        {"safe": "value", "nested": {"client_secret": "short-secret"}},
        max_depth=1,
        max_items=8,
        max_text_chars=32,
    )
    assert changed is True
    assert sanitized == {"safe": "value", "nested": "[redacted]"}


def test_text_sanitizer_redacts_json_like_credential_fields():
    text, changed = sanitize_sensitive_text(
        '{"api_key":"abcd123456","client_secret": "json-secret"}',
        max_chars=256,
    )

    assert changed is True
    assert "abcd123456" not in text
    assert "json-secret" not in text
    assert text.count("[redacted]") == 2


def test_text_sanitizer_consumes_authorization_and_spaced_absolute_paths():
    sensitive = (
        "header-secret-456",
        "dXNlcjpwYXNz",
        "Alice Doe/private",
        "Doe/private",
        r"Alice Doe\private",
        r"Doe\private",
    )
    text, changed = sanitize_sensitive_text(
        "Authorization = Bearer header-secret-456; "
        "'authorization' : 'Basic dXNlcjpwYXNz'; "
        'POSIX "/srv/Alice Doe/private/result.txt" keep-posix; '
        r'DRIVE "C:\Users\Alice Doe\private\result.txt" keep-drive; '
        r'UNC "\\server\share\Alice Doe\private\result.txt" keep-unc; '
        r'DEVICE "\\?\C:\Users\Alice Doe\private\result.txt" keep-device',
        max_chars=1024,
    )

    assert changed is True
    for value in sensitive:
        assert value not in text
    assert text.count("[redacted]") == 6
    for ordinary in (
        "keep-posix",
        "keep-drive",
        "keep-unc",
        "keep-device",
    ):
        assert ordinary in text

    ordinary, ordinary_changed = sanitize_sensitive_text(
        "Authorization design remains ordinary prose",
        max_chars=128,
    )
    assert ordinary_changed is False
    assert ordinary == "Authorization design remains ordinary prose"


def test_state_store_merges_shadow_metadata_without_losing_request_metadata(tmp_path):
    store = SQLiteAgentStateStore(tmp_path / "state.sqlite3")
    store.start_run(
        {
            "trace_id": "shadow-persist",
            "status": "running",
            "metadata": {"request_tag": "keep-me"},
        }
    )

    store.update_run_metadata(
        "shadow-persist",
        {
            "harness_shadow": {
                "status": "matched",
                "plan_fingerprint": "a" * 64,
                "api_key": "must-not-persist",
            }
        },
    )

    metadata = store.get_run("shadow-persist")["metadata"]
    assert metadata["request_tag"] == "keep-me"
    assert metadata["harness_shadow"]["status"] == "matched"
    assert metadata["harness_shadow"]["api_key"] == "[REDACTED]"

    with pytest.raises(KeyError, match="missing-trace"):
        store.update_run_metadata("missing-trace", {"harness_shadow": {}})


def test_memory_admission_requires_validated_structured_content(tmp_path):
    store = SQLiteAgentStateStore(tmp_path / "agent_state.sqlite3")

    memory_id = store.add_memory(
        {
            "user_id": "user-1",
            "category": "confirmed_preference",
            "content": {"preferred_target": "PDE5"},
            "tags": ["PDE5", "preference"],
            "confidence": 1.0,
            "validated": True,
            "provenance": {"source": "user_confirmation"},
        }
    )

    assert memory_id
    memories = store.search_memories(user_id="user-1", tags=["PDE5"])
    assert memories[0]["content"] == {"preferred_target": "PDE5"}

    with pytest.raises(ValueError, match="validated"):
        store.add_memory(
            {
                "category": "workflow_summary",
                "content": {"claim": "unverified"},
                "validated": False,
            }
        )

    with pytest.raises(ValueError, match="credential"):
        store.add_memory(
            {
                "category": "confirmed_preference",
                "content": {"token": "sk-example-secret"},
                "validated": True,
            }
        )


def test_store_records_artifacts_tool_executions_and_routing_feedback(tmp_path):
    store = SQLiteAgentStateStore(tmp_path / "agent_state.sqlite3")
    store.start_run({"trace_id": "trace-2", "status": "running", "query": "CCO"})

    store.record_tool_execution(
        {
            "trace_id": "trace-2",
            "step_id": "properties",
            "tool_name": "property_calculator",
            "status": "succeeded",
            "input": {"smiles": "CCO"},
            "output": {"qed": 0.4},
            "elapsed_ms": 12,
        }
    )
    artifact_id = store.register_artifact(
        {
            "trace_id": "trace-2",
            "step_id": "docking",
            "path": "outputs/docking/pose.pdbqt",
            "sha256": "abc123",
            "media_type": "chemical/x-pdbqt",
            "validated": True,
        }
    )
    feedback_id = store.add_routing_feedback(
        {
            "trace_id": "trace-2",
            "query": "全面分析 CCO",
            "selected_skill": "admet_assessment",
            "corrected_skill": "comprehensive_evaluation",
            "accepted": True,
        }
    )

    assert artifact_id
    assert feedback_id
    assert store.get_tool_executions("trace-2")[0]["output"] == {"qed": 0.4}
    assert store.get_routing_feedback("全面分析 CCO")[0]["corrected_skill"] == (
        "comprehensive_evaluation"
    )


def test_store_stable_ids_make_event_execution_and_checkpoint_idempotent(
    tmp_path,
):
    db_path = tmp_path / "stable-ids.sqlite3"
    store = SQLiteAgentStateStore(db_path)
    event = {
        "id": "event-stable",
        "trace_id": "stable-trace",
        "event": "tool_completed",
        "message": "done",
    }
    execution = {
        "id": "execution-stable",
        "trace_id": "stable-trace",
        "step_id": "properties",
        "tool_name": "property_calculator",
        "status": "succeeded",
    }
    checkpoint = {
        "id": "checkpoint-stable",
        "trace_id": "stable-trace",
        "step_id": "properties",
        "status": "succeeded",
    }

    assert store.append_event(event) == store.append_event(event) == 1
    assert store.record_tool_execution(execution) == "execution-stable"
    assert store.record_tool_execution(execution) == "execution-stable"
    store.save_checkpoint(checkpoint)
    store.save_checkpoint(checkpoint)

    with sqlite3.connect(db_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM agent_events WHERE id = ?",
            ("event-stable",),
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM tool_executions WHERE id = ?",
            ("execution-stable",),
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM agent_checkpoints WHERE id = ?",
            ("checkpoint-stable",),
        ).fetchone()[0] == 1


def test_redact_sensitive_handles_keys_and_credential_shaped_values():
    redacted = redact_sensitive(
        {
            "password": "plain",
            "safe": "value",
            "items": [{"note": "Bearer abcdefghijklmnop"}, "sk-test-secret"],
        }
    )

    assert redacted["password"] == "[REDACTED]"
    assert redacted["safe"] == "value"
    assert redacted["items"] == [{"note": "[REDACTED]"}, "[REDACTED]"]


def test_store_creates_all_declared_tables(tmp_path):
    db_path = tmp_path / "agent_state.sqlite3"
    SQLiteAgentStateStore(db_path)

    with sqlite3.connect(db_path) as connection:
        names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }

    assert {
        "agent_runs",
        "agent_checkpoints",
        "agent_events",
        "tool_executions",
        "workflow_artifacts",
        "long_term_memories",
        "routing_feedback",
        "evaluation_runs",
        "evaluation_cases",
        "evaluation_results",
    } <= names
