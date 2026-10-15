"""Offline target carry from owned, confirmed, version-bound scientific views."""
from contextlib import closing
from dataclasses import replace

import pytest

from src.agent.contracts.resolved_molecule import ResolvedScientificMolecule
from src.agent.contracts.scientific_references import ScientificPresentation
from src.agent.contracts.target_request import analyze_target_request
from src.agent.persistence.sqlite_store import SQLiteAgentStateStore
from src.web.scientific_references import (
    REFERENCE_CLARIFICATION, ScientificReferenceService, view_reference,
)
from tests.agent.test_scientific_reference_store import seed


SOURCE = "以 PDE 为靶点设计三个候选分子，要求分子量小于 450、logP 小于 3，返回 SMILES 并逐项检查约束。"
FOLLOWUP = "对第 2 个候选做两种优化，优先降低 logP，仍保留分子量小于 450 的约束。"


def source(store, query=SOURCE, *, trace="source", owner="owner"):
    selections = seed(store, trace=trace, session=owner)
    store.start_run({"trace_id": trace, "session_id": owner, "status": "succeeded",
                     "workflow_version": "1", "query": query, "metadata": {}})
    return selections


def publish(store, query=SOURCE, **options):
    selections = source(store, query)
    return store.publish_scientific_presentation(
        "source", session_id="owner", selections=selections, **options)


def pointer(view):
    return {"trace_id": view["source_trace_id"],
            "presentation_id": view["presentation_id"], "revision": view["revision"]}


def resolved(view):
    row = view["ordered_candidates"][1]
    return ResolvedScientificMolecule(**pointer(view), observation_id=row["observation_id"],
        candidate_id=row["candidate"]["candidate_id"],
        canonical_smiles=row["candidate"]["canonical_smiles"])


def continuation(svc, query, molecule, *, owner="owner"):
    method = getattr(svc, "continuation_query", None)
    assert callable(method), "confirmed target continuation boundary is missing"
    return method(query, molecule, session_id=owner)


@pytest.fixture
def confirmed(tmp_path):
    store = SQLiteAgentStateStore(tmp_path / "state.sqlite")
    view = publish(store)
    svc = ScientificReferenceService(store)
    assert svc.confirm(view_reference(view), session_id="owner")
    molecule = svc.resolve(FOLLOWUP, pointer(view), session_id="owner", enable_tools=True)
    assert molecule.canonical_smiles == "CCN"
    return store, svc, view, molecule


@pytest.mark.parametrize("query,expected", [
    (SOURCE, "PDE"),
    ("针对 pde5a 生成三个候选分子", "PDE5A"),
    ("针对 BuChE 生成三个候选分子", "BCHE"),
    ("Generate three candidates targeting EGFR", "EGFR"),
    ("生成三个候选分子", None),
    (None, None),
    ("针对 UNKNOWN 生成三个候选分子", None),
    ("针对 PDE 和 EGFR 生成三个候选分子", None),
    ("针对 PDE 和 UNKNOWN 生成三个候选分子", None),
    ("不要针对 PDE 生成三个候选分子", None),
    ("针对 PDE 生成三个候选分子，改为 EGFR", None),
    ("针对 PDE 生成选择性抑制剂", None),
    ("阅读 PDE 的介绍，生成三个候选分子", None),
])
def test_publication_derives_only_single_explicit_unqualified_source_target(tmp_path, query, expected):
    store = SQLiteAgentStateStore(tmp_path / "state.sqlite")
    view = publish(store, query)
    assert view is not None
    assert view["target"] == expected


def test_target_is_in_stable_revision_and_requires_exact_ack(tmp_path):
    store = SQLiteAgentStateStore(tmp_path / "state.sqlite")
    selections = source(store)
    view = store.publish_scientific_presentation("source", session_id="owner", selections=selections)
    assert view["target"] == "PDE"
    svc = ScientificReferenceService(store)
    assert svc.get(pointer(view), session_id="owner") is None
    assert not svc.confirm({**view_reference(view), "target": "EGFR"}, session_id="owner")
    assert svc.confirm(view_reference(view), session_id="owner")
    again = store.publish_scientific_presentation("source", session_id="owner", selections=selections)
    assert again == view  # No new revision, presentation, or TTL on replay.
    reopened = ScientificReferenceService(SQLiteAgentStateStore(store.db_path))
    assert reopened.get(pointer(view), session_id="owner") == view
    with pytest.raises(ValueError):
        ScientificPresentation.from_dict({**view, "target": "EGFR"})


def test_explicit_server_target_is_not_replaced_by_automatic_source_target(tmp_path):
    store = SQLiteAgentStateStore(tmp_path / "state.sqlite")
    assert publish(store, target="PDE5A")["target"] == "PDE5A"


def test_normal_web_projection_publishes_owned_source_target(tmp_path):
    from tests.agent.test_scientific_reference_web import seed as web_seed
    store = SQLiteAgentStateStore(tmp_path / "state.sqlite")
    result = web_seed(store)
    store.start_run({"trace_id": "trace", "session_id": "owner", "status": "completed",
                     "workflow_version": "1", "query": SOURCE, "metadata": {}})
    svc = ScientificReferenceService(store)
    event = svc.project(result, session_id="owner")[0]
    assert svc.confirm(event["reference"], session_id="owner")
    p = {key: event["reference"][key] for key in ("trace_id", "presentation_id", "revision")}
    assert svc.get(p, session_id="owner")["target"] == "PDE"


