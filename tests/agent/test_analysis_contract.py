"""Non-projecting analysis boundaries; real in-memory RDKit, no model assets."""
from copy import deepcopy
from dataclasses import replace
from threading import Event

import pytest

from src.agent.contracts import AgentErrorCode, ObservationStatus, ToolProvenance, ToolResult, WorkflowArtifact
from src.agent.tooling.factory import LegacyQueryInput, build_tool_registry
from src.agent.tools import admet_predictor as admet_module
from src.agent.tools.admet_predictor import ADMETPredictor
from src.agent.tools.base_tool import execute_tool_compat
from src.agent.tools.drug_likeness_assessment import DrugLikenessAssessment
from src.agent.tools.property_calculator import PropertyCalculator


PRODUCERS = {
    "property_calculator": PropertyCalculator,
    "drug_likeness_assessment": DrugLikenessAssessment,
    "admet_predictor": ADMETPredictor,
}
ASPIRIN = "CC(=O)Oc1ccccc1C(=O)O"


def analysis_rows(name, smiles=("CCO",)):
    """Real in-memory descriptor rows for lifecycle tests, never model assets.

    Force the deterministic RDKit ADMET method directly, independent of whether
    optional adme_py is installed. Callers may add explicit fixture extensions.
    """
    producer = PRODUCERS[name]()
    slot, calculate = {
        "property_calculator": ("properties", "calculate_properties"),
        "drug_likeness_assessment": ("assessment", "assess_drug_likeness"),
        "admet_predictor": ("admet", "_predict_admet_with_rdkit"),
    }[name]
    rows = [{"smiles": value, slot: getattr(producer, calculate)(value)} for value in smiles]
    assert all(row[slot] is not None for row in rows)
    return rows


@pytest.mark.parametrize("leaf", ["pains", "brenk", "zinc"])
@pytest.mark.parametrize("value", [None, False, True])
@pytest.mark.parametrize("form", ["raw", "normalized", "snapshot"])
@pytest.mark.parametrize("state", ["succeeded", "partial", "failed"])
def test_admet_three_alerts_tristate_preserves_observation(leaf, value, form, state):
    rows = analysis_rows("admet_predictor")
    rows[0]["admet"]["medicinal"][leaf] = value
    raw = {"success": state == "succeeded", "status": state, "data": rows}
    if form == "normalized":
        raw = ToolResult("admet_predictor", state == "succeeded", "fixture", data=rows, status=ObservationStatus(state))
    elif form == "snapshot":
        raw = ToolResult.error_result("admet_predictor", AgentErrorCode.INTERNAL_ERROR, "fixture", {"raw_result": raw})
    tool = CountingTool("admet_predictor", raw)
    expected = execute_tool_compat(CountingTool(tool.name, deepcopy(raw)), "CCO")
    registry = build_tool_registry([tool])
    try:
        result = registry.resolve(tool.name).execute("CCO")
        expected.elapsed_ms = result.elapsed_ms
        assert result == expected
    finally:
        registry.close()


@pytest.mark.parametrize("leaf", ["pains", "brenk", "zinc"])
@pytest.mark.parametrize("value", [0, 1, "false", [], {}, "missing"])
def test_nullable_alerts_remain_required_and_strict(leaf, value):
    rows = analysis_rows("admet_predictor")
    if value == "missing":
        del rows[0]["admet"]["medicinal"][leaf]
    else:
        rows[0]["admet"]["medicinal"][leaf] = value
    tool = CountingTool("admet_predictor", {"success": True, "data": rows})
    registry = build_tool_registry([tool])
    try:
        invalid(registry.resolve(tool.name).execute("CCO"))
    finally:
        registry.close()


