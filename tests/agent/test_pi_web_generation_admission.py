"""Raw Web admission and server-selected contextual compilation, without science execution."""
from copy import deepcopy

import pytest

from src.agent.contracts import AgentContext
from src.agent.contracts.generation_request import GenerationRequestError, preflight_generation_request
from src.agent.scientific_session.web_requirements import compile_web_requirements
from src.web.decision_request import DecisionAdmissionError
from src.web.web_request import prepare_web_request


MALFORMED = [
    "Generate 7.5 potent",
    "Generate 3 potent + synthesize 4 potent",
    "Generate 3 potent plus synthesize 4 potent",
    "Generate 3 potent and also synthesize 4 potent",
]
ASPIRIN = "CC(=O)Oc1ccccc1C(=O)O"


def prepare(query, **options):
    return prepare_web_request(
        {"message": query, "enable_rag": False, **options},
        session_id="generation-owner", trace_id="generation-admission")


@pytest.mark.parametrize("query", MALFORMED)
def test_server_selected_generation_preflight_rejects_nounless_counts(query):
    """The explicit molecular context, not a new grammar, activates validation."""
    assert preflight_generation_request(query) is None
    with pytest.raises(GenerationRequestError) as rejected:
        preflight_generation_request(query, active_molecular_skill=True)
    assert rejected.value.reason == "malformed_requested_count"


@pytest.mark.parametrize("query", MALFORMED)
def test_server_selected_generation_compile_rejects_before_execution(query):
    """Real compilation preserves baseline active_skill; no browser skill field."""
    context = AgentContext(query, "contextual-generation", active_skill="molecular_design")
    with pytest.raises(GenerationRequestError) as rejected:
        compile_web_requirements(context, allowed_tools={"llm_molecular_generator"})
    assert rejected.value.reason == "malformed_requested_count"


@pytest.mark.parametrize("query,count", [
    ("Generate 7.5 molecules", 5),
    ("Generate 7 molecules", 3),
])
def test_explicit_count_is_authoritative_in_frozen_compiled_request(query, count):
    payload = {"message": query, "mol_count": count, "temperature": 0.23, "enable_rag": False}
    original = deepcopy(payload)
    assert preflight_generation_request(query, count, count_supplied=True) == count
    prepared = prepare_web_request(payload, session_id="owner", trace_id="explicit")
    assert payload == original
    assert prepared.context.mol_count == count
    assert prepared.context.metadata.get("requested_count") == count
    assert prepared.requirements.metadata["requested_count"] == count
    assert prepared.requirements.request["metadata"]["requested_count"] == count
    assert prepared.context.query == query
    assert prepared.context.temperature == 0.23
    assert prepared.required_tools == {"llm_molecular_generator"}
    # Neither caller mutation nor mutable context projections alter frozen authority.
    payload["mol_count"] = 10
    prepared.context.metadata["requested_count"] = 10
    assert prepared.context.metadata["requested_count"] == count


@pytest.mark.parametrize("query,options,count", [
    ("Generate 7 molecules", {}, 7),
    ("Generate 7 molecules", {"mol_count": None}, 7),
    ("Generate molecules", {}, 1),
    ("生成 2 个候选分子", {}, 2),
    ("生成 2 个候选分子", {"mol_count": 10}, 10),
    ("Generate 10 molecules", {"mol_count": 1}, 1),
])
@pytest.mark.parametrize("temperature", [0, 0.23, 2])
def test_normal_generation_preserves_count_temperature_and_query(query, options, count, temperature):
    prepared = prepare(query, temperature=temperature, **options)
    assert prepared.request_kind == "scientific"
    assert prepared.required_tools == {"llm_molecular_generator"}
    assert prepared.requirements.metadata["requested_count"] == count
    assert prepared.context.query == prepared.requirements.request["query"] == query
    assert prepared.context.temperature == prepared.requirements.request["temperature"] == temperature
    if options.get("mol_count") is None:
        assert "requested_count" not in prepared.context.metadata
    else:
        assert prepared.context.metadata["requested_count"] == count


@pytest.mark.parametrize("count", [True, False, 0, -1, 11, 3.0, "3", {}, [3]])
def test_explicit_count_preserves_strict_integer_parse_semantics(count):
    with pytest.raises(DecisionAdmissionError):
        prepare("Generate 2 molecules", mol_count=count)


@pytest.mark.parametrize("options", [{}, {"mol_count": None}, {"mol_count": 3}])
def test_complete_smiles_and_property_request_are_not_rewritten(options):
    query = f"只计算 {ASPIRIN} 的分子量与 logP"
    assert preflight_generation_request(query) is None
    prepared = prepare(query, temperature=0.41, **options)
    assert prepared.context.query == prepared.requirements.request["query"] == query
    assert prepared.context.temperature == 0.41
    assert prepared.required_tools == {"property_calculator"}
    assert ASPIRIN in prepared.context.query


def test_explicit_count_does_not_bypass_invalid_complete_molecular_input():
    with pytest.raises(DecisionAdmissionError):
        prepare("优化 CC(C)((，生成 2 个候选分子", mol_count=3)


@pytest.mark.parametrize("query", ["Design 11 slides", "Generate 7.5 reports"])
def test_non_molecular_creation_does_not_activate_molecular_count_rules(query):
    assert preflight_generation_request(query) is None
    prepared = prepare(query)
    assert prepared.request_kind == "chat"
    assert not prepared.required_tools


@pytest.mark.parametrize("query", ["Generate 11 molecules", "Generate 7.5 molecules"])
def test_canonical_molecular_preflight_precedes_reference_resolution(query):
    class UnusedReferences:
        def resolve(self, *args, **kwargs):
            pytest.fail("malformed generation reached reference resolution")

    with pytest.raises(DecisionAdmissionError):
        prepare_web_request({"message": query}, session_id="owner", trace_id="preflight",
                            references=UnusedReferences())


def test_optimization_keeps_complete_seed_smiles_with_explicit_count():
    query = f"优化 {ASPIRIN}，降低 logP，生成 2 个候选分子"
    prepared = prepare(query, mol_count=3, temperature=0.23)
    assert prepared.context.query == prepared.requirements.request["query"] == query
    assert prepared.requirements.metadata["requested_count"] == 3
    assert "llm_molecular_generator" in prepared.required_tools
    assert prepared.context.temperature == 0.23
