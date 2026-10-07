from types import SimpleNamespace

import src.activity.prepared_training as prepared_training


def test_training_provenance_git_probe_does_not_inherit_secrets(monkeypatch):
    monkeypatch.setenv("PATH", "synthetic-path")
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-secret")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-secret")
    monkeypatch.setenv("MEDCHAT_AGENT_SESSION_DB", "synthetic-session-db")
    observed = {}

    def fake_run(*args, **kwargs):
        observed["args"] = args
        observed["kwargs"] = kwargs
        return SimpleNamespace(stdout="a" * 40)

    monkeypatch.setattr(prepared_training.subprocess, "run", fake_run)

    result = prepared_training.training_code_provenance()

    environment = observed["kwargs"]["env"]
    assert environment["PATH"] == "synthetic-path"
    assert environment["GIT_TERMINAL_PROMPT"] == "0"
    assert "OPENAI_API_KEY" not in environment
    assert "DEEPSEEK_API_KEY" not in environment
    assert "MEDCHAT_AGENT_SESSION_DB" not in environment
    assert result["git_commit"] == "a" * 40
