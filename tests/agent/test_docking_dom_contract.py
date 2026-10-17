"""Actual server view -> native JS contract, with synthetic science only."""
import asyncio
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from src.web.docking_view import capture_docking_view
from src.web.docking_coordinator import DockingRequestCoordinator, _SECTIONS
from tests.task_runtime.test_chat_docking_execution_context import execution_case, target_case
from tests.agent.test_docking_publication_candidate import settle
from tests.agent.test_docking_publication_receipt import publish


def test_actual_captured_ready_and_verified_result_are_accepted_by_dom(tmp_path, target_case, monkeypatch):
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node is required for the native homepage contract')
    captured = {}
    begin = DockingRequestCoordinator.begin
    async def observed_begin(coordinator, **kwargs):
        view = await begin(coordinator, **kwargs)
        root = coordinator._roots[kwargs['root_id']]
        captured['source'] = await capture_docking_view(coordinator,
            root_id=root.root_id, owner_session_id=root.owner, origin_turn_id=root.origin,
            sequence=1, send_generation=1)
        assert captured['source']['reason_code'] == 'scientific_selection_required'
        captured['pages'] = {}
        for section in _SECTIONS:
            pages, offset = [], 0
            while True:
                page = await coordinator.source_page(root_id=root.root_id, owner_session_id=root.owner,
                    source_sha256=view['source_preview']['source_sha256'], section=section, offset=offset, limit=32)
                assert page['ok']
                pages.append(page)
                if page['next_offset'] is None:
                    break
                offset = page['next_offset']
            captured['pages'][section] = pages
        return view
    monkeypatch.setattr(DockingRequestCoordinator, 'begin', observed_begin)
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            args = dict(root_id=case.root.root_id, owner_session_id=case.root.owner,
                        origin_turn_id=case.root.origin)
            ready = await capture_docking_view(case.coordinator, **args, sequence=1, send_generation=1)
            assert ready['view_status'] == 'READY' and ready['preview']['consent']
            await settle(case)  # Explicit synthetic approval, not a real Vina job.
            assert (await publish(case)).status == 'SUCCEEDED'
            result = await capture_docking_view(case.coordinator, **args, sequence=2, send_generation=2)
            assert result['view_status'] == 'SUCCEEDED' and result['result']
            return [ready, result]
    captured['views'] = asyncio.run(run())
    repo = Path(__file__).resolve().parents[2]
    result = subprocess.run([node, 'tests/home_docking_consent_test.js', '--captured'], input=json.dumps(captured), text=True,
                            capture_output=True, cwd=repo, timeout=30, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
