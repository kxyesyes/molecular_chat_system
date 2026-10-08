"""TDD contract tests for the ADMET-AI 1.4.0 boundary.

These tests deliberately use a small injected backend for deterministic failure
and data-lineage checks.  The real-weight smoke test is opt-in and lives in the
same file so CI does not silently pretend that the optional model is installed.
"""

from __future__ import annotations

import time

import pytest

from src.agent.tools.admet_predictor import ADMETPredictor
from src.agent.contracts import AgentErrorCode
from src.agent.tooling.factory import build_tool_registry
from src.agent.tools.candidate_ranker import CandidateRanker
from src.agent.planning.bindings import BindingResolver
from src.agent.tools.admet_ai_backend import ADMETAIBackend


def _prediction(smiles: str, *, molecule_id: str) -> dict:
    return {
        "molecule_id": molecule_id,
        "smiles": smiles,
        "canonical_smiles": smiles,
        "status": "succeeded",
        "admet": {
            "prediction_method": "admet_ai",
            "backend_version": "1.4.0",
            "model_name": "ADMET-AI",
            "model_version": "1.4.0",
            "weights_id": "sha256:" + "a" * 64,
            "data_version": "sha256:" + "b" * 64,
            "demo_mode": False,
            "fallback_used": False,
            "source": "local_admet_ai",
            "evidence": {
                "type": "model_output",
                "source": "admet_ai_model",
                "model_version": "1.4.0",
                "weights_id": "sha256:" + "a" * 64,
                "data_version": "sha256:" + "b" * 64,
                "row_count": 1,
            },
            "physicochemical_source": "rdkit",
            "endpoints": {
                "HIA_Hou": {
                    "value": 0.91,
                    "unit": "-",
                    "task_type": "classification",
                    "source": "admet_ai_model",
                }
            },
            "units": {"HIA_Hou": "-"},
            "risk_endpoint_ids": [],
            "risk_count": 0,
            "total_endpoints": 1,
            "risk_threshold": 0.5,
            "risk_summary_method": "adverse_classification_probability_ge_0.5",
        },
        "warnings": [],
    }


class FakeBackend:
    version = "1.4.0"
    weights_id = "sha256:" + "a" * 64
    data_version = "sha256:" + "b" * 64

    def __init__(self, *, fail_ids: set[str] | None = None, delay: float = 0.0):
        self.fail_ids = fail_ids or set()
        self.delay = delay
        self.calls: list[tuple[list[str], list[str]]] = []
        self.closed = False

    def close(self):
        self.closed = True

    def predict_batch(self, smiles: list[str], molecule_ids: list[str]) -> list[dict]:
        self.calls.append((list(smiles), list(molecule_ids)))
        if self.delay:
            time.sleep(self.delay)
        rows = []
        for smiles_value, molecule_id in zip(smiles, molecule_ids):
            if molecule_id in self.fail_ids:
                rows.append({
                    "molecule_id": molecule_id,
                    "smiles": smiles_value,
                    "canonical_smiles": smiles_value,
                    "status": "failed",
                    "error": "backend fixture failure",
                    "admet": {},
                    "warnings": ["fixture failure"],
                })
            else:
                rows.append(_prediction(smiles_value, molecule_id=molecule_id))
        return rows


def test_model_unavailable_is_explicit_and_never_rdkit_fallback(monkeypatch):
    monkeypatch.setattr(
        "src.agent.tools.admet_predictor.get_admet_ai_backend",
        lambda **_: None,
    )
    result = ADMETPredictor().execute("请预测 ADMET。SMILES: CCO")

    assert result["success"] is False
    assert result["data"] is None
    assert "ADMET-AI" in result["message"]
    assert "rdkit_rules" not in repr(result)


def test_batch_preserves_ids_and_exposes_item_failure_without_row_mixing():
    backend = FakeBackend(fail_ids={"molecule-002"})
    result = ADMETPredictor(backend=backend).execute("SMILES: CCO\nSMILES: CCN")

    assert result["success"] is False
    assert result["status"] == "partial"
    assert [row["molecule_id"] for row in result["data"]] == [
        "molecule-001",
        "molecule-002",
    ]
    assert [row["smiles"] for row in result["data"]] == ["CCO", "CCN"]
    assert result["data"][0]["admet"]["endpoints"]["HIA_Hou"]["value"] == 0.91
    assert result["data"][1]["status"] == "failed"
    assert "HIA_Hou" not in result["data"][1]["admet"]
    assert backend.calls == [(["CCO", "CCN"], ["molecule-001", "molecule-002"])]


def test_mixed_admet_batch_is_not_reported_as_success():
    result = ADMETPredictor(backend=FakeBackend(fail_ids={"molecule-002"})).execute(
        "SMILES: CCO\nSMILES: CCN"
    )

    assert result["status"] == "partial"
    assert result["success"] is False


