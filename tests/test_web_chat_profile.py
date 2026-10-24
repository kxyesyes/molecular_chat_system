import pytest


def test_web_chat_profile_defaults_to_pi_with_full_web_admission(monkeypatch):
    from src.web.app import resolve_web_chat_profile

    monkeypatch.delenv("MEDCHAT_CHAT_PROFILE", raising=False)
    monkeypatch.delenv("MEDCHAT_DECISION_WIRE_MODE", raising=False)

    assert resolve_web_chat_profile() == ("pi", "native", "medchat-web-v1")


@pytest.mark.parametrize(
    "profile,expected",
    [
        ("pi", ("pi", "native", "medchat-web-v1")),
        ("decision_a2", ("pi", "json", "a1_closed")),
        ("semantic_v1", ("pi", "native", "semantic_v1")),
    ],
)
def test_web_chat_profile_selects_pi_and_preserves_policy(monkeypatch, profile, expected):
    from src.web.app import resolve_web_chat_profile

    monkeypatch.setenv("MEDCHAT_DECISION_WIRE_MODE", "json" if profile == "decision_a2" else "native")

    assert resolve_web_chat_profile(profile) == expected
    monkeypatch.setenv("MEDCHAT_CHAT_PROFILE", profile)
    assert resolve_web_chat_profile() == expected


def test_web_chat_profile_rejects_obsolete_legacy(monkeypatch):
    from src.web.app import resolve_web_chat_profile

    with pytest.raises(ValueError, match="obsolete.*pi"):
        resolve_web_chat_profile("legacy")
    monkeypatch.setenv("MEDCHAT_CHAT_PROFILE", "legacy")
    with pytest.raises(ValueError, match="obsolete.*pi"):
        resolve_web_chat_profile()


def test_web_chat_profile_rejects_unknown_values():
    from src.web.app import resolve_web_chat_profile

    with pytest.raises(ValueError, match="MEDCHAT_CHAT_PROFILE"):
        resolve_web_chat_profile("experimental")
    with pytest.raises(ValueError, match="MEDCHAT_DECISION_WIRE_MODE"):
        resolve_web_chat_profile("pi", wire_mode="yaml")
