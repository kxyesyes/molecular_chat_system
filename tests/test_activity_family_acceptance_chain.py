"""Offline engineering acceptance: untrained CPU RGNN, never scientific claims."""
import json
from pathlib import Path
import urllib.request

import pytest


def test_entry_timing_uses_actual_high_resolution_clock_even_on_failure(monkeypatch):
    from tests import family_acceptance_chain_support as chain
    ticks = iter([2.0, 2.125])
    monkeypatch.setattr(chain, 'perf_counter', lambda: next(ticks))
    timer, stages = chain.EntryTimings(), {}
    timer.start(stages, 'predictor')
    timer.close()
    assert stages['predictor']['latency_ms'] == 125.0
    assert stages['predictor']['checks']['entry_validated'] is False


@pytest.mark.parametrize('chain_status,source_change', [('passed', False), ('failed', False), ('partial', False), ('failed', True)])
def test_worker_preserves_nested_science_and_final_source_check(tmp_path, monkeypatch, capfd, chain_status, source_change):
    from tests import family_acceptance_chain_support as chain
    from tests import family_real_acceptance_support as support
    from types import SimpleNamespace
    calls = []
    snapshot = SimpleNamespace(source_digests={'registry': 'a' * 64})
    def snap(config, family, destination):
        calls.append(('snapshot', family))
        assert destination == tmp_path / 'models'
        print('synthetic private model stdout')
        __import__('os').write(1, b'synthetic native stdout')
        __import__('os').write(2, b'synthetic native stderr')
        return snapshot
    def run(selected, **kwargs):
        assert selected is snapshot
        calls.append(('chain', kwargs['mode']))
        print('synthetic private stderr', file=__import__('sys').stderr)
        return {'status': chain_status, 'stages': {'retained': {'status': 'failed'}}}
    def verify(config, selected):
        calls.append(('verify', selected is snapshot))
        if source_change:
            raise ValueError('source_changed')
        return True
    monkeypatch.setattr(support, 'snapshot_family', snap)
    monkeypatch.setattr(support, 'verify_source', verify)
    monkeypatch.setattr(chain, 'run_family_chain', run)
    code = chain.worker_main(['--source', str(tmp_path / 'synthetic-source'),
        '--family', 'pde-family', '--bundle', 'synthetic-pde', '--work-dir', str(tmp_path),
        '--mode', 'synthetic_fixture'])
    captured = capfd.readouterr()
    assert code == 0 and captured.err == ''
    envelope = json.loads(captured.out)
    assert envelope['status'] == 'passed'
    report = envelope['scientific_report']
    assert report['stages'] == {'retained': {'status': 'failed'}}
    assert report['status'] == ('failed' if source_change else chain_status)
    assert report['source_check'] == ('changed' if source_change else 'passed')
    assert calls == [('snapshot', 'pde-family'), ('chain', 'synthetic_fixture'), ('verify', True)]


def test_worker_exception_still_verifies_and_emits_no_raw_exception(tmp_path, monkeypatch, capfd):
    from tests import family_acceptance_chain_support as chain
    from tests import family_real_acceptance_support as support
    from types import SimpleNamespace
    checked = []
    monkeypatch.setattr(support, 'snapshot_family', lambda *a: SimpleNamespace(source_digests={}))
    def fail(*a, **k):
        raise RuntimeError('synthetic private exception')
    monkeypatch.setattr(chain, 'run_family_chain', fail)
    monkeypatch.setattr(support, 'verify_source', lambda *a: checked.append(True) or True)
    assert chain.worker_main(['--source', str(tmp_path / 'source'), '--family', 'pde-family',
        '--bundle', 'synthetic-pde', '--work-dir', str(tmp_path), '--mode', 'synthetic_fixture']) == 0
    result = json.loads(capfd.readouterr().out)['scientific_report']
    assert checked == [True]
    assert result['source_check'] == 'passed' and result['reason'] == 'chain_mismatch'


def test_worker_budget_includes_snapshot_time(tmp_path, monkeypatch, capfd):
    from types import SimpleNamespace
    from tests import family_acceptance_chain_support as chain
    from tests import family_real_acceptance_support as support
    now, remaining = [0.0], []
    monkeypatch.setattr(chain, 'time', SimpleNamespace(monotonic=lambda: now[0]))
    def snapshot(*a):
        now[0] = 117.0
        return SimpleNamespace(source_digests={})
    def run(*a, **kwargs):
        remaining.append(kwargs.get('deadline', 0) - now[0])
        return {'status': 'failed', 'reason': 'child_timeout'}
    monkeypatch.setattr(support, 'snapshot_family', snapshot)
    monkeypatch.setattr(support, 'verify_source', lambda *a: True)
    monkeypatch.setattr(chain, 'run_family_chain', run)
    chain.worker_main(['--source', str(tmp_path / 'source'), '--family', 'pde-family',
        '--bundle', 'synthetic-pde', '--work-dir', str(tmp_path), '--mode', 'synthetic_fixture'])
    assert remaining == [3.0]


def test_worker_module_cli_invalid_args_is_bounded_and_lazy(tmp_path):
    import os
    import sys
    from tests.family_acceptance_process_support import child_environment, run_owned_child
    result = run_owned_child([sys.executable, '-B', '-m', 'tests.family_acceptance_chain_support',
        '--unknown-synthetic-argument'], env=child_environment(os.environ, tmp_path),
        cwd=Path(__file__).absolute().parents[1], environment_dir=tmp_path, timeout=10)
    assert result.status == 'passed'
    assert result.report['scientific_report']['reason'] == 'invalid_configuration'
    assert result.report['scientific_report']['source_check'] == 'not_completed'


