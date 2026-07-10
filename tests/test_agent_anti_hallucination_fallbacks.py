from __future__ import annotations

import requests

from src.activity.predictor import ActivityPredictor
from src.agent.tools import get_optional_tool
from src.agent.tools.rxn_chemistry_agent import RXNChemistryAgent


def test_activity_predictor_without_real_weights_returns_no_simulated_score() -> None:
    predictor = ActivityPredictor()
    predictor._loaded = True
    predictor.demo_mode = True
    predictor.current_model_path = None

    result = predictor.predict("CCO")[0]

    assert result["success"] is False
    assert "activity_score" not in result
    assert "model" in result["error"].lower()


def test_rxn_authentication_failure_never_returns_mock_products(monkeypatch) -> None:
    class UnauthorizedResponse:
        status_code = 401
        text = "unauthorized"

        @staticmethod
        def json():
            return {}

    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: UnauthorizedResponse())
    result = RXNChemistryAgent(api_key="invalid").execute(
        "Predict the reaction product for CCO"
    )

    assert result["success"] is False
    assert result["data"] is None
    assert ".oxidized" not in str(result)


def test_rxn_without_api_key_fails_without_network_call(monkeypatch) -> None:
    calls = []

    class UnauthorizedResponse:
        status_code = 401
        text = "unauthorized"

        @staticmethod
        def json():
            return {}

    def record_call(*args, **kwargs):
        calls.append((args, kwargs))
        return UnauthorizedResponse()

    monkeypatch.setattr(requests, "post", record_call)
    result = RXNChemistryAgent(api_key="").execute(
        "Predict the reaction product for CCO"
    )

    assert result["success"] is False
    assert result["data"] is None
    assert calls == []


def test_rxn_retrosynthesis_network_failure_never_returns_mock_routes(
    monkeypatch,
) -> None:
    def fail(*args, **kwargs):
        raise requests.ConnectionError("offline")

    monkeypatch.setattr(requests, "post", fail)
    result = RXNChemistryAgent(api_key="configured").execute(
        "Plan retrosynthesis for CCO"
    )

    assert result["success"] is False
    assert result["data"] is None
    assert "amide formation" not in str(result)


def test_rxn_literature_search_does_not_fabricate_citations() -> None:
    result = RXNChemistryAgent(api_key="configured").execute(
        "Find literature for CCO"
    )

    assert result["success"] is False
    assert result["data"] is None
    assert "10.1021/jo.example" not in str(result)


def test_rxn_tool_is_available_to_the_legacy_react_tool_pool() -> None:
    tool = get_optional_tool("RXNChemistryAgent")

    assert tool.name == "rxn_chemistry_agent"
