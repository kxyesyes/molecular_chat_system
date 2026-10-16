"""Owned text refinement; real stores/model transport, no scientific execution."""
import asyncio
import json

import pytest

from tests.agent.test_web_docking_coordinator import Rig, proposal
from tests.agent.test_web_docking_sources import source_rig, invalidate_source
from tests.agent.test_web_docking_selection import select, _complete_source_child
from tests.agent.test_web_docking_parameters import confirmation_args
from tests.agent.test_web_docking_consent import target_case
from src.docking import source_preparation


def entry(coordinator):
    method = getattr(coordinator, 'refine_text', None)
    assert callable(method), 'owned docking text refinement is absent'
    return method


def response(text, values, index=1):
    source = f'refinement-{index}'
    spans = [dict(source_id=source, start=0, end=len(text), role='request')]
    fields = {}
    for key, value in values.items():
        raw = json.dumps(value, separators=(',', ':')) if type(value) is not str else value
        start = text.index(key + '=') + len(key) + 1
        spans.append(dict(source_id=source, start=start, end=start + len(raw), role='field'))
        fields[key] = dict(value=value, span_indices=[len(spans) - 1])
    return dict(version='1', kind='docking_refinement', unresolved=False,
                source_spans=spans, fields=fields)


async def begin(rig, coordinator, query='run docking'):
    rig.output = proposal(query)
    return await rig.begin(coordinator, query=query)


async def refine(coordinator, text='exhaustiveness=8', revision=0, **changes):
    args = dict(root_id='root-1', owner_session_id='owner-1', expected_revision=revision,
                text=text)
    args.update(changes)
    return await entry(coordinator)(**args)


@pytest.mark.parametrize('mode', ['native', 'json'])
def test_refinement_keeps_root_issuer_query_expiry_and_spent_budget(tmp_path, mode):
    rig = Rig(tmp_path)
    rig.mode = mode
    async def run():
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            entry(coordinator)
            original = await begin(rig, coordinator)
            root = coordinator._roots['root-1']
            snapshot, issuer = root.snapshot, root.issuer
            before = rig.read(original)
            rig.output = response('exhaustiveness=8', {'exhaustiveness': 8})
            view = await refine(coordinator)
            after = rig.read(view)
            assert root.accepted_revision == 1
            assert root.snapshot is snapshot and root.issuer is issuer
            assert view['task_id'] == original['task_id']
            assert view['preparation_id'] == original['preparation_id']
            assert after['identity']['query_digest'] == before['identity']['query_digest']
            assert after['expires_at_ms'] == before['expires_at_ms']
            assert after['monotonic_expires'] == before['monotonic_expires']
            assert after['draft']['budget'] == dict(limit_ms=180000, spent_ms=60000, proposal_slots_spent=2)
            assert after['draft']['slots']['exhaustiveness'] == dict(state='fixed', value=8)
            assert after['draft']['slots']['receptor_ref'] == dict(state='pending')
            assert view['input_ready'] is False and view['execution_authorized'] is False
            assert 'exhaustiveness' not in view['missing_fields']
            assert len(rig.calls) == 2 and rig.gate._readers == 0
            payload = json.loads(rig.calls[-1].content)
            sources = [json.loads(message['content']) for message in payload['messages']
                       if message['role'] == 'user']
            assert len(sources) == 2 and sources[0]['query'] == 'run docking'
            refinements = json.loads(payload['messages'][-1]['content'])['refinements']
            assert refinements[0]['source_id'] == 'refinement-1'
            assert refinements[0]['text'] == 'exhaustiveness=8'
            assert refinements[0]['server_turn_id'] != root.origin
            assert 'exhaustiveness=8' not in json.dumps(rig.rows()) + json.dumps(rig.metadata())
            await coordinator.close_root(root_id=root.root_id, owner_session_id=root.owner)
    asyncio.run(run())


@pytest.mark.parametrize('changes', [dict(owner_session_id='foreign'), dict(root_id='missing'),
    dict(expected_revision=True), dict(expected_revision=-1), dict(expected_revision=1),
    dict(text='exhaustiveness=8; delete history'), dict(text='ligand_ref=unowned'),
    dict(text=''), dict(text='num_modes=0')])