@pytest.mark.parametrize('synthetic_snapshot', ['PDE', 'BuChE'], indirect=True)
def test_actual_worker_cli_uses_only_explicit_synthetic_snapshot(synthetic_snapshot, tmp_path):
    import os
    import sys
    from tests.family_acceptance_process_support import child_environment, run_owned_child
    from tests.family_real_acceptance_support import public_report, _cleanup_owned, _checked_path
    owned = tmp_path / 'owned-worker'
    owned.mkdir()
    original_identity = _checked_path(owned, directory=True)[0][-1]
    child = run_owned_child([sys.executable, '-B', '-m', 'tests.family_acceptance_chain_support',
        '--source', str(synthetic_snapshot.models_dir), '--family', synthetic_snapshot.family_id,
        '--bundle', synthetic_snapshot.bundle_id, '--work-dir', str(owned), '--mode', 'synthetic_fixture'],
        env=child_environment(os.environ, owned), cwd=Path(__file__).absolute().parents[1],
        environment_dir=owned, timeout=120)
    assert child.status == 'passed', child.reason
    assert child.exit_code == 0 and child.ownership_released and child.cleanup_complete
    public = public_report(child.report['scientific_report'], mode='synthetic_fixture')
    assert public['status'] == 'passed', public
    assert public['source_check'] == 'passed' and len(public['cases']) == 21
    assert _cleanup_owned(owned, original_identity)


def test_public_report_never_accepts_forged_rejection_rows(synthetic_snapshot, tmp_path):
    from copy import deepcopy
    from tests.family_acceptance_chain_support import run_family_chain
    from tests.family_real_acceptance_support import public_report
    report = run_family_chain(synthetic_snapshot, work_dir=tmp_path / 'chain', mode='synthetic_fixture')
    report.update(source_check='passed', source_digests=synthetic_snapshot.source_digests)
    assert public_report(report)['status'] == 'passed'
    report['rejections']['unknown_target']['stages']['predictor']['rows'] = deepcopy(report['stages']['predictor']['rows'][:1])
    assert public_report(report)['status'] == 'failed'


def test_public_report_rejects_missing_or_mutated_obligations(synthetic_snapshot, tmp_path):
    from copy import deepcopy
    from tests.family_acceptance_chain_support import run_family_chain
    from tests.family_real_acceptance_support import public_report
    original = run_family_chain(synthetic_snapshot, work_dir=tmp_path / 'chain', mode='synthetic_fixture')
    original.update(source_check='passed', source_digests=synthetic_snapshot.source_digests)
    assert public_report(original)['status'] == 'passed'
    original['expected_identity']['models']['classification']['private_training_note'] = 'synthetic-not-for-publication'
    projected = public_report(original)
    assert projected['status'] == 'passed'
    assert 'synthetic-not-for-publication' not in json.dumps(projected)
    mutations = {
        'checks': lambda r: r['stages']['api_batch'].update(checks={}),
        'missing_stage': lambda r: r['stages'].pop('dom'),
        'missing_rejection': lambda r: r['rejections'].pop('unknown_target'),
        'mixed_count': lambda r: r['mixed']['rows'].pop(),
        'nan': lambda r: r['stages']['predictor'].update(latency_ms=float('nan')),
        'bool_time': lambda r: r['stages']['predictor'].update(latency_ms=True),
        'missing_digests': lambda r: r.pop('source_digests'),
        'wrong_digest': lambda r: r['source_digests'].update(registry='not-a-hash'),
        'digest_identity_mismatch': lambda r: r['source_digests'].update({'classification.weights': '0' * 64}),
        'baseline_mismatch': lambda r: r['stages']['api_batch']['rows'][0].update(predicted_pIC50=123.0),
        'bad_order': lambda r: r['stages']['api_batch']['rows'].append(r['stages']['api_batch']['rows'].pop(0)),
        'empty_decision_rows': lambda r: r['stages']['decision'].update(rows=[]),
        'wrong_tool': lambda r: r['stages']['decision']['tool_trace'][0].update(tool_name='other_tool'),
        'terminal_payload': lambda r: r['stages']['websocket']['event_trace'][-1]['payload'].update(status='failed'),
        'missing_event': lambda r: (r['stages']['decision']['events'].pop(1), r['stages']['decision']['event_trace'].pop(1)),
        'false_rejection': lambda r: r['rejections']['unknown_target']['stages']['predictor'].update(scientific_status='passed'),
        'long_warning': lambda r: r['stages']['tool'].update(warnings=['x' * 513]),
        'large_array': lambda r: r['stages']['decision'].update(events=['task_started'] * 129),
        'private_path': lambda r: r['stages']['tool'].update(warnings=['C:/synthetic-private/model']),
        'secret_shape': lambda r: r['stages']['tool'].update(warnings=['sk-' + 'synthetic' * 4]),
    }
    accepted = []
    for name, mutate in mutations.items():
        report = deepcopy(original)
        mutate(report)
        public = public_report(report)
        if public['status'] != 'failed':
            accepted.append(name)
        if name == 'baseline_mismatch':
            assert next(case for case in public['cases'] if case['case_id'] == 'valid.api_batch')['status'] == 'failed'
        encoded = json.dumps(public, allow_nan=False)
        assert 'synthetic-private' not in encoded and 'synthetic' * 4 not in encoded
    assert accepted == [], accepted