@pytest.mark.parametrize("section,leaf", [
    ("pharmacokinetics", "blood_brain_barrier_permeant"),
    ("physicochemical", "num_heavy_atoms"), ("physicochemical", "molecular_weight"),
    ("solubility", "log_s_esol"), ("medicinal", "synthetic_accessibility"),
    ("druglikeness", "ghose"), ("medicinal", "leadlikeness"),
])
def test_other_admet_known_leaves_never_become_nullable(section, leaf):
    rows = analysis_rows("admet_predictor")
    rows[0]["admet"][section][leaf] = None
    tool = CountingTool("admet_predictor", {"success": True, "data": rows})
    registry = build_tool_registry([tool])
    try:
        invalid(registry.resolve(tool.name).execute("CCO"))
    finally:
        registry.close()


@pytest.mark.parametrize("leaf", ["pains", "brenk", "zinc", "blood_brain_barrier_permeant"])
@pytest.mark.parametrize("missing", [True, False])
def test_adme_py_sparse_nulls_are_limited_to_uncomputed_alerts(leaf, missing):
    props = {"prediction_method": "adme_py", "backend_version": "fixture"}
    for section in ("physicochemical", "solubility", "lipophilicity", "pharmacokinetics", "druglikeness", "medicinal"):
        props[section] = {}
    if not missing:
        props["pharmacokinetics" if leaf == "blood_brain_barrier_permeant" else "medicinal"][leaf] = None
    raw = {"success": True, "data": [{"smiles": "CCO", "admet": props}]}
    tool = CountingTool("admet_predictor", raw)
    registry = build_tool_registry([tool])
    try:
        result = registry.resolve(tool.name).execute("CCO")
        if leaf == "blood_brain_barrier_permeant" and not missing:
            invalid(result)
        else:
            assert result.success and result.data == raw["data"]
    finally:
        registry.close()


class CountingTool:
    def __init__(self, name, raw):
        self.name, self.raw = name, raw
        self.inputs, self.closed = [], 0

    def execute(self, query):
        self.inputs.append(query)
        return self.raw

    def close(self):
        self.closed += 1


@pytest.fixture(params=list(PRODUCERS))
def boundary(request, monkeypatch):
    monkeypatch.setattr(admet_module, "ADME_PY_AVAILABLE", False)
    name = request.param
    raw = PRODUCERS[name]().execute("CCO")
    assert raw["success"], raw["message"]
    tool = CountingTool(name, raw)
    registry = build_tool_registry([tool])
    try:
        yield registry.resolve(name), tool
    finally:
        registry.close()
        assert tool.closed == 1


def invalid(result, code=AgentErrorCode.INVALID_OUTPUT):
    assert result.success is False
    assert result.error.code is code
    assert result.error.details is None
    assert "private-marker" not in repr(result)


def poison(raw, name):
    row = raw["data"][0]
    if name == "property_calculator":
        row["properties"]["qed"] = float("nan")
    elif name == "drug_likeness_assessment":
        row["assessment"]["qed_score"] = float("nan")
    else:
        row["admet"]["solubility"]["log_s_esol"] = float("nan")


def test_registration_only_selects_contract_without_execution(boundary):
    adapter, tool = boundary
    assert tool.inputs == []
    assert adapter.spec.input_schema is not LegacyQueryInput
    assert adapter.spec.output_schema.schema_version == "1"
    assert adapter.spec.owner_agents == {"property_admet"}
    assert adapter.spec.retry_policy.max_attempts == 1
    assert adapter.spec.side_effects == "none"


