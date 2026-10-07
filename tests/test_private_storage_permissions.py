import os
import stat

import pytest


pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX permissions only")


def _mode(path):
    return stat.S_IMODE(path.stat().st_mode)


def test_task_database_is_private(tmp_path):
    from src.task_runtime.database import connect

    db_path = tmp_path / "runtime" / "tasks.sqlite"
    with connect(db_path) as connection:
        connection.execute("CREATE TABLE probe (value TEXT)")

    assert _mode(db_path.parent) == 0o700
    assert _mode(db_path) == 0o600


def test_agent_state_database_is_private(tmp_path):
    from src.agent.persistence.sqlite_store import SQLiteAgentStateStore

    db_path = tmp_path / "agent" / "state.sqlite"
    SQLiteAgentStateStore(db_path)

    assert _mode(db_path.parent) == 0o700
    assert _mode(db_path) == 0o600


def test_browser_session_database_is_private(tmp_path):
    from src.web.agent_session import AgentSessionStore

    db_path = tmp_path / "sessions" / "agent.sqlite"
    store = AgentSessionStore(db_path)
    store.issue()

    assert _mode(db_path.parent) == 0o700
    assert _mode(db_path) == 0o600


def test_molecular_design_saved_results_are_private(tmp_path):
    from src.molecular_design.storage import DesignStorage

    save_dir = tmp_path / "design" / "session"
    DesignStorage(save_dir).save_molecule("CCO", {})

    assert _mode(save_dir) == 0o700
    assert _mode(save_dir / "saved_molecules.csv") == 0o600


def test_target_database_sidecars_are_private(tmp_path):
    from src.target_search.database import get_connection

    db_path = tmp_path / "data" / "target_db" / "target_database.sqlite"
    connection = get_connection(tmp_path)
    try:
        connection.execute("CREATE TABLE probe (value TEXT)")
        connection.commit()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        connection.close()

    assert _mode(db_path.parent) == 0o700
    assert _mode(db_path) == 0o600
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = db_path.with_name(db_path.name + suffix)
        if sidecar.exists():
            assert _mode(sidecar) == 0o600