def test_parent_aggregates_nested_report_and_real_directory_cleanup(synthetic_snapshot, tmp_path, monkeypatch):
    from copy import deepcopy
    from tests.family_acceptance_chain_support import run_family_chain
    from tests.family_acceptance_process_support import ChildResult
    from tests import family_real_acceptance_support as support
    report = run_family_chain(synthetic_snapshot, work_dir=tmp_path / 'chain', mode='synthetic_fixture')
    report.update(source_check='passed', source_digests=synthetic_snapshot.source_digests)
    config = {'MEDCHAT_RUN_FAMILY_REAL_ACCEPTANCE': '1',
        'MEDCHAT_FAMILY_ACCEPTANCE_MODELS_DIR': str(tmp_path / 'never-read'),
        'MEDCHAT_FAMILY_ACCEPTANCE_PDE_BUNDLE_ID': 'synthetic-selected',
        'MEDCHAT_FAMILY_ACCEPTANCE_BUCHE_BUNDLE_ID': 'synthetic-buche'}
    def forbidden(*a, **k):
        pytest.fail('parent must not read source')
    monkeypatch.setattr(support, 'snapshot_family', forbidden)
    monkeypatch.setattr(support, 'verify_source', forbidden)
    for status, expected in [('passed', 'passed'), ('failed', 'partial'), ('partial', 'partial')]:
        directories = []
        def runner(argv, **kwargs):
            directory = kwargs['environment_dir']
            directories.append(directory)
            (directory / 'synthetic-state').write_text('temporary only')
            value = deepcopy(report)
            if argv[argv.index('--family') + 1] == 'buche-family':
                # Synthetic protocol fixture, NOT a second scientific inference.
                value = json.loads(json.dumps(value).replace('pde-family', 'buche-family').replace('synthetic-selected', 'synthetic-buche'))
                value['status'] = status
            return ChildResult('passed', None, 0, {'status': 'passed', 'scientific_report': value}, True, True)
        public = support.run_acceptance(config, repo_dir=Path(__file__).absolute().parents[1],
            temporary_root=tmp_path, mode='synthetic_fixture', runner=runner)
        assert public['status'] == expected, public
        assert len(public['families']) == 2
        assert all(not directory.exists() for directory in directories)
        assert all(f['cleanup_complete'] and f['process_cleanup_complete'] for f in public['families'])
        assert public['scope']['production_selection'] == 'unchanged'
        support.write_report(tmp_path / f'{status}.json', public)
    from dataclasses import replace
    status = 'passed'
    for fault in ('exit', 'ownership', 'process_cleanup', 'filesystem_cleanup'):
        def controlled(argv, **kwargs):
            child = runner(argv, **kwargs)
            return replace(child, **{'exit': {'exit_code': 7}, 'ownership': {'ownership_released': False},
                'process_cleanup': {'cleanup_complete': False}, 'filesystem_cleanup': {}}[fault])
        with monkeypatch.context() as patcher:
            if fault == 'filesystem_cleanup':
                patcher.setattr(support, '_cleanup_owned', lambda *a: False)
            public = support.run_acceptance(config, repo_dir=Path(__file__).absolute().parents[1],
                temporary_root=tmp_path, mode='synthetic_fixture', runner=controlled)
        assert public['status'] == 'failed'
        assert all(f['status'] == 'failed' for f in public['families'])
        assert all(f['cleanup_complete'] is (fault == 'exit') for f in public['families'])


@pytest.fixture
def synthetic_snapshot(tmp_path, monkeypatch, request):
    import httpx
    import pandas as pd
    import requests
    import torch
    from src.activity import predictor, prediction_service
    from src.agent.planning.task_planner import TaskPlanner
    from tests.family_model_test_support import make_forward_bundle
    from tests.family_real_acceptance_support import read_config, snapshot_family, verify_source

    touched = []

    def forbidden(*args, **kwargs):
        touched.append(True)
        raise AssertionError("offline boundary reached")

    monkeypatch.setenv("MEDCHAT_RUN_FAMILY_REAL_ACCEPTANCE", "0")
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    monkeypatch.setattr(predictor, "get_predictor", forbidden)
    monkeypatch.setattr(predictor.ActivityPredictor, "load", forbidden)
    monkeypatch.setattr(predictor.ActivityPredictor, "_find_checkpoint", forbidden)
    monkeypatch.setattr(TaskPlanner, "plan", forbidden)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)
    monkeypatch.setattr(requests.Session, "send", forbidden)
    monkeypatch.setattr(urllib.request.OpenerDirector, "open", forbidden)
    family = getattr(request, "param", "PDE")
    threads = torch.get_num_threads()
    prediction_service._family_predictor.cache_clear()
    try:
        torch.set_num_threads(1)
        registry, _ = make_forward_bundle(tmp_path, monkeypatch, family=family,
                                          bundle_id="synthetic-selected")
        config = read_config({"MEDCHAT_RUN_FAMILY_REAL_ACCEPTANCE": "1",
            "MEDCHAT_FAMILY_ACCEPTANCE_MODELS_DIR": str(registry.models_dir),
            "MEDCHAT_FAMILY_ACCEPTANCE_PDE_BUNDLE_ID": "synthetic-selected" if family == "PDE" else "unused-pde",
            "MEDCHAT_FAMILY_ACCEPTANCE_BUCHE_BUNDLE_ID": "synthetic-selected" if family == "BuChE" else "unused-buche"})
        # Source assets above are generated by this test; no original CSV may reopen.
        monkeypatch.setattr(pd, "read_csv", forbidden)
        monkeypatch.setattr("src.activity.family_dataset.load_family_dataset", forbidden)
        original_open = Path.open

        def no_dataset(path, *args, **kwargs):
            if path.suffix.lower() in {".csv", ".tsv"}:
                forbidden()
            return original_open(path, *args, **kwargs)

        monkeypatch.setattr(Path, "open", no_dataset)
        from src.activity.family_contract import resolve_activity_family
        snapshot = snapshot_family(config, resolve_activity_family(family), tmp_path / "snapshot")
        monkeypatch.setenv("ACTIVITY_MODEL_DIR", str(snapshot.models_dir))
        yield snapshot
        assert verify_source(config, snapshot) is True
    finally:
        prediction_service._family_predictor.cache_clear()
        torch.set_num_threads(threads)
        assert not touched, "Offline guard reached, including swallowed exceptions"


