"""Pi integration contracts with explicit scripted science/model fixtures."""
import asyncio
import importlib
from pathlib import Path

import pytest

from src.agent.contracts import AgentContext
from tests.agent.test_decision_loop import setup_loop, tool, finish_last, clarify, CountingTool, last_observation


def runtime_type():
    path = Path(__file__).parents[2] / 'src/agent/pi_runtime/runtime.py'
    assert path.is_file(), 'Pi must be the decision runtime, not an optional demo'
    return importlib.import_module('src.agent.pi_runtime.runtime').PiAgentRuntime


@pytest.mark.parametrize('failed', [False, True])
def test_real_pi_owns_observation_branch_and_python_keeps_evidence(setup_loop, failed):
    runtime = runtime_type()
    def branch(messages):
        return clarify() if not last_observation(messages)['success'] else tool('drug_likeness_assessment')
    bundle = setup_loop([tool(), branch, finish_last],
        [CountingTool(fail=failed), CountingTool('drug_likeness_assessment')])
    agent = runtime(bundle.model, bundle.registry, bundle.store)
    failures = []
    run_session = agent.run_session
    async def observed_session(*args, **kwargs):
        try:
            return await run_session(*args, **kwargs)
        except Exception as exc:
            failures.append((type(exc).__name__, str(exc)))
            raise
    agent.run_session = observed_session
    result = asyncio.run(agent.run(AgentContext('SMILES: CCO', 'pi-branch'),
        request_kind='scientific', allowed_tools={'property_calculator', 'drug_likeness_assessment'},
        required_tools={'property_calculator'}, event_bus=bundle.bus))
    assert result.metadata['backend'] == 'pi'
    assert not failures, failures
    assert result.success is (not failed)
    assert len(bundle.tools[1].inputs) == (0 if failed else 1)
    assert len(bundle.model.messages) == (2 if failed else 3)
    assert result.tool_results[0].provenance.input_digest
    if not failed:
        assert '46.069' in result.final_answer
        assert 'model prose' not in result.final_answer


def test_runtime_has_no_python_decision_loop():
    runtime_type()
    root = Path(__file__).parents[2] / 'src/agent'
    source = (root / 'scientific_session/controller.py').read_text(encoding='utf-8')
    assert 'StateGraph' not in source
    assert 'ModelDecisionLoop' not in source
    assert 'self.model.decide' not in source
    assert 'while True' not in source


@pytest.mark.parametrize('cancelled', [False, True])
def test_lost_pi_terminal_never_publishes_pending_clarification(setup_loop, cancelled):
    from src.agent.pi_runtime.bridge import PiBridgeError
    bundle = setup_loop([clarify()])
    original = bundle.loop.run_session

    async def lose_settlement(*args, **kwargs):
        await original(*args, **kwargs)
        if cancelled:
            raise asyncio.CancelledError
        raise PiBridgeError('pi_process_exited')

    bundle.loop.run_session = lose_settlement
    result = asyncio.run(bundle.loop.run(
        AgentContext('SMILES: CCO', 'pi-lost-terminal', user_id='owner', session_id='session'),
        request_kind='scientific', allowed_tools={'property_calculator'},
        required_tools={'property_calculator'}, event_bus=bundle.bus))
    assert not result.success
    assert result.metadata['stop_reason'] == ('cancelled' if cancelled else 'decision_runtime_failed')
    assert not result.metadata.get('waiting_for_input')
    assert not result.metadata.get('continuation_id')
    assert not bundle.store.get_run('pi-lost-terminal')['metadata'].get('decision_continuation')


def test_cancellation_during_owner_settlement_invalidates_clarification(setup_loop):
    from src.agent.runtime.worker_ownership import WorkerOwner
    bundle = setup_loop([clarify()])
    owner = WorkerOwner()
    original = bundle.loop.run_session

    async def cancel_at_settlement(*args, **kwargs):
        result = await original(*args, **kwargs)
        asyncio.get_running_loop().call_soon(asyncio.current_task().cancel)
        return result

    bundle.loop.run_session = cancel_at_settlement
    result = asyncio.run(bundle.loop.run(
        AgentContext('SMILES: CCO', 'pi-owner-cancel', user_id='owner', session_id='session'),
        request_kind='scientific', allowed_tools={'property_calculator'},
        required_tools={'property_calculator'}, event_bus=bundle.bus, worker_owner=owner))
    assert owner.status == 'settled' and owner.pending_roots == 0
    assert result.metadata['stop_reason'] == 'cancelled'
    assert not result.metadata.get('waiting_for_input')
    assert not result.metadata.get('continuation_id')
    assert not bundle.store.get_run('pi-owner-cancel')['metadata'].get('decision_continuation')


def test_pi_resume_uses_its_settled_history_without_reexecuting_science(setup_loop):
    class ResumeTool(CountingTool):
        # This test verifies replay, not wall-clock timeout behavior.
        timeout_seconds = 5
    bundle = setup_loop([tool(), clarify(), tool(), finish_last], [ResumeTool()])
    failures = []
    original = bundle.loop.run_session
    async def observed(*args, **kwargs):
        request = kwargs['request_model']
        async def observed_request(payload):
            try:
                return await request(payload)
            except Exception:
                from src.agent.pi_runtime.provider import _MESSAGE_FIELDS
                failures.append(('shape', [(message.get('role'), sorted(set(message)
                    - _MESSAGE_FIELDS.get(message.get('role'), set())))
                    for message in payload['messages']]))
                raise
        kwargs['request_model'] = observed_request
        try:
            return await original(*args, **kwargs)
        except Exception as exc:
            failures.append((type(exc).__name__, str(exc)))
            raise
    bundle.loop.run_session = observed
    def run(**kwargs):
        return asyncio.run(bundle.loop.run(
            AgentContext('SMILES: CCO', 'pi-resume', user_id='owner', session_id='session'),
            request_kind='scientific', allowed_tools={'property_calculator'},
            required_tools={'property_calculator'}, event_bus=bundle.bus, **kwargs))
    waiting = run()
    assert not failures
    assert waiting.metadata['waiting_for_input']
    result = run(continuation_id=waiting.metadata['continuation_id'], clarified_query='SMILES: CCO')
    assert not failures, failures
    assert result.success, result.metadata
    assert len(bundle.tools[0].inputs) == 1
    assert result.metadata['reused_decisions'] == 1
