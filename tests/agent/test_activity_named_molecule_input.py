"""Chinese molecule labels must not become targets; service rows are synthetic."""
import copy
from itertools import permutations

import pytest

from src.agent.contracts import ObservationStatus
from src.agent.tools.activity_input import parse_activity_input
from src.agent.tools.activity_predictor_tool import ActivityPredictorTool
from src.agent.tools.base_tool import execute_tool_compat
from tests.agent.test_family_activity_tool import boundary, family_row


ASPIRIN = "CC(=O)Oc1ccccc1C(=O)O"
CAFFEINE = "CN1C(=O)N(C)c2ncn(C)c2C1=O"
PARACETAMOL = "CC(=O)Nc1ccc(O)cc1"
S06 = (
    f"分别预测阿司匹林 {ASPIRIN}、咖啡因 {CAFFEINE} 和对乙酰氨基酚 {PARACETAMOL} "
    "对 BuChE 的活性，保留每个候选的原始预测和复核提示。"
)
S11 = (
    f"比较阿司匹林 {ASPIRIN} 和对乙酰氨基酚 {PARACETAMOL} 的性质、ADMET 和 BuChE 活性，"
    "按可用且通过检查的证据排序，保留所有原始结果与排除原因。"
)
S06_ORDERINGS = list(permutations([
    ("阿司匹林", ASPIRIN), ("咖啡因", CAFFEINE), ("对乙酰氨基酚", PARACETAMOL),
]))


@pytest.mark.parametrize("query,smiles", [
    (S06, [ASPIRIN, CAFFEINE, PARACETAMOL]),
    (S11, [ASPIRIN, PARACETAMOL]),
], ids=["s06-three-candidates", "s11-two-candidates"])
@pytest.mark.parametrize("structured", [False, True])
def test_browser_activity_queries_preserve_target_and_ordered_candidates(query, smiles, structured):
    payload = {"query": query, "smiles": smiles, "target": "BuChE"} if structured else query
    tool = ActivityPredictorTool()

    assert parse_activity_input(payload, tool) == (query, smiles, "BuChE")
    assert tool.should_use(payload)


@pytest.mark.parametrize("entries", S06_ORDERINGS, ids=[f"order-{i}" for i in range(6)])
@pytest.mark.parametrize("separator", ["、", " 和", "；", ";", "\n"],
                         ids=["comma", "conjunction", "chinese-semicolon", "semicolon", "newline"])
@pytest.mark.parametrize("structured", [False, True])
def test_s06_all_candidate_orders_and_list_boundaries(entries, separator, structured):
    query = "分别预测" + separator.join(f"{name} {smiles}" for name, smiles in entries)
    query += " 对 BuChE 的活性，保留每个候选的原始预测和复核提示。"
    smiles = [value for _, value in entries]
    payload = {"query": query, "smiles": smiles, "target": "BuChE"} if structured else query

    assert parse_activity_input(payload, ActivityPredictorTool()) == (query, smiles, "BuChE")


@pytest.mark.parametrize("prefix", ["", "预测", "请评估 ", "比较"])
@pytest.mark.parametrize("name,smiles", [
    ("对氨基苯酚", "Nc1ccc(O)cc1"),
    ("对甲苯胺", "Cc1ccc(N)cc1"),
    ("对苯二甲酸", "O=C(O)c1ccc(C(=O)O)cc1"),
])
def test_first_compound_label_uses_chemical_syntax_not_literal_name(prefix, name, smiles):
    query = f"{prefix}{name} {smiles} 对 BuChE 的活性"
    assert parse_activity_input(query, ActivityPredictorTool()) == (query, [smiles], "BuChE")


@pytest.mark.parametrize("query", [
    "预测阿司匹林 {aspirin} 和对氨基苯酚 Nc1ccc(O)cc1 对BuChE的活性",
    "评估阿司匹林 {aspirin} 及对氨基苯酚 Nc1ccc(O)cc1 对 BuChE 的活性",
    "比较阿司匹林 {aspirin}、对氨基苯酚 Nc1ccc(O)cc1 对 BuChE 的活性",
    "比较阿司匹林 {aspirin} 与 对氨基苯酚 Nc1ccc(O)cc1 对 BuChE 的活性",
], ids=["attached-target", "spaced-target", "list-comma", "spaced-conjunction"])
def test_chinese_named_structure_positions_are_not_specific_to_paracetamol(query):
    query = query.format(aspirin=ASPIRIN)
    expected = ([ASPIRIN] if ASPIRIN in query else []) + ["Nc1ccc(O)cc1"]
    assert parse_activity_input(query, ActivityPredictorTool()) == (query, expected, "BuChE")


def test_consecutive_named_candidates_keep_every_structure():
    query = f"预测乙醇 CCO、对乙酰氨基酚 {PARACETAMOL} 和对氨基苯酚 Nc1ccc(O)cc1 对 BuChE 的活性"
    assert parse_activity_input(query, ActivityPredictorTool()) == (
        query, ["CCO", PARACETAMOL, "Nc1ccc(O)cc1"], "BuChE",
    )


def test_s11_structured_bindings_preserve_candidate_ids_and_target(boundary):
    tool, calls, _ = boundary
    payload = {"query": S11, "target": "BuChE", "smiles": [ASPIRIN, PARACETAMOL],
               "candidate_ids": ["candidate-aspirin", "candidate-paracetamol"], "endpoint": "pIC50"}
    original = copy.deepcopy(payload)

    result = execute_tool_compat(tool, payload)

    assert result.success
    assert calls == [([ASPIRIN, PARACETAMOL], "BuChE")]
    assert [row["smiles"] for row in result.data] == payload["smiles"]
    assert [row["candidate_id"] for row in result.data] == payload["candidate_ids"]
    assert all(row["requested_target"] == "BuChE" for row in result.data)
    assert payload == original


