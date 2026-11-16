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


@pytest.mark.parametrize("query,count,properties", [
    ("请生成两个符合 Lipinski 五规则的类药分子。", 2, False),
    ("设计三个满足五规则的分子。", 3, False),
    ("生成两个候选；要求符合 Lipinski 五规则。", 2, False),
    ("生成两个候选，要求符合 Lipinski 五规则。", 2, False),
    ("Generate two drug-like molecules satisfying the Lipinski rule of five.", 2, False),
    ("生成两个符合 Lipinski 的分子，并计算它们的分子量。", 2, True),
])
def test_generation_checks_lipinski_on_generated_structures(query, count, properties):
    from src.web.scientific_obligations import ScientificObligationCompiler
    assert not ScientificObligationCompiler.is_profile_candidate(query)
    prepared = prepare(query)
    req = prepared.requirements
    assert req.workflow_name == 'molecular_design' and req.metadata['requested_count'] == count
    assert req.request['query'] == query
    assert prepared.required_tools == {'llm_molecular_generator', 'drug_likeness_assessment',
                                       *({'property_calculator'} if properties else set())}
    for step in req.steps[1:]:
        assert step.input_binding == '$.outputs.result'
        assert req.dependencies[step.name] == ('molecular_design',)


def test_generation_with_no_lipinski_check_keeps_generation_only():
    assert prepare('生成两个分子，不要评估 Lipinski 五规则。').required_tools == {'llm_molecular_generator'}


def test_existing_molecule_evaluation_preserves_generation_prohibition():
    prepared = prepare('不要生成分子，只评估 CCO 是否符合 Lipinski 五规则。')
    assert 'drug_likeness_assessment' in prepared.required_tools
    assert 'llm_molecular_generator' not in prepared.required_tools


def test_generation_cannot_replace_other_subject_or_skip_additional_admet():
    for query in ('生成两个分子，再评估 CCO 是否符合 Lipinski 五规则。',
                  '生成两个符合 Lipinski 的分子，同时预测 ADMET。'):
        with pytest.raises(DecisionAdmissionError):
            prepare(query)


def test_lipinski_optimization_reuses_the_seed_and_checks_its_candidates():
    req = prepare('优化 CCO，生成两个符合 Lipinski 五规则的分子。').requirements
    assert req.workflow_name == 'hit_to_lead_optimization'
    assert 'CCO' in req.steps[0].input_data
    check, = [s for s in req.steps if s.tool_name == 'drug_likeness_assessment']
    assert check.input_binding == '$.outputs.candidates' and check.metadata['require_lipinski_compliance']


def test_generation_with_an_opted_out_goal_does_not_require_assessment():
    assert prepare('生成两个分子，不要求符合 Lipinski 五规则。').required_tools == {'llm_molecular_generator'}
    assert prepare('生成两个符合 Lipinski 的分子，先不做 ADMET。').required_tools == {
        'llm_molecular_generator', 'drug_likeness_assessment'}


def test_lipinski_generation_goal_uses_the_observed_verdict():
    import json
    from src.agent.scientific_session.web_acceptance import _scientific_data, web_observation_reasons
    from src.agent.contracts import AgentResult, RunOutcome, ToolProvenance
    from src.web.decision_chat import web_scientific_display
    from src.agent.tools.drug_likeness_assessment import DrugLikenessAssessment
    from src.system.tool_adapter import execute_tool_compat
    query = '生成一个符合 Lipinski 五规则的分子。'
    step, = [s for s in prepare(query).requirements.steps if s.name == 'drug_likeness']
    rows = []
    for smiles, expected in [('CCO', True), ('C' * 40, False)]:
        result = execute_tool_compat(DrugLikenessAssessment(), smiles)
        assert result.data[0]['assessment']['lipinski_rule_of_five']['compliance'] is expected
        result.quality['evidence_id'] = 'fixture'
        result.provenance = ToolProvenance(tool_name='drug_likeness_assessment')
        reasons = web_observation_reasons(result, {'input_data': {'query': smiles}}, step, request_query=query)
        assert reasons == ([] if expected else ['lipinski_goal_not_satisfied'])
        rows.extend(_scientific_data(result))
    audit = json.dumps({'acceptance': {'profile': 'medchat-web-v1'}, 'observations': [{
        'tool': 'drug_likeness_assessment', 'evidence_id': 'fixture', 'status': 'succeeded', 'data': rows}]})
    result = AgentResult('display', False, audit, final_answer=audit, outcome=RunOutcome.PARTIAL,
                         metadata={'binding_profile': 'medchat-web-v1'})
    text = web_scientific_display(result, concise=True)
    assert text.index('CCO') < text.index('C' * 40) < text.index('不符合')
    assert '超限指标：分子量、logP' in text


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