@pytest.mark.parametrize("query", ["CCO", ASPIRIN])
def test_actual_rdkit_characterization(boundary, query):
    adapter, tool = boundary
    raw = PRODUCERS[tool.name]().execute(query)
    assert raw["success"] and len(raw["data"]) == 1
    assert raw["data"][0]["smiles"] == query
    if tool.name == "property_calculator":
        props = raw["data"][0]["properties"]
        assert props["molecular_formula"] == ("C2H6O" if query == "CCO" else "C9H8O4")
        assert props["molecular_weight"] == (46.07 if query == "CCO" else 180.16)
    elif tool.name == "drug_likeness_assessment":
        assessment = raw["data"][0]["assessment"]
        assert assessment["lipinski_rule_of_five"]["violation_count"] == 0
        assert assessment["overall_assessment"]["component_scores"]["qed"] != assessment["qed_score"]
    else:
        admet = raw["data"][0]["admet"]
        assert admet["prediction_method"] == "rdkit_rules"
        assert admet["backend_version"]
        assert admet["physicochemical"]["molecular_weight"] == pytest.approx(46.069 if query == "CCO" else 180.159)
    tool.raw = raw
    expected = execute_tool_compat(CountingTool(tool.name, deepcopy(raw)), query)
    actual = adapter.execute({"query": query})
    expected.elapsed_ms = actual.elapsed_ms
    assert actual == expected
    assert tool.inputs == [query]


@pytest.mark.parametrize("form", ["raw", "normalized", "snapshot"])
@pytest.mark.parametrize("state", ["succeeded", "partial", "failed"])
def test_nonfinite_observation_rejected_even_in_failures(boundary, form, state):
    adapter, tool = boundary
    poison(tool.raw, tool.name)
    tool.raw.update(success=state == "succeeded", status=state)
    if form == "normalized":
        tool.raw = ToolResult(tool.name, state == "succeeded", "private-marker",
                              data=tool.raw["data"], status=ObservationStatus(state))
    elif form == "snapshot":
        tool.raw = ToolResult.error_result(tool.name, AgentErrorCode.INTERNAL_ERROR,
                                          "private-marker", {"raw_result": tool.raw})
    invalid(adapter.execute({"query": "CCO"}))
    assert tool.inputs == ["CCO"]


@pytest.mark.parametrize("payload", [None, True, 12, b"private-marker", ["CCO"], {},
    {"query": None}, {"query": False}, {"query": 3}, {"query": b"private-marker"},
    {"query": ["CCO"]}, {"query": {"smiles": "CCO"}}, {"smiles": "CCO"}])
def test_wrong_transport_rejected_before_execution(boundary, payload):
    adapter, tool = boundary
    invalid(adapter.execute(payload), AgentErrorCode.INVALID_INPUT)
    assert tool.inputs == []


@pytest.mark.parametrize("payload", ["  CCO  ", {"query": "  CCO  "},
    {"query": "  CCO  ", "smiles": "CCN", "metadata": {"x": [1]}}])
def test_query_forwarded_exactly_without_structured_smiles_support(boundary, payload):
    adapter, tool = boundary
    assert adapter.execute(payload).success
    assert tool.inputs == ["  CCO  "]


@pytest.mark.parametrize("field,value", [
    ("success", 1), ("status", "private-marker"), ("status", "failed"),
    ("error", "private-marker"), ("data", None), ("data", []), ("data", {}),
    ("message", 4), ("formatted", []), ("warnings", "private-marker"), ("warnings", [3]),
    ("evidence", {}), ("evidence", ["private-marker"]), ("quality", []),
    ("artifacts", [3]), ("artifacts", [{"metadata": []}]),
    ("provenance", {}), ("tool_name", "other_tool"),
])
@pytest.mark.parametrize("normalized", [False, True])
def test_malformed_envelopes(boundary, field, value, normalized):
    adapter, tool = boundary
    if normalized:
        tool.raw = execute_tool_compat(CountingTool(tool.name, tool.raw), "CCO")
        setattr(tool.raw, field, value)
    else:
        tool.raw[field] = value
    invalid(adapter.execute({"query": "CCO"}))