@pytest.mark.parametrize("synthetic_snapshot", ["PDE", "BuChE"], indirect=True)
def test_same_snapshot_reaches_all_real_entrypoints(synthetic_snapshot, tmp_path):
    from tests.family_acceptance_chain_support import run_family_chain
    report = run_family_chain(synthetic_snapshot, work_dir=tmp_path / "chain", mode="synthetic_fixture")
    assert report["status"] == "passed", report
    assert report["mode"] == "synthetic_fixture"
    assert report["decision_model"] == "scripted"
    assert report["expected_identity"] == report["actual_identity"]
    entries = list(report['stages'].values())
    entries += [stage for case in report['rejections'].values() for stage in case['stages'].values()]
    entries += [report['mixed'], report['mixed']['dom']]
    assert len(entries) == 21
    for entry in entries:
        assert type(entry.get('latency_ms')) in (int, float)
        assert entry['latency_ms'] > 0
        assert entry['checks']['entry_validated'] is True
    from tests.family_real_acceptance_support import public_report
    public = public_report({**report, 'source_check': 'passed',
        'source_digests': synthetic_snapshot.source_digests}, mode='synthetic_fixture')
    assert public['status'] == 'passed', public
    assert len(public['cases']) == 21
    assert all(case['artifacts'] == [] for case in public['cases'])
    rejected = {case['case_id']: case for case in public['cases'] if not case['case_id'].startswith('valid.')}
    assert rejected['invalid_smiles.tool']['status'] == 'passed'
    assert rejected['invalid_smiles.tool']['result_status'] == 'failed'
    assert rejected['invalid_smiles.tool']['scientific_error_code'] == 'invalid_input'
    assert rejected['unknown_target.decision']['actual_tools'] == []
    assert rejected['unknown_target.decision']['result_status'] in ('failed', 'rejected')
    assert public['cases'][0]['expected_identity'] == public['cases'][0]['actual_identity']
    assert set(report["stages"]) == {"predictor", "api_single", "api_batch", "tool", "decision", "websocket", "dom"}
    assert all(stage["status"] == "passed" for stage in report["stages"].values())
    assert [row["smiles"] for row in report["stages"]["api_batch"]["rows"]] == ["CCO", "CCN", "CCO"]
    assert report["stages"]["dom"]["rows"] == 3
    assert report["stages"]["tool"]["observation_status"] == "succeeded"
    assert report["stages"]["tool"]["checks"]["formatted_numbers"] is True
    for name in ("decision", "websocket"):
        stage = report["stages"][name]
        assert stage["agent_status"] == "completed"
        assert stage["model_calls"] == 2
        assert stage["tool_trace"][0]["input_digest"]
        assert stage["checks"]["observed_evidence"] is True
        assert stage["checks"]["input_digest"] is True
        assert [event["event"] for event in stage["event_trace"]] == stage["events"]
        assert all(event["trace_id"] == stage["trace_id"] for event in stage["event_trace"])
        assert all(type(event["timestamp"]) in (int, float) for event in stage["event_trace"])
        assert stage["event_trace"][-1]["payload"] == {"success": True, "status": "completed", "partial": False}
    assert report["stages"]["websocket"]["checks"]["terminal"] is True
    decision = report["stages"]["decision"]
    websocket = report["stages"]["websocket"]
    assert decision["trace_id"] != websocket["trace_id"]
    assert decision["tool_trace"][0]["evidence_id"] != websocket["tool_trace"][0]["evidence_id"]
    encoded = json.dumps(report, allow_nan=False)
    assert len(encoded.encode()) < 1024 * 1024
    assert str(tmp_path) not in encoded
    assert "model_config" not in encoded and "weights_file" not in encoded
    from src.activity import prediction_service
    assert prediction_service._family_predictor.cache_info().currsize == 0


@pytest.mark.parametrize("synthetic_snapshot", ["PDE", "BuChE"], indirect=True)
@pytest.mark.parametrize("bad_input", ["smiles", "target"])
def test_invalid_inputs_are_not_model_unavailability(synthetic_snapshot, tmp_path, monkeypatch, bad_input):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.activity import prediction_service
    from src.agent.tools.activity_predictor_tool import ActivityPredictorTool
    from src.web.routes.api_routes import setup_api_routes
    from tests.family_acceptance_chain_support import (
        ALIASES, decision_session, invoke_decision, websocket_decision, check_rejected_decision,
    )
    target = "AChE" if bad_input == "target" else ALIASES[synthetic_snapshot.family_id][1]
    smiles = "CC(C)((" if bad_input == "smiles" else "CCO"
    app = FastAPI()
    setup_api_routes(app)
    with TestClient(app) as client:
        responses = [client.post("/api/activity/predict", data={"smiles": smiles, "target": target}),
                     client.post("/api/activity/batch_predict", data={"target": target},
                                 files={"file": ("invalid.smi", smiles.encode(), "text/plain")})]
    for response in responses:
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "failed" and not body["success"]
        row = body["results"][0]
        assert row["predicted_pIC50"] is row["activity_probability"] is None
        assert row["errors"].get("input") and "bundle" not in row["errors"]

    def no_service(*args, **kwargs):
        pytest.fail("Invalid parsing must not call the scientific service")

    monkeypatch.setattr(prediction_service, "predict_activity", no_service)
    tool = ActivityPredictorTool().execute({"smiles": smiles, "target": target})
    assert tool.status.value == "invalid_input"
    assert tool.error.code.value == "invalid_input" and tool.data is None
    for transport in (False, True):
        with decision_session(tmp_path, target=target, query=f"SMILES: {smiles}") as session:
            if transport:
                result, frames = websocket_decision(session)
            else:
                result = invoke_decision(session)
            check_rejected_decision(session, result, frames if transport else None)


@pytest.mark.parametrize("damage", ["missing_weights", "bad_card", "bad_hash", "missing_stage"])
def test_damaged_copy_fails_without_fallback_and_keeps_stage_errors(synthetic_snapshot, tmp_path, damage):
    from src.activity.model_registry import REGISTRY_STATE_FILE
    from tests.family_acceptance_chain_support import run_family_chain
    snapshot = synthetic_snapshot
    model = snapshot.expected_models["regression"]
    if damage == "missing_weights":
        (snapshot.models_dir / model["weights_file"]).unlink()
    elif damage == "bad_card":
        (snapshot.models_dir / model["model_card_file"]).write_text("{}", encoding="utf-8")
    elif damage == "bad_hash":
        with (snapshot.models_dir / model["weights_file"]).open("ab") as handle:
            handle.write(b"synthetic-corruption")
    else:
        path = snapshot.models_dir / REGISTRY_STATE_FILE
        state = json.loads(path.read_bytes())
        del state["family_bundles"][snapshot.bundle_id]["models"]["regression"]
        path.write_text(json.dumps(state), encoding="utf-8")
    report = run_family_chain(snapshot, work_dir=tmp_path / "chain", mode="synthetic_fixture")
    assert report["status"] == "failed"
    stage = report["stages"]["predictor"]
    assert stage["status"] == "failed"
    if damage == "missing_stage":
        # Registry rejects the incomplete mapping before a predictor can emit rows.
        assert report["reason"] == stage["error"] == "invalid_registry"
        assert stage["rows"] == []
        return
    assert report["reason"] == "chain_mismatch"
    assert report["actual_identity"]["family_id"] == synthetic_snapshot.family_id
    assert report["actual_identity"]["bundle_id"] is None
    for row in stage["rows"]:
        assert row["status"] == "failed"
        assert row["errors"] == {"bundle": "family_model_bundle_unavailable_or_invalid"}
        assert row["predicted_pIC50"] is row["activity_probability"] is None


