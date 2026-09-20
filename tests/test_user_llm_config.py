"""User config persistence uses synthetic credentials and private temp directories."""
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.fixture
def store():
    # Import in tests so RED reports missing implementation, not collection errors.
    return importlib.import_module("src.web.user_llm_config")


@pytest.fixture
def path(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDCHAT_LLM_LOCK_DIR", str(tmp_path / "locks"))
    monkeypatch.setenv("MEDCHAT_USER_CONFIG_DIR", str(tmp_path / "private"))
    return tmp_path / "private" / "llm.env"


def test_default_is_deepseek_with_no_legacy_credentials(store, path, monkeypatch):
    monkeypatch.setenv("OPENAI_COMPATIBLE_API_KEY", "synthetic-legacy-secret")
    monkeypatch.setenv("MEDCHAT_LLM_PROVIDER", "modelscope")
    config = store.load_user_llm_config(path)
    assert config == dict(provider="openai_compatible", base_url="https://api.deepseek.com/chat/completions",
                          model_name="deepseek-v4-pro", api_key="", stream=True)
    assert not path.exists()


def test_path_is_independent_of_working_directory(store, path, tmp_path, monkeypatch):
    first = store.user_llm_config_path()
    repo = tmp_path / "checkout"
    repo.mkdir()
    (repo / ".git").touch()
    monkeypatch.chdir(repo)
    assert store.user_llm_config_path() == first == path


@pytest.mark.parametrize("location", ["relative", "repo", "linked"])
def test_rejects_unsafe_config_directory(store, tmp_path, monkeypatch, location):
    if location == "relative":
        root = Path("relative")
    else:
        root = tmp_path / location
        if location == "repo":
            root.mkdir()
            (root / ".git").touch()
            root = root / "settings"
        else:
            target = tmp_path / "target"
            target.mkdir()
            try:
                root.symlink_to(target, target_is_directory=True)
            except OSError:
                pytest.skip("directory symlinks unavailable")
    monkeypatch.setenv("MEDCHAT_USER_CONFIG_DIR", str(root))
    with pytest.raises(ValueError):
        store.user_llm_config_path()


def test_save_restart_and_new_process_preserve_configuration(store, path):
    raw = dict(store.default_user_llm_config(), api_key="synthetic-saved-secret")
    saved, signature = store.save_user_llm_config(path, raw)
    assert store.load_user_llm_config(path) == saved == raw
    assert store.user_llm_signature(path) == signature
    script = "from pathlib import Path; from src.web.user_llm_config import load_user_llm_config; c=load_user_llm_config(Path(__import__('sys').argv[1])); print(c['api_key']=='synthetic-saved-secret')"
    result = subprocess.run([sys.executable, "-B", "-c", script, str(path)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "True"


@pytest.mark.parametrize("change,expected", [({}, "synthetic-saved-secret"),
    ({"base_url": "https://other.example/chat/completions"}, ""),
    ({"provider": "custom"}, "")])
def test_blank_key_reuse_is_endpoint_and_provider_bound(store, path, change, expected):
    raw = dict(store.default_user_llm_config(), api_key="synthetic-saved-secret")
    store.save_user_llm_config(path, raw)
    raw.update(api_key="", **change)
    saved, _ = store.save_user_llm_config(path, raw)
    assert saved["api_key"] == store.load_user_llm_config(path)["api_key"] == expected


def test_explicit_clear_survives_restart_and_env(store, path, monkeypatch):
    raw = dict(store.default_user_llm_config(), api_key="synthetic-saved-secret")
    store.save_user_llm_config(path, raw)
    monkeypatch.setenv("OPENAI_COMPATIBLE_API_KEY", "synthetic-legacy-secret")
    store.save_user_llm_config(path, raw, clear_api_key=True)
    assert store.load_user_llm_config(path)["api_key"] == ""
    assert "synthetic-saved-secret" not in path.read_text()


def test_failed_replace_does_not_destroy_previous_config(store, path, monkeypatch):
    raw = dict(store.default_user_llm_config(), api_key="synthetic-saved-secret")
    store.save_user_llm_config(path, raw)
    before = path.read_bytes()
    def fail(*args, **kwargs):
        raise OSError("synthetic-sensitive-error")
    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises((OSError, ValueError)) as exc:
        store.save_user_llm_config(path, dict(raw, model_name="changed"))
    assert "synthetic-sensitive-error" not in str(exc.value)
    assert path.read_bytes() == before


@pytest.mark.parametrize("invalid", ["", "garbage", "MEDCHAT_LLM_PROVIDER=bogus\n", "MEDCHAT_LLM_PROVIDER=ollama\nMEDCHAT_LLM_PROVIDER=custom\n"])
def test_corrupt_file_fails_closed(store, path, invalid):
    store.save_user_llm_config(path, store.default_user_llm_config())
    path.write_text(invalid, encoding="utf-8")
    with pytest.raises(ValueError):
        store.load_user_llm_config(path)


def test_newline_key_rejected_without_modification(store, path):
    store.save_user_llm_config(path, store.default_user_llm_config())
    before = path.read_bytes()
    with pytest.raises(ValueError):
        store.save_user_llm_config(path, dict(store.default_user_llm_config(), api_key="fake\nEVIL=yes"))
    assert path.read_bytes() == before


def test_public_config_never_contains_key_fragments():
    from src.web.llm_runtime_config import public_llm_config
    config = public_llm_config({"api_key": "synthetic-secret-abcd"})
    assert config["api_key_hint"] == "********"
    assert "synt" not in json.dumps(config)
    assert "abcd" not in json.dumps(config)


def test_untrusted_boundary_is_rejected(store, path, monkeypatch):
    store.save_user_llm_config(path, store.default_user_llm_config())
    def untrusted(*args, **kwargs):
        raise ValueError("untrusted")
    monkeypatch.setattr(store, "capture_trusted_path_boundary", untrusted)
    with pytest.raises(ValueError):
        store.load_user_llm_config(path)
    with pytest.raises(ValueError):
        store.save_user_llm_config(path, store.default_user_llm_config())


@pytest.mark.skipif(os.name == "nt", reason="POSIX private modes")
def test_posix_permissions(store, path):
    store.save_user_llm_config(path, store.default_user_llm_config())
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    path.chmod(0o644)
    with pytest.raises(ValueError):
        store.load_user_llm_config(path)


def test_windows_readonly_public_acl_is_not_private(store):
    # ACCESS_ALLOWED_ACE: read permission only. Existing report trust permits it;
    # plaintext credentials must require a trusted reader too.
    ace = bytes([0, 0, 12, 0]) + (1).to_bytes(4, "little") + b"SID!"
    acl = bytes([2, 0, 20, 0, 1, 0, 0, 0]) + ace
    assert not store._private_windows_acl(acl, {"owner"}, sid_decoder=lambda *_: "everyone")
    assert store._private_windows_acl(acl, {"owner"}, sid_decoder=lambda *_: "owner")


def test_concurrent_process_writers_keep_one_complete_configuration(store, path):
    store.save_user_llm_config(path, store.default_user_llm_config())
    script = "from src.web.user_llm_config import *; import sys; c=default_user_llm_config(); c.update(model_name=sys.argv[2],api_key='synthetic-'+sys.argv[2]); save_user_llm_config(sys.argv[1],c)"
    processes = [subprocess.Popen([sys.executable, "-B", "-c", script, str(path), f"model-{i}"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for i in range(4)]
    for process in processes:
        stdout, stderr = process.communicate(timeout=30)
        assert process.returncode == 0, stderr
        assert not stdout
    final = store.load_user_llm_config(path)
    assert final["api_key"] == "synthetic-" + final["model_name"]


def test_key_reuse_compares_normalized_request_endpoint(store):
    current = dict(store.default_user_llm_config(), api_key="synthetic-current")
    equivalent = dict(current, api_key="", base_url="https://API.DEEPSEEK.COM:443/chat/completions/")
    assert store.resolve_user_llm_request(equivalent, current)["api_key"] == "synthetic-current"


@pytest.mark.parametrize("url", ["https://u:password@example.com", "https://example.com?key=secret", "https://example.com/#fragment", "file:///local/path"])
def test_rejects_credentials_in_url(store, path, url):
    with pytest.raises(ValueError):
        store.save_user_llm_config(path, dict(store.default_user_llm_config(), base_url=url))


def test_file_reparse_point_is_rejected(store, path, monkeypatch):
    store.save_user_llm_config(path, store.default_user_llm_config())
    original = Path.lstat
    def reparse(target, *args, **kwargs):
        info = original(target, *args, **kwargs)
        if target == path:
            from types import SimpleNamespace
            return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400)
        return info
    monkeypatch.setattr(Path, "lstat", reparse)
    with pytest.raises(ValueError):
        store.load_user_llm_config(path)


def test_explicit_legacy_retirement_preserves_unrelated_settings(tmp_path):
    from src.web import llm_runtime_config as legacy
    env = tmp_path / "legacy.env"
    cache = tmp_path / "runtime.json"
    env.write_text("# deployment\nMEDCHAT_PORT=6001\nRXN_API_KEY=synthetic-unrelated\nOPENAI_COMPATIBLE_API_KEY=synthetic-retired\nOPENAI_COMPATIBLE_MODEL=legacy-model\nMEDCHAT_LLM_PROVIDER=openai_compatible\nOLLAMA_MODEL=gmm-llama:latest\n", encoding="utf-8")
    cache.write_text('{"model_name":"legacy-model"}', encoding="utf-8")
    legacy.retire_legacy_ui_llm_config(env, cache)
    content = env.read_text(encoding="utf-8")
    assert "synthetic-retired" not in content
    assert "OPENAI_COMPATIBLE" not in content
    assert "MEDCHAT_LLM_PROVIDER" not in content
    assert "RXN_API_KEY=synthetic-unrelated" in content
    assert "MEDCHAT_PORT=6001" in content
    assert "OLLAMA_MODEL=gmm-llama:latest" in content
    assert json.loads(cache.read_text()) == {}
    assert not list(tmp_path.glob("*.bak"))
@pytest.mark.parametrize("field,value", [
    ("model_name", "   "),
    ("model_name", "abc\u2028def"),
    ("api_key", "synthetic\u2029secret"),
    ("model_name", "abc\x85"),
    ("api_key", "synthetic\x0bsecret"),
])
def test_invalid_serialized_values_preserve_saved_config(store, tmp_path, field, value):
    path = tmp_path / "private" / "llm.env"
    saved = dict(store.default_user_llm_config(), api_key="synthetic-existing")
    store.save_user_llm_config(path, saved)
    before = path.read_bytes()
    with pytest.raises(ValueError):
        store.save_user_llm_config(path, dict(saved, **{field: value}))
    assert path.read_bytes() == before
    assert store.load_user_llm_config(path) == saved