def test_invalid_refinement_never_dispatches_or_changes_existing_root(tmp_path, changes):
    rig = Rig(tmp_path)
    async def run():
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            entry(coordinator)
            original = await begin(rig, coordinator)
            before = rig.read(original)
            await refine(coordinator, **changes)
            assert rig.read(original) == before and len(rig.calls) == 1
            assert coordinator._roots['root-1'].closed is False
            await coordinator.close_root(root_id='root-1', owner_session_id='owner-1')
    asyncio.run(run())


def test_exact_duplicate_old_revision_never_reissues_model_capability(tmp_path):
    rig = Rig(tmp_path)
    async def run():
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            entry(coordinator)
            await begin(rig, coordinator)
            rig.output = response('exhaustiveness=8', {'exhaustiveness': 8})
            first = await refine(coordinator)
            before = rig.read(first)
            duplicate = await refine(coordinator)
            assert duplicate['task_id'] == first['task_id'] and len(rig.calls) == 2
            assert rig.read(first) == before
            # A stale revision cannot revoke by changing the previously accepted text.
            await refine(coordinator, 'exhaustiveness=9')
            assert coordinator._roots['root-1'].closed is False and len(rig.calls) == 2
            await coordinator.close_root(root_id='root-1', owner_session_id='owner-1')
    asyncio.run(run())


@pytest.mark.parametrize('original', ['run docking', 'run docking; exhaustiveness=8'])
def test_changed_fixed_or_original_input_closes_without_new_proposal(tmp_path, original):
    rig = Rig(tmp_path)
    async def run():
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            entry(coordinator)
            await begin(rig, coordinator, original)
            root = coordinator._roots['root-1']
            if original == 'run docking':
                rig.output = response('exhaustiveness=8', {'exhaustiveness': 8})
                await refine(coordinator)
            calls = len(rig.calls)
            result = await refine(coordinator, 'exhaustiveness=9', root.accepted_revision)
            assert root.closed and result['reason_code'] == 'new_root_required'
            assert len(rig.calls) == calls
    asyncio.run(run())


def test_three_refinements_keep_complete_prefix_and_four_model_slots(tmp_path):
    rig = Rig(tmp_path)
    async def run():
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            entry(coordinator)
            await begin(rig, coordinator)
            for index, (text, values) in enumerate([
                ('exhaustiveness=8', {'exhaustiveness': 8}),
                ('num_modes=9', {'num_modes': 9}),
                ('size=[20,20,20]', {'size': [20, 20, 20]})], 1):
                rig.output = response(text, values, index)
                view = await refine(coordinator, text, index - 1)
                assert coordinator._roots['root-1'].accepted_revision == index
                messages = json.loads(rig.calls[-1].content)['messages']
                assert len(json.loads(messages[-1]['content'])['refinements']) == index
            assert rig.read(view)['draft']['budget']['proposal_slots_spent'] == 4
            await refine(coordinator, 'center=[1,2,3]', 3)
            assert len(rig.calls) == 4
            await coordinator.close_root(root_id='root-1', owner_session_id='owner-1')
    asyncio.run(run())


@pytest.mark.parametrize('when', ['before_selection', 'after_selection'])
def test_numeric_refinement_survives_source_selection_and_needs_explicit_confirmation(
        tmp_path, target_case, monkeypatch, when):
    rig, pointer = source_rig(tmp_path, target_case)
    monkeypatch.setattr(source_preparation, '_source_candidate', _complete_source_child)
    async def run():
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            entry(coordinator)
            source = await rig.begin(coordinator, structure_context=pointer)
            root = coordinator._roots['root-1']
            if when == 'after_selection':
                await select(coordinator, source)
            rig.output = response('num_modes=5', {'num_modes': 5})
            view = await refine(coordinator, 'num_modes=5', root.accepted_revision,
                                owner_session_id='owner-session')
            if when == 'before_selection':
                view = await select(coordinator, source)
            assert view['phase'] == 'AWAITING_INPUT', view
            assert view['prepared_preview']['parameters_suggestion']['num_modes'] == 5
            assert 'confirmed_parameters' not in view['prepared_preview']
            assert root.accepted_slots['num_modes'] == dict(state='fixed', value=5)
            confirmed = await coordinator.confirm_parameters(**confirmation_args(view))
            assert confirmed['reason_code'] == 'inputs_confirmed_staging_required', confirmed
            assert confirmed['input_ready'] is False and root.input_context is None
            await coordinator.close_root(root_id='root-1', owner_session_id='owner-session')
    asyncio.run(run())


