"""Closed success DTO from actual C2 receipts; all science SYNTHETIC, not Vina."""
import asyncio
from copy import copy
from dataclasses import replace
import importlib
import inspect
import json
import sqlite3
import threading

import pytest

from tests.task_runtime.test_chat_docking_execution_context import execution_case, target_case
from tests.agent.test_docking_publication_candidate import settle, publication_api
from tests.agent.test_docking_publication_receipt import publish, checkpoint
from tests.agent.test_web_docking_sources import invalidate_source


def projector():
    name = 'src.web.docking_result_view'
    try:
        module = importlib.import_module(name)
    except ModuleNotFoundError as exc:
        if exc.name != name:
            raise
        pytest.fail('D4 verified result projection is absent', pytrace=False)
    entry = getattr(module, 'verified_docking_result', None)
    assert callable(entry), 'D4 verified result projection callable is absent'
    return entry


async def project(case, **kwargs):
    return await projector()(case.coordinator, root_id=case.root.root_id,
        owner_session_id=kwargs.pop('owner', case.root.owner), **kwargs)


def test_projection_accepts_only_owned_live_identity():
    assert set(inspect.signature(projector()).parameters) == {'coordinator', 'root_id', 'owner_session_id'}


def test_exact_closed_science_detached_and_non_sliding(tmp_path, target_case, monkeypatch):
    projector()
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            await settle(case)
            outcome = await publish(case)
            grant = case.root.publication.grant
            consent = case.rig.tasks.get_docking_consent(case.root.preparation_id)
            science = outcome.projection
            expected = {name: getattr(science, name) for name in (
                'tool_name', 'tool_version', 'execution_backend', 'input_hash', 'config_hash',
                'tool_policy_digest', 'source_digest', 'original_receptor_digest',
                'ligand_identity_digest', 'projection_digest', 'score_kind', 'pose_count')}
            expected.update(best_energy_kcal_mol=science.energy_kcal_mol, energy_unit='kcal/mol',
                pose=dict(artifact_id='pose', size_bytes=science.pose_size, sha256=science.pose_sha256,
                    download_path=f'/api/agent/docking/tasks/{case.root.task_id}/artifacts/pose'))
            first = await project(case)
            assert first == expected
            assert type(first['best_energy_kcal_mol']) is float
            assert type(first['pose_count']) is int
            assert json.loads(json.dumps(first, allow_nan=False)) == expected
            first['best_energy_kcal_mol'] = 999
            first['pose']['download_path'] = 'https://foreign.invalid'
            case.rig.wall += 421_000
            case.rig.mono += 421
            assert await project(case) == expected
            assert grant is case.root.publication.grant
            assert case.raw == [True] and case.rig.gate._readers == 0
            assert case.rig.tasks.get_docking_consent(case.root.preparation_id) == consent
    asyncio.run(run())


@pytest.mark.parametrize('phase', ['ready', 'settled', 'candidate', 'pending', 'suppressed'])
def test_projection_never_starts_publication_or_execution(tmp_path, target_case, monkeypatch, phase):
    projector()
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            if phase != 'ready':
                await settle(case)
            if phase == 'candidate':
                await publication_api().capture_docking_publication_candidate(case.coordinator,
                    root_id=case.root.root_id, owner_session_id=case.root.owner)
            if phase in {'pending', 'suppressed'}:
                state = publication_api()._Publication(case.context, asyncio.get_running_loop().time() + 5)
                case.root.publication = state
                if phase == 'suppressed':
                    state.suppress('cancelled')
            state, raw = case.root.publication, list(case.raw)
            def forbidden(*args, **kwargs):
                pytest.fail('read-only projection dispatched SQL/file work before confirmed publication')
            with monkeypatch.context() as patch:
                patch.setattr(case.coordinator, '_sql', forbidden)
                with pytest.raises(publication_api().DockingPublicationUnavailable, match='^docking_publication_unavailable$'):
                    await project(case)
            assert case.root.publication is state and case.raw == raw
    asyncio.run(run())


@pytest.mark.parametrize('change', ['foreign', 'root_copy', 'state_copy', 'outcome_copy',
    'candidate_copy', 'context_copy', 'grant_copy', 'restart', 'expiry', 'source', 'pose'])