@pytest.mark.parametrize("damage", ["family", "smiles", "empty", "demo", "fallback", "wrong_hash",
                                     "bool_pic50", "numeric_drift", "threshold", "warnings", "errors", "class"])
def test_service_corruption_cannot_count_as_forward_success(synthetic_snapshot, tmp_path, monkeypatch, damage):
    from src.activity import prediction_service
    from tests.family_acceptance_chain_support import run_family_chain
    original = prediction_service.predict_activity

    def corrupted(*args, **kwargs):
        summary = original(*args, **kwargs)
        row = summary["results"][0]
        if damage == "family":
            row["family_id"] = "buche-family"
        elif damage == "smiles":
            row["smiles"] = "CCC"
        elif damage == "empty":
            summary["results"] = []
        elif damage in {"bool_pic50", "numeric_drift", "threshold", "warnings", "errors", "class"}:
            row.update({"bool_pic50": {"predicted_pIC50": True},
                        "numeric_drift": {"predicted_pIC50": row["predicted_pIC50"] + .01},
                        "threshold": {"label_threshold": 6.0},
                        "warnings": {"warnings": ["injected-warning"]},
                        "errors": {"errors": {"regression": "injected-error"}},
                        "class": {"activity_class": "unsupported"}}[damage])
        else:
            model = row["provenance"]["models"]["regression"]
            model.update({"demo": {"demo_mode": True}, "fallback": {"fallback_used": True},
                          "wrong_hash": {"weights_sha256": "0" * 64}}[damage])
        return summary

    monkeypatch.setattr(prediction_service, "predict_activity", corrupted)
    report = run_family_chain(synthetic_snapshot, work_dir=tmp_path / "chain", mode="synthetic_fixture")
    assert report["status"] == "failed" and report["reason"] == "chain_mismatch"
    assert report["stages"]["predictor"]["status"] == "passed"
    assert report["stages"]["api_single"]["status"] == "failed"
    assert "decision" not in report["stages"]


def test_partial_forward_keeps_classification_and_both_pins(synthetic_snapshot, tmp_path, monkeypatch):
    from src.activity.family_predictor import _PinnedPredictor
    from tests.family_acceptance_chain_support import run_family_chain
    original = _PinnedPredictor.predict

    def fail_regression(self, smiles):
        if self._metadata["task_type"] == "regression":
            return [{"smiles": smi, "success": False} for smi in smiles]
        return original(self, smiles)

    monkeypatch.setattr(_PinnedPredictor, "predict", fail_regression)
    report = run_family_chain(synthetic_snapshot, work_dir=tmp_path / "chain", mode="synthetic_fixture")
    assert report["status"] == "failed"
    for row in report["stages"]["predictor"]["rows"]:
        assert row["status"] == "partial" and row["predicted_pIC50"] is None
        assert 0 <= row["activity_probability"] <= 1
        assert row["errors"] == {"regression": "regression_failed_or_invalid_output"}
        assert set(row["models"]) == {"classification", "regression"}


@pytest.mark.parametrize("synthetic_snapshot", ["PDE", "BuChE"], indirect=True)
def test_clarification_uses_current_molecule_not_previous_evidence(synthetic_snapshot, tmp_path):
    from src.agent.contracts.decision import ClarifyDecision
    from src.activity.prediction_service import predict_activity
    from tests.family_acceptance_chain_support import (
        ALIASES, activity_decision, decision_session, finish_observed, invoke_decision, check_rows,
    )
    target = ALIASES[synthetic_snapshot.family_id][1]
    clarify = ClarifyDecision(version="1", action="clarify", question="Provide corrected input", missing_fields=["smiles"])
    with decision_session(tmp_path, target=target, query="SMILES: CCO",
                          decisions=[activity_decision(), clarify, activity_decision(), finish_observed]) as session:
        waiting = invoke_decision(session)
        assert waiting.metadata["waiting_for_input"]
        old_id = waiting.tool_results[0].quality["evidence_id"]
        result = invoke_decision(session, continuation_id=waiting.metadata["continuation_id"], clarified_query="SMILES: CCN")
        assert result.success, result.metadata
        assert session.inputs == [{"query": "SMILES: CCO", "target": target}, {"query": "SMILES: CCN", "target": target}]
        answer = json.loads(result.final_answer.removeprefix("```json\n").removesuffix("\n```"))
        assert answer["evidence_id"] != old_id
        assert old_id not in result.final_answer
        check_rows(answer["data"], predict_activity(["CCN"], target=target)["results"], synthetic_snapshot, target=target)


def test_clarification_cannot_silently_change_target(synthetic_snapshot, tmp_path):
    from src.agent.contracts.decision import ClarifyDecision
    from tests.family_acceptance_chain_support import activity_decision, decision_session, invoke_decision
    clarify = ClarifyDecision(version="1", action="clarify", question="Provide input", missing_fields=["smiles"])
    with decision_session(tmp_path, target="PDE5A", query="SMILES: CCO",
                          decisions=[activity_decision(), clarify, activity_decision()]) as session:
        waiting = invoke_decision(session)
        result = invoke_decision(session, continuation_id=waiting.metadata["continuation_id"],
                                 clarified_query="靶点：BuChE；SMILES: CCN")
        assert not result.success
        assert len(session.inputs) == 1
        assert result.metadata["stop_reason"] == "activity_target_conflict_or_unknown"
        assert '"predicted_pIC50":' not in result.final_answer


def test_unauthorized_activity_decision_never_executes(synthetic_snapshot, tmp_path):
    from tests.family_acceptance_chain_support import decision_session, invoke_decision
    with decision_session(tmp_path, target="PDE5A", query="SMILES: CCO") as session:
        result = invoke_decision(session, allowed_tools=set())
        assert not result.success
        assert result.metadata["stop_reason"] == "tool_not_authorized"
        assert session.inputs == session.outputs == []


