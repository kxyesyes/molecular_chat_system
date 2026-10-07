import os
from pathlib import Path


def test_entrypoints_share_one_env_file_loader():
    from main import load_env_file as main_loader
    from scripts.health_check import load_env_file as health_loader
    from src.system.env import load_env_file as canonical_loader
    from src.web.app import load_env_file as app_loader

    assert health_loader is canonical_loader
    assert main_loader.__globals__["_load_env_file"] is canonical_loader
    assert app_loader.__globals__["_load_env_file"] is canonical_loader


def test_env_file_loader_preserves_existing_environment_values(tmp_path, monkeypatch):
    from src.system.env import load_env_file

    path = Path(tmp_path) / ".env"
    path.write_text(
        "# comment\nFROM_FILE=file-value\nKEEP_EXISTING=file-value\n\n"
        "QUOTED=\"quoted value\"\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("KEEP_EXISTING", "process-value")

    load_env_file(path)

    assert os.environ.get("FROM_FILE") == "file-value"
    assert os.environ.get("KEEP_EXISTING") == "process-value"
    assert os.environ.get("QUOTED") == "quoted value"


def test_shared_parser_exposes_storage_errors(tmp_path, monkeypatch):
    from src.system import env as shared

    path = Path(tmp_path) / ".env"
    path.write_text("VALUE=one\n", encoding="utf-8")
    def fail_read(*_args, **_kwargs):
        raise PermissionError("synthetic-denied")
    monkeypatch.setattr(Path, "read_text", fail_read)

    import pytest
    with pytest.raises(PermissionError, match="synthetic-denied"):
        shared.load_env_file(path)


def test_entrypoint_wrappers_keep_logging_policy(monkeypatch, caplog):
    from main import load_env_file as main_loader
    from src.web.app import load_env_file as app_loader

    def fail(_path):
        raise PermissionError("synthetic-denied")

    monkeypatch.setitem(main_loader.__globals__, "_load_env_file", fail)
    monkeypatch.setitem(app_loader.__globals__, "_load_env_file", fail)
    main_loader("synthetic.env")
    app_loader("synthetic.env")
    assert caplog.text.count("Unable to load env file synthetic.env") == 2
