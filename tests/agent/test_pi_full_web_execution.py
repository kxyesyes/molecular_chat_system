"""Real Pi process, real RDKit; scripted model/generation are not live LLM acceptance."""
import asyncio
import json

import pytest

from src.agent.contracts import AgentContext, AgentErrorCode, ToolResult
from src.agent.evidence import EvidenceLedger
from src.agent.runtime.worker_ownership import WorkerOwner
from src.agent.tools.property_calculator import PropertyCalculator
from src.agent.scientific_session.web_requirements import compile_web_requirements
from tests.agent.test_decision_loop import setup_loop, tool, finish, clarify, last_observation
from tests.agent.test_generation_ranking_contract import CountingTool, GEN, output


def all_evidence(messages):
    found = []
    for message in messages:
        if message.get('role') != 'tool':
            continue
        try:
            evidence_id = json.loads(message['content']).get('quality', {}).get('evidence_id')
        except (TypeError, ValueError):
            continue
        if evidence_id and evidence_id not in found:
            found.append(evidence_id)
    return found


def make_case(setup_loop, *, failed=False, resume=False):
    context = AgentContext('生成 1 个分子，并计算理化性质', 'pi-full-web',
        user_id='owner', session_id='session', active_skill='molecular_design',
        mol_count=1, temperature=0.23, metadata={'requested_count': 1})
    allowed = {GEN, 'property_calculator'}
    requirements = compile_web_requirements(context, allowed_tools=allowed)
    steps = {step.tool_name: step.name for step in requirements.steps}
    generation = tool(GEN, {'step_ref': steps[GEN]})
    properties = tool('property_calculator', {'step_ref': steps['property_calculator']})
    branches = []

    def after_generation(messages):
        observation = last_observation(messages)
        branches.append(observation['success'])
        assert observation['tool_name'] == GEN
        return clarify() if not observation['success'] else properties

    decisions = [generation, after_generation, lambda messages: finish(all_evidence(messages))]
    if resume:
        decisions = [generation, clarify(), generation, properties,
                     lambda messages: finish(all_evidence(messages))]
    raw = (ToolResult.error_result(GEN, AgentErrorCode.MODEL_UNAVAILABLE, 'Explicit test failure')
           if failed else output(GEN))
    generator = CountingTool(GEN, raw)
    bundle = setup_loop(decisions, [generator, PropertyCalculator()], binding_profile='medchat-web-v1')
    errors = []
    original = bundle.loop.run_session

    async def observed(*args, **kwargs):
        try:
            return await original(*args, **kwargs)
        except Exception as exc:
            import traceback
            errors.append(traceback.format_exc())
            raise

    bundle.loop.run_session = observed

    def run(**kwargs):
        owner = WorkerOwner()
        result = asyncio.run(bundle.loop.run(context, request_kind='scientific',
            allowed_tools=allowed, required_tools=allowed, requirements=requirements,
            event_bus=bundle.bus, worker_owner=owner, **kwargs))
        assert owner.status == 'settled' and owner.pending_roots == 0
        return result

    return bundle, generator, branches, errors, run


@pytest.mark.parametrize('failed', [False, True])
def test_official_pi_full_web_generation_feedback_and_real_properties(setup_loop, failed):
    bundle, generator, branches, errors, run = make_case(setup_loop, failed=failed)
    result = run()
    assert not errors, errors
    assert generator.calls == 1, result.metadata
    assert branches == [not failed]
    assert result.metadata['backend'] == 'pi'
    assert result.success is (not failed), result.metadata
    assert len(result.tool_results) == (1 if failed else 2)
    # The compiled Web plan completes as soon as its observations are ready.
    assert len(bundle.model.messages) == 2
    if failed:
        assert result.metadata['waiting_for_input']
        assert result.tool_results[0].error.code is AgentErrorCode.MODEL_UNAVAILABLE
        return
    generated, properties = result.tool_results
    assert generator.inputs[0]['metadata']['temperature'] == 0.23
    assert generator.inputs[0]['metadata']['requested_count'] == 1
    assert properties.data[0]['candidate_id'] == generated.data['candidates'][0]['candidate_id']
    assert properties.data[0]['properties']['molecular_weight'] == pytest.approx(46.069, abs=0.001)
    assert properties.provenance.output_digest == EvidenceLedger.output_digest(properties.data)
    assert properties.quality['binding_proof']['roles'][0]['evidence_id'] == generated.quality['evidence_id']
    assert result.metadata['task_acceptance']['finish_eligible'] is True
    assert 'CCO' in result.final_answer and 'model prose' not in result.final_answer


