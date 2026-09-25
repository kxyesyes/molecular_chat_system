"""Offline Task3 contracts: assembly is not readiness or execution authority."""
import importlib
import json
from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

from src.agent.tooling.registry import ToolRegistry


FOUR = frozenset({"property_calculator", "drug_likeness_assessment",
                  "activity_predictor", "target_database_search"})
DESCRIPTOR = {"provider": "openai_compatible", "model": "test-model", "mode": "json"}
CAP_ERROR = "ordinary_capabilities_unavailable"
ADMISSION_ERROR = "ordinary_admission_invalid"


def api():
    try:
        records = importlib.import_module("src.agent.contracts.ordinary_admission")
        catalog = importlib.import_module("src.web.ordinary_capabilities")
    except ModuleNotFoundError as exc:
        assert False, "Task3 module missing: " + exc.name
    return records, catalog


def snapshot(**changes):
    _, catalog = api()
    args = dict(registered_names=FOUR, provider_descriptor=DESCRIPTOR,
                semantic_profile=True, intent_capable=True, original_four_profile=True,
                scientific_tools=True, permitted_names=FOUR,
                model_generation="model-gen-1", capability_generation="cap-gen-1")
    args.update(changes)
    return catalog.build_capability_snapshot(**args)


def carry_args(kind="known_chat", record=None):
    a, _ = api()
    cap = snapshot()
    binding = a.build_admission_binding(cap, query="hello", history=[],
        assessment_revision="assessment-v1", intent_kind=kind,
        intent_requests=0 if record is None else 1)
    return dict(segment=a.ActiveSegment(10.0, 30.0, 40.0),
        intent_requests=binding["intent_requests"],
        intent_record_json=None if record is None else json.dumps(record),
        binding_json=json.dumps(binding), capability_json=cap.model_dump_json(),
        resume_expires_at=None)