@pytest.mark.parametrize("before_ack", [True, False])
def test_source_query_change_invalidates_target_revision_and_ack(tmp_path, before_ack):
    store = SQLiteAgentStateStore(tmp_path / "state.sqlite")
    view = publish(store)
    svc = ScientificReferenceService(store)
    if not before_ack:
        assert svc.confirm(view_reference(view), session_id="owner")
    with closing(store._connect()) as connection, connection:
        connection.execute("UPDATE agent_runs SET query=? WHERE trace_id=?",
                           ("针对 EGFR 生成三个候选分子", "source"))
    assert not svc.confirm(view_reference(view), session_id="owner")
    assert svc.get(pointer(view), session_id="owner") is None
    with pytest.raises(ValueError):
        continuation(svc, FOLLOWUP, resolved(view))


@pytest.mark.parametrize("query", [FOLLOWUP, "Optimize candidate 2 to lower logP.",
                                  "Optimise the second candidate molecule."])
def test_confirmed_optimization_carries_only_bound_target_without_changing_structure(confirmed, query):
    store, svc, view, molecule = confirmed
    before = store.get_scientific_presentation(**pointer(view), session_id="owner")
    updated = continuation(svc, query, molecule)
    assert updated == query + "\n靶点: PDE"
    assert molecule.routing_query(updated) == updated + "\nSMILES: CCN"
    target = analyze_target_request(molecule.routing_query(updated))
    assert target.targets == ("PDE",) and not target.needs_clarification
    assert continuation(svc, updated, molecule) == updated  # No repeated label.
    assert store.get_scientific_presentation(**pointer(view), session_id="owner") == before


@pytest.mark.parametrize("query", [
    "对第2个候选针对 EGFR 做优化", "Optimize candidate 2 against UNKNOWN.",
    "优化第2个候选，靶点: UNKNOWN", "优化第2个候选，针对 PDE 和 EGFR",
    "优化第2个候选，不要针对 PDE", "优化第2个候选，换一个靶点",
    "优化第2个候选，不指定靶点", "Optimize candidate 2 without a target.",
    "优化第2个候选，改为 UNKNOWN", "优化第2个候选，提高选择性",
    "计算第2个候选的 logP", "Generate three new molecules.",
    "不要优化第2个候选，仅计算 logP", "Do not optimize candidate 2; calculate logP.",
    "解释如何优化第2个候选", "Explain optimization of candidate 2.",
    "优化 SMILES: CCC，降低 logP",
])
def test_current_target_instructions_and_nonoptimization_requests_never_inherit(confirmed, query):
    _, svc, _, molecule = confirmed
    assert continuation(svc, query, molecule) == query


@pytest.mark.parametrize("target", [None, "UNKNOWN", "PDE and EGFR", "靶点: PDE"])
def test_missing_or_nonidentifier_view_target_never_infers_from_prose(tmp_path, target):
    store = SQLiteAgentStateStore(tmp_path / "state.sqlite")
    view = publish(store, "生成三个候选分子", target=target)
    svc = ScientificReferenceService(store)
    assert svc.confirm(view_reference(view), session_id="owner")
    assert continuation(svc, FOLLOWUP, resolved(view)) == FOLLOWUP


@pytest.mark.parametrize("invalid", ["unconfirmed", "foreign", "revision", "candidate", "smiles", "revoked", "expired"])
def test_continuation_revalidates_owned_confirmed_candidate(tmp_path, monkeypatch, invalid):
    from src.agent.persistence import scientific_references as boundary
    store = SQLiteAgentStateStore(tmp_path / "state.sqlite")
    view = publish(store)
    svc = ScientificReferenceService(store)
    molecule = resolved(view)
    if invalid != "unconfirmed":
        assert svc.confirm(view_reference(view), session_id="owner")
    owner = "other" if invalid == "foreign" else "owner"
    if invalid == "revision": molecule = replace(molecule, revision="0" * 64)
    if invalid == "candidate": molecule = replace(molecule, candidate_id="foreign")
    if invalid == "smiles": molecule = replace(molecule, canonical_smiles="CCC")
    if invalid == "revoked": store.update_run_status("source", "failed")
    if invalid == "expired": monkeypatch.setattr(boundary.time, "time", lambda: view["expires_at"])
    with pytest.raises(ValueError) as denied:
        continuation(svc, FOLLOWUP, molecule, owner=owner)
    assert str(denied.value) == REFERENCE_CLARIFICATION


def test_no_resolved_reference_never_looks_up_other_history(confirmed):
    _, svc, _, _ = confirmed
    assert continuation(svc, FOLLOWUP, None) == FOLLOWUP
    assert continuation(svc, FOLLOWUP, None, owner="other") == FOLLOWUP


def test_browser_shaped_selection_cannot_supply_continuation_context(confirmed):
    _, svc, view, _ = confirmed
    with pytest.raises(ValueError):
        continuation(svc, FOLLOWUP, {**pointer(view), "target": "EGFR", "canonical_smiles": "CCN"})


def test_revocation_during_continuation_read_cannot_append_target(confirmed, monkeypatch):
    store, svc, _, molecule = confirmed
    get = svc.get
    def revoked_after_read(*args, **kwargs):
        result = get(*args, **kwargs)
        store.update_run_status("source", "failed")
        return result
    monkeypatch.setattr(svc, "get", revoked_after_read)
    with pytest.raises(ValueError):
        continuation(svc, FOLLOWUP, molecule)