@pytest.mark.parametrize('mutation', ['source', 'model', 'root', 'deadline'])
def test_refinement_cannot_outlive_its_original_authority(tmp_path, target_case, monkeypatch, mutation):
    rig, pointer = source_rig(tmp_path, target_case)
    monkeypatch.setattr(source_preparation, '_source_candidate', _complete_source_child)
    async def run():
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            entry(coordinator)
            await rig.begin(coordinator, structure_context=pointer)
            root = coordinator._roots['root-1']
            rig.output = response('num_modes=5', {'num_modes': 5})
            invalidate_source(rig, mutation)
            await refine(coordinator, 'num_modes=5', owner_session_id='owner-session')
            assert root.closed and len(rig.calls) == 1
            assert root.accepted_slots['num_modes'] == dict(state='pending')
            await coordinator.close_root(root_id='root-1', owner_session_id='owner-session')
    asyncio.run(run())


def test_concurrent_duplicate_cannot_issue_a_second_model_call(tmp_path):
    rig = Rig(tmp_path)
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        import httpx
        from tests.agent.test_web_docking_coordinator import wire
        async def handler(request):
            rig.calls.append(request)
            if len(rig.calls) == 2:
                entered.set()
                await release.wait()
            return httpx.Response(200, json=wire(rig.mode, rig.output))
        async with rig.client(handler) as client:
            coordinator = rig.coordinator(client)
            entry(coordinator)
            await begin(rig, coordinator)
            rig.output = response('exhaustiveness=8', {'exhaustiveness': 8})
            pending = asyncio.create_task(refine(coordinator))
            try:
                await asyncio.wait_for(entered.wait(), 5)
                duplicate = await refine(coordinator)
                assert duplicate['phase'] == 'BUSY' and len(rig.calls) == 2
                assert len(coordinator._roots['root-1'].text_refinements) == 1
            finally:
                release.set()
                await pending
                await coordinator.close_root(root_id='root-1', owner_session_id='owner-1')
    asyncio.run(run())


@pytest.mark.parametrize('kind', ['wrong_source', 'unresolved', 'wrong_value'])
def test_unconfirmed_model_fields_never_fill_draft(tmp_path, kind):
    rig = Rig(tmp_path)
    async def run():
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            entry(coordinator)
            original = await begin(rig, coordinator)
            rig.output = response('exhaustiveness=8', {'exhaustiveness': 8})
            if kind == 'wrong_source':
                rig.output['source_spans'][0]['source_id'] = 'query'
            elif kind == 'unresolved':
                rig.output['unresolved'] = True
            else:
                rig.output['fields']['exhaustiveness']['value'] = 9
            result = await refine(coordinator)
            assert result['reason_code'] == 'clarification_required'
            after = rig.read(original)
            assert after['draft']['slots']['exhaustiveness'] == dict(state='pending')
            assert after['draft']['budget']['proposal_slots_spent'] == 2
            assert coordinator._roots['root-1'].accepted_revision == 0
            await coordinator.close_root(root_id='root-1', owner_session_id='owner-1')
    asyncio.run(run())


def test_cancellation_retains_original_issuer_budget_and_actual_model_reader(tmp_path):
    rig = Rig(tmp_path)
    async def run():
        import httpx
        from tests.agent.test_web_docking_coordinator import wire
        entered, release, writer_entered = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def handler(request):
            rig.calls.append(request)
            if len(rig.calls) == 2:
                entered.set()
                while not release.is_set():
                    try:
                        await release.wait()
                    except asyncio.CancelledError:
                        pass
            return httpx.Response(200, json=wire(rig.mode, rig.output))
        async def writer():
            async with rig.gate.exclusive():
                writer_entered.set()
        async with rig.client(handler) as client:
            coordinator = rig.coordinator(client)
            entry(coordinator)
            original = await begin(rig, coordinator)
            rig.output = response('exhaustiveness=8', {'exhaustiveness': 8})
            root = coordinator._roots['root-1']
            pending = asyncio.create_task(refine(coordinator))
            write = None
            try:
                await asyncio.wait_for(entered.wait(), 5)
                pending.cancel()
                write = asyncio.create_task(writer())
                await asyncio.sleep(.03)
                pending.cancel()
                assert not pending.done() and not writer_entered.is_set()
                assert rig.gate._readers == 1 and root.closed
                assert rig.read(original)['draft']['budget']['proposal_slots_spent'] == 2
                await refine(coordinator)
                assert len(rig.calls) == 2
            finally:
                release.set()
                with pytest.raises(asyncio.CancelledError):
                    await pending
                if write is not None:
                    await write
                await coordinator.close_root(root_id='root-1', owner_session_id='owner-1')
            assert len(rig.calls) == 2 and rig.gate._readers == 0
            assert (await begin(rig, coordinator))['phase'] == 'UNAVAILABLE'
    asyncio.run(run())


