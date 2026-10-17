"""Real local RDKit + synthetic confirmed sources; no model/scientific API."""
import asyncio
from copy import deepcopy

import pytest

from src.agent.harness.factory import HarnessFactory
from src.agent.supervisor import SupervisorAgent
from src.web.chat_handler import ChatHandler
from tests.agent.test_browser_s02_context import NoScienceModel, RecordingProperties
from tests.agent.test_scientific_reference_execution import Socket, confirmed
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
    agent = SupervisorAgent(tools={properties.name: properties}, state_store=store,
                            harness_factory=HarnessFactory(mode="legacy"))
    return ChatHandler(NoScienceModel(), None, agent, {}, scientific_references=references), properties


def turn(chat, query, history, *, reference=None, selection=None, owner="owner"):
    socket = Socket()
    asyncio.run(chat._process_message(socket, query, False, True,
        conversation_history=history, session_id=owner, reference=reference, selection=selection))
    frames = [frame for frame in socket.messages if frame.get("type") == "complete"]
    assert frames, socket.messages
    return frames[-1]


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
