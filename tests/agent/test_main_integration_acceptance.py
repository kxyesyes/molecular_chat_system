"""Bounded offline HTTP/session -> WS acceptance; real RDKit, no model API."""
from functools import wraps
import json
import os
from pathlib import Path
from queue import Empty
import re
import subprocess
import sys
from tempfile import TemporaryDirectory
import time

import pytest
from fastapi.testclient import TestClient

from src.agent.contracts import AgentErrorCode, ToolResult
from src.agent.tools.drug_likeness_assessment import DrugLikenessAssessment
from tests.agent.test_scientific_reference_browser_lab import (
    DeadlineSocket, capture, receive_until,
)
from tests.scientific_reference_browser_lab import NOTICE, create_lab


BASE = 'http://127.0.0.1:6017'
PROPERTY_QUERY = '计算 CCO 的基础理化性质'
INJECTED_ERROR = 'OFFLINE TEST INJECTION: drug likeness deliberately unavailable'
NUMERIC_CLAIM = re.compile(
    r'(?:QED|LogP|pIC50|(?:binding\s+)?energy|结合能)'
    r'[^\dA-Za-z\n，。；、]{0,32}[-+]?\d+(?:\.\d+)?', re.I,
)


def _run_child(code, *args, timeout=30):
    """Bound imports, execution AND TestClient teardown; run kills/waits on timeout."""
    with TemporaryDirectory(prefix='medchat-acceptance-') as directory:
        root = Path(directory)
        env = {key: os.environ[key] for key in ('SYSTEMROOT', 'WINDIR', 'PATH', 'COMSPEC') if key in os.environ}
        env.update({key: str(root / name) for key, name in {
            'TEMP': '.', 'TMP': '.', 'HOME': '.', 'USERPROFILE': '.', 'APPDATA': '.', 'LOCALAPPDATA': '.',
            'MOLECULAR_CHAT_CONFIG': 'missing.yaml', 'MEDCHAT_ENV_FILE': 'not-loaded.env',
            'MEDCHAT_USER_CONFIG_DIR': 'user-config', 'MEDCHAT_LLM_LOCK_DIR': 'locks',
            'MEDCHAT_AGENT_SESSION_DB': 'sessions.sqlite', 'AGENT_STATE_DB': 'agent.sqlite',
            'MEDCHAT_TASK_DB_PATH': 'tasks.sqlite', 'TARGET_DB_PATH': 'targets.sqlite',
            'TARGET_CACHE_DIR': 'target-cache', 'MEDCHAT_FRAGMENT_DB_PATH': '.',
        }.items()})
        env.update(PYTHONDONTWRITEBYTECODE='1', PYTHONIOENCODING='utf-8', AGENT_HARNESS_MODE='legacy',
                   MEDCHAT_TASK_BACKEND='local', MEDCHAT_TEMPORAL_CANARY_PERCENT='0',
                   AGENT_LANGGRAPH_CANARY_PERCENT='0', MEDCHAT_RUN_FAMILY_REAL_ACCEPTANCE='0',
                   MEDCHAT_RUN_OPENSANDBOX_ACCEPTANCE='0', MEDCHAT_RUN_OPENSANDBOX_STABILITY_SOAK='0',
                   RUN_REAL_TARGET_SEARCH='0')
        result = subprocess.run([sys.executable, '-B', '-s', '-c', code, *args], cwd=root,
                                env=env, capture_output=True, timeout=timeout)
        assert result.returncode == 0, (result.stdout + result.stderr).decode('utf-8', errors='replace')


def isolated_scenario(function):
    @wraps(function)
    def run(tmp_path, *args, **kwargs):
        _run_child(
            "import json, runpy, sys; from pathlib import Path; "
            "sys.path.insert(0, str(Path(sys.argv[1]).parents[2])); "
            "module = runpy.run_path(sys.argv[1]); args, kwargs = json.loads(sys.argv[3]); "
            "module[sys.argv[2]].__wrapped__(Path.cwd(), *args, **kwargs)",
            str(Path(__file__).resolve()), function.__name__, json.dumps([args, kwargs]))
    return run


def test_subprocess_deadline_kills_and_reaps_child(monkeypatch):
    scenarios = [value for name, value in globals().items()
                 if name.startswith('test_') and name != 'test_subprocess_deadline_kills_and_reaps_child']
    assert len(scenarios) == 4 and all(hasattr(case, '__wrapped__') for case in scenarios), (
        'every TestClient scenario must run behind an external process deadline'
    )
    children = []
    original = subprocess.Popen
    def observe(*args, **kwargs):
        child = original(*args, **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(subprocess, 'Popen', observe)
    try:
        with pytest.raises(subprocess.TimeoutExpired) as expired:
            _run_child("import threading; print('ready', flush=True); threading.Event().wait()", timeout=3)
        assert b'ready' in expired.value.output
        assert len(children) == 1 and children[0].returncode is not None
        assert children[0].poll() is not None  # run() already killed AND waited.
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)


