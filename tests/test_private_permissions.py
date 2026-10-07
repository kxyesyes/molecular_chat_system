import os

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
