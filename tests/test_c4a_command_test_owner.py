"""Explicit local owner qualification, not C4a feature/scientific acceptance.

Select this file or its nodes explicitly. Directory-wide CI skips before reading
other worktrees. No wrapper/primitives/qualification code loads at collection.
"""
import builtins
import hashlib
import os
from pathlib import Path
import subprocess
import sys
from types import ModuleType, SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / 'scratch/c4a_command_gate_supervisor.py'
LAUNCHER = ROOT / 'scratch/c4a_command_gate_run.py'
RUNNER = ROOT / 'scratch/ordinary_chat_offline_runner.py'
B1 = ROOT.parent / 'dynamic-bindings-b1'
ORIGINAL = B1 / 'scratch/b1_web_root_gate_supervisor.py'
WINDOWS = B1 / 'tests/agent/test_b1_web_root_supervisor_windows.py'
TRUSTED = ROOT.parent / 'docking-consent-execution/src/docking/adapters/base.py'
ORIGINAL_SHA = 'AC259293AC752A0F305CFDBAB492BC7932EECF47C7D54CD023B747E0090E5E09'
WINDOWS_SHA = '89E91E073B7C2BED6CF58C009C3794B4FA2E04BB9D4470E53241B87869C30EB1'
TRUSTED_SHA = '431EA4AB580A755C8D903F157EA4D9CCF91F6E44FC124491366FA4C6A9042726'
RUNNER_SHA = '0AD88551C1DC20527364D940855ABEE303EFB465DB67A4258FDEB25353EBCD61'
PREFIX = 'tests/test_docking_command_cancellation.py::'
SELECTORS = tuple(PREFIX + name for name in (
    'test_c4a_command_scope_api_present',
    'test_c4a_scope_registration_and_seal_precede_creation',
    'test_c4a_scope_normal_command_settles_real_resources',
    'test_c4a_windows_job_configuration_failure_retains_acquired_handle',
    'test_c4a_fast_spawn_failure_registered_before_start_returns',
    'test_c4a_windows_late_spawn_retains_actual_owner',
    'test_c4a_windows_setup_failure_keeps_acquired_resources',
    'test_c4a_windows_exited_root_job_close_failure_is_unresolved',
    'test_c4a_capture_exit_notification_is_not_join',
    'test_c4a_spawner_finally_notification_is_not_thread_exit',
    'test_c4a_posix_pending_spawn_and_setup_failure_retains_owner',
    'test_c4a_posix_root_exit_does_not_settle_live_group',
    'test_c4a_windows_root_exit_does_not_settle_live_job',
    'test_c4a_job_attach_failure_after_real_acquisition_cannot_settle',
    'test_c4a_popen_after_real_child_failure_cannot_settle',
)) + ('tests/test_docking_command_cancellation.py', 'tests/task_runtime/test_docking_consent.py')


@pytest.fixture(autouse=True)
def explicit_local_owner_selection(request):
    selected = any(
        not str(arg).startswith('-')
        and Path(str(arg).split('::', 1)[0]).resolve() == Path(__file__).resolve()
        for arg in request.config.args
    )
    if not selected:
        pytest.skip('local cross-worktree owner qualification requires explicit file/node selection')


def _checked_bytes(path, expected):
    assert path.is_file(), 'missing explicitly requested owner prerequisite: ' + path.name
    data = path.read_bytes()
    assert hashlib.sha256(data).hexdigest().upper() == expected, 'owner prerequisite hash mismatch'
    return data


def _load(path, name):
    # Called from test bodies, so a missing implementation is CALL-phase RED.
    assert path.is_file(), 'missing C4a test-owner implementation: ' + path.name
    data = path.read_bytes()
    module = ModuleType(name)
    module.__file__ = str(path)
    exec(compile(data, str(path), 'exec'), module.__dict__)
    return module


def _gate():
    _checked_bytes(TRUSTED, TRUSTED_SHA)
    return _load(HELPER, '_c4a_owner_control')


def test_c4a_owner_fixed_api_control():
    gate = _gate()
    assert callable(gate.supervise) and callable(gate._load_trusted_primitives)
    assert gate._ROOT == ROOT and gate._TRUSTED_BASE == TRUSTED
    assert gate._TRUSTED_SHA256 == TRUSTED_SHA
    assert gate._NODES == gate._TASK8_ORDER == SELECTORS
    assert gate._JOIN_NODES == {}
    assert gate._TASK8_TARGETS == {node: (node,) for node in SELECTORS}
    assert gate._active_owner is None
    launcher = _load(LAUNCHER, '_c4a_launcher_control')
    assert callable(launcher.main) and callable(launcher._gate.supervise)


