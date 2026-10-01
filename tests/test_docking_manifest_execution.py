"""Regression checks for truthful execution records (no real docking here)."""
import json
import hashlib
import subprocess
from pathlib import Path

import pytest

from src.docking.adapters.base import CommandCancelledError, CommandOwnershipUncertainError
from src.docking.molecular_docking_service import DockingConfig, MolecularDockingService


@pytest.fixture
def run_inputs(tmp_path):
    service = MolecularDockingService()
    service.work_dir = str(tmp_path)
    receptor, ligand = tmp_path / 'receptor.pdbqt', tmp_path / 'ligand.pdbqt'
    receptor.write_text('receptor', encoding='utf-8')
    ligand.write_text('ligand', encoding='utf-8')
    config = DockingConfig(manual_center=True)
    service._write_run_manifest(str(tmp_path), str(receptor), str(ligand), 'file', config,
                                service._resolve_docking_box(str(receptor), config))
    return service, receptor, ligand, config, tmp_path / 'result.pdbqt'


def manifest(run_inputs):
    return json.loads((run_inputs[-1].parent / 'run_manifest.json').read_text(encoding='utf-8'))


@pytest.mark.parametrize('error,status', [
    (subprocess.TimeoutExpired('vina', 1), 'timeout'),
    (CommandCancelledError(), 'cancelled'),
    (CommandOwnershipUncertainError(), 'ownership_uncertain'),
    (OSError('launch failed'), 'failed'),
])
def test_vina_exception_does_not_leave_running_manifest(run_inputs, monkeypatch, error, status):
    service, receptor, ligand, config, output = run_inputs
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(service.vina_adapter, 'run_config', fail)
    if isinstance(error, OSError):
        assert service.run_vina_docking(str(receptor), str(ligand), config, str(output)) is False
    else:
        with pytest.raises(type(error)):
            service.run_vina_docking(str(receptor), str(ligand), config, str(output))
    assert manifest(run_inputs)['execution']['status'] == status


def test_manifest_failure_prevents_unrecorded_vina_success(run_inputs, monkeypatch):
    service, receptor, ligand, config, output = run_inputs
    calls = []
    def run(*args, **kwargs):
        calls.append(True)
        return subprocess.CompletedProcess([], 0)
    monkeypatch.setattr(service.vina_adapter, 'run_config', run)
    def fail_replace(*args):
        raise OSError('disk unavailable')
    monkeypatch.setattr('src.docking.molecular_docking_service.os.replace', fail_replace)
    assert service.run_vina_docking(str(receptor), str(ligand), config, str(output)) is False
    assert calls == []
    assert not (output.parent / '.run_manifest.json.tmp').exists()


def test_recorded_command_comes_from_actual_adapter(run_inputs, monkeypatch):
    service, receptor, ligand, config, output = run_inputs
    from src.docking.adapters.vina_adapter import VinaAdapter

    service.vina_adapter = VinaAdapter(str(output.parent / 'actual-vina.bat'))
    argv = ['cmd', '/c', str(output.parent / 'actual-vina.bat'), '--config', str(output.parent / 'config.txt')]
    def run(*_args, **_kwargs):
        output.write_text('synthetic output', encoding='utf-8')
        return subprocess.CompletedProcess(argv, 0)
    monkeypatch.setattr(service.vina_adapter, 'run_config', run)
    assert service.run_vina_docking(str(receptor), str(ligand), config, str(output)) is True
    execution = manifest(run_inputs)['execution']
    assert execution['command'] == ['cmd', '/c', 'actual-vina.bat', '--config', 'config.txt']
    assert execution['status'] == 'completed'
    assert execution['output_sha256'] == hashlib.sha256(output.read_bytes()).hexdigest()
    assert str(output.parent) not in json.dumps(execution)


@pytest.mark.parametrize('bad_config', [DockingConfig(center_x=float('nan')),
                                      DockingConfig(size_y=0), DockingConfig(size_z=float('inf'))])
def test_direct_vina_cannot_bypass_box_validation(run_inputs, monkeypatch, bad_config):
    service, receptor, ligand, _, output = run_inputs
    calls = []
    monkeypatch.setattr(service.vina_adapter, 'run_config',
                        lambda *a, **kw: calls.append(True) or subprocess.CompletedProcess([], 0))
    assert service.run_vina_docking(str(receptor), str(ligand), bad_config, str(output)) is False
    assert calls == []


@pytest.mark.parametrize('derived', [
    (float('nan'), 0.0, 0.0, 20.0, 20.0, 20.0),
    (1.0, 2.0, 3.0, 0.0, 20.0, 20.0),
])
def test_derived_co_crystal_box_is_validated(run_inputs, monkeypatch, derived):
    service, receptor, _, config, _ = run_inputs
    monkeypatch.setattr(service, '_auto_box_from_co_crystal', lambda *_a, **_kw: derived)
    config = DockingConfig()
    with pytest.raises(ValueError, match='invalid_docking_box'):
        service._resolve_docking_box(str(receptor), config)


def test_successful_preprocessing_updates_aggregate_state(run_inputs):
    service, _, _, _, _ = run_inputs
    service._update_run_manifest(
        str(run_inputs[-1].parent),
        preprocessing_state={
            'status': 'in_progress',
            'receptor': {'status': 'completed'},
        },
    )
    service._update_run_manifest(
        str(run_inputs[-1].parent),
        preprocessing_state={
            'status': 'completed',
            'ligand': {'status': 'completed'},
        },
    )
    assert manifest(run_inputs)['preprocessing_state']['status'] == 'completed'


def test_file_ligand_direct_preparation_records_direct_method(run_inputs, monkeypatch):
    service, _, _, _, output = run_inputs
    trace = {}
    monkeypatch.setattr(service, '_run_prepare_ligand', lambda *a, **kw: (True, ''))
    assert service.prepare_ligand_from_file(
        'input.sdf', str(output.parent / 'prepared.pdbqt'), trace=trace
    )
    assert trace['ligand_method'] == 'meeko_direct'
