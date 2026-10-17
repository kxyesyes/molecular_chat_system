"""Browser GET provenance with real sessions/C2 and synthetic pose, NOT Vina."""
import asyncio
import hashlib

import pytest

from tests.task_runtime.test_chat_docking_execution_context import execution_case, target_case
from tests.agent.test_docking_publication_candidate import settle
from tests.agent.test_docking_publication_receipt import publish
from tests.agent.test_docking_publication_artifacts import client_for, path
from tests.agent.test_web_docking_http import ORIGIN


@pytest.mark.parametrize('mode', ['cors', 'navigate'])
def test_browser_same_origin_get_without_origin(tmp_path, target_case, monkeypatch, mode):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            await settle(case)
            outcome = await publish(case)
            async with client_for(case, tmp_path) as client:
                response = await client.get(path(case), headers={
                    'Sec-Fetch-Site': 'same-origin', 'Sec-Fetch-Mode': mode})
            assert response.status_code == 200
            assert hashlib.sha256(response.content).hexdigest() == outcome.projection.pose_sha256
            assert response.headers['cache-control'] == 'private, no-store'
            assert case.raw == [True] and case.rig.gate._readers == 0
    asyncio.run(run())


def test_untrusted_browser_provenance_rejected_before_grant_or_file_io(tmp_path, target_case, monkeypatch):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            await settle(case)
            await publish(case)
            import src.web.docking_artifacts as artifacts
            original = artifacts.docking_artifact_response
            calls = []
            def response(*args, **kwargs):
                calls.append(True)
                return original(*args, **kwargs)
            monkeypatch.setattr(artifacts, 'docking_artifact_response', response)
            invalid = [[], [('Referer', ORIGIN + '/')]]
            for site in ('cross-site', 'same-site', 'none', '', 'Same-Origin',
                         'same-origin, same-origin', ' same-origin'):
                invalid.append([('Sec-Fetch-Site', site)])
                invalid.append([('Origin', ORIGIN), ('Sec-Fetch-Site', site)])
            invalid += [
                [('Sec-Fetch-Site', 'same-origin'), ('Sec-Fetch-Site', 'same-origin')],
                [('Origin', ORIGIN), ('Sec-Fetch-Site', 'same-origin'), ('Sec-Fetch-Site', 'cross-site')],
            ]
            for origin in ('https://foreign.invalid', 'null', '', ORIGIN + '/'):
                invalid.append([('Origin', origin), ('Sec-Fetch-Site', 'same-origin')])
            invalid.append([('Origin', ORIGIN), ('Origin', ORIGIN), ('Sec-Fetch-Site', 'same-origin')])
            async with client_for(case, tmp_path) as client:
                for headers in invalid:
                    result = await client.get(path(case), headers=headers)
                    assert result.status_code == 403, headers
                    assert not calls, headers
                # Rejections do not revoke a legitimate owner's live grant.
                for headers in ({'Origin': ORIGIN}, {'Origin': ORIGIN, 'Sec-Fetch-Site': 'same-origin'}):
                    assert (await client.get(path(case), headers=headers)).status_code == 200
            assert len(calls) == 2 and case.rig.gate._readers == 0
    asyncio.run(run())


def test_fetch_metadata_never_replaces_mutation_origin(tmp_path, target_case, monkeypatch):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            before = case.root.phase
            paths = ['/api/agent/docking/preparations',
                f'/api/agent/docking/preparations/{case.root.preparation_id}/approve',
                f'/api/agent/docking/preparations/{case.root.preparation_id}/revoke',
                f'/api/agent/docking/tasks/{case.root.task_id}/cancel']
            async with client_for(case, tmp_path) as client:
                for url in paths:
                    result = await client.post(url, headers={'Sec-Fetch-Site': 'same-origin'})
                    assert result.status_code == 403
            assert case.root.phase == before and not case.raw
    asyncio.run(run())


@pytest.mark.parametrize('foreign', [False, True])
def test_same_origin_metadata_is_not_grant_authority(tmp_path, target_case, monkeypatch, foreign):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            if foreign:
                await settle(case)
                await publish(case)
            def forbidden(*args, **kwargs):
                pytest.fail('unpublished/foreign request reached physical artifact read')
            monkeypatch.setattr(case.runtime._docking_private_output.completions,
                'read_verified_artifact', forbidden)
            async with client_for(case, tmp_path, foreign=foreign) as client:
                assert (await client.get(path(case), headers={'Sec-Fetch-Site': 'same-origin'})).status_code == 404
    asyncio.run(run())