# Each path comes from current calculation methods; labels/rules are not recomputed.
BAD_LEAVES = [
    ("property_calculator", "properties.molecular_formula", 4),
    ("property_calculator", "properties.molecular_weight", "46.07"),
    ("property_calculator", "properties.logp", True),
    ("property_calculator", "properties.tpsa", float("inf")),
    ("property_calculator", "properties.hba", -1),
    ("property_calculator", "properties.hbd", 1.5),
    ("property_calculator", "properties.rotatable_bonds", False),
    ("property_calculator", "properties.qed", 1.1),
    ("drug_likeness_assessment", "assessment.qed_score", "0.4"),
    ("drug_likeness_assessment", "assessment.molecular_properties.aromatic_rings", -1),
    ("drug_likeness_assessment", "assessment.molecular_properties.heteroatoms", True),
    ("drug_likeness_assessment", "assessment.lipinski_rule_of_five.details.hba.value", True),
    ("drug_likeness_assessment", "assessment.lipinski_rule_of_five.details.logp.limit", "5"),
    ("drug_likeness_assessment", "assessment.lipinski_rule_of_five.details.hbd.pass", 1),
    ("drug_likeness_assessment", "assessment.lipinski_rule_of_five.violation_count", 5),
    ("drug_likeness_assessment", "assessment.lipinski_rule_of_five.violations", [1]),
    ("drug_likeness_assessment", "assessment.veber_rules.details.tpsa.value", float("nan")),
    ("drug_likeness_assessment", "assessment.veber_rules.overall_compliance", "true"),
    ("drug_likeness_assessment", "assessment.lead_likeness.details.logp.range", 3),
    ("drug_likeness_assessment", "assessment.overall_assessment.score", -0.1),
    ("drug_likeness_assessment", "assessment.overall_assessment.component_scores.qed", float("nan")),
    ("drug_likeness_assessment", "assessment.overall_assessment.grade", []),
    ("admet_predictor", "admet.prediction_method", "unknown-method"),
    ("admet_predictor", "admet.backend_version", 1),
    ("admet_predictor", "admet.physicochemical.formula", False),
    ("admet_predictor", "admet.physicochemical.num_heavy_atoms", -1),
    ("admet_predictor", "admet.physicochemical.sp3_carbon_ratio", 1.1),
    ("admet_predictor", "admet.physicochemical.molar_refractivity", "12"),
    ("admet_predictor", "admet.solubility.solubility_esol", -1),
    ("admet_predictor", "admet.lipophilicity.wlogp", True),
    ("admet_predictor", "admet.pharmacokinetics.blood_brain_barrier_permeant", "false"),
    ("admet_predictor", "admet.druglikeness.lipinski", True),
    ("admet_predictor", "admet.druglikeness.ghose", []),
    ("admet_predictor", "admet.medicinal.pains", 0),
    ("admet_predictor", "admet.medicinal.synthetic_accessibility", 11),
    ("admet_predictor", "admet.medicinal.leadlikeness", "private-marker"),
]


@pytest.mark.parametrize("name,path,value", BAD_LEAVES)
@pytest.mark.parametrize("missing", [False, True])
def test_known_leaves_required_and_strict(monkeypatch, name, path, value, missing):
    monkeypatch.setattr(admet_module, "ADME_PY_AVAILABLE", False)
    raw = PRODUCERS[name]().execute("CCO")
    parent = raw["data"][0]
    *parts, leaf = path.split(".")
    for part in parts:
        parent = parent[part]
    if missing:
        del parent[leaf]
    else:
        parent[leaf] = value
    tool = CountingTool(name, raw)
    registry = build_tool_registry([tool])
    try:
        invalid(registry.resolve(name).execute({"query": "CCO"}))
        assert tool.inputs == ["CCO"]
    finally:
        registry.close()


@pytest.mark.parametrize("normalized", [False, True])
@pytest.mark.parametrize("state,success", [("succeeded", True), ("partial", True),
                                          ("partial", False), ("failed", False)])
