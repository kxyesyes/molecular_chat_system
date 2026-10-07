import pytest


def test_web_chat_profile_defaults_to_stable_legacy(monkeypatch):
    from src.web.app import resolve_web_chat_profile

    monkeypatch.delenv("MEDCHAT_CHAT_PROFILE", raising=False)
    monkeypatch.delenv("MEDCHAT_DECISION_WIRE_MODE", raising=False)

    assert resolve_web_chat_profile() == ("legacy", "native", "a1_closed")


@pytest.mark.parametrize(
    "profile,expected",
    [
        ("decision_a2", ("decision_a2", "json", "a1_closed")),
        ("semantic_v1", ("decision_a2", "native", "semantic_v1")),
    ],
)
def test_web_chat_profile_explicitly_selects_decision_chain(monkeypatch, profile, expected):
    from src.web.app import resolve_web_chat_profile

    monkeypatch.setenv("MEDCHAT_DECISION_WIRE_MODE", "json" if profile == "decision_a2" else "native")

    assert resolve_web_chat_profile(profile) == expected


def test_web_chat_profile_rejects_unknown_values():
    from src.web.app import resolve_web_chat_profile

    with pytest.raises(ValueError, match="MEDCHAT_CHAT_PROFILE"):
        resolve_web_chat_profile("experimental")
    with pytest.raises(ValueError, match="MEDCHAT_DECISION_WIRE_MODE"):
        resolve_web_chat_profile("legacy", wire_mode="yaml")