def test_registration_is_not_readiness(monkeypatch):
    class Adapter:
        def __init__(self, name):
            self.spec = SimpleNamespace(name=name, aliases={"alias_" + name}, capabilities=set())
            self._last_execution = {"success": True, "available": True}

        def health(self):
            pytest.fail("health must not be called")

    registry = ToolRegistry()
    for name in FOUR:
        registry.register(Adapter(name))
    a, _ = api()  # Missing implementation fails here, not during collection.
    # Warm the existing lazy bounds imports before forbidding file access.
    snapshot()
    def forbidden(*args, **kwargs):
        pytest.fail("unexpected I/O or registry hook")
    import builtins
    import io
    import socket
    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(io, "open", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    for name in ("health", "resolve", "by_capability", "resolve_capability"):
        monkeypatch.setattr(registry, name, forbidden)
    cap = snapshot(registered_names=frozenset(registry.as_mapping()))
    features = {f.id: f for f in cap.features}
    assert len(features) == 11
    assert all(features[n].wired and features[n].permitted for n in FOUR)
    assert features["ordinary_chat"].wired
    assert all(f.readiness == "unknown" for f in cap.features)
    assert all(not f.wired and not f.permitted for f in cap.features
               if f.id not in FOUR | {"ordinary_chat"})
    assert a.capability_digest(cap) == a.capability_digest(cap)


def test_frozen_detached_views_and_aliases():
    a, catalog = api()
    descriptor = dict(DESCRIPTOR)
    cap = snapshot(provider_descriptor=descriptor)
    descriptor["model"] = "changed"
    view = cap.model_dump(mode="json")
    view["features"][0]["wired"] = False
    view["provider_descriptor"]["model"] = "changed"
    assert cap.features[0].wired and cap.provider_descriptor.model == "test-model"
    for value, name, changed in [(cap, "version", "2"),
                                 (cap.features[0], "wired", False),
                                 (cap.provider_descriptor, "model", "changed")]:
        with pytest.raises(ValueError):
            setattr(value, name, changed)
    assert type(cap.features) is tuple and type(catalog.PRODUCT_CATALOG) is tuple
    assert a.parse_capability_snapshot(cap.model_dump_json()) == cap
    assert a.CapabilitySnapshot.model_validate_json(cap.model_dump_json()) == cap
    with pytest.raises(ValueError):
        a.CapabilitySnapshot(**cap.model_dump(mode="json"))  # Direct Python lists stay strict.
    with pytest.raises(TypeError):
        catalog.PRODUCT_CATALOG[0][1] = "changed"
    alias = snapshot(registered_names=frozenset({"alias_property_calculator", "rag_retrieval"}))
    assert not any(f.wired for f in alias.features if f.id != "ordinary_chat")


@pytest.mark.parametrize("flags", [
    {"scientific_tools": False}, {"original_four_profile": False},
    {"permitted_names": frozenset()}, {"semantic_profile": False}, {"intent_capable": False},
])
def test_permissions_profile_and_presence_are_separate(flags):
    cap = snapshot(**flags)
    science = [f for f in cap.features if f.id in FOUR]
    if "semantic_profile" in flags or "intent_capable" in flags:
        assert not cap.features[0].wired
    else:
        assert all(not f.permitted for f in science)
        assert all(f.wired == (flags.get("original_four_profile", True)) for f in science)
    assert all(f.product_description and f.readiness == "unknown" for f in cap.features)


@pytest.mark.parametrize("changes", [
    {"registered_names": set(FOUR)}, {"registered_names": object()},
    {"scientific_tools": 1}, {"intent_capable": "yes"}, {"permitted_names": list(FOUR)},
    {"model_generation": True}, {"capability_generation": "sk-testcredential"},
    {"provider_descriptor": {**DESCRIPTOR, "api_key": "synthetic"}},
    {"provider_descriptor": {**DESCRIPTOR, "model": "sk-testcredential"}},
    {"provider_descriptor": {**DESCRIPTOR, "provider": "https://example.invalid"}},
    {"provider_descriptor": {**DESCRIPTOR, "mode": "auto"}},
])
def test_builder_rejects_nonplain_or_unsafe_inputs(changes):
    with pytest.raises(ValueError, match="^" + CAP_ERROR + "$"):
        snapshot(**changes)


@pytest.mark.parametrize("fault", ["extra", "bool", "reason", "duplicate", "33", "size", "secret", "nan"])
def test_snapshot_ingress_is_closed_bounded_and_private(fault):
    a, _ = api()
    view = snapshot().model_dump(mode="json")
    if fault == "extra":
        view["observation_source"] = "not-approved"
    elif fault == "bool":
        view["features"][0]["wired"] = 1
    elif fault == "reason":
        view["features"][0]["reason"] = "arbitrary"
    elif fault == "33":
        view["features"] *= 3
    elif fault == "size":
        view["features"][0]["product_description"] = "a" * 16384
    elif fault == "secret":
        view["provider_descriptor"]["model"] = "password=synthetic"
    elif fault == "nan":
        view["model_generation"] = float("nan")
    raw = json.dumps(view)
    if fault == "duplicate":
        raw = raw[:-1] + ',"version":"1"}'
    with pytest.raises(ValueError, match="^" + CAP_ERROR + "$"):
        a.parse_capability_snapshot(raw)


@pytest.mark.parametrize("values", [
    (True, 1.0, 2.0), (1, 1.0, 2.0), (0.0, 0.0, 0.0),
    (0.0, 301.0, 301.0), (0.0, float("inf"), float("inf")),
    (-1.0, 2.0, 1.0), (0.0, 2.0, 3.0), (float("nan"), 1.0, 1.0),
])
def test_active_segment_exact_finite_local_bounds(values):
    a, _ = api()
    with pytest.raises(ValueError, match=ADMISSION_ERROR):
        a.ActiveSegment(*values)


def test_carry_frozen_fresh_and_binding_digest():
    a, _ = api()
    args = carry_args()
    carry = a.AdmissionCarryIn(**args)
    with pytest.raises(FrozenInstanceError):
        carry.intent_requests = 1
    with pytest.raises(FrozenInstanceError):
        carry.segment.allowance = 40.0
    first = carry.binding()
    first["intent_kind"] = "mixed"
    assert carry.binding()["intent_kind"] == "known_chat"
    assert carry.intent_record() is None
    assert carry.capability_snapshot() == snapshot()
    a.validate_carry_in(carry, capability_snapshot=snapshot(),
                        query="hello", history=[], assessment_revision="assessment-v1")
    from src.agent.evidence.ledger import EvidenceLedger
    assert a.binding_digest(carry.binding()) == EvidenceLedger.output_digest(carry.binding())
    assert a.capability_digest(snapshot()) == EvidenceLedger.output_digest(snapshot().model_dump(mode="json"))
    assert a.binding_digest(dict(reversed(list(carry.binding().items())))) == a.binding_digest(carry.binding())


@pytest.mark.parametrize("fault", ["binding-size", "record-size", "capability-size", "duplicate",
    "nonfinite", "secret", "extra", "kind", "count", "record", "digest", "generation", "profile",
    "segment-type", "counter-bool", "expiry", "expiry-int"])
def test_carry_rejects_malformed_conflicting_metadata(fault):
    a, _ = api()
    args = carry_args()
    binding = json.loads(args["binding_json"])
    if fault.endswith("-size"):
        key, limit = {"binding-size": ("binding_json", 4096),
                      "record-size": ("intent_record_json", 8192),
                      "capability-size": ("capability_json", 16384)}[fault]
        args[key] = '"' + 'a' * limit + '"'
    elif fault == "duplicate":
        args["binding_json"] = args["binding_json"][:-1] + ',"version":"1"}'
    elif fault in {"nonfinite", "secret", "extra", "kind", "count", "digest", "generation", "profile"}:
        key, value = {"nonfinite": ("intent_requests", float("nan")),
            "secret": ("assessment_revision", "password=synthetic"), "extra": ("extra", 1),
            "kind": ("intent_kind", "invented"), "count": ("intent_requests", True),
            "digest": ("capability_digest", "0" * 64), "generation": ("model_generation", "stale"),
            "profile": ("profile_revision", "stale")}[fault]
        binding[key] = value
        args["binding_json"] = json.dumps(binding)
    elif fault == "record":
        args["intent_record_json"] = "{}"
    elif fault == "segment-type":
        args["segment"] = {"started_at": 10.0, "allowance": 30.0, "deadline": 40.0}
    elif fault == "counter-bool":
        args["intent_requests"] = False
    else:
        args["resume_expires_at"] = -1.0 if fault == "expiry" else 100
    with pytest.raises(ValueError):
        a.AdmissionCarryIn(**args)


@pytest.mark.parametrize("change", ["query", "history", "assessment_revision", "model_generation", "capability_generation", "scientific_tools"])
def test_current_view_rejects_stale_binding(change):
    a, _ = api()
    carry = a.AdmissionCarryIn(**carry_args())
    kw = dict(capability_snapshot=snapshot(), query="hello", history=[], assessment_revision="assessment-v1")
    if change in {"query", "history", "assessment_revision"}:
        kw[change] = [{"role": "user", "content": "old"}] if change == "history" else "changed"
    else:
        kw["capability_snapshot"] = snapshot(**{change: False if change == "scientific_tools" else "changed"})
    with pytest.raises(ValueError, match=ADMISSION_ERROR):
        a.validate_carry_in(carry, **kw)


def successful_task2_journal(mode):
    import asyncio
    import httpx
    from src.agent.decision_transport import IntentJournal
    from src.agent.openai_compatible_model import OpenAICompatibleModel
    journal = IntentJournal(intent_id="intent-1", trace_id="trace-1", turn_id="turn-1",
                            model_generation="model-gen-1", capability_generation="cap-gen-1")
    async def request():
        def handle(request):
            raw = json.dumps({"intent": {"version": "1", "kind": "capability",
                                        "history_relation": "none", "unresolved": False}})
            message = {"role": "assistant", "content": raw}
            if mode == "native":
                message.update(content=None, tool_calls=[{"id": "intent-call-1", "type": "function",
                    "function": {"name": "ordinary_intent", "arguments": raw}}])
            return httpx.Response(200, json={"choices": [{"message": message,
                "finish_reason": "tool_calls" if mode == "native" else "stop"}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7}})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            model = OpenAICompatibleModel("fake-test-key", "test-model", "https://example.invalid/v1", client=client)
            response = await model.propose_ordinary_intent([{"role": "user", "content": "hello"}],
                                                          mode=mode, _journal=journal)
            assert response.success
    asyncio.run(request())
    return journal.snapshot()


@pytest.mark.parametrize("mode", ["native", "json"])
def test_actual_task2_journal_preserved_without_invented_proof(mode):
    a, _ = api()
    record = successful_task2_journal(mode)
    assert record["http_status"] == 200
    assert record["model_call_metadata"]["mode"] == mode
    assert record["model_call_metadata"]["finish_reason"] == ("tool_calls" if mode == "native" else "stop")
    carry = a.AdmissionCarryIn(**carry_args("capability", record))
    assert carry.intent_record() == record
    assert carry.intent_record()["model_call_metadata"]["usage"] == {"prompt": 3, "completion": 4, "total": 7}
    for kind in ("capability", "general_knowledge", "conversation", "follow_up", "scientific_execution", "retrieval", "mixed", "uncertain"):
        assert a.AdmissionCarryIn(**carry_args(kind, record)).intent_requests == 1
    for key, value in [("model_generation", "stale"), ("request_id", "0" * 32),
                       ("phase", "agent_decision"), ("stages", ["parsed"]),
                       ("http_status", True), ("parser_outcome", "not_started")]:
        bad = {**record, key: value}
        with pytest.raises(ValueError, match=ADMISSION_ERROR):
            a.AdmissionCarryIn(**carry_args("capability", bad))
    detached = carry.intent_record()
    detached["stages"].clear()
    assert carry.intent_record()["stages"][-1] == "parsed"


@pytest.mark.parametrize("mode", ["native", "json"])
@pytest.mark.parametrize("fault", ["http201", "http204", "missing-mode", "missing-finish",
    "mode-mismatch", "finish-mismatch", "wrong-mode", "wrong-finish",
    "mode-int", "mode-list", "finish-bool", "finish-list"])
def test_carry_rejects_journal_transport_success_conflicts(mode, fault):
    a, _ = api()
    record = successful_task2_journal(mode)
    # The unmodified record is an actual Task2 success, not a fabricated receipt.
    assert a.AdmissionCarryIn(**carry_args("capability", record)).intent_record() == record
    meta = record["model_call_metadata"]
    if fault.startswith("http"):
        record["http_status"] = int(fault[4:])
    elif fault == "missing-mode":
        del meta["mode"]
    elif fault == "missing-finish":
        del meta["finish_reason"]
    else:
        key, value = {
            "mode-mismatch": ("mode", "json" if mode == "native" else "native"),
            "finish-mismatch": ("finish_reason", "stop" if mode == "native" else "tool_calls"),
            "wrong-mode": ("mode", "auto"), "wrong-finish": ("finish_reason", "length"),
            "mode-int": ("mode", 1), "mode-list": ("mode", [mode]),
            "finish-bool": ("finish_reason", True), "finish-list": ("finish_reason", ["stop"]),
        }[fault]
        meta[key] = value
    with pytest.raises(ValueError, match="^" + ADMISSION_ERROR + "$"):
        a.AdmissionCarryIn(**carry_args("capability", record))


def checkpoint(**changes):
    a, _ = api()
    args = dict(trace_id="trace-1", continuation_id="continuation-1", remaining_seconds=0.0,
                created_at=50.0, intent_requests=1, decision_requests=2, binding_digest="a" * 64)
    args.update(changes)
    return a.WaitingCheckpoint(**args)


def test_exchange_thin_assignment_set_once_and_identity():
    a, _ = api()
    exchange = a.AdmissionExchange()
    assert exchange.checkpoint is None
    exchange.checkpoint = checkpoint()
    assert exchange.verify(trace_id="trace-1", continuation_id="continuation-1", binding_digest="a" * 64) == checkpoint()
    for name, value in [("trace_id", "other"), ("continuation_id", "other"), ("binding_digest", "b" * 64)]:
        args = dict(trace_id="trace-1", continuation_id="continuation-1", binding_digest="a" * 64)
        args[name] = value
        with pytest.raises(ValueError, match="^continuation_rejected$"):
            exchange.verify(**args)
    for value in (None, checkpoint(), object()):
        with pytest.raises(ValueError, match="^continuation_rejected$"):
            exchange.checkpoint = value
    class Derived(a.AdmissionExchange):
        pass
    with pytest.raises(ValueError, match="^continuation_rejected$"):
        a.validate_exchange(Derived())
    with pytest.raises(ValueError, match="^continuation_rejected$"):
        a.AdmissionExchange(object())


@pytest.mark.parametrize("changes", [
    {"remaining_seconds": -1.0}, {"remaining_seconds": 301.0}, {"remaining_seconds": False},
    {"created_at": float("inf")}, {"intent_requests": True}, {"intent_requests": 2},
    {"decision_requests": -1}, {"decision_requests": 17},
    {"binding_digest": "bad"}, {"trace_id": "password=synthetic"},
])
def test_checkpoint_bounds(changes):
    with pytest.raises(ValueError):
        checkpoint(**changes)


def test_forged_records_revalidated_on_ingress():
    a, _ = api()
    carry = a.AdmissionCarryIn(**carry_args())
    object.__setattr__(carry.segment, "allowance", 300.0)
    with pytest.raises(ValueError, match=ADMISSION_ERROR):
        a.validate_carry_in(carry, capability_snapshot=snapshot(), query="hello", history=[],
                            assessment_revision="assessment-v1")
    cp = checkpoint()
    object.__setattr__(cp, "intent_requests", True)
    with pytest.raises(ValueError, match="continuation_rejected"):
        a.AdmissionExchange().checkpoint = cp
