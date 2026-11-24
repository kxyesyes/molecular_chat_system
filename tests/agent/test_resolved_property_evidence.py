"""Real local RDKit + synthetic confirmed sources; no model/scientific API."""
from copy import deepcopy

import pytest

from tests.agent.test_browser_s02_context import RecordingProperties
from tests.agent.test_scientific_reference_execution import confirmed
from tests.agent.pi_web_support import cleanup_web_handlers, make_web_handler, run_web_turn
from tests.agent.test_scientific_reference_web import seed, service, pointer
from src.agent.persistence.sqlite_store import SQLiteAgentStateStore


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    import requests
    import socket

    def forbidden(*args, **kwargs):
        pytest.fail("resolved property tests must not use external services")

    monkeypatch.setattr(requests.Session, "request", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


def handler(store, references, properties=None):
    properties = properties or RecordingProperties()
    chat = make_web_handler({properties.name: properties}, store)
    chat.scientific_references = references
    return chat, properties


def turn(chat, query, history, *, reference=None, selection=None, owner="owner"):
    complete, _ = run_web_turn(chat, query, history, owner, reference=reference, selection=selection)
    return complete


def test_reread_and_repeat_follow_the_rendered_order_through_web_turns(tmp_path):
    from src.agent.utils.validators import InputValidator

    class ReverseRows(RecordingProperties):
        def execute(self, query):
            result = super().execute(query)
            result['data'].reverse()
            return result

    properties = ReverseRows()
    chat = make_web_handler([properties], SQLiteAgentStateStore(str(tmp_path / 'agent.sqlite')))
    history = []
    batch = turn(chat, '比较 CCO 和 CCN 的分子量。', history)
    assert batch['status'] == 'completed', (batch['content'], properties.calls)
    assert batch['content'].index('CCN') < batch['content'].index('CCO')
    assert len(properties.calls) == 1
    for query, smiles in [('第二个的分子量是多少？', 'CCO'), ('那第一个呢？', 'CCN'), ('它的分子量是多少？', 'CCN')]:
        view = turn(chat, query, history)
        assert view['status'] == 'completed' and smiles in view['content']
        assert ('CCO' if smiles == 'CCN' else 'CCN') not in view['content']
        assert len(properties.calls) == 1
    repeat = turn(chat, '重新算一次', history)
    assert repeat['status'] == 'completed' and 'CCN' in repeat['content']
    assert len(properties.calls) == 2
    assert list(InputValidator().analyze_molecular_input(properties.calls[-1]).valid_smiles) == ['CCN']
    assert 'CCO' in turn(chat, '那第二个呢？', history)['content']
    assert len(properties.calls) == 2


@pytest.mark.parametrize("query,selection,smiles", [
    ("只计算刚才第一个分子的分子量和 logP，不要 ADMET 或活性", None, "CCO"),
    ("只计算分子量和 logP，不要 ADMET 或活性", {"ordinal": 2}, "CCN"),
])
def test_confirmed_identity_supplies_properties_and_next_turn_evidence(tmp_path, query, selection, smiles):
    store, references, p = confirmed(tmp_path)
    chat, properties = handler(store, references)
    history = []
    expected = properties.calculate_properties(smiles)
    complete = turn(chat, query, history, reference=p, selection=selection)
    assert properties.calls == [smiles]
    evidence = history[-1].get("property_evidence")
    assert evidence is not None, complete
    assert evidence["canonical_smiles"] == smiles and evidence["session_id"] == "owner"
    assert evidence["molecular_weight"] == expected["molecular_weight"]
    assert evidence["logp"] == expected["logp"]
    assert evidence["trace_id"] and evidence["evidence_id"]
    assert "证据不可用" not in complete["content"]
    assert "RDKit" in complete["content"] and "非实测" in complete["content"]
    assert history[-1]["user"] == query
    saved = deepcopy(evidence)
    # Use the existing unambiguous anonymous prior-turn reference, not a name.
    followup = "只比较 CCC 和上一轮的分子量与 logP，不要 ADMET 或活性"
    answer = turn(chat, followup, history)
    assert "上轮（复用）" in answer["content"] and smiles in answer["content"]
    from src.agent.utils.validators import InputValidator
    assert len(properties.calls) == 2 and properties.calls[0] == smiles
    assert list(InputValidator().analyze_molecular_input(properties.calls[1]).valid_smiles) == ["CCC"]
    assert history[-1]["property_evidence"]["canonical_smiles"] == "CCC"
    assert history[-2]["property_evidence"] == saved
    assert history[-1]["user"] == followup


def test_selected_card_takes_precedence_over_conversational_descriptor(tmp_path):
    store, references, pointer = confirmed(tmp_path)
    chat, properties = handler(store, references)
    history = []
    turn(chat, "请计算乙醇（SMILES: CCO）的分子量和 LogP。", history)
    assert history[-1]["property_evidence"]["canonical_smiles"] == "CCO"
    displayed = turn(chat, "刚才那个分子有多重来着？", history, reference=pointer)
    assert "46.07" in displayed["content"] and properties.calls == ["CCO"]
    answer = turn(chat, "刚才那个分子的分子量是多少来着？", history,
                  reference=pointer, selection={"ordinal": 2})
    assert properties.calls == ["CCO", "CCN"]
    assert "45.08" in answer["content"] and "46.07" not in answer["content"]
    same = turn(chat, "它的分子量是多少？不要重新计算。", history,
                reference=pointer, selection={"ordinal": 2})
    assert "45.08" in same["content"] and properties.calls == ["CCO", "CCN"]
    read_only = turn(chat, "它的分子量是多少？不要重新计算。", history,
                     reference=pointer, selection={"ordinal": 1})
    assert read_only["status"] == "not_calculated" and properties.calls == ["CCO", "CCN"]
    foreign = turn(chat, "刚才那个分子有多重来着？", history, owner="other",
                   reference=pointer, selection={"ordinal": 2})
    assert foreign["status"] == "rejected" and "45.08" not in foreign["content"]
    assert properties.calls == ["CCO", "CCN"]


def test_repeat_direct_two_metric_card_request(tmp_path):
    store, references, pointer = confirmed(tmp_path)
    chat, properties = handler(store, references)
    history = []
    for i, query in enumerate(["只计算分子量和 logP，不要 ADMET 或活性", "重新算一次", "再算一次"], 1):
        result = turn(chat, query, history, reference=pointer, selection={"ordinal": 2})
        assert properties.calls == ["CCN"] * i
        assert result["status"] == "completed"
        assert set(history[-1]["property_evidence"]["requested_metrics"]) == {"logp", "mw"}


def test_explicit_card_reruns_use_current_selection_once_per_request(tmp_path):
    store, references, pointer = confirmed(tmp_path)
    chat, properties = handler(store, references)
    history = []

    def select(ordinal, query):
        return turn(chat, query, history, reference=pointer, selection={"ordinal": ordinal})

    assert "46.07" in select(1, "它的分子量是多少？")["content"]
    assert "45.08" in select(2, "它的分子量是多少？")["content"]
    assert "45.08" in select(2, "它的分子量是多少？")["content"]
    expected = ["CCO", "CCN"]
    assert properties.calls == expected
    for ordinal, smiles, weight in [(2, "CCN", "45.08"), (2, "CCN", "45.08"), (1, "CCO", "46.07")]:
        prior_trace = history[-1]["property_evidence"]["trace_id"]
        result = select(ordinal, "重新算一次")
        expected.append(smiles)
        assert properties.calls == expected
        assert result["status"] == "completed" and weight in result["content"]
        evidence = history[-1]["property_evidence"]
        assert evidence["trace_id"] != prior_trace and evidence["canonical_smiles"] == smiles


@pytest.mark.parametrize("defect", ["foreign_identity", "missing_logp"])
def test_resolved_identity_does_not_rescue_wrong_or_incomplete_observation(tmp_path, defect):
    class DefectiveProperties(RecordingProperties):
        def execute(self, query):
            result = super().execute(query)
            if defect == "foreign_identity":
                result["data"][0]["smiles"] = "CCN"
            else:
                result["data"][0]["properties"].pop("logp")
            return result

    store, references, p = confirmed(tmp_path)
    chat, properties = handler(store, references, DefectiveProperties())
    history = []
    complete = turn(chat, "只计算刚才第一个分子的分子量和 logP", history, reference=p)
    assert properties.calls == ["CCO"]
    assert not history[-1].get("property_evidence")
    assert "证据不可用" in complete["content"] or complete.get("status") == "partial"


@pytest.mark.parametrize("defect", ["foreign_owner", "revoked", "unconfirmed"])
def test_unaccepted_source_never_grants_property_execution(tmp_path, defect):
    if defect == "unconfirmed":
        store = SQLiteAgentStateStore(tmp_path / "state.db")
        references = service(store)
        p = pointer(references.project(seed(store), session_id="owner")[0])
    else:
        store, references, p = confirmed(tmp_path)
    if defect == "revoked":
        store.update_run_status("trace", "failed")
    chat, properties = handler(store, references)
    history = []
    turn(chat, "只计算刚才第一个分子的分子量和 logP", history,
         reference=p, owner="other" if defect == "foreign_owner" else "owner")
    assert properties.calls == []
    assert not any(item.get("property_evidence") for item in history)