def test_node_receives_actual_summary_and_bounded_remaining_time(synthetic_snapshot, tmp_path, monkeypatch):
    from types import SimpleNamespace
    from tests import family_acceptance_chain_support as chain
    from tests.family_acceptance_process_support import ChildResult
    calls = []
    now = [0.0]
    original_check = chain.check_decision

    def clock_after_websocket(*args, **kwargs):
        checked = original_check(*args, **kwargs)
        if args[-1] is not None:
            now[0] = 105.0
        return checked

    monkeypatch.setattr(chain, "time", SimpleNamespace(monotonic=lambda: now[0]))
    monkeypatch.setattr(chain, "check_decision", clock_after_websocket)

    def inspect_child(argv, *, env, cwd, timeout):
        calls.append(argv)
        assert Path(argv[0]).is_absolute() and Path(argv[0]).name.lower() in {"node", "node.exe"}
        assert argv[-2] == "--input"
        summary = json.loads(Path(argv[-1]).read_bytes())
        assert [row["smiles"] for row in summary["results"]] == chain.SMILES
        assert summary["status"] == "passed"
        assert "ACTIVITY_MODEL_DIR" not in env
        assert timeout == 15.0  # min(30, remaining family budget), not a fresh 30s.
        return ChildResult("failed", "child_timeout", None, None, True, True)

    monkeypatch.setattr(chain, "run_owned_child", inspect_child)
    report = chain.run_family_chain(synthetic_snapshot, work_dir=tmp_path / "chain", mode="synthetic_fixture")
    assert calls
    assert report["status"] == "failed" and report["reason"] == "child_timeout"
    assert report["stages"]["websocket"]["status"] == "passed"
    assert report["stages"]["dom"]["status"] == "failed"


def test_rejected_finish_preserves_actual_tool_and_agent_failure(synthetic_snapshot, tmp_path, monkeypatch):
    from src.agent.contracts.decision import FinishDecision
    from tests import family_acceptance_chain_support as chain

    def stale_evidence(messages):
        return FinishDecision(version="1", action="finish", response_kind="scientific",
                              text="Not evidence", evidence_ids=["old-unrelated-evidence"])

    monkeypatch.setattr(chain, "finish_observed", stale_evidence)
    report = chain.run_family_chain(synthetic_snapshot, work_dir=tmp_path / "chain", mode="synthetic_fixture")
    assert report["status"] == "failed" and report["reason"] == "chain_mismatch"
    stage = report["stages"]["decision"]
    assert stage["status"] == "failed"
    # A successful tool followed by a rejected finish is an Agent partial,
    # while the acceptance verdict must still be failed.
    assert stage["agent_status"] == "partial"
    assert stage["stop_reason"] == "evidence_not_usable_in_this_trace"
    assert stage["tool_trace"][0]["input_digest"]
    assert stage["rows"][0]["status"] == "passed"
    assert stage["rows"][0]["models"]["regression"]["weights_sha256"] == synthetic_snapshot.expected_models["regression"]["weights_sha256"]


def test_websocket_lost_scientific_evidence_cannot_pass(synthetic_snapshot, tmp_path, monkeypatch):
    from tests import family_acceptance_chain_support as chain
    original = chain.websocket_decision

    def truncated(session):
        result, frames = original(session)
        public = next(frame for frame in frames if frame["type"] == "agent_result")
        public["tool_result_sequence"][0]["data"][0]["predicted_pIC50"] = None
        public["display_redacted_or_truncated"] = True
        return result, frames

    monkeypatch.setattr(chain, "websocket_decision", truncated)
    report = chain.run_family_chain(synthetic_snapshot, work_dir=tmp_path / "chain", mode="synthetic_fixture")
    assert report["status"] == "failed"
    assert report["stages"]["decision"]["status"] == "passed"
    assert report["stages"]["websocket"]["status"] == "failed"
    assert report["stages"]["websocket"]["agent_status"] == "completed"


def test_bounded_report_cannot_pass_when_evidence_is_oversized(synthetic_snapshot, tmp_path, monkeypatch):
    from tests import family_acceptance_chain_support as chain
    original = chain.decision_record

    def oversized(*args, **kwargs):
        record = original(*args, **kwargs)
        record["tool_trace"][0]["warnings"] = ["x" * 513]
        return record

    monkeypatch.setattr(chain, "decision_record", oversized)
    report = chain.run_family_chain(synthetic_snapshot, work_dir=tmp_path / "chain", mode="synthetic_fixture")
    assert report["status"] == "failed" and report["reason"] == "invalid_report"
    assert "x" * 513 not in json.dumps(report)


def test_settings_and_caches_restore_even_when_node_is_missing(synthetic_snapshot, tmp_path, monkeypatch):
    import os
    import torch
    from src.activity import prediction_service
    from tests import family_acceptance_chain_support as chain
    original_dir = os.environ["ACTIVITY_MODEL_DIR"]
    original_cuda = torch.cuda.is_available
    threads = torch.get_num_threads()
    monkeypatch.setattr(chain.shutil, "which", lambda _: None)
    report = chain.run_family_chain(synthetic_snapshot, work_dir=tmp_path / "chain", mode="synthetic_fixture")
    assert report["status"] == "failed" and report["reason"] == "dependency_unavailable"
    assert os.environ["ACTIVITY_MODEL_DIR"] == original_dir
    assert torch.cuda.is_available is original_cuda and torch.get_num_threads() == threads
    assert prediction_service._family_predictor.cache_info().currsize == 0