def test_official_pi_full_web_resume_reuses_generated_candidates(setup_loop):
    bundle, generator, _, errors, run = make_case(setup_loop, resume=True)
    waiting = run()
    assert not errors, errors
    assert waiting.metadata.get('waiting_for_input'), waiting.metadata
    before = waiting.tool_results[0]
    result = run(continuation_id=waiting.metadata['continuation_id'], clarified_query='继续计算理化性质')
    assert not errors, errors
    assert result.success, result.metadata
    assert generator.calls == 1
    assert result.metadata['reused_decisions'] == 1
    assert len(bundle.model.messages) == 4
    assert result.tool_results[0].to_legacy_dict() == before.to_legacy_dict()
    assert result.tool_results[1].data[0]['candidate_id'] == before.data['candidates'][0]['candidate_id']


@pytest.mark.parametrize('kind', ['target_design', 'lead_optimization'])
def test_official_pi_full_composite_keeps_optional_failures_visible(setup_loop, kind):
    from src.agent.tools.candidate_ranker import CandidateRanker
    from src.agent.tools.lead_optimization_verifier import LeadOptimizationVerifier
    from tests.agent.test_target_driven_design_workflow import FakeTool
    target_design = kind == 'target_design'
    optional = [CountingTool(name, ToolResult.error_result(name,
        AgentErrorCode.MODEL_UNAVAILABLE, 'Explicit fixture has no prediction model'))
        for name in ('admet_predictor', 'activity_predictor')]
    generator = CountingTool(GEN, output(GEN))
    supplied = [generator, PropertyCalculator(), *optional]
    supplied += ([FakeTool('target_database_search'), CandidateRanker()] if target_design
                 else [LeadOptimizationVerifier()])
    context = AgentContext(
        '针对 PDE5A 设计 1 个候选分子并筛选前 1 个' if target_design
        else '优化 CCC，降低 logP，生成 1 个候选分子',
        'pi-' + kind, user_id='owner', session_id='session',
        active_skill='target_driven_design' if target_design else 'hit_to_lead_optimization',
        mol_count=1, temperature=0.23, metadata={'requested_count': 1})
    allowed = {item.name for item in supplied}
    requirements = compile_web_requirements(context, allowed_tools=allowed)
    decisions = [tool(step.tool_name, {'step_ref': step.name}) for step in requirements.steps]
    decisions.append(lambda messages: finish(all_evidence(messages)))
    bundle = setup_loop(decisions, supplied, binding_profile='medchat-web-v1')
    owner = WorkerOwner()
    result = asyncio.run(bundle.loop.run(context, request_kind='scientific',
        allowed_tools=allowed, required_tools=allowed, requirements=requirements,
        event_bus=bundle.bus, worker_owner=owner))
    assert owner.status == 'settled' and owner.pending_roots == 0
    assert result.metadata['stop_reason'] == 'task_requirements_unfulfilled', result.metadata
    assert result.outcome.value == 'partial' and not result.success
    assert len(result.tool_results) == (6 if target_design else 8)
    assert len(bundle.model.messages) == len(result.tool_results)
    assert generator.calls == 1
    assert all(item.error.code is AgentErrorCode.MODEL_UNAVAILABLE
               for item in result.tool_results if item.tool_name in {'admet_predictor', 'activity_predictor'})
    final = result.tool_results[-1]
    assert final.success
    assert 'model_unavailable' in result.final_answer
    assert 'model prose' not in result.final_answer
    if target_design:
        assert final.data['top_candidates'][0]['canonical_smiles'] == 'CCO'
    else:
        assert final.data['status'] == 'supported'
