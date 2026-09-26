"""Never allow UI configuration tests/imports to touch real user credentials."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest


def pytest_configure(config):
    # app.py constructs its global app during collection, before fixtures run.
    config._llm_test_directory = TemporaryDirectory(prefix="medchat-test-config-")
    root = Path(config._llm_test_directory.name)
    paths = {
        "MEDCHAT_USER_CONFIG_DIR": root / "user-config",
        "MEDCHAT_LLM_LOCK_DIR": root / "llm-locks",
        "MEDCHAT_ENV_FILE": root / "not-loaded.env",
        "MEDCHAT_AGENT_SESSION_DB": root / "agent-sessions.sqlite",
    }
    config._llm_test_environment = {key: os.environ.get(key) for key in paths}
    os.environ.update({key: str(path) for key, path in paths.items()})


def pytest_unconfigure(config):
    for key, value in getattr(config, "_llm_test_environment", {}).items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    directory = getattr(config, "_llm_test_directory", None)
    if directory is not None:
        directory.cleanup()


@pytest.fixture(autouse=True)
def isolated_user_llm_configuration(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDCHAT_USER_CONFIG_DIR", str(tmp_path / "user-config"))
    monkeypatch.setenv("MEDCHAT_LLM_LOCK_DIR", str(tmp_path / "llm-locks"))
    monkeypatch.setenv("MEDCHAT_ENV_FILE", str(tmp_path / "not-loaded.env"))
    monkeypatch.setenv("MEDCHAT_AGENT_SESSION_DB", str(tmp_path / "agent-sessions.sqlite"))


@pytest.fixture
def rag_ip_loader(request, monkeypatch):
    """Opt-in real IP loader variants; never assume a FAISS Python subclass."""
    variant = getattr(request, "param", "native")
    assert variant in {"native", "base-flat-ip"}
    if variant == "base-flat-ip":
        import faiss
        import numpy as np

        read = faiss.read_index

        def load(path):
            loaded = read(path)
            assert loaded.metric_type == faiss.METRIC_INNER_PRODUCT
            base = faiss.IndexFlat(loaded.d, faiss.METRIC_INNER_PRODUCT)
            if loaded.ntotal:
                vectors = loaded.reconstruct_n(0, loaded.ntotal)
                base.add(vectors)
                np.testing.assert_array_equal(base.reconstruct_n(0, base.ntotal), vectors)
            assert base.ntotal == loaded.ntotal and base.d == loaded.d
            return base

        monkeypatch.setattr(faiss, "read_index", load)
    return variant