@pytest.mark.parametrize('committed', [False, True])
def test_refinement_cas_lost_receipt_cannot_be_recovered_or_retried(tmp_path, monkeypatch, committed):
    rig = Rig(tmp_path)
    async def run():
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            entry(coordinator)
            initial = await begin(rig, coordinator)
            root = coordinator._roots['root-1']
            rig.output = response('exhaustiveness=8', {'exhaustiveness': 8})
            attempts = []
            actual = rig.tasks.refine_docking_consent_draft
            def lost(**kwargs):
                attempts.append(True)
                if committed:
                    actual(**kwargs)
                raise OSError('synthetic lost receipt')
            monkeypatch.setattr(rig.tasks, 'refine_docking_consent_draft', lost)
            result = await refine(coordinator)
            assert result['phase'] == 'UNAVAILABLE' and root.closed
            assert root.accepted_revision == 0
            await refine(coordinator)
            assert attempts == [True] and len(rig.calls) == 2
            row = rig.tasks.get_docking_consent(initial['preparation_id'])
            if committed:
                assert row['state'] == 'AWAITING_INPUT'
                assert root.close_state == 'unconfirmed'
            else:
                assert row['state'] == 'REVOKED'
    asyncio.run(run())


@pytest.mark.parametrize('changed', [False, True])
def test_ready_text_never_refines_or_approves_and_changed_fixed_input_revokes(
        tmp_path, target_case, monkeypatch, changed):
    from tests.task_runtime.test_chat_docking_execution_context import execution_case
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            calls = len(case.rig.calls)
            before = case.root.accepted_revision
            view = await refine(case.coordinator, 'exhaustiveness=' + ('9' if changed else '8'),
                before, owner_session_id='owner-session')
            assert case.root.accepted_revision == before
            assert not case.raw and len(case.rig.calls) == calls
            if changed:
                assert case.root.closed
                assert view['reason_code'] == 'new_root_required'
                assert case.rig.tasks.get_docking_consent(case.root.preparation_id)['state'] == 'REVOKED'
            else:
                assert not case.root.closed
                assert case.rig.tasks.get_docking_consent(case.root.preparation_id)['state'] == 'READY'
    asyncio.run(run())


@pytest.mark.parametrize('selected', [False, True])
def test_verified_pocket_cannot_be_replaced_when_original_query_omits_pocket_clause(
        tmp_path, target_case, monkeypatch, selected):
    rig, pointer = source_rig(tmp_path, target_case)
    rig.args['query'] = '用 CCO 与 1ABC 对接。'
    rig.output = proposal(rig.args['query'])
    monkeypatch.setattr(source_preparation, '_source_candidate', _complete_source_child)
    async def run():
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            source = await rig.begin(coordinator, structure_context=pointer)
            if selected:
                await select(coordinator, source)
            root = coordinator._roots['root-1']
            revision = root.accepted_revision
            rig.output = response('center=[100,100,100]', {'center': [100, 100, 100]})
            await refine(coordinator, 'center=[100,100,100]', revision, owner_session_id='owner-session')
            assert root.accepted_revision == revision
            assert root.accepted_slots['center'] == dict(state='pending')
            assert len(rig.calls) == 1
            if selected:
                assert json.loads(root.prepared_preview_json)['parameters_suggestion']['center'] == [5.5, 6.5, 7.5]
            await coordinator.close_root(root_id='root-1', owner_session_id='owner-session')
    asyncio.run(run())