def exchange(app, query, *, terminal='complete'):
    """A queued ping is a processing barrier, including post-complete frames."""
    messages = []
    with TestClient(app, base_url=BASE) as client:
        page = client.get('/')
        assert page.status_code == 200 and NOTICE in page.text
        assert 'medchat_agent_session' in client.cookies
        with client.websocket_connect('ws://127.0.0.1:6017/ws', headers={'Origin': BASE}) as transport:
            ws = DeadlineSocket(transport, app.state.inbox)
            deadline = time.monotonic() + 10
            receive_until(ws, 'connection_ready', timeout=deadline - time.monotonic())
            ws.send_json({'message': query, 'enable_rag': False, 'enable_tools': True})
            ws.send_json({'type': 'ping', 'timestamp': 1729})
            for _ in range(128):
                remaining = deadline - time.monotonic()
                assert remaining > 0, 'WS total receive deadline exceeded'
                try:
                    message = ws.receive_json(timeout=remaining)
                except Empty:
                    pytest.fail('WS total receive deadline exceeded')
                assert message['type'] != 'error', message
                if message['type'] == 'pong':
                    assert message['timestamp'] == 1729
                    break
                messages.append(message)
            else:
                pytest.fail('WS message limit exceeded before processing barrier')
        stats = client.get('/api/agent/workflows/lab/stats')
        assert stats.status_code == 200 and stats.json()['offline_fixture'] is True
        assert stats.json()['calls'] == app.state.calls
    completed = [m for m in messages if m['type'] in {'complete', 'message'}]
    assert len(completed) == 1, messages
    assert completed[0]['type'] == terminal
    assert messages[-1] == completed[0], 'answer must be the final response frame'
    assert not any(m['type'] == 'molecule_candidates' or m.get('candidate_set')
                   for m in messages)
    events = [m['event'] for m in messages if m['type'] == 'agent_event']
    return completed[0], events


def terminal_events(events):
    return [e for e in events if e['event'] in {'tool_completed', 'tool_failed'}]


def assert_real_properties(event, body):
    assert event['event'] == 'tool_completed' and event['tool'] == 'property_calculator'
    payload = event['payload']
    assert payload['success'] is True and payload['status'] == 'succeeded'
    row, = payload['data']
    assert row['smiles'] == 'CCO'
    assert row['properties'] == {
        'molecular_formula': 'C2H6O', 'molecular_weight': 46.07, 'logp': -0.001,
        'hba': 1, 'hbd': 1, 'tpsa': 20.23, 'rotatable_bonds': 0, 'qed': 0.407,
    }
    for value in ('CCO', 'C2H6O', '46.07', '-0.001', '20.23', '0.407',
                  'RDKit', '不构成实验', '不是成药成功概率'):
        assert value in body
    assert NOTICE not in body
    assert '合理的生物利用度' not in body
    assert '预测具有良好的口服生物利用度' not in body


@isolated_scenario
def test_invalid_smiles_is_rejected_without_scientific_execution(tmp_path):
    app = capture(create_lab(tmp_path))
    complete, events = exchange(app, '请分析这个 SMILES 的成药性：CC(C)((。')
    body = complete['content']
    assert 'SMILES 无效' in body and '本次未进行科学计算' in body
    assert NOTICE not in body
    assert not any(app.state.calls.values())
    assert not terminal_events(events)
    assert not NUMERIC_CLAIM.search(body), body


@pytest.mark.parametrize('query', ['你好', 'hello', '什么是药物分子设计'])
@isolated_scenario
def test_plain_chat_uses_offline_model_without_scientific_events(tmp_path, query):
    app = capture(create_lab(tmp_path))
    answer, events = exchange(app, query, terminal='message')
    assert answer['message'] == NOTICE
    assert events == []
    assert not any(app.state.calls.values())


@isolated_scenario
def test_real_ethanol_properties_and_likeness_keep_evidence_boundaries(tmp_path):
    app = capture(create_lab(tmp_path))
    complete, events = exchange(app, PROPERTY_QUERY)
    assert app.state.calls == {'llm_molecular_generator': [], 'property_calculator': [PROPERTY_QUERY],
                               'drug_likeness_assessment': [PROPERTY_QUERY]}
    properties, likeness = terminal_events(events)
    body = complete['content']
    assert_real_properties(properties, body)
    assert likeness['event'] == 'tool_completed'
    assert likeness['tool'] == 'drug_likeness_assessment'
    assert likeness['payload']['success'] is True
    row, = likeness['payload']['data']
    assert row['smiles'] == 'CCO'
    assert row['assessment']['qed_score'] == 0.407
    assert row['assessment']['overall_assessment']['score'] == 0.693
    for evidence in ('总体评分: 0.693', 'QED评分: 0.407', '启发式规则',
                     '加权规则评分', '不能确定口服生物利用度或疗效'):
        assert evidence in body


@isolated_scenario
def test_likeness_failure_preserves_actual_rdkit_output_as_partial(tmp_path):
    def fail_likeness(self, query):
        assert query == PROPERTY_QUERY
        return ToolResult.error_result(
            self.name, AgentErrorCode.INTERNAL_ERROR, INJECTED_ERROR,
        )

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(DrugLikenessAssessment, 'execute', fail_likeness)
        app = capture(create_lab(tmp_path))
        complete, events = exchange(app, PROPERTY_QUERY)
    assert app.state.calls == {'llm_molecular_generator': [], 'property_calculator': [PROPERTY_QUERY],
                               'drug_likeness_assessment': [PROPERTY_QUERY]}
    properties, failure = terminal_events(events)
    body = complete['content']
    assert_real_properties(properties, body)
    assert failure['event'] == 'tool_failed'
    assert failure['tool'] == 'drug_likeness_assessment'
    assert failure['payload']['success'] is False
    assert failure['payload']['data'] is None
    assert failure['payload']['error']['code'] == AgentErrorCode.INTERNAL_ERROR.value
    assert complete['status'] == 'partial' and complete['partial'] is True
    failed, = complete['failed_steps']
    assert failed['step_id']
    assert failed['tool_name'] == 'drug_likeness_assessment'
    assert failed['status'] == 'failed'
    assert failed['error_code'] == AgentErrorCode.INTERNAL_ERROR.value
    assert failed['message'] == INJECTED_ERROR
    assert INJECTED_ERROR in body and '部分完成，并非全部步骤成功' in body
    assert '0.693' not in body and '类药性评估结果' not in body
    assert not re.search(r'(?:总体评分|综合评分|overall_score|score)\s*[:=：]\s*\d', body, re.I)