def test_preserves_compat_observations_and_opaque_extensions(boundary, normalized, state, success):
    adapter, tool = boundary
    raw = tool.raw
    raw["data"][0]["extension"] = {"untouched": [1.23456789012345, "1.2", False]}
    # No producer/domain validator defines a named scientific evidence slot.
    opaque = {"prediction": "opaque", "raw_result": {"data": "not an envelope"}}
    raw.update(success=success, status=state, warnings=["observed warning"], evidence=[opaque],
               artifacts=[WorkflowArtifact("report", "synthetic.txt", "test", metadata=opaque)],
               quality={"extension": opaque}, provenance=ToolProvenance(tool.name).to_dict())
    if normalized:
        raw = execute_tool_compat(CountingTool(tool.name, raw), "CCO")
    tool.raw = raw
    expected = execute_tool_compat(CountingTool(tool.name, deepcopy(raw)), "CCO")
    result = adapter.execute({"query": "CCO"})
    expected.elapsed_ms = result.elapsed_ms
    assert result == expected
    assert tool.inputs == ["CCO"]


@pytest.mark.parametrize("query", ["CCO(", "CCO.C1CC", "CCO\nC1CC", "\n".join(["CCO"] * 101)])
def test_complete_invalid_smiles_and_batches_preserve_legacy_failure(boundary, query):
    adapter, tool = boundary
    raw = PRODUCERS[tool.name]().execute(query)
    assert not raw["success"] and raw["data"] is None
    tool.raw = raw
    expected = execute_tool_compat(CountingTool(tool.name, deepcopy(raw)), query)
    result = adapter.execute({"query": query})
    expected.elapsed_ms = result.elapsed_ms
    assert result == expected


def test_unavailable_backend_retains_legacy_error(boundary, monkeypatch):
    from src.agent.tools import base_tool
    adapter, tool = boundary
    monkeypatch.setattr(base_tool, "RDKIT_AVAILABLE", False)
    monkeypatch.setattr(admet_module, "RDKIT_AVAILABLE", False)
    raw = PRODUCERS[tool.name]().execute("CCO")
    assert not raw["success"]
    tool.raw = raw
    expected = execute_tool_compat(CountingTool(tool.name, deepcopy(raw)), "CCO")
    result = adapter.execute({"query": "CCO"})
    expected.elapsed_ms = result.elapsed_ms
    assert result == expected


def test_caller_validator_sees_untouched_raw_first(boundary):
    adapter, tool = boundary
    poison(tool.raw, tool.name)
    seen = []
    def caller(raw):
        seen.append(raw)
        assert raw is tool.raw
        raise ConnectionError("caller failure")
    result = adapter.execute({"query": "CCO"}, raw_validator=caller)
    assert result.error.code is AgentErrorCode.PROVIDER_ERROR
    assert seen == [tool.raw] and tool.inputs == ["CCO"]


def test_validation_uses_worker_deadline_and_releases_slot(boundary):
    adapter, tool = boundary
    adapter.spec = replace(adapter.spec, timeout_seconds=0.02)
    entered, release, finished = Event(), Event(), Event()
    def caller(raw):
        entered.set()
        try:
            assert release.wait(3)
        finally:
            finished.set()
    try:
        result = adapter.execute({"query": "CCO"}, raw_validator=caller)
        assert entered.is_set()
        assert result.error.code is AgentErrorCode.TOOL_TIMEOUT
        assert result.quality["invocation_may_still_be_running"] is True
        busy = adapter.execute({"query": "CCO"})
        assert busy.quality["capacity_exhausted"] is True
        assert tool.inputs == ["CCO"]
    finally:
        release.set()
        assert finished.wait(3)
    # Acquiring after worker completion waits for the future's release callback.
    assert adapter._invocation_slots.acquire(timeout=3)
    adapter._invocation_slots.release()
    adapter.spec = replace(adapter.spec, timeout_seconds=3)
    assert adapter.execute({"query": "CCO"}).success


@pytest.mark.parametrize("depth", [0, 16, 17])
def test_snapshot_limit(boundary, depth):
    adapter, tool = boundary
    raw = {"success": False, "message": "unavailable"}
    for _ in range(depth):
        raw = {"success": False, "error": {"details": {"raw_result": raw}}}
    tool.raw = raw
    result = adapter.execute({"query": "CCO"})
    if depth > 16:
        invalid(result)
    else:
        assert result.error.code is AgentErrorCode.INTERNAL_ERROR


