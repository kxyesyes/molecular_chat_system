"""Actual independent Python processes; no real API key or model inference.

Mounted /ws admission, session ownership, CAS, events, evidence and RDKit are
real. Model responses are the existing offline protocol fixture. A passing
result does not attest production workers, HTTPS/WSS, proxies or Temporal.
"""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tests.family_acceptance_process_support import child_environment


ROOT = Path(__file__).resolve().parents[2]
WORKER = Path(__file__).with_name('web_recovery_process_support.py')


def run_phase(directory, phase, pointer=None):
    environment = child_environment(os.environ, directory)
    child = subprocess.run([sys.executable, '-B', str(WORKER), str(directory), phase],
        input=json.dumps(pointer or {}), capture_output=True, text=True, encoding='utf-8',
        env=environment, cwd=ROOT, timeout=90)
    # Never expose captured response data (including the ephemeral test cookie)
    # or diagnostics in assertion output.
    assert child.returncode == 0, 'isolated recovery process failed'
    try:
        result = json.loads(child.stdout)
    except ValueError:
        pytest.fail('isolated recovery process returned invalid JSON', pytrace=False)
    return result


@pytest.mark.parametrize('cached', [False, True])
def test_actual_process_restart_recovers_owner_waiting_and_preserves_evidence(tmp_path, cached):
    waiting = run_phase(tmp_path, 'wait_cached' if cached else 'wait')
    pointer = waiting.pop('pointer')
    assert waiting['status'] == 'waiting_for_input'
    assert waiting['invocations'] == int(cached)

    foreign = run_phase(tmp_path, 'foreign', pointer)
    assert foreign['status'] == 'rejected'
    assert foreign['invocations'] == foreign['model_calls'] == 0

    resumed = run_phase(tmp_path, 'resume', pointer)
    assert resumed['pid'] != waiting['pid']
    assert resumed['success'] is True and resumed['status'] == 'succeeded'
    assert resumed['trace_id'] == pointer['trace_id']
    assert resumed['claimed'] is True
    assert waiting['invocations'] + resumed['invocations'] == 1
    assert len(resumed['results']) == 1
    row = resumed['results'][0]
    assert row['tool_name'] == 'property_calculator' and row['success'] is True
    assert row['data'][0]['smiles'] == 'CCO'
    assert row['data'][0]['properties']['molecular_weight'] == pytest.approx(46.07)
    assert row['quality']['evidence_id']
    if cached:
        assert resumed['results'] == waiting['results']
    assert resumed['terminal_frames'] == 1 and resumed['answer_matches'] is True
    assert 'tool_completed' in resumed['events'] and 'task_completed' in resumed['events']
    assert resumed['event_sequences'] == sorted(set(resumed['event_sequences']))

    replay = run_phase(tmp_path, 'replay', pointer)
    assert replay['status'] == 'rejected'
    assert replay['invocations'] == replay['model_calls'] == 0


def test_competing_processes_claim_waiting_continuation_exactly_once(tmp_path):
    waiting = run_phase(tmp_path, 'wait')
    pointer = waiting.pop('pointer')
    with ThreadPoolExecutor(max_workers=2) as pool:
        attempts = list(pool.map(lambda _: run_phase(tmp_path, 'resume', pointer), range(2)))
    assert len({item['pid'] for item in attempts}) == 2
    assert sorted(item['status'] for item in attempts) == ['rejected', 'succeeded']
    assert sum(item['invocations'] for item in attempts) == 1
    winner = next(item for item in attempts if item['status'] == 'succeeded')
    assert winner['claimed'] is True
    assert winner['trace_id'] == pointer['trace_id']
    assert winner['terminal_frames'] == 1


@pytest.mark.parametrize('phase', ['wait_rotated', 'wait_refreshed'])
def test_restart_after_live_config_publication_can_resume(tmp_path, phase):
    waiting = run_phase(tmp_path, phase)
    resumed = run_phase(tmp_path, 'resume', waiting.pop('pointer'))
    assert resumed['success'] is True
    assert resumed['stored_status'] == 'succeeded'
    assert resumed['invocations'] == 1


def test_credential_rotation_while_stopped_invalidates_continuation(tmp_path):
    waiting = run_phase(tmp_path, 'wait_key')
    resumed = run_phase(tmp_path, 'resume_changed_key', waiting.pop('pointer'))
    assert resumed['status'] == 'rejected'
    assert resumed['invocations'] == resumed['model_calls'] == 0
