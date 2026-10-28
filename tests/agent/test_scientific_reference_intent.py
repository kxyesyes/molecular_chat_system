"""Offline distinction between new generation counts and confirmed references."""
import pytest

from src.agent.persistence.sqlite_store import SQLiteAgentStateStore
from src.web.scientific_references import (
    REFERENCE_CLARIFICATION,
    ScientificReferenceService,
    explicit_ordinal,
)
from tests.agent.test_scientific_reference_web import pointer, seed


NEW_GENERATION_QUERIES = [
    "请使用分子生成工具生成 2 个候选小分子，返回有效 SMILES，并说明生成模型来源",
    "以 PDE 为靶点设计三个候选分子，要求分子量小于 450、logP 小于 3，返回 SMILES 并逐项检查约束。",
    "设计 3 个候选分子，计算 logP。",
    "生成两个候选，要求分子量小于 450。",
    "生成候选分子并计算 logP。",
    "Design three candidate molecules with logP below 3.",
    "Generate 3 candidates and calculate logP.",
    "Create two molecules with logP below 3.",
    "Generate a candidate molecule with logP below 3.",
    "Generate candidate molecules and calculate logP.",
    "Generate three candidates with logP above 1 and below 3.",
    "Generate three candidates for the selected target with logP below 3.",
    "为上述靶点生成三个候选分子并计算 logP。",
    "基于 PDE 靶点设计三个候选分子，计算 logP。",
    "Generate three molecules based on the selected target and calculate logP.",
    "Generate three molecules from scratch and calculate logP.",
    "Generate three molecules and calculate logP.",
    "Generate three molecules from these targets and calculate logP.",
    "Generate three molecules based on those targets and calculate logP.",
    "Generate three molecules based on those PDE targets and calculate logP.",
]

ORDINAL_QUERIES = [
    "计算第2个候选的 logP",
    "计算第 2 个候选的 logP",
    "计算第二个候选分子的性质",
    "calculate logP for candidate 2",
    "calculate logP for the second candidate molecule",
    "calculate logP for molecule #2",
    "以第2个候选为基础生成三个分子，计算 logP",
    "Generate three molecules from candidate 2 with lower logP.",
    "以第2个候选为基础生成三个分子",
    "Generate three molecules from candidate 2.",
]


@pytest.fixture
def reference_view(tmp_path):
    store = SQLiteAgentStateStore(tmp_path / "state.db")
    svc = ScientificReferenceService(store)
    event = svc.project(seed(store), session_id="owner")[0]
    return store, svc, event


@pytest.mark.parametrize("query", NEW_GENERATION_QUERIES)
def test_new_generation_count_does_not_require_previous_reference(query):
    # No source store is needed: this is a new task, not a reference lookup.
    svc = ScientificReferenceService(None)
    assert svc.resolve(query, session_id="fresh", enable_tools=True) is None


@pytest.mark.parametrize("query", NEW_GENERATION_QUERIES)
@pytest.mark.parametrize("selected", [False, True])
def test_new_generation_does_not_reuse_stale_card(reference_view, query, selected):
    store, svc, event = reference_view
    assert svc.confirm(event["reference"], session_id="owner")
    store.update_run_status("trace", "failed")
    selection = {"ordinal": 2} if selected else None
    assert svc.resolve(query, pointer(event), selection,
                       session_id="owner", enable_tools=True) is None


@pytest.mark.parametrize("query", NEW_GENERATION_QUERIES)
def test_cardinal_generation_numbers_are_not_ordinals(query):
    assert explicit_ordinal(query) is None


@pytest.mark.parametrize("query", ORDINAL_QUERIES)
def test_ordinal_references_require_owner_confirmed_view(reference_view, query):
    _, svc, event = reference_view
    p = pointer(event)
    with pytest.raises(ValueError) as missing:
        svc.resolve(query, session_id="owner", enable_tools=True)
    assert str(missing.value) == REFERENCE_CLARIFICATION
    with pytest.raises(ValueError):
        svc.resolve(query, p, session_id="owner", enable_tools=True)
    assert svc.confirm(event["reference"], session_id="owner")
    with pytest.raises(ValueError):
        svc.resolve(query, p, session_id="other", enable_tools=True)
    with pytest.raises(ValueError):
        svc.resolve(query, p, {"ordinal": 1}, session_id="owner", enable_tools=True)
    resolved = svc.resolve(query, p, session_id="owner", enable_tools=True)
    assert resolved.canonical_smiles == "CCN"
    assert resolved.candidate_id == event["candidate_set"]["candidates"][1]["candidate_id"]


def test_property_provenance_wording_keeps_confirmed_candidate_selection(reference_view):
    _, svc, event = reference_view
    assert svc.confirm(event["reference"], session_id="owner")
    key = event["reference"]["ordered_keys"][1]
    selection = {"observation_id": key[0], "candidate_id": key[1]}
    query = "请分析当前选中的候选分子的分子性质，并说明真实工具来源"

    resolved = svc.resolve(query, pointer(event), selection,
                           session_id="owner", enable_tools=True)

    assert resolved.canonical_smiles == "CCN"
    assert resolved.candidate_id == key[1]