def test_admet_result_records_structure_model_data_source_and_evidence():
    result = ADMETPredictor(backend=FakeBackend()).execute("SMILES: CCO")

    assert result["success"] is True
    provenance = result["provenance"]
    assert provenance["model_version"] == "1.4.0"
    quality = result["quality"]
    assert quality["data_version"] == "sha256:" + "b" * 64
    assert quality["source"] == "local_admet_ai"
    assert quality["input_structures"] == [{
        "molecule_id": "molecule-001",
        "smiles": "CCO",
        "canonical_smiles": "CCO",
    }]
    assert result["evidence"] == [{
        "type": "model_output",
        "source": "admet_ai_model",
        "model_version": "1.4.0",
        "weights_id": "sha256:" + "a" * 64,
        "data_version": "sha256:" + "b" * 64,
        "row_count": 1,
    }]
    admet = result["data"][0]["admet"]
    assert admet["data_version"] == quality["data_version"]
    assert admet["source"] == quality["source"]
    assert admet["evidence"] == result["evidence"][0]


def test_timeout_returns_no_scientific_values():
    backend = FakeBackend(delay=0.05)
    result = ADMETPredictor(backend=backend, timeout_seconds=0.001).execute(
        "SMILES: CCO"
    )

    assert result["success"] is False
    assert result["data"] is None
    assert "超时" in result["message"] or "timeout" in result["message"].lower()
    assert "HIA_Hou" not in repr(result)


def test_real_backend_smoke_requires_explicit_opt_in():
    try:
        from admet_ai import ADMETModel  # noqa: F401
    except ImportError:
        pytest.skip("ADMET-AI 1.4.0 is not installed in this interpreter")

    predictor = ADMETPredictor()
    result = predictor.execute("SMILES: CCO")
    assert result["success"] is True, result
    row = result["data"][0]
    assert row["admet"]["prediction_method"] == "admet_ai"
    assert row["admet"]["demo_mode"] is False
    assert row["admet"]["fallback_used"] is False
    assert row["admet"]["weights_id"].startswith("sha256:")
    assert row["admet"]["endpoints"]


class RawTool:
    name = "admet_predictor"
    version = "2"
    description = "fixture"

    def __init__(self, raw):
        self.raw = raw

    def execute(self, query):
        return self.raw

    def close(self):
        pass


def test_admet_ai_rows_pass_analysis_contract_with_provenance():
    row = _prediction("CCO", molecule_id="molecule-001")
    raw = {
        "success": True,
        "status": "succeeded",
        "data": [row],
        "provenance": {
            "tool_name": "admet_predictor",
            "tool_version": "2",
            "model_name": "ADMET-AI",
            "model_version": "1.4.0",
            "demo_mode": False,
            "fallback_used": False,
            "input_digest": "b" * 64,
            "output_digest": "c" * 64,
        },
    }
    registry = build_tool_registry([RawTool(raw)])
    try:
        result = registry.resolve("admet_predictor").execute({"query": "CCO"})
        assert result.success is True, result
        assert result.data[0]["admet"]["prediction_method"] == "admet_ai"
    finally:
        registry.close()


def test_admet_ai_contract_rejects_missing_weight_identity():
    row = _prediction("CCO", molecule_id="molecule-001")
    del row["admet"]["weights_id"]
    raw = {"success": True, "status": "succeeded", "data": [row]}
    registry = build_tool_registry([RawTool(raw)])
    try:
        result = registry.resolve("admet_predictor").execute({"query": "CCO"})
        assert result.success is False
        assert result.error.code is AgentErrorCode.INVALID_OUTPUT
    finally:
        registry.close()


def test_admet_ai_rows_are_consumed_by_candidate_ranker():
    admet_row = _prediction("CCO", molecule_id="candidate-001")
    result = CandidateRanker().execute({
        "metadata": {"docking_top_n": 1},
        "outputs": {
            "molecules": {"candidates": [{
                "candidate_id": "candidate-001",
                "smiles": "CCO",
                "canonical_smiles": "CCO",
            }]},
            "properties": [{
                "smiles": "CCO",
                "properties": {"qed": 0.4, "logp": 0.1},
            }],
            "admet": [admet_row],
        },
    })

    assert result["success"] is True, result
    ranked = result["data"]["ranked_candidates"]
    assert ranked[0]["candidate_id"] == "candidate-001"
    assert ranked[0]["ranking_evidence"]["admet_score"] == 1.0


def test_candidate_ids_survive_binding_and_admet_batch_execution():
    candidates = {
        "candidates": [
            {"candidate_id": "cand-101", "smiles": "CCO"},
            {"candidate_id": "cand-102", "smiles": "CCN"},
        ]
    }
    bound = BindingResolver().resolve(
        "$.outputs.molecules", "molecule_batch", {}, {"molecules": candidates}
    )
    assert bound == {
        "smiles": ["CCO", "CCN"],
        "molecule_ids": ["cand-101", "cand-102"],
    }

    result = ADMETPredictor(backend=FakeBackend()).execute(bound)
    assert result["success"] is True
    assert [row["molecule_id"] for row in result["data"]] == [
        "cand-101", "cand-102"
    ]