def test_c4a_owner_source_inverse_control():
    original = _checked_bytes(ORIGINAL, ORIGINAL_SHA)
    _checked_bytes(WINDOWS, WINDOWS_SHA)
    _checked_bytes(RUNNER, RUNNER_SHA)
    assert HELPER.is_file(), 'missing C4a derived owner source'
    derived = HELPER.read_bytes()
    # Only these three source regions may differ; not AST-normalized equality.
    for start, end in (
        (b'_ROOT = ', b'_WindowsJob = '),
        (b'_NODES = ', b'_JOIN_PREFIX = '),
        (b'# Closed Task8 manifest;', b'_OS_FIELDS = '),
    ):
        left, right = derived.index(start), derived.index(end)
        old_left, old_right = original.index(start), original.index(end)
        assert left < right and old_left < old_right
        derived = derived[:left] + original[old_left:old_right] + derived[right:]
    assert derived == original
    assert hashlib.sha256(derived).hexdigest().upper() == ORIGINAL_SHA


@pytest.mark.parametrize('fault', ['none', 'digest', 'missing'])
def test_c4a_owner_trusted_same_buffer_control(monkeypatch, fault):
    gate = _gate()
    source = _checked_bytes(TRUSTED, TRUSTED_SHA)
    supplied = source if fault != 'digest' else source + b'\n# mismatched bytes\n'
    native_compile = builtins.compile
    reads, compiled = [], []
    missing = FileNotFoundError('fixed missing primitive control')

    def read(path):
        if path != TRUSTED:
            pytest.fail('trusted loader attempted alternate source')
        reads.append(path)
        assert len(reads) == 1, 'trusted loader reread after validation'
        if fault == 'missing':
            raise missing
        return supplied

    def compile_source(data, filename, mode, *args, **kwargs):
        assert data is supplied, 'compile must use the identical validated byte buffer'
        compiled.append(data)
        return native_compile(data, filename, mode, *args, **kwargs)

    def forbidden(*args, **kwargs):
        pytest.fail('primitive verification started a process or reserved an owner')

    before = {key for key in sys.modules if key == 'src.docking' or key.startswith('src.docking.')}
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'read_bytes', read)
        patch.setattr(builtins, 'compile', compile_source)
        patch.setattr(subprocess, 'Popen', forbidden)
        patch.setattr(gate, '_Owner', forbidden)
        if fault == 'missing':
            with pytest.raises(FileNotFoundError) as caught:
                gate._load_trusted_primitives()
            assert caught.value is missing
        elif fault == 'digest':
            with pytest.raises(RuntimeError, match='^untrusted command primitive$'):
                gate._load_trusted_primitives()
        else:
            module = gate._load_trusted_primitives()
            assert isinstance(module, ModuleType) and callable(module.CommandAdapter)
    assert reads == [TRUSTED]
    assert len(compiled) == (1 if fault == 'none' else 0)
    assert {key for key in sys.modules if key == 'src.docking' or key.startswith('src.docking.')} == before
    assert gate._active_owner is None


@pytest.mark.parametrize('selector', SELECTORS)
def test_c4a_owner_fixed_argv_control(monkeypatch, selector):
    gate = _gate()
    calls = []
    sentinel = RuntimeError('finite Popen seam stop; no child created')
    owner = gate._Owner(100.0)
    monkeypatch.setattr(gate, '_now', lambda: 100.0)
    monkeypatch.setattr(gate, '_WindowsJob', lambda: SimpleNamespace())
    monkeypatch.setattr(gate, '_CommandAdapter', SimpleNamespace(_windows_creationflags=lambda: 0x204))

    def popen(argv, **kwargs):
        calls.append((argv, kwargs))
        raise sentinel

    monkeypatch.setattr(gate, '_popen', popen)
    assert gate._TASK8_TARGETS[selector] == (selector,)
    gate._launch(owner, gate._TASK8_TARGETS[selector])
    assert calls == [([
        'C:/Users/xkx52/.conda/envs/MedChat/python.exe', '-I', '-S', '-B', str(RUNNER), selector,
    ], dict(cwd=str(ROOT), env={key: os.environ[key] for key in gate._OS_FIELDS if key in os.environ},
            shell=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=0x08000204))]
    assert owner.deadline == 820 and owner.cleanup_deadline == 825
    assert owner.errors == [sentinel] and owner.outcome == 'supervisor_error'
    assert owner.process is None and owner.unknown and not owner.slot_released
    assert owner.launch_thread is owner.cleanup_thread is None