@pytest.mark.parametrize("cycle", [False, True])
def test_invalid_snapshot_or_cycle_is_payload_free(boundary, cycle):
    adapter, tool = boundary
    raw = {"success": False, "error": {"details": {}}}
    raw["error"]["details"]["raw_result"] = raw if cycle else "private-marker"
    tool.raw = raw
    invalid(adapter.execute({"query": "CCO"}))


@pytest.mark.parametrize("field,value", [("tool_name", "other"), ("demo_mode", "false")])
@pytest.mark.parametrize("normalized", [False, True])
def test_provenance_identity_and_types(boundary, field, value, normalized):
    adapter, tool = boundary
    if normalized:
        tool.raw = execute_tool_compat(CountingTool(tool.name, tool.raw), "CCO")
        tool.raw.provenance = replace(ToolProvenance(tool.name), **{field: value})
    else:
        tool.raw["provenance"] = ToolProvenance(tool.name).to_dict()
        tool.raw["provenance"][field] = value
    invalid(adapter.execute({"query": "CCO"}))


@pytest.mark.parametrize("payload", [{"query": True}, {"query": ["CCO"]}, {"query": None}])
def test_constructed_input_revalidated(boundary, payload):
    adapter, tool = boundary
    model = adapter.spec.input_schema.model_construct(**payload)
    invalid(adapter.execute(model), AgentErrorCode.INVALID_INPUT)
    assert tool.inputs == []


def test_valid_model_input(boundary):
    adapter, tool = boundary
    model = adapter.spec.input_schema.model_validate({"query": "CCO", "extension": [1]})
    assert adapter.execute(model).success
    assert tool.inputs == ["CCO"]


@pytest.mark.parametrize("normalized", [False, True])
@pytest.mark.parametrize("field,value", [
    ("data", [None]), ("data", [{"smiles": 1}]),
    ("error", {"details": []}), ("error", {"message": False}),
    ("elapsed_ms", "private-marker"), ("elapsed_ms", True), ("elapsed_ms", -1),
])
def test_failure_envelope_containers(boundary, normalized, field, value):
    adapter, tool = boundary
    tool.raw = {"success": False, "message": "failure"}
    if normalized:
        tool.raw = execute_tool_compat(CountingTool(tool.name, tool.raw), "CCO")
        setattr(tool.raw, field, value)
    else:
        tool.raw[field] = value
    invalid(adapter.execute({"query": "CCO"}))


@pytest.mark.parametrize("kind", ["sparse", "extension", "known", "missing_section"])
def test_adme_py_sparse_known_and_extension_leaves(kind):
    raw = {"success": True, "data": [{"smiles": "CCO", "admet": {
        "prediction_method": "adme_py", "backend_version": "unknown",
    }}]}
    admet = raw["data"][0]["admet"]
    for key in ("physicochemical", "solubility", "lipophilicity", "pharmacokinetics", "druglikeness", "medicinal"):
        admet[key] = {}
    if kind == "extension":
        admet["medicinal"]["future_model"] = {"raw_result": "opaque", "score": "opaque"}
    elif kind == "known":
        admet["lipophilicity"]["wlogp"] = "private-marker"
    elif kind == "missing_section":
        del admet["solubility"]
    tool = CountingTool("admet_predictor", raw)
    registry = build_tool_registry([tool])
    try:
        result = registry.resolve(tool.name).execute({"query": "CCO"})
        if kind in {"known", "missing_section"}:
            invalid(result)
        else:
            assert result.success
            assert result.data == raw["data"]
            assert "wlogp" not in result.data[0]["admet"]["lipophilicity"]
    finally:
        registry.close()