@pytest.mark.parametrize("synthetic_snapshot", ["PDE", "BuChE"], indirect=True)
def test_runner_reports_real_rejections_and_mixed_api_dom(synthetic_snapshot, tmp_path, monkeypatch):
    from tests import family_acceptance_chain_support as chain
    summaries = []
    original = chain.run_owned_child

    def capture_summary(argv, **kwargs):
        summaries.append(json.loads(Path(argv[-1]).read_bytes()))
        return original(argv, **kwargs)

    monkeypatch.setattr(chain, "run_owned_child", capture_summary)
    report = chain.run_family_chain(synthetic_snapshot, work_dir=tmp_path / "chain", mode="synthetic_fixture")
    assert report["status"] == "passed", report
    assert set(report["rejections"]) == {"invalid_smiles", "unknown_target"}
    for name, case in report["rejections"].items():
        assert case["status"] == "passed" and case["scientific_status"] == "failed"
        assert set(case["stages"]) == {"predictor", "api_single", "api_batch", "tool", "decision", "websocket"}
        assert all(stage["status"] == "passed" for stage in case["stages"].values())
        for entry in ("predictor", "api_single", "api_batch"):
            stage = case["stages"][entry]
            assert stage["scientific_status"] == "failed"
            row = stage["rows"][0]
            assert row["status"] == "failed" and not row["success"]
            assert row["predicted_pIC50"] is row["activity_probability"] is None
            assert row["errors"] == {"input": "invalid_smiles" if name == "invalid_smiles" else "unknown_or_ambiguous_family"}
        assert case["stages"]["tool"]["error"] == "invalid_input"
        for entry in ("decision", "websocket"):
            assert case["stages"][entry]["agent_status"] in {"failed", "rejected"}
            assert case["stages"][entry]["checks"]["rejected_without_service"] is True
            stage = case["stages"][entry]
            assert stage["event_trace"][-1]["event"] == "task_" + stage["agent_status"]
            assert stage["event_trace"][-1]["payload"]["status"] == stage["agent_status"]
            assert stage["events"].count("tool_started") == (0 if name == "unknown_target" else 1)
        assert case["stages"]["websocket"]["checks"]["public_matches_actual"] is True
    mixed = report["mixed"]
    assert mixed["status"] == "passed" and mixed["scientific_status"] == "partial"
    assert [row["smiles"] for row in mixed["rows"]] == ["CCO", "CC(C)((", "CCN", "CCO"]
    assert [row["status"] for row in mixed["rows"]] == ["passed", "failed", "passed", "passed"]
    assert mixed["rows"][0] == mixed["rows"][3]
    assert mixed["dom"]["status"] == "passed" and mixed["dom"]["rows"] == 4
    assert len(summaries) == 2
    assert summaries[0]["status"] == "passed" and summaries[1]["status"] == "partial"
    assert mixed["rows"] == [chain.project_row(row) for row in summaries[1]["results"]]


@pytest.mark.parametrize("tail", ["complete", "molecular_generation", "agent_event", "overflow"])
def test_tail_frames_after_bridge_cannot_pass(synthetic_snapshot, tmp_path, monkeypatch, tail):
    from src.web.chat_handler import ChatHandler
    from tests import family_acceptance_chain_support as chain
    original = ChatHandler.process_decision_message

    async def with_tail(self, socket, **kwargs):
        result = await original(self, socket, **kwargs)
        frame = {"type": "agent_event" if tail == "overflow" else tail,
                 "trace_id": result.trace_id, "event": {"event": "tool_progress"}}
        for _ in range(130 if tail == "overflow" else 1):
            await socket.send_text(json.dumps(frame))
        return result

    monkeypatch.setattr(ChatHandler, "process_decision_message", with_tail)
    report = chain.run_family_chain(synthetic_snapshot, work_dir=tmp_path / "chain", mode="synthetic_fixture")
    assert report["status"] == "failed", "Tail frames were silently discarded"
    assert report["reason"] == "chain_mismatch"
    assert report["stages"]["websocket"]["status"] == "failed"


@pytest.mark.parametrize("bad_input", ["smiles", "target"])
@pytest.mark.parametrize("forgery", ["completed_numeric", "error", "tool_sequence"])
def test_invalid_assertions_reject_forged_public_frames(synthetic_snapshot, tmp_path, monkeypatch, bad_input, forgery):
    from tests import family_acceptance_chain_support as chain
    original = chain.websocket_decision

    def forged(session):
        result, frames = original(session)
        public = next(frame for frame in frames if frame["type"] == "agent_result")
        if forgery == "completed_numeric":
            public.update(success=True, status="completed", final_answer='{"predicted_pIC50": 9.9}')
            frames[-1].update(status="completed", content=public["final_answer"])
        elif forgery == "error":
            public["error"] = None
        else:
            public["tool_result_sequence"] = [{"data": [{"predicted_pIC50": 9.9}]}]
        return result, frames

    monkeypatch.setattr(chain, "websocket_decision", forged)
    # The same checker serves standalone negatives and the later opt-in runner.
    target = "AChE" if bad_input == "target" else chain.ALIASES[synthetic_snapshot.family_id][1]
    smiles = chain.INVALID_SMILES if bad_input == "smiles" else "CCO"
    with chain.decision_session(tmp_path, target=target, query=f"SMILES: {smiles}") as session:
        result, frames = chain.websocket_decision(session)
        with pytest.raises(chain.ChainFailure, match="^chain_mismatch$"):
            chain.check_rejected_decision(session, result, frames)


def test_websocket_wait_for_close_has_a_cooperative_deadline(synthetic_snapshot, tmp_path, monkeypatch):
    import asyncio
    from src.web.chat_handler import ChatHandler
    from tests import family_acceptance_chain_support as chain
    cancelled = []

    async def stalled(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)

    monkeypatch.setattr(ChatHandler, "process_decision_message", stalled)
    with chain.decision_session(tmp_path, target="PDE5A", query="SMILES: CCO", timeout=.05) as session:
        with pytest.raises(chain.ChainFailure, match="^child_timeout$"):
            chain.websocket_decision(session)
    assert cancelled == [True]


def test_websocket_requires_normal_server_close(synthetic_snapshot, tmp_path, monkeypatch):
    from fastapi import WebSocket
    from tests import family_acceptance_chain_support as chain
    original = WebSocket.close

    async def abnormal(self, code=1000, reason=None):
        await original(self, code=1008, reason=reason)

    monkeypatch.setattr(WebSocket, "close", abnormal)
    report = chain.run_family_chain(synthetic_snapshot, work_dir=tmp_path / "chain", mode="synthetic_fixture")
    assert report["status"] == "failed" and report["reason"] == "chain_mismatch"