@pytest.mark.parametrize("wrapped", [False, True])
def test_candidate_batch_passes_real_registered_adapter(wrapped):
    backend = FakeBackend()
    registry = build_tool_registry([ADMETPredictor(backend=backend)])
    batch = {"smiles": ["CCO", "CCO"], "molecule_ids": ["a", "b"]}
    try:
        result = registry.resolve("admet_predictor").execute(
            {"query": batch} if wrapped else batch
        )
        assert result.success, result
        assert [row["molecule_id"] for row in result.data] == ["a", "b"]
        assert backend.calls == [(["CCO", "CCO"], ["a", "b"])]
    finally:
        registry.close()


def test_binding_does_not_drop_distinct_ids_with_the_same_smiles():
    result = BindingResolver().resolve("$.outputs.molecules", "molecule_batch", {}, {
        "molecules": {"candidates": [
            {"candidate_id": "a", "smiles": "CCO"},
            {"candidate_id": "b", "smiles": "CCO"},
        ]},
    })
    assert result == {"smiles": ["CCO", "CCO"], "molecule_ids": ["a", "b"]}


def test_constructing_predictor_does_not_load_model(monkeypatch):
    calls = []
    monkeypatch.setattr("src.agent.tools.admet_predictor.get_admet_ai_backend",
                        lambda: calls.append(True))
    ADMETPredictor()
    assert calls == []


def test_lazy_cached_backend_is_not_closed_by_tool_instance(monkeypatch):
    backend = FakeBackend()
    monkeypatch.setattr(
        "src.agent.tools.admet_predictor.get_admet_ai_backend",
        lambda: backend,
    )
    predictor = ADMETPredictor()
    assert predictor.execute("SMILES: CCO")["success"] is True
    predictor.close()
    assert backend.closed is False


def test_injected_backend_is_closed_by_tool_instance():
    backend = FakeBackend()
    predictor = ADMETPredictor(backend=backend)
    assert predictor.execute("SMILES: CCO")["success"] is True
    predictor.close()
    assert backend.closed is True


@pytest.mark.parametrize("invalid", [[], [""], ["CCO"] * 101, ["C" * 8193]])
def test_structured_batch_input_limits_apply_before_backend(invalid):
    backend = FakeBackend()
    result = ADMETPredictor(backend=backend).execute({"smiles": invalid})
    assert not result["success"]
    assert backend.calls == []


def test_admet_output_requires_smiles_even_with_complete_model_metadata():
    row = _prediction("CCO", molecule_id="a")
    del row["smiles"]
    registry = build_tool_registry([RawTool({"success": True, "data": [row]})])
    try:
        assert not registry.resolve("admet_predictor").execute("CCO").success
    finally:
        registry.close()


class ModelFrameFixture:
    def __init__(self, *, reverse=False, nan=False):
        self.calls = []
        self.reverse, self.nan = reverse, nan

    def predict(self, smiles):
        import pandas as pd
        self.calls.append(list(smiles))
        values = [0.1 + index / 10 for index in range(len(smiles))]
        if self.nan:
            values[-1] = float("nan")
        result = pd.DataFrame({"HIA_Hou": values}, index=smiles)
        return result.iloc[::-1] if self.reverse else result


def frame_backend(model):
    return ADMETAIBackend(model=model, weights_id="sha256:" + "a" * 64, endpoint_info={
        "HIA_Hou": {"name": "HIA", "category": "Absorption",
                    "task_type": "classification", "unit": "-"},
    })


def test_backend_invalid_and_duplicate_smiles_retain_all_identities():
    model = ModelFrameFixture()
    rows = frame_backend(model).predict_batch(["CCO", "CC(C)((", "CCO"], ["a", "b", "c"])
    assert [row["molecule_id"] for row in rows] == ["a", "b", "c"]
    assert [row["status"] for row in rows] == ["succeeded", "failed", "succeeded"]
    assert model.calls == [["CCO", "CCO"]]
    assert rows[1]["admet"] == {}


def test_backend_rejects_reordered_prediction_index():
    with pytest.raises(RuntimeError, match="identity"):
        frame_backend(ModelFrameFixture(reverse=True)).predict_batch(["CCO", "CCN"], ["a", "b"])


def test_backend_reports_nonfinite_item_without_losing_valid_row():
    rows = frame_backend(ModelFrameFixture(nan=True)).predict_batch(["CCO", "CCN"], ["a", "b"])
    assert rows[0]["status"] == "succeeded"
    assert rows[1]["status"] == "failed"
    assert rows[1]["admet"] == {}
    assert "non-finite" in rows[1]["error"]


@pytest.mark.parametrize("text", ["CCO ethanol", "CCO |name|", "", " "])
def test_backend_never_accepts_rdkit_name_or_extension(text):
    model = ModelFrameFixture()
    rows = frame_backend(model).predict_batch([text], ["a"])
    assert rows[0]["status"] == "failed"
    assert model.calls == []