def test_contract_itself_runs_in_invocation_worker(boundary, monkeypatch):
    from threading import get_ident
    adapter, tool = boundary
    original = adapter._validate_observation
    caller_thread, checked_threads = get_ident(), []
    def check(raw):
        if raw is tool.raw:
            checked_threads.append(get_ident())
        return original(raw)
    monkeypatch.setattr(adapter, "_validate_observation", check)
    assert adapter.execute({"query": "CCO"}).success
    assert len(checked_threads) == 1 and checked_threads[0] != caller_thread


@pytest.mark.parametrize("state", ["success", "partial", "failure"])
def test_every_output_validation_owns_worker_and_slot(boundary, monkeypatch, state):
    from threading import get_ident
    adapter, tool = boundary
    if state == "partial":
        tool.raw["status"] = "partial"
    elif state == "failure":
        tool.raw["success"] = False
    original = adapter._validate_observation
    caller_thread, checks = get_ident(), []
    def check(raw):
        slot_free = adapter._invocation_slots.acquire(blocking=False)
        if slot_free:
            adapter._invocation_slots.release()
        checks.append((isinstance(raw, ToolResult), get_ident(), slot_free))
        return original(raw)
    monkeypatch.setattr(adapter, "_validate_observation", check)
    result = adapter.execute({"query": "CCO"})
    assert result.success is (state != "failure")
    assert {normalized for normalized, _, _ in checks} == {False, True}
    assert all(thread != caller_thread and not slot_free for _, thread, slot_free in checks), checks
    assert tool.inputs == ["CCO"]


def test_caller_only_slow_validation_cannot_escape_deadline(boundary, monkeypatch):
    from threading import get_ident
    from time import perf_counter, sleep
    adapter, _ = boundary
    adapter.spec = replace(adapter.spec, timeout_seconds=0.03)
    original = adapter._validate_observation
    caller_thread, caller_checks = get_ident(), []
    def check(raw):
        if get_ident() == caller_thread:
            caller_checks.append(raw)
            sleep(0.08)
        return original(raw)
    monkeypatch.setattr(adapter, "_validate_observation", check)
    started = perf_counter()
    result = adapter.execute("CCO")
    elapsed = perf_counter() - started
    assert not caller_checks, (elapsed, result.status)
    assert result.success or result.error.code is AgentErrorCode.TOOL_TIMEOUT


@pytest.mark.parametrize("stage", ["raw", "normalized"])
def test_each_validation_stage_is_deadline_bounded_and_holds_slot(boundary, monkeypatch, stage):
    adapter, tool = boundary
    adapter.spec = replace(adapter.spec, timeout_seconds=0.03)
    original = adapter._validate_observation
    entered, release, finished = Event(), Event(), Event()
    def check(raw):
        selected = isinstance(raw, ToolResult) if stage == "normalized" else raw is tool.raw
        if selected and (not isinstance(raw, ToolResult) or raw.success):
            entered.set()
            try:
                assert release.wait(3)
            finally:
                finished.set()
        return original(raw)
    monkeypatch.setattr(adapter, "_validate_observation", check)
    try:
        result = adapter.execute("CCO")
        assert entered.is_set()
        assert result.error.code is AgentErrorCode.TOOL_TIMEOUT
        assert result.quality["deadline_exceeded"] is True
        busy = adapter.execute("CCO")
        assert busy.quality["capacity_exhausted"] is True
        assert tool.inputs == ["CCO"]
    finally:
        release.set()
        assert finished.wait(3)
    assert adapter._invocation_slots.acquire(timeout=3)
    adapter._invocation_slots.release()
    adapter.spec = replace(adapter.spec, timeout_seconds=3)
    assert adapter.execute("CCO").success


