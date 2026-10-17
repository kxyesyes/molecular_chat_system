"""Offline static-workflow regression; model rows are explicit test fixtures."""
from copy import deepcopy
import json

import pytest

from evidence_report_fixture import Generator, snapshot
from test_browser_admet_evidence import DisclaimerBackend, DISCLAIMERS
from src.agent.contracts import AgentContext
from src.agent.evidence.ledger import EvidenceLedger
from src.agent.orchestrators import WorkflowOrchestrator, WorkflowStep
from src.agent.persistence.sqlite_store import SQLiteAgentStateStore
from src.agent.presentation.evidence_report import build_evidence_report
from src.agent.tooling.factory import build_tool_registry
from src.agent.tools.admet_predictor import ADMETPredictor
from src.agent.tools.candidate_ranker import CandidateRanker
from src.agent.tools.property_calculator import PropertyCalculator
from src.web.scientific_references import ScientificReferenceService


class RecordedADMET(ADMETPredictor):
    # Isolate the alignment contract from the independently diagnosed
    # production predictor/registry version mismatch.
    version = "2"

    def __init__(self, digest_mode="valid", extra_row=False):
        super().__init__(backend=DisclaimerBackend())
        self.digest_mode, self.extra_row = digest_mode, extra_row
        self.returned = []

    def execute(self, query):
        raw = super().execute(query)
        if self.extra_row:
            extra = self.backend.predict_batch(["CCC"], ["fixture-extra"])[0]
            raw["data"].append(extra)
            raw["provenance"]["output_digest"] = EvidenceLedger.output_digest(raw["data"])
        if self.digest_mode == "forged":
            raw["provenance"]["output_digest"] = "0" * 64
        elif self.digest_mode == "absent":
            raw["provenance"]["output_digest"] = None
        self.returned.append(deepcopy(raw))
        return raw


def design_execution(tmp_path, *, digest_mode="valid", extra_row=False):
    store = SQLiteAgentStateStore(tmp_path / "design-report.sqlite")
    admet = RecordedADMET(digest_mode, extra_row)
    steps = [
        WorkflowStep("generate", "llm_molecular_generator", output_key="molecules"),
        WorkflowStep("properties", "property_calculator", output_key="properties",
                     input_binding="$.outputs.molecules", input_transform="smiles_text",
                     metadata={"candidate_source": "molecules"}),
        WorkflowStep("admet", "admet_predictor", output_key="admet", required=False,
                     input_binding="$.outputs.molecules", input_transform="molecule_batch",
                     metadata={"candidate_source": "molecules"}),
        WorkflowStep("ranking", "candidate_ranker", input_binding="$.workflow",
                     output_key="ranking", metadata={
                         "docking_top_n": 2,
                         "workflow_output_keys": ("molecules", "properties", "admet", "activity"),
                         "workflow_optional_output_keys": ("admet", "activity"),
                         "workflow_metadata_keys": ("docking_top_n",),
                     }),
    ]
    registry = build_tool_registry([Generator(), PropertyCalculator(), admet, CandidateRanker()])
    try:
        result = WorkflowOrchestrator(state_store=store).run(
            context=AgentContext("offline design fixture", "browser-design-report-fixture",
                                 session_id="owner", active_skill="target_driven_design",
                                 mol_count=2),
            steps=steps, tools=registry.as_mapping())
    finally:
        registry.close()
    execution = result.to_legacy_dict()
    execution.update(trace_id=result.trace_id, agent_result=result,
                     status="partial" if result.partial else "completed",
                     workflow_plan={"workflow_name": "target_driven_design", "metadata": {}})
    events = ScientificReferenceService(store).project(execution, session_id="owner")
    snap = snapshot(store, execution, events)
    return admet, execution, events, snap


@pytest.mark.parametrize("extra_row", [False, True])
def test_static_alignment_binds_verified_output_before_ledger_and_report(tmp_path, extra_row):
    provider, execution, events, snap = design_execution(tmp_path, extra_row=extra_row)
    raw = json.loads(snap["latest"]["admet"]["output_json"])
    original = provider.returned[0]
    assert original["provenance"]["output_digest"] == EvidenceLedger.output_digest(original["data"])
    assert raw["provenance"]["output_digest"] == EvidenceLedger.output_digest(raw["data"])
    assert raw["provenance"]["input_digest"] == snap["latest"]["admet"]["input_hash"]
    assert raw["provenance"]["tool_version"] == snap["latest"]["admet"]["tool_version"] == "2"
    assert all("candidate_id" in row for row in raw["data"])
    assert all("candidate_id" not in row for row in original["data"])
    assert raw["warnings"][:6] == DISCLAIMERS * 2
    assert raw["quality"]["candidate_alignment"]["discarded_count"] == int(extra_row)
    assert raw["status"] == ("partial" if extra_row else "succeeded")
    before = deepcopy(snap)
    report = build_evidence_report(snap, execution, events)
    assert snap == before
    assert report is not None
    admet_step = next(s for s in report["steps"] if s["tool_name"] == "admet_predictor")
    assert admet_step["reason_code"] == "none"
    assert admet_step["status"] == raw["status"]
    assert report["ranking"]["state"] == "partial"  # activity is intentionally absent
    assert len(report["ranking"]["top_candidates"]) == 2
    assert all(row["state"] == "available" for row in report["property_rows"])


def test_static_alignment_rejects_forged_provider_digest_instead_of_resealing(tmp_path):
    _, execution, events, snap = design_execution(tmp_path, digest_mode="forged")
    raw = json.loads(snap["latest"]["admet"]["output_json"])
    assert raw["success"] is False
    assert raw["status"] == "failed"
    assert raw["error"]["code"] == "invalid_output"
    assert raw["data"] is None
    assert raw["quality"]["validated"] is False
    ledger = execution["metadata"]["evidence_ledger"]
    assert not next(r for r in ledger if r["step_id"] == "admet")["scientific_usable"]
    report = build_evidence_report(snap, execution, events)
    assert report is not None
    assert not any(s["tool_name"] == "admet_predictor" for s in report["sources"])
    assert all("admet" in row["missing_evidence"] for row in report["ranking"]["top_candidates"])


def test_static_alignment_does_not_invent_missing_provider_digest(tmp_path):
    _, _, _, snap = design_execution(tmp_path, digest_mode="absent")
    raw = json.loads(snap["latest"]["admet"]["output_json"])
    assert raw["provenance"]["output_digest"] is None


@pytest.mark.parametrize("mutation", ["output_digest", "tool_version", "input_digest", "data"])
def test_projection_still_refuses_source_tampering(tmp_path, mutation):
    _, execution, events, snap = design_execution(tmp_path)
    cp = snap["latest"]["admet"]
    raw = json.loads(cp["output_json"])
    if mutation == "data":
        raw["data"][0]["admet"]["endpoints"]["HIA_Hou"]["value"] = 0.12
    else:
        raw["provenance"][mutation] = "forged"
    cp["output_json"] = json.dumps(raw)
    index = next(i for i, row in enumerate(execution["tool_result_sequence"])
                 if row["tool_name"] == "admet_predictor")
    execution["tool_result_sequence"][index] = {**raw, "tool_name": cp["tool_name"], "step_id": cp["step_id"]}
    report = build_evidence_report(snap, execution, events)
    assert report is not None
    assert not any(s["tool_name"] == "admet_predictor" for s in report["sources"])
    assert report["ranking"]["state"] == "not_provided"
    assert report["ranking"]["top_candidates"] == []