@pytest.mark.parametrize('selector', [
    None, True, [], '', '-q', 'tests/test_docking_configuration.py',
    PREFIX + 'not_approved', SELECTORS[0] + '[extra]',
    'tests/agent/test_b1_normal_web_runtime.py::test_b1_scientific_dataflow_is_observation_driven[functional-native]',
    'tests/agent/_b1_permanent_join_child.py::test_b1_permanent_join_child[native]',
])
def test_c4a_owner_rejects_before_reservation_control(monkeypatch, selector):
    gate = _gate()
    calls = []

    def forbidden(*args, **kwargs):
        calls.append(True)
        raise AssertionError('unknown selector acquired resources')

    monkeypatch.setattr(gate, '_Owner', forbidden)
    monkeypatch.setattr(gate, '_popen', forbidden)
    with pytest.raises(ValueError, match='^unsupported B1 selector$'):
        gate.supervise(selector)
    assert calls == [] and gate._active_owner is None


@pytest.mark.parametrize('change', [
    'none', 'test_failed', 'test_watchdog_timeout', 'supervisor_error',
    'nonzero', 'unknown_rc', 'bool_rc', 'uncertain', 'not_released', 'int_released', 'retained',
    'extra_key', 'missing_key',
])
def test_c4a_owner_launcher_receipt_control(monkeypatch, change):
    launcher = _load(LAUNCHER, '_c4a_launcher_receipt_control')
    receipt = dict(outcome='passed', returncode=0, ownership='settled', slot_released=True)
    if change in ('test_failed', 'test_watchdog_timeout', 'supervisor_error'):
        receipt['outcome'] = change
    elif change in ('nonzero', 'unknown_rc', 'bool_rc'):
        receipt['returncode'] = {'nonzero': 1, 'unknown_rc': None, 'bool_rc': False}[change]
    elif change == 'uncertain':
        receipt['ownership'] = 'ownership_uncertain'
    elif change in ('not_released', 'int_released'):
        receipt['slot_released'] = False if change == 'not_released' else 1
    elif change == 'extra_key':
        receipt['extra'] = 0
    elif change == 'missing_key':
        del receipt['ownership']
    before = receipt.copy()
    calls = []

    def supervise(selector):
        calls.append(selector)
        return receipt

    monkeypatch.setattr(launcher._gate, 'supervise', supervise)
    monkeypatch.setattr(launcher._gate, '_active_owner', object() if change == 'retained' else None)
    result = launcher.main([SELECTORS[0]])
    assert type(result) is int and result == (0 if change == 'none' else 1)
    assert calls == [SELECTORS[0]] and receipt == before


@pytest.mark.parametrize('argv', [[], ['-q'], [SELECTORS[0], SELECTORS[1]],
                                  ['tests/test_docking_configuration.py'], [True]])
def test_c4a_owner_launcher_rejects_arbitrary_argv_control(monkeypatch, argv):
    launcher = _load(LAUNCHER, '_c4a_launcher_rejection_control')
    calls = []

    def forbidden(*args, **kwargs):
        calls.append(True)
        raise AssertionError('invalid argv reached supervisor')

    monkeypatch.setattr(launcher._gate, 'supervise', forbidden)
    with pytest.raises(ValueError):
        launcher.main(argv)
    assert calls == []


@pytest.mark.parametrize('case_name', ['clean', 'descendant', 'late'])
def test_c4a_owner_windows_qualification(monkeypatch, case_name):
    assert os.name == 'nt', 'real Windows qualification unavailable'
    assert HELPER.is_file(), 'missing C4a derived owner source'
    _checked_bytes(TRUSTED, TRUSTED_SHA)
    _checked_bytes(RUNNER, RUNNER_SHA)
    source = _checked_bytes(WINDOWS, WINDOWS_SHA)
    qualification = ModuleType('_c4a_reused_windows_qualification')
    qualification.__file__ = str(WINDOWS)
    # Hash and execute the same verified buffer. No import/second loader read.
    exec(compile(source, str(WINDOWS), 'exec'), qualification.__dict__)
    qualification.ROOT, qualification.HELPER, qualification.RUNNER = ROOT, HELPER, RUNNER
    fixture = qualification.windows_case.__wrapped__(monkeypatch)
    try:
        case = next(fixture)
        case.fixed_invocation = (SELECTORS[0], (SELECTORS[0],))
        test = {
            'clean': qualification.test_windows_clean_exit_has_physical_receipt,
            'descendant': qualification.test_windows_parent_exit_retains_pipe_held_descendant,
            'late': qualification.test_windows_late_popen_return_never_resumes,
        }[case_name]
        test(case)
    finally:
        # Execute the original retained-owner cleanup and profile restoration.
        fixture.close()