@pytest.mark.parametrize("query", [
    "以刚才的候选生成三个分子，计算 logP",
    "基于这个分子生成两个候选，计算 logP",
    "Generate three candidates from the previous molecule with lower logP.",
    "Generate two molecules from this candidate and calculate logP.",
    "Generate three molecules like it.",
    "基于那个分子生成两个候选",
    "计算候选分子的 logP",
    "calculate logP for the candidate molecule",
])
def test_ambiguous_prior_reference_still_requires_explicit_selection(reference_view, query):
    _, svc, event = reference_view
    assert svc.confirm(event["reference"], session_id="owner")
    with pytest.raises(ValueError):
        svc.resolve(query, pointer(event), session_id="owner", enable_tools=True)


@pytest.mark.parametrize("query", [
    "以第33个候选生成三个分子，计算 logP",
    "以第1个和第2个候选生成三个分子，计算 logP",
    "Generate three molecules from candidates 1 and 2 and calculate logP.",
])
def test_generation_wording_does_not_bypass_ordinal_bounds(reference_view, query):
    _, svc, event = reference_view
    assert svc.confirm(event["reference"], session_id="owner")
    with pytest.raises(ValueError):
        svc.resolve(query, pointer(event), session_id="owner", enable_tools=True)


UNSUPPORTED_ORDINAL_QUERIES = [
    "以第 1 和 2 个候选为基础生成三个分子，计算 logP",
    "以第 1 到 2 个候选为基础生成三个分子，计算 logP",
    "以第 1、2 个候选为基础生成三个分子，计算 logP",
    "以第 1 和 第 2 个候选为基础生成三个分子，计算 logP",
    "以第 1 和 2 候选为基础生成三个分子，计算 logP",
    "以第2个和第 3 候选为基础生成三个分子，计算 logP",
    "Generate three molecules from candidate 1234 and calculate logP.",
    "Generate three molecules from the 2nd candidate and calculate logP.",
    "Generate three molecules from the fourth candidate and calculate logP.",
    "Generate three molecules from the first candidates and calculate logP.",
    "Generate three molecules from first candidate and fourth and calculate logP.",
    "Generate three molecules from candidate 1.5 and calculate logP.",
    "Generate three molecules from candidate -1 and calculate logP.",
    "Generate three molecules from candidate #unknown and calculate logP.",
]


@pytest.mark.parametrize("query", UNSUPPORTED_ORDINAL_QUERIES)
def test_unparsed_explicit_ordinal_is_not_absent(query, reference_view):
    # Unsupported forms must not become either a new task or a UI selection.
    with pytest.raises(ValueError) as invalid:
        explicit_ordinal(query)
    assert str(invalid.value) == REFERENCE_CLARIFICATION
    _, svc, event = reference_view
    assert svc.confirm(event["reference"], session_id="owner")
    for p in (None, pointer(event)):
        for selection in (None, {"ordinal": 2}):
            with pytest.raises(ValueError) as rejected:
                svc.resolve(query, p, selection, session_id="owner", enable_tools=True)
            assert str(rejected.value) == REFERENCE_CLARIFICATION


@pytest.mark.parametrize("query", [
    "基于上面的分子生成三个候选，计算 logP",
    "参照前面给出的候选生成三个分子，计算 logP",
    "从候选分子出发生成三个分子，计算 logP",
    "以上次选定的分子为基础生成三个候选，计算 logP",
    "Generate three molecules from the last candidate and calculate logP.",
    "Generate three molecules based on the previously optimized candidate and calculate logP.",
    "Generate three molecules using the candidate and calculate logP.",
    "Generate three molecules like the molecule and calculate logP.",
    "Generate three molecules from the candidate above and calculate logP.",
    "Generate three molecules from these and calculate logP.",
    "Generate three molecules based on those and calculate logP.",
])
def test_source_reference_needs_confirmed_owned_view_and_explicit_selection(reference_view, query):
    store, svc, event = reference_view
    p = pointer(event)
    selection = {"ordinal": 2}
    for ptr in (None, p):
        with pytest.raises(ValueError) as unconfirmed:
            svc.resolve(query, ptr, selection, session_id="owner", enable_tools=True)
        assert str(unconfirmed.value) == REFERENCE_CLARIFICATION
    assert svc.confirm(event["reference"], session_id="owner")
    for ptr, selected, owner in (
        (None, None, "fresh"),
        (p, None, "owner"),
        (p, selection, "other"),
        ({**p, "revision": "0" * 64}, selection, "owner"),
        (p, {"ordinal": 0}, "owner"),
        (p, {"ordinal": 3}, "owner"),
        (p, {"ordinal": 33}, "owner"),
        (p, {"observation_id": "foreign", "candidate_id": "foreign"}, "owner"),
    ):
        with pytest.raises(ValueError) as rejected:
            svc.resolve(query, ptr, selected, session_id=owner, enable_tools=True)
        assert str(rejected.value) == REFERENCE_CLARIFICATION
    resolved = svc.resolve(query, p, selection, session_id="owner", enable_tools=True)
    assert resolved.canonical_smiles == "CCN"
    key = event["reference"]["ordered_keys"][1]
    by_key = {"observation_id": key[0], "candidate_id": key[1]}
    assert svc.resolve(query, p, by_key, session_id="owner", enable_tools=True) == resolved
    store.update_run_status("trace", "failed")
    with pytest.raises(ValueError):
        svc.resolve(query, p, selection, session_id="owner", enable_tools=True)
