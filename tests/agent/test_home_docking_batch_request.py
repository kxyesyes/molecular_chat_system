import pytest

from src.agent.persistence.sqlite_store import SQLiteAgentStateStore
from src.web.docking_request import OwnedDockingStructureEvidence
from src.web.scientific_references import ScientificReferenceService
from tests.agent.test_scientific_reference_web import seed, pointer
from tests.agent.test_web_docking_consent import target_case, republish


def parse(query, **kwargs):
    from src.web.docking_batch_request import parse_batch_request

    inputs = dict(reference=None, references=None, session_id="owner",
                  structure_context=None, max_ligands=4)
    return parse_batch_request(query, **{**inputs, **kwargs})


def test_explicit_list_order_and_stereochemistry():
    assert parse("把 CCO 和 CCN 分别与 PDB 6SAM 对接") == (
        "6SAM", [{"ordinal": 1, "smiles": "CCO"}, {"ordinal": 2, "smiles": "CCN"}])
    assert parse("请把 C[C@H](F)Cl、C[C@@H](F)Cl 和 F/C=C/F 与 6sam 对接。")[1] == [
        {"ordinal": 1, "smiles": "C[C@H](F)Cl"}, {"ordinal": 2, "smiles": "C[C@@H](F)Cl"},
        {"ordinal": 3, "smiles": "F/C=C/F"}]


def test_conjunction_before_named_receptor_and_english_request():
    wanted = ("6SAM", [{"ordinal": 1, "smiles": "CCO"}, {"ordinal": 2, "smiles": "CCN"}])
    assert parse("把 CCO 和 CCN 分别和 PDB 6SAM 对接") == wanted
    assert parse("用配体 CCO 和配体 CCN 与 PDB 6SAM 进行分子对接。") == wanted
    assert parse("Please dock CCO and CCN against PDB 6sam.") == wanted


@pytest.mark.parametrize("rows", ["CCO 和 C1CC", "CCO 和 CCN garbage", "CCO 和 CCN?oops",
                                  "CCO、、CCN", "CCO 和 "])
def test_invalid_or_empty_whole_row_blocks_list(rows):
    with pytest.raises(ValueError, match="第 2"):
        parse(f"把 {rows} 分别与 PDB 6SAM 对接")


def test_limit_and_missing_or_incomplete_receptor():
    with pytest.raises(ValueError, match="2"):
        parse("把 CCO、CCN、CCC 都与 PDB 6SAM 对接", max_ligands=2)
    for query in ("把 CCO 和 CCN 分别对接", "把 CCO 和 CCN 与刚才的受体对接",
                  "把 CCO 和 CCN 与 PDB 6SAMXYZ 对接"):
        with pytest.raises(ValueError, match="PDB"):
            parse(query)


@pytest.mark.parametrize("query", [
    "把 CCO 与 6SAM 对接", "用 CCO 与 PDB 6SAM 进行分子对接。",
    "用 CCO 和 PDB 6SAM 对接", "使用上述 PDB 6SAM 和 CCO 对接",
    "把候选 3 与刚才的受体对接", "预测 CCO 和 CCN 的性质",
    "不要把 CCO 和 CCN 与 6SAM 对接", "把 CCO 和 CCN 与 6SAM 对接并预测活性",
    "把 CCO 和 CCN 与 6SAM 对接吗？",
])
def test_single_other_and_negated_operations_fall_through(query):
    assert parse(query) is None


def test_candidate_list_uses_only_confirmed_server_rows_and_keeps_ordinals(tmp_path):
    store = SQLiteAgentStateStore(tmp_path / "candidates.sqlite")
    refs = ScientificReferenceService(store)
    event = refs.project(seed(store, (("CCO", "CCN", "CCC"),)), session_id="owner")[0]
    args = dict(reference=pointer(event), references=refs)
    query = "把候选 3 和 1 都与 PDB 6SAM 对接"
    with pytest.raises(ValueError, match="确认"):
        parse(query, **args)
    assert refs.confirm(event["reference"], session_id="owner")
    assert parse(query, **args) == (
        "6SAM", [{"ordinal": 3, "smiles": "CCC"}, {"ordinal": 1, "smiles": "CCO"}])
    with pytest.raises(ValueError, match="确认"):
        parse(query, session_id="another", **args)
    with pytest.raises(ValueError, match="候选 4"):
        parse("把候选 3 和候选 4 都与 6SAM 对接", **args)
    with pytest.raises(ValueError, match="第 2"):
        parse("把候选 3 和 1.5 都与 6SAM 对接", **args)
    with pytest.raises(ValueError, match="候选 3.*重复"):
        parse("把候选 3 和候选 3 都与 6SAM 对接", **args)


def test_previous_receptor_requires_one_owned_structure(target_case):
    owner = "owner-session"
    refs = ScientificReferenceService(target_case.store)
    evidence = OwnedDockingStructureEvidence(target_case.store)
    context = evidence.capture(target_case.agent, session_id=owner)
    args = dict(structure_context=context, references=refs, session_id=owner)
    query = "把 CCO 和 CCN 都与刚才的受体对接"
    assert parse(query, **args)[0] == "1ABC"
    assert parse("把 CCO 和 CCN 都与 PDB 6SAM 对接", **args)[0] == "6SAM"
    with pytest.raises(ValueError, match="PDB"):
        parse(query, **{**args, "session_id": "another"})
    republish(target_case, lambda rows: rows[0]["recommended_structures"].append(
        {**rows[0]["recommended_structures"][0], "structure_id": "2XYZ"}))
    with pytest.raises(ValueError, match="PDB"):
        parse(query, **args)
    args["structure_context"] = evidence.capture(target_case.agent, session_id=owner)
    with pytest.raises(ValueError, match="PDB"):
        parse(query, **args)


@pytest.mark.parametrize("both_confirmed", [False, True])
def test_multi_collection_with_single_pointer_cannot_guess_card_numbers(tmp_path, both_confirmed):
    store = SQLiteAgentStateStore(tmp_path / "multiple.sqlite")
    refs = ScientificReferenceService(store)
    first, second = refs.project(seed(store, (("CCO", "CCN"), ("CCC", "O"))), session_id="owner")
    if both_confirmed:
        assert refs.confirm(first["reference"], session_id="owner")
    assert refs.confirm(second["reference"], session_id="owner")
    # The later collection is labelled 3–4 on initial render and 1–2 when
    # restored alone. Both send the very same confirmation pointer.
    with pytest.raises(ValueError, match="SMILES"):
        parse("把候选 2 和 1 都与 PDB 6SAM 对接", reference=pointer(second), references=refs)


def test_single_confirmed_collection_keeps_its_last_visible_card(tmp_path):
    store = SQLiteAgentStateStore(tmp_path / "full.sqlite")
    refs = ScientificReferenceService(store)
    molecules = tuple("C" * n for n in range(1, 33))
    event, = refs.project(seed(store, (molecules,)), session_id="owner")
    assert refs.confirm(event["reference"], session_id="owner")
    assert parse("把候选 32 和 1 都与 PDB 6SAM 对接", reference=pointer(event), references=refs)[1] == [
        {"ordinal": 32, "smiles": molecules[-1]}, {"ordinal": 1, "smiles": "C"}]
