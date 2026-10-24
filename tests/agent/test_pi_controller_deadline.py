"""Controller deadline propagation through official Pi; no live inference.

Model replies/HTTP and monotonic clocks are controlled; passive probes read
worker caps. Ownership, generator, adapters, controller, scientific Session,
and persistence execute normally.
"""
import asyncio
import threading
from types import SimpleNamespace

import pytest

from src.agent.contracts import AgentContext, RunOutcome
from src.agent.contracts.decision_bindings import B1_PROFILE_REVISION
from src.agent.runtime import worker_ownership
from src.agent.scientific_session import controller, decision_binding_continuation
from src.agent.scientific_session.web_requirements import compile_web_requirements
from src.agent.tools.property_calculator import PropertyCalculator
from tests.agent.test_decision_binding_acceptance import requirements
from tests.agent.test_decision_loop import setup_loop, tool, clarify
from tests.agent.test_generation_transport_characterization import isolated_production_context, mocked_client


def controlled_clock(monkeypatch):
    now = [1000.0]
    clock = SimpleNamespace(monotonic=lambda: now[0])
    monkeypatch.setattr(controller, 'time', clock)
    monkeypatch.setattr(worker_ownership, 'time', clock)
    return now


@pytest.mark.parametrize('phase', ['root', 'tail'])
@pytest.mark.parametrize('budget', [80, 300])
def test_bound_controller_workers_receive_root_and_finalization_caps(setup_loop, monkeypatch, phase, budget):
    now = controlled_clock(monkeypatch)
    observed = {}
    prepare = controller.prepare_binding_requirements
    snapshot = decision_binding_continuation.snapshot_payload

    def observe_prepare(*args, **kwargs):
        observed['root'] = worker_ownership.provider_call_timeout(1000)
        return prepare(*args, **kwargs)

    def observe_snapshot(*args, **kwargs):
        observed['tail'] = worker_ownership.provider_call_timeout(1000)
        return snapshot(*args, **kwargs)

    monkeypatch.setattr(controller, 'prepare_binding_requirements', observe_prepare)
    monkeypatch.setattr(decision_binding_continuation, 'snapshot_payload', observe_snapshot)
    bundle = setup_loop([clarify()], [PropertyCalculator()],
        binding_profile=B1_PROFILE_REVISION, timeout_seconds=budget)
    owner = worker_ownership.WorkerOwner()
    context = AgentContext('请计算性质', 'bound-deadline', user_id='owner', session_id='session')
    result = asyncio.run(bundle.loop.run(context, request_kind='scientific',
        allowed_tools={'property_calculator'}, required_tools={'property_calculator'},
        requirements=requirements(), worker_owner=owner, event_bus=bundle.bus))
    assert result.metadata.get('waiting_for_input') and result.metadata.get('continuation_id'), result.metadata
    assert owner.status == 'settled' and owner.pending_roots == 0
    assert len(bundle.model.messages) == 1 and not result.tool_results
    assert now == [1000.0]
    assert observed[phase] == (budget if phase == 'root' else min(30, budget / 4))


@pytest.mark.parametrize('stop', ['deadline', 'cancel'])
def test_controller_stops_real_generation_retry_and_drains_before_terminal(setup_loop, tmp_path, monkeypatch, stop):
    now = controlled_clock(monkeypatch)
    entered, release, exited = (threading.Event() for _ in range(3))
    owner = worker_ownership.WorkerOwner()
    with isolated_production_context(tmp_path) as production:
        with mocked_client(production, (('ok', 'CCO'),) * 5) as (model, transport):
            generator = production.generator(model)
            context = AgentContext('生成 2 个候选分子', 'controller-generation',
                user_id='owner', session_id='session', mol_count=2, metadata={'requested_count': 2})
            allowed = {generator.name}
            req = compile_web_requirements(context, allowed_tools=allowed)
            decision = tool(generator.name, {'step_ref': req.steps[0].name})
            bundle = setup_loop([decision], [generator], binding_profile='medchat-web-v1', timeout_seconds=300)

            def provider_entry(event):
                if len(transport.calls) == 1:
                    entered.set()
                    try:
                        assert release.wait(10), 'test must release the provider'
                    finally:
                        exited.set()

            transport.on_request = provider_entry

            async def exercise():
                task = asyncio.create_task(bundle.loop.run(context, request_kind='scientific',
                    allowed_tools=allowed, required_tools=allowed, requirements=req,
                    worker_owner=owner, event_bus=bundle.bus))
                try:
                    assert await asyncio.to_thread(entered.wait, 10), 'provider did not start'
                    if stop == 'deadline':
                        now[0] += 300
                    for _ in range(2):
                        if stop == 'cancel':
                            task.cancel()
                        await asyncio.sleep(0.02)
                        assert not task.done() and not exited.is_set()
                        assert owner.status == 'pending' and owner.pending_roots > 0
                        assert bundle.store.get_run(context.trace_id)['status'] == 'running'
                        terminal = {'task_completed', 'task_partial', 'task_failed', 'task_cancelled', 'task_rejected'}
                        assert not terminal.intersection(event.event.value for event in bundle.bus.events)
                finally:
                    release.set()
                    result = await task
                assert exited.is_set() and owner.status == 'settled' and owner.pending_roots == 0
                assert not result.success
                assert result.outcome == (RunOutcome.CANCELLED if stop == 'cancel' else RunOutcome.FAILED)
                assert len(bundle.model.messages) == 1
                assert len(transport.calls) == 1, 'controller deadline/cancellation allowed generator retries'
                if stop == 'deadline':
                    assert result.metadata['stop_reason'] == 'task_deadline_exceeded', result.metadata
                return result

            asyncio.run(exercise())
            assert len(transport.calls) == 1, 'controller deadline/cancellation allowed generator retries'