@pytest.mark.parametrize('valid', [False, True])
def test_unresolved_or_inconsistent_response_cannot_republish_revoked_source(
        tmp_path, target_case, monkeypatch, valid):
    import httpx
    from tests.agent.test_web_docking_coordinator import wire
    rig, pointer = source_rig(tmp_path, target_case)
    monkeypatch.setattr(source_preparation, '_source_candidate', _complete_source_child)
    async def run():
        async def handler(request):
            rig.calls.append(request)
            if len(rig.calls) == 2:
                invalidate_source(rig, 'source')
            return httpx.Response(200, json=wire(rig.mode, rig.output))
        async with rig.client(handler) as client:
            coordinator = rig.coordinator(client)
            await rig.begin(coordinator, structure_context=pointer)
            root = coordinator._roots['root-1']
            rig.output = response('num_modes=5', {'num_modes': 5})
            rig.output['unresolved'] = True
            if not valid:
                rig.output['fields']['num_modes']['value'] = 6
            view = await refine(coordinator, 'num_modes=5', owner_session_id='owner-session')
            assert root.closed and view['phase'] == 'UNAVAILABLE'
            assert 'source_preview' not in view
            assert root.accepted_slots['num_modes'] == dict(state='pending')
            await coordinator.close_root(root_id='root-1', owner_session_id='owner-session')
    asyncio.run(run())


@pytest.mark.parametrize('ready', [False, True])
def test_unchanged_text_revalidates_authority_before_returning_previews(
        tmp_path, target_case, monkeypatch, ready):
    from tests.task_runtime.test_chat_docking_execution_context import execution_case
    async def run():
        if not ready:
            rig, pointer = source_rig(tmp_path, target_case)
            rig.args['query'] = '用 CCO 与 1ABC 对接。exhaustiveness=8。'
            rig.output = proposal(rig.args['query'])
            monkeypatch.setattr(source_preparation, '_source_candidate', _complete_source_child)
            async with rig.client() as client:
                coordinator = rig.coordinator(client)
                source = await rig.begin(coordinator, structure_context=pointer)
                await select(coordinator, source)
                root = coordinator._roots['root-1']
                invalidate_source(rig, 'source')
                result = await refine(coordinator, 'exhaustiveness=8', root.accepted_revision,
                    owner_session_id='owner-session')
                assert result['phase'] == 'UNAVAILABLE' and root.closed
                assert 'source_preview' not in result and 'prepared_preview' not in result
                await coordinator.close_root(root_id='root-1', owner_session_id='owner-session')
        else:
            async with execution_case(tmp_path, target_case, monkeypatch) as case:
                invalidate_source(case.rig, 'source')
                result = await refine(case.coordinator, 'exhaustiveness=8', case.root.accepted_revision,
                    owner_session_id='owner-session')
                assert result['phase'] == 'UNAVAILABLE' and case.root.closed
                assert not result['input_ready'] and 'consent_preview' not in result
    asyncio.run(run())


def test_changed_fixed_text_closes_busy_root_without_losing_physical_owner(tmp_path):
    import httpx
    from tests.agent.test_web_docking_coordinator import wire
    rig = Rig(tmp_path)
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        async def handler(request):
            rig.calls.append(request)
            if len(rig.calls) == 2:
                entered.set()
                while not release.is_set():
                    try:
                        await release.wait()
                    except asyncio.CancelledError:
                        pass
            return httpx.Response(200, json=wire(rig.mode, rig.output))
        async with rig.client(handler) as client:
            coordinator = rig.coordinator(client)
            await begin(rig, coordinator, 'run docking; exhaustiveness=8')
            root = coordinator._roots['root-1']
            rig.output = response('num_modes=5', {'num_modes': 5})
            pending = asyncio.create_task(refine(coordinator, 'num_modes=5'))
            try:
                await asyncio.wait_for(entered.wait(), 5)
                result = await refine(coordinator, 'exhaustiveness=9')
                assert root.closed and result['reason_code'] == 'new_root_required'
                assert not pending.done() and rig.gate._readers == 1
            finally:
                release.set()
                await pending
                await coordinator.close_root(root_id='root-1', owner_session_id='owner-1')
            assert root.accepted_revision == 0 and len(rig.calls) == 2
    asyncio.run(run())