@pytest.mark.parametrize("target", ["EGFR", "XPDE5A", "BuChE和PDE5A", "BuChE和EGFR", "BuChE$EGFR"])
def test_named_molecules_do_not_hide_unknown_or_conflicting_targets(boundary, target):
    tool, calls, _ = boundary
    result = execute_tool_compat(tool, S06.replace("BuChE", target))
    assert result.status == ObservationStatus.INVALID_INPUT
    assert "靶点" in result.message
    assert calls == []


@pytest.mark.parametrize("payload", [
    {"query": S06, "smiles": [ASPIRIN, CAFFEINE, PARACETAMOL], "target": "PDE5A"},
    "预测CCO对乙酰胆碱酯酶的活性；BuChE",
    "靶点：对乙酰氨基酚 CCO；预测 BuChE 活性",
    "预测对乙酰胆碱酯酶 CCO 的活性；BuChE",
    "预测CCO和对丁酰胆碱酯酶 CCN 的活性；PDE5A",
])
def test_explicit_target_validation_remains_authoritative(boundary, payload):
    tool, calls, _ = boundary
    assert execute_tool_compat(tool, payload).status == ObservationStatus.INVALID_INPUT
    assert calls == []


@pytest.mark.parametrize("label", [
    "乙酰胆碱酯酶", "未知激酶", "肿瘤相关蛋白", "雌激素受体", "钠离子通道",
    "未知靶点", "未知候选", "未知蛋白苯酚", "未知受体苯胺",
])
@pytest.mark.parametrize("structured", [False, True])
@pytest.mark.parametrize("prefix", ["预测", "预测CCO和", "预测CCO；"])
def test_unknown_target_like_labels_are_not_reclassified_as_molecule_names(boundary, label, structured, prefix):
    tool, calls, _ = boundary
    query = f"{prefix}对{label} CCN 的活性；BuChE"
    payload = {"query": query, "smiles": ["CCO", "CCN"], "target": "BuChE"} if structured else query

    result = execute_tool_compat(tool, payload)

    assert result.status == ObservationStatus.INVALID_INPUT
    assert "靶点" in result.message
    assert calls == []


@pytest.mark.parametrize("target", [None, "EGFR", "BuChE 和 PDE5A"])
def test_first_compound_label_does_not_supply_or_override_target(boundary, target):
    tool, calls, _ = boundary
    query = f"预测对乙酰氨基酚 {PARACETAMOL} 的活性"
    if target is not None:
        query += f"；对 {target} 的活性"
    result = execute_tool_compat(tool, query)
    assert result.status == ObservationStatus.INVALID_INPUT
    assert calls == []


@pytest.mark.parametrize("invalid", [PARACETAMOL + "$bad", "C1CC", PARACETAMOL + " |atomProp:0.foo.bar|"])
def test_first_named_invalid_structure_is_never_repaired(boundary, invalid):
    tool, calls, _ = boundary
    query = f"预测对乙酰氨基酚 {invalid}、阿司匹林 {ASPIRIN} 对 BuChE 的活性"
    assert execute_tool_compat(tool, query).status == ObservationStatus.INVALID_INPUT
    assert calls == []


@pytest.mark.parametrize("invalid", [PARACETAMOL + "$bad", "C1CC", PARACETAMOL + " |atomProp:0.foo.bar|"])
def test_named_invalid_candidate_is_never_dropped_or_repaired(boundary, invalid):
    tool, calls, _ = boundary
    assert execute_tool_compat(tool, S06.replace(PARACETAMOL, invalid)).status == ObservationStatus.INVALID_INPUT
    assert calls == []


def test_three_candidate_dispatch_preserves_raw_predictions_and_review_conflicts(boundary):
    from src.activity.request_selection import ActivityModelRequest, request_identity

    tool, calls, state = boundary
    rows = []
    for index, smiles in enumerate([ASPIRIN, CAFFEINE, PARACETAMOL]):
        row = family_row(smiles=smiles, requested_target="BuChE", family_id="buche-family")
        if index == 1:
            row.update(success=False, status="partial", execution_status="passed",
                       predicted_pIC50=6.23456789, activity_probability=0.12345678,
                       classification_regression_consistent=False,
                       warnings=["分类与回归预测不一致，需复核；已保留两项原始结果。"])
        provenance = row["provenance"]
        provenance["request"]["family_id"] = "buche-family"
        for model in provenance["models"].values():
            model["target_id"] = "buche-family"
        provenance["request"]["identity"] = request_identity(
            ActivityModelRequest("buche-family", "pIC50", "pIC50", None, "endpoint_ready"),
            provenance["bundle_id"], provenance["models"],
        )
        rows.append(row)
    state.update(rows=rows, status="partial")
    original = copy.deepcopy(rows)

    result = execute_tool_compat(tool, S06)

    assert calls == [([ASPIRIN, CAFFEINE, PARACETAMOL], "BuChE")]
    assert result.status == ObservationStatus.PARTIAL
    assert not result.success and result.error is None
    assert result.data == original
    assert result.evidence == [{"prediction": row} for row in original]
    assert result.quality["model_provenance"] == [row["provenance"] for row in original]
    assert rows == original
    assert original[1]["warnings"][0] in result.warnings
    assert "计算已完成，分类与回归不一致，需复核" in result.formatted