def test_websocket_rejects_close_received_after_deadline(synthetic_snapshot, tmp_path, monkeypatch):
    from types import SimpleNamespace
    from starlette.testclient import WebSocketTestSession
    from tests import family_acceptance_chain_support as chain
    original = WebSocketTestSession.receive
    now = [0.0]

    def delayed_close(self):
        message = original(self)
        if message["type"] == "websocket.close":
            now[0] = 121.0
        return message

    monkeypatch.setattr(chain, "time", SimpleNamespace(monotonic=lambda: now[0]))
    monkeypatch.setattr(WebSocketTestSession, "receive", delayed_close)
    with chain.decision_session(tmp_path, target="PDE5A", query="SMILES: CCO") as session:
        with pytest.raises(chain.ChainFailure, match="^child_timeout$"):
            chain.websocket_decision(session)


@pytest.mark.parametrize("bad_input", ["smiles", "target"])
def test_runner_rejects_forged_rejection_over_actual_asgi(synthetic_snapshot, tmp_path, monkeypatch, bad_input):
    from src.web.chat_handler import ChatHandler
    from tests import family_acceptance_chain_support as chain
    original = ChatHandler.process_decision_message

    async def forge_rejection(self, socket, **kwargs):
        context = kwargs["context"]
        matches = (context.metadata["target"] == "AChE" if bad_input == "target"
                   else chain.INVALID_SMILES in context.query)
        if not matches:
            return await original(self, socket, **kwargs)

        class ForgingSocket:
            async def send_text(self, text):
                frame = json.loads(text)
                if frame["type"] == "agent_result":
                    frame.update(success=True, status="completed", final_answer='{"predicted_pIC50": 9.9}')
                elif frame["type"] == "complete":
                    frame.update(status="completed", content='{"predicted_pIC50": 9.9}')
                await socket.send_text(json.dumps(frame))

        return await original(self, ForgingSocket(), **kwargs)

    monkeypatch.setattr(ChatHandler, "process_decision_message", forge_rejection)
    report = chain.run_family_chain(synthetic_snapshot, work_dir=tmp_path / "chain", mode="synthetic_fixture")
    assert report["status"] == "failed" and report["reason"] == "chain_mismatch"
    assert report["stages"]["websocket"]["status"] == "passed"
    case = "unknown_target" if bad_input == "target" else "invalid_smiles"
    stage = report["rejections"][case]["stages"]["websocket"]
    assert stage["status"] == "failed" and stage["agent_status"] in {"failed", "rejected"}
    assert "9.9" not in json.dumps(stage)


@pytest.mark.parametrize("corruption", ["wrong_trace", "duplicate_terminal", "drop_rejection_events"])
def test_runner_rejects_event_stream_corruption(synthetic_snapshot, tmp_path, monkeypatch, corruption):
    from src.web.chat_handler import ChatHandler
    from tests import family_acceptance_chain_support as chain
    original = ChatHandler.process_decision_message
    mutations = []

    async def corrupt(self, socket, **kwargs):
        context = kwargs["context"]
        is_rejection = context.metadata["target"] == "AChE" or chain.INVALID_SMILES in context.query

        class CorruptingSocket:
            async def send_text(self, text):
                frame = json.loads(text)
                if frame["type"] == "agent_event":
                    if corruption == "wrong_trace":
                        frame["event"]["trace_id"] = "previous-unrelated-trace"
                        mutations.append(True)
                    elif corruption == "drop_rejection_events" and is_rejection:
                        mutations.append(True)
                        return
                    elif corruption == "duplicate_terminal" and frame["event"]["event"] == "task_completed":
                        mutations.append(True)
                        await socket.send_text(json.dumps(frame))
                await socket.send_text(json.dumps(frame))

        return await original(self, CorruptingSocket(), **kwargs)

    monkeypatch.setattr(ChatHandler, "process_decision_message", corrupt)
    report = chain.run_family_chain(synthetic_snapshot, work_dir=tmp_path / "chain", mode="synthetic_fixture")
    assert mutations
    assert report["status"] == "failed", "Corrupted public event stream was accepted"
    assert report["reason"] == "chain_mismatch"
    stage = (report["rejections"]["invalid_smiles"]["stages"]["websocket"]
             if corruption == "drop_rejection_events" else report["stages"]["websocket"])
    assert stage["status"] == "failed"


@pytest.mark.parametrize("corruption", ["internal_trace", "public_timestamp", "public_terminal_status"])
def test_event_checker_compares_actual_bus_and_public_provenance(synthetic_snapshot, tmp_path, corruption):
    from tests import family_acceptance_chain_support as chain
    target = chain.ALIASES[synthetic_snapshot.family_id][1]
    with chain.decision_session(tmp_path, target=target, query=f"SMILES: {chain.INVALID_SMILES}") as session:
        result, frames = chain.websocket_decision(session)
        chain.check_rejected_decision(session, result, frames)
        events = [frame["event"] for frame in frames if frame["type"] == "agent_event"]
        if corruption == "internal_trace":
            session.bus.events[0].trace_id = "unrelated-internal-trace"
        elif corruption == "public_timestamp":
            events[0]["timestamp"] += 1
        else:
            events[-1]["payload"]["status"] = "completed"
        with pytest.raises(chain.ChainFailure, match="^chain_mismatch$"):
            chain.check_rejected_decision(session, result, frames)


def test_runner_does_not_accept_model_unavailable_as_invalid_smiles(synthetic_snapshot, tmp_path, monkeypatch):
    from src.activity import prediction_service
    from tests import family_acceptance_chain_support as chain
    original = prediction_service.predict_activity

    def unavailable(smiles, **kwargs):
        summary = original(smiles, **kwargs)
        for row in summary["results"]:
            if row["smiles"] == chain.INVALID_SMILES:
                row["errors"] = {"bundle": "family_model_bundle_unavailable_or_invalid"}
        return summary

    monkeypatch.setattr(prediction_service, "predict_activity", unavailable)
    report = chain.run_family_chain(synthetic_snapshot, work_dir=tmp_path / "chain", mode="synthetic_fixture")
    assert report["status"] == "failed" and report["reason"] == "chain_mismatch"
    stage = report["rejections"]["invalid_smiles"]["stages"]["api_single"]
    assert stage["status"] == stage["scientific_status"] == "failed"
    assert stage["rows"][0]["errors"] == {"bundle": "family_model_bundle_unavailable_or_invalid"}
