import pytest


def test_task_database_permission_failure_aborts_connect(tmp_path, monkeypatch):
    from src.task_runtime import database

    real_os = database.os

    class PosixOsProxy:
        name = "posix"

        def __getattr__(self, name):
            return getattr(real_os, name)

    monkeypatch.setattr(database, "os", PosixOsProxy())

    def deny_chmod(self, mode):
        raise PermissionError("synthetic permission failure")

    monkeypatch.setattr(database.Path, "chmod", deny_chmod)
    with pytest.raises(PermissionError, match="synthetic permission failure"):
        database.connect(tmp_path / "runtime" / "tasks.sqlite")
