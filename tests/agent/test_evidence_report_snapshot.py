from copy import deepcopy
from contextlib import closing

import pytest

from evidence_report_fixture import execute, snapshot


def test_snapshot_owned_readonly_and_current(tmp_path):
    store, execution, events = execute(tmp_path)
    before = store.get_run(execution["trace_id"])
    with closing(store._connect()) as connection:
        before_checkpoints = [tuple(row) for row in connection.execute("SELECT * FROM agent_checkpoints ORDER BY rowid")]
    snap = snapshot(store, execution, events)
    assert snap is not None
    assert store.get_run(execution["trace_id"]) == before
    with closing(store._connect()) as connection:
        assert [tuple(row) for row in connection.execute("SELECT * FROM agent_checkpoints ORDER BY rowid")] == before_checkpoints
    assert store.get_scientific_report_snapshot(execution["trace_id"], session_id="other",
        references=[events[0]["reference"]]) is None
    bad = deepcopy(events)
    bad[0]["reference"]["ordered_keys"].reverse()
    assert snapshot(store, execution, bad) is None
    store.update_run_status(execution["trace_id"], "failed")
    assert snapshot(store, execution, events) is None


@pytest.mark.parametrize("mutation", ["expired", "wrong_pointer", "missing_owner", "latest_changed", "duplicate"])
def test_snapshot_refuses_stale_or_unowned_views(tmp_path, monkeypatch, mutation):
    import src.agent.persistence.scientific_references as boundary
    store, execution, events = execute(tmp_path)
    if mutation == "expired":
        monkeypatch.setattr(boundary.time, "time", lambda: 999999999999)
    elif mutation == "wrong_pointer":
        events[0]["reference"]["revision"] = "0" * 64
    elif mutation == "missing_owner":
        with closing(store._connect()) as connection, connection:
            connection.execute("UPDATE agent_runs SET session_id=NULL")
    elif mutation == "latest_changed":
        with closing(store._connect()) as connection, connection:
            connection.execute("UPDATE agent_checkpoints SET tool_version='new' WHERE step_id='properties'")
    elif mutation == "duplicate":
        events.append(deepcopy(events[0]))
    assert snapshot(store, execution, events) is None