def test_projection_rejects_invalid_live_authority_without_science(tmp_path, target_case, monkeypatch, change):
    projector()
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            await settle(case)
            outcome = await publish(case)
            state = case.root.publication
            kwargs = {}
            if change == 'foreign': kwargs['owner'] = 'foreign-owner'
            elif change == 'root_copy': case.coordinator._roots[case.root.root_id] = copy(case.root)
            elif change == 'state_copy': case.root.publication = copy(state)
            elif change == 'outcome_copy': state.outcome = replace(outcome)
            elif change == 'candidate_copy': state.candidate = replace(state.candidate)
            elif change == 'context_copy': case.root.input_context = copy(case.context)
            elif change == 'grant_copy': state.grant = replace(state.grant)
            elif change == 'restart': case.coordinator._roots.clear()
            elif change == 'expiry': case.rig.wall += 86_400_000
            elif change == 'source': invalidate_source(case.rig, 'source')
            elif change == 'pose':
                manifest = case.runtime._docking_private_output.completions.manifest_path(case.root.task_id)
                pose = manifest.parent / json.loads(manifest.read_text(encoding='utf-8'))['pose']['path']
                assert pose.is_file()
                pose.write_bytes(b'corrupt synthetic pose')
            with pytest.raises(publication_api().DockingPublicationUnavailable, match='^docking_publication_unavailable$'):
                await project(case, **kwargs)
            assert outcome.status == 'SUCCEEDED' and case.raw == [True]
    asyncio.run(run())


def test_cancelled_projection_does_not_return_late_science_or_detach_reader(tmp_path, target_case, monkeypatch):
    projector()
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            await settle(case)
            outcome = await publish(case)
            module = publication_api()
            started, release = threading.Event(), threading.Event()
            original = module._capture_guarded
            def held(*args, **kwargs):
                started.set()
                assert release.wait(5), 'test barrier was not released'
                return original(*args, **kwargs)
            monkeypatch.setattr(module, '_capture_guarded', held)
            task = asyncio.create_task(project(case))
            try:
                await checkpoint(started.is_set)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert not case.root.publication.observation.done()
                assert case.rig.gate._readers == 1
            finally:
                release.set()
                await asyncio.gather(task, return_exceptions=True)
                if case.root.publication.observation is not None:
                    await asyncio.gather(case.root.publication.observation, return_exceptions=True)
            assert case.root.publication.outcome is outcome
            assert case.rig.gate._readers == 0
            assert (await project(case))['best_energy_kcal_mol'] == -7.2
    asyncio.run(run())


def test_latch_with_unfinished_publication_owner_is_not_a_fresh_observation(tmp_path, target_case, monkeypatch):
    projector()
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            await settle(case)
            await publish(case)
            state = case.root.publication
            original = state.task
            held = asyncio.get_running_loop().create_future()
            state.task = held
            try:
                with pytest.raises(publication_api().DockingPublicationUnavailable):
                    await project(case)
                assert state.observation is None and not state.access_disabled
            finally:
                held.set_result(None)
                state.task = original
            assert (await project(case))['best_energy_kcal_mol'] == -7.2
    asyncio.run(run())


@pytest.mark.parametrize('database', ['tasks', 'state'])
def test_projection_actual_writer_contention_does_not_revoke_success(tmp_path, target_case, monkeypatch, database):
    projector()
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            await settle(case)
            outcome = await publish(case)
            blocker = sqlite3.connect(getattr(case.rig, database).db_path, timeout=0)
            blocker.execute('BEGIN IMMEDIATE')
            try:
                with pytest.raises(publication_api().DockingPublicationUnavailable):
                    await project(case)
                assert not case.root.publication.access_disabled
                assert case.root.publication.outcome is outcome
                assert case.rig.gate._readers == 0
            finally:
                blocker.rollback()
                blocker.close()
            assert (await project(case))['best_energy_kcal_mol'] == -7.2
    asyncio.run(run())


@pytest.mark.parametrize('change', ['model', 'root'])
def test_after_observation_replacement_is_rejected_and_sticky(tmp_path, target_case, monkeypatch, change):
    projector()
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            await settle(case)
            outcome = await publish(case)
            module = importlib.import_module('src.web.docking_result_view')
            original = module._confirmed_observation
            generation = case.rig.context['model_generation']
            calls = []
            async def observed(*args, **kwargs):
                value = await original(*args, **kwargs)
                calls.append(True)
                if change == 'model': case.rig.context['model_generation'] = 'replacement'
                else: case.coordinator._roots[case.root.root_id] = copy(case.root)
                return value
            with monkeypatch.context() as patch:
                patch.setattr(module, '_confirmed_observation', observed)
                with pytest.raises(publication_api().DockingPublicationUnavailable):
                    await project(case)
            assert calls == [True] and case.root.publication.access_disabled
            case.rig.context['model_generation'] = generation
            case.coordinator._roots[case.root.root_id] = case.root
            with pytest.raises(publication_api().DockingPublicationUnavailable):
                await project(case)
            assert case.root.publication.outcome is outcome and case.rig.gate._readers == 0
    asyncio.run(run())
