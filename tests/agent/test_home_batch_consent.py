"""The confirmation covers the complete, ordered batch and the prepared receptor."""
from types import SimpleNamespace

import pytest
from tests.agent.test_web_docking_consent import target_case


def test_batch_binding_changes_with_each_confirmed_input():
    from src.web.docking_batch_consent import batch_binding

    root = SimpleNamespace(owner='alice', task_id='task', accepted_revision=4,
        batch={'pdb_id': '6SAM', 'ligands': [{'ordinal': 3, 'smiles': 'CCO'},
                                             {'ordinal': 1, 'smiles': 'CCN'}]},
        batch_ligands=[{'ordinal': 3, 'smiles': 'CCO', 'sdf_bytes': b'ethanol'},
                       {'ordinal': 1, 'smiles': 'CCN', 'sdf_bytes': b'ethylamine'}],
        projected={'pocket': {'residue': {'name': 'LIG'}}})
    context = SimpleNamespace(_digest=b'confirmed-source-policy', _receptor=b'receptor',
        _parameters={'center': [1, 2, 3], 'size': [20, 20, 20], 'num_modes': 9, 'exhaustiveness': 8})
    digest, preview = batch_binding(root, context)
    assert preview['ligands'] == root.batch['ligands']
    assert preview['pdb_id'] == '6SAM' and len(digest) == 64
    root.batch_ligands[1]['sdf_bytes'] += b'changed'
    assert batch_binding(root, context)[0] != digest
    root.batch_ligands.reverse()
    with pytest.raises(ValueError):
        batch_binding(root, context)


def test_invalid_batch_gets_specific_clarification_without_starting_docking():
    import asyncio
    import json
    from src.web.docking_chat_controller import DockingChatController

    async def run():
        controller = object.__new__(DockingChatController)
        controller.enabled = True
        controller.runtime = SimpleNamespace(sockets=[], closing=False, references=None)
        frames = []
        async def send(_text, *, capture):
            frames.append(json.loads(capture()))
        sender = SimpleNamespace(writable=True, receiver_attached=True, _runtime=controller.runtime,
            websocket=SimpleNamespace(scope={'agent_session_id': 'alice'}), send_text=send)
        controller.runtime.sockets.append(sender)
        assert await controller.handle(sender, {'type': 'chat', 'message': '把 CCO 和 XYZ 与 PDB 6SAM 对接'})
        assert len(frames) == 1 and '第 2 项' in frames[0]['message']
        assert frames[0]['docking_batch_clarification'] and not hasattr(sender, '_chat_docking')
    asyncio.run(run())


def test_batch_http_confirmation_executes_one_batch_and_serves_only_owned_poses(
        tmp_path, target_case, monkeypatch):
    import asyncio
    import httpx
    from src.docking import source_preparation
    from tests.agent.test_home_docking_batch_execution import service
    from tests.agent.test_web_docking_http import app_for, cookies, ORIGIN
    from tests.agent.test_web_docking_parameters import confirmation_args
    from tests.agent.test_web_docking_selection import select, _complete_source_child
    from tests.agent.test_web_docking_sources import source_rig
    from tests.task_runtime.test_docking_execution import _pose_result, _vina_pdbqt

    calls = []
    def worker(payload, *, job_id, **kwargs):
        calls.append(job_id)
        pose = tmp_path / 'private' / ('docking_' + job_id) / 'result.pdbqt'
        pose.parent.mkdir(parents=True)
        pose.write_text(_vina_pdbqt(-2.7), encoding='ascii')
        return _pose_result(pose, energy=-2.7)

    monkeypatch.setattr(source_preparation, '_source_candidate', _complete_source_child)
    rig, pointer = source_rig(tmp_path, target_case)
    execution, runner = service(tmp_path, worker)
    rig.tasks = runner.store
    rig.context['policy'] = runner.policy
    rows = [{'ordinal': 3, 'smiles': 'CCO'}, {'ordinal': 1, 'smiles': 'CCN'}]
    async def run():
        async with rig.client() as model:
            coordinator = rig.coordinator(model)
            coordinator.batch_execution = execution
            initial = await rig.begin(coordinator, structure_context=pointer, batch=dict(pdb_id='1ABC', ligands=rows))
            prepared = await select(coordinator, initial)
            await coordinator.confirm_parameters(**confirmation_args(prepared))
            root = coordinator._owned('root-1', 'owner-session')
            case = SimpleNamespace(root=root, coordinator=coordinator, runtime=runner.runtime)
            app, token, foreign = app_for(case, tmp_path)
            prefix = '/api/agent/docking/tasks/' + root.task_id
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN,
                                         headers={'Origin': ORIGIN}, cookies=cookies(token)) as browser:
                receipt = await browser.post(prefix + '/batch-preview', json={'revision': root.accepted_revision})
                assert receipt.status_code == 200, receipt.text
                assert receipt.json()['ligands'] == rows and not calls
                approval = {key: receipt.json()[key] for key in ('approval_nonce', 'binding_digest')}
                approval['confirm'] = True
                responses = await asyncio.gather(*(browser.post(prefix + '/batch-approve', json=approval)
                                                   for _ in range(2)))
                assert [r.status_code for r in responses] == [200, 200], [r.text for r in responses]
                assert root.close_state == 'confirmed'
                assert coordinator._owned(root.root_id, root.owner) is None
                for _ in range(200):
                    result = (await browser.get(prefix + '/batch-status')).json()
                    if result['status'] == 'completed':
                        break
                    await asyncio.sleep(.02)
                assert result['status'] == 'completed', result
                assert len(set(calls)) == len(calls) == 2
                assert [r['ordinal'] for r in result['rows']] == [3, 1]
                for row in result['rows']:
                    pose = await browser.get(row['download_path'])
                    assert pose.status_code == 200 and b'-2.700' in pose.content
                browser.cookies = cookies(foreign)
                for url in [prefix + '/batch-status', result['rows'][0]['download_path']]:
                    assert (await browser.get(url)).status_code == 404
        await execution.shutdown()
        await runner.runtime.close()
    asyncio.run(run())
