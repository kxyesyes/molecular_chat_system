import os
from pathlib import Path

import pytest
from fastapi import HTTPException

from src.docking.history_index import (
    build_history_record,
    get_history_record,
    read_history_page,
    upsert_history_record,
)
from src.web.routes.docking_routes import _owned_history


def _request(session_id):
    return type("Request", (), {"scope": {"agent_session_id": session_id}})()


def test_history_listing_and_lookup_are_owner_scoped(tmp_path):
    work_dir = tmp_path / "docking"
    for job_id, owner in (("mine", "session-a"), ("foreign", "session-b")):
        job_dir = work_dir / f"docking_{job_id}"
        job_dir.mkdir(parents=True)
        upsert_history_record(
            work_dir,
            build_history_record(job_dir, job_id=job_id, owner_session_id=owner),
        )

    rows, total, _, _ = read_history_page(work_dir, owner_session_id="session-a")
    assert total == 1
    assert [row["job_id"] for row in rows] == ["mine"]
    assert get_history_record(work_dir, "mine", owner_session_id="session-a")["job_id"] == "mine"
    assert get_history_record(work_dir, "foreign", owner_session_id="session-a") is None


def test_ownerless_history_is_not_owned_by_any_session(tmp_path):
    work_dir = tmp_path / "docking"
    job_dir = work_dir / "docking_legacy"
    job_dir.mkdir(parents=True)
    upsert_history_record(work_dir, build_history_record(job_dir, job_id="legacy"))

    assert get_history_record(work_dir, "legacy", owner_session_id="session-a") is None
    with pytest.raises(HTTPException) as error:
        _owned_history(_request("session-a"), str(work_dir), "legacy")
    assert error.value.status_code == 404


def test_owned_history_rejects_missing_server_session(tmp_path):
    work_dir = tmp_path / "docking"
    job_dir = work_dir / "docking_job"
    job_dir.mkdir(parents=True)
    upsert_history_record(
        work_dir,
        build_history_record(job_dir, job_id="job", owner_session_id="session-a"),
    )
    with pytest.raises(HTTPException) as error:
        _owned_history(type("Request", (), {"scope": {}})(), str(work_dir), "job")


@pytest.mark.skipif(os.name != "posix", reason="POSIX permissions only")
def test_history_index_and_lock_are_private(tmp_path):
    import stat

    work_dir = tmp_path / "docking"
    work_dir.mkdir()
    upsert_history_record(
        work_dir,
        build_history_record(work_dir / "docking_job", job_id="job", owner_session_id="session-a"),
    )
    index = work_dir / "docking_history_index.json"
    lock = work_dir / "docking_history_index.lock"
    assert stat.S_IMODE(work_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(index.stat().st_mode) == 0o600
    assert stat.S_IMODE(lock.stat().st_mode) == 0o600
    assert error.value.status_code == 401
