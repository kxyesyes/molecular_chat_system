import os
from pathlib import Path

import pytest

from src.task_runtime.private_permissions import restrict_private_path


def test_private_permissions_harden_existing_directory_and_file(tmp_path):
    private_dir = tmp_path / "private"
    private_dir.mkdir()
    private_file = private_dir / "state.sqlite"
    private_file.write_bytes(b"synthetic")

    restrict_private_path(private_dir, 0o700, required=True)
    restrict_private_path(private_file, 0o600, required=True)

    if os.name == "posix":
        assert private_dir.stat().st_mode & 0o077 == 0
        assert private_file.stat().st_mode & 0o077 == 0


@pytest.mark.parametrize("required", [False, True])
@pytest.mark.parametrize("path_kind", ["missing", "not_directory", "invalid"])
def test_missing_or_invalid_path_retains_optional_and_required_behavior(tmp_path, required, path_kind):
    target = tmp_path / "absent"
    if path_kind == "not_directory":
        target.write_bytes(b"synthetic")
        target = target / "child"
    elif path_kind == "invalid":
        target = Path(str(tmp_path) + "\0invalid")
    if required:
        with pytest.raises(FileNotFoundError):
            restrict_private_path(target, 0o600, required=True)
    else:
        restrict_private_path(target, 0o600)


@pytest.mark.parametrize("required", [False, True])
def test_unreadable_path_still_propagates_access_error(tmp_path, monkeypatch, required):
    target = tmp_path / "private"

    def denied(*args, **kwargs):
        raise PermissionError("synthetic access denied")

    monkeypatch.setattr(Path, "stat", denied)
    with pytest.raises(PermissionError, match="synthetic access denied"):
        restrict_private_path(target, 0o600, required=required)