@pytest.mark.parametrize("field", ["warnings", "elapsed_ms"])
def test_normalized_postcheck_is_not_skipped(boundary, monkeypatch, field):
    from src.agent.tooling.adapters import ToolAdapter
    adapter, tool = boundary
    # Patch the actual data-transforming normalization, now performed in the
    # worker; the analysis outer hook only attaches trusted elapsed timing.
    normalize = ToolAdapter._normalize
    def malformed(self, raw, elapsed_ms):
        result = normalize(self, raw, elapsed_ms)
        setattr(result, field, "private-marker")
        return result
    monkeypatch.setattr(ToolAdapter, "_normalize", malformed)
    invalid(adapter.execute({"query": "CCO"}))


def test_redaction_of_known_scientific_field_is_checked(boundary):
    adapter, tool = boundary
    field = {"property_calculator": "qed", "drug_likeness_assessment": "qed_score",
             "admet_predictor": "log_s_esol"}[tool.name]
    adapter.spec = replace(adapter.spec, sensitive_fields={field})
    invalid(adapter.execute("CCO"))


def test_redaction_itself_stays_in_worker_deadline_and_slot(boundary, monkeypatch):
    from threading import get_ident
    import src.agent.tooling.adapters as adapters
    adapter, tool = boundary
    adapter.spec = replace(adapter.spec, timeout_seconds=0.03)
    redact = adapters.redact_sensitive
    caller_thread, calls = get_ident(), []
    entered, release, finished = Event(), Event(), Event()
    def blocked(value, fields):
        free = adapter._invocation_slots.acquire(blocking=False)
        if free:
            adapter._invocation_slots.release()
        calls.append((get_ident(), free))
        entered.set()
        try:
            assert release.wait(3)
            return redact(value, fields)
        finally:
            finished.set()
    monkeypatch.setattr(adapters, "redact_sensitive", blocked)
    try:
        result = adapter.execute("CCO")
        assert entered.is_set()
        assert result.error.code is AgentErrorCode.TOOL_TIMEOUT
        assert adapter.execute("CCO").quality["capacity_exhausted"] is True
        assert tool.inputs == ["CCO"]
    finally:
        release.set()
        assert finished.wait(3)
    assert adapter._invocation_slots.acquire(timeout=3)
    adapter._invocation_slots.release()
    assert calls and all(thread != caller_thread and not free for thread, free in calls)


@pytest.mark.parametrize("name,method", [
    ("property_calculator", "calculate_properties"),
    ("drug_likeness_assessment", "assess_drug_likeness"),
    ("admet_predictor", "predict_admet_with_adme_py"),
])
@pytest.mark.parametrize("wrapped", [False, True])
def test_registry_real_producers_never_compute_invalid_whole_input(monkeypatch, name, method, wrapped):
    monkeypatch.setattr(admet_module, "ADME_PY_AVAILABLE", False)
    producer = PRODUCERS[name]()
    monkeypatch.setattr(producer, method, lambda *_: pytest.fail("invalid batch must not compute"))
    registry = build_tool_registry([producer])
    try:
        query = "CCO\nC1CC"
        result = registry.resolve(name).execute({"query": query} if wrapped else query)
        assert not result.success and result.data is None
        # Legacy invalid molecular input remains a producer failure, not a new
        # transport error or partially computed valid prefix.
        assert result.error.code is AgentErrorCode.INTERNAL_ERROR
        assert result.error.details["raw_result"]["query"] == query
    finally:
        registry.close()


@pytest.mark.parametrize("failure", [False, True])
def test_admet_domain_gate_reused_including_failure_rows(monkeypatch, failure):
    monkeypatch.setattr(admet_module, "ADME_PY_AVAILABLE", False)
    raw = ADMETPredictor().execute("CCO")
    raw["success"] = not failure
    seen = []
    def reject(self, result):
        seen.append(result.data)
        return "private-marker"
    monkeypatch.setattr("src.agent.validators.domain_validators.ADMETResultValidator.validate", reject)
    tool = CountingTool("admet_predictor", raw)
    registry = build_tool_registry([tool])
    try:
        invalid(registry.resolve(tool.name).execute({"query": "CCO"}))
        assert raw["data"] in seen
    finally:
        registry.close()
