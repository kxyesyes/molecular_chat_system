import asyncio
import logging
import os
import subprocess
import sys
import threading
import time
import warnings
from pathlib import Path
from unittest.mock import patch

import pytest

from src.agent.tools.molecular_docking import MolecularDocking
from src.docking.adapters.adfr_adapter import ADFRAdapter
from src.docking.adapters.base import (
    CommandAdapter,
    CommandCancelledError,
    CommandOutputLimitError,
    CommandOwnershipUncertainError,
)
from src.docking.adapters.meeko_adapter import MeekoAdapter
from src.docking.adapters.openbabel_adapter import OpenBabelAdapter
from src.docking.adapters.vina_adapter import VinaAdapter
from src.docking.molecular_docking_service import (
    DockingConfig,
    DockingResult,
    MolecularDockingService,
)


def test_pre_cancelled_command_does_not_spawn(tmp_path):
    cancel = threading.Event()
    cancel.set()
    adapter = CommandAdapter(sys.executable)

    with patch("src.docking.adapters.base.subprocess.Popen") as popen:
        with pytest.raises(CommandCancelledError) as caught:
            adapter.run(
                [sys.executable, "-c", "raise SystemExit(0)"],
                cwd=str(tmp_path),
                timeout=2,
                cancel_event=cancel,
            )

    popen.assert_not_called()
    assert str(caught.value) == "Docking command was cancelled"
    assert "python" not in repr(caught.value).lower()


def test_cancel_event_terminates_controlled_command(tmp_path):
    adapter = CommandAdapter(sys.executable)
    cancel = threading.Event()
    outcome = {}

    def invoke():
        try:
            adapter.run(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                cwd=str(tmp_path),
                timeout=60,
                cancel_event=cancel,
            )
        except BaseException as exc:
            outcome["error"] = exc

    thread = threading.Thread(target=invoke)
    thread.start()
    time.sleep(0.3)
    cancel.set()
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert isinstance(outcome.get("error"), CommandCancelledError)


def test_cancel_event_terminates_spawned_process_tree(tmp_path):
    child_script = tmp_path / "child.py"
    launcher_script = tmp_path / "launcher.py"
    marker = tmp_path / "child-survived.marker"
    child_script.write_text(
        "\n".join(
            [
                "import sys, time",
                "from pathlib import Path",
                "time.sleep(2)",
                "Path(sys.argv[1]).write_text('survived', encoding='utf-8')",
            ]
        ),
        encoding="utf-8",
    )
    launcher_script.write_text(
        "\n".join(
            [
                "import subprocess, sys",
                "subprocess.run([sys.executable, *sys.argv[1:]], check=False)",
            ]
        ),
        encoding="utf-8",
    )
    cancel = threading.Event()
    outcome = {}

    def invoke():
        try:
            CommandAdapter(sys.executable).run(
                [
                    sys.executable,
                    str(launcher_script),
                    str(child_script),
                    str(marker),
                ],
                cwd=str(tmp_path),
                timeout=10,
                cancel_event=cancel,
            )
        except BaseException as exc:
            outcome["error"] = exc

    thread = threading.Thread(target=invoke)
    thread.start()
    time.sleep(0.4)
    cancel.set()
    thread.join(timeout=5)
    time.sleep(2.1)

    assert not thread.is_alive()
    assert isinstance(outcome.get("error"), CommandCancelledError)
    assert not marker.exists()


def test_cancellation_precedes_timeout_when_observed_first(tmp_path):
    cancel = threading.Event()
    adapter = CommandAdapter(sys.executable)
    timer = threading.Timer(0.1, cancel.set)
    timer.start()
    try:
        with pytest.raises(CommandCancelledError):
            adapter.run(
                [sys.executable, "-c", "import time; time.sleep(10)"],
                cwd=str(tmp_path),
                timeout=2,
                cancel_event=cancel,
            )
    finally:
        timer.cancel()


def test_verified_normal_completion_wins_over_late_cancel(tmp_path):
    cancel = threading.Event()
    result = CommandAdapter(sys.executable).run(
        [sys.executable, "-c", "print('done')"],
        cwd=str(tmp_path),
        timeout=5,
        cancel_event=cancel,
    )
    cancel.set()

    assert result.returncode == 0
    assert result.stdout.strip() == "done"


def test_cleanup_failure_has_distinct_ownership_error(tmp_path):
    cancel = threading.Event()
    cancel.set()
    adapter = CommandAdapter(sys.executable)

    if os.name == "nt":
        target = "src.docking.adapters.base.CommandAdapter._cleanup_windows_process"
    else:
        target = "src.docking.adapters.base.CommandAdapter._terminate_posix_process_group"

    cancel.clear()
    timer = threading.Timer(0.1, cancel.set)
    timer.start()
    try:
        with patch(target, side_effect=RuntimeError("unsafe cleanup")):
            with pytest.raises(CommandOwnershipUncertainError) as caught:
                adapter.run(
                    [sys.executable, "-c", "import time; time.sleep(10)"],
                    cwd=str(tmp_path),
                    timeout=5,
                    cancel_event=cancel,
                )
    finally:
        timer.cancel()

    assert str(caught.value) == "Command process ownership is uncertain"
    assert "unsafe cleanup" not in str(caught.value)


@pytest.mark.skipif(os.name != "nt", reason="Windows bounded-spawn cancellation")
def test_windows_cancel_during_bounded_spawn_never_resumes(tmp_path):
    adapter = CommandAdapter(sys.executable)
    cancel = threading.Event()
    real_popen = subprocess.Popen
    spawned = threading.Event()
    marker = tmp_path / "resumed.marker"

    def delayed_popen(*args, **kwargs):
        time.sleep(0.5)
        process = real_popen(*args, **kwargs)
        spawned.set()
        return process

    timer = threading.Timer(0.1, cancel.set)
    timer.start()
    try:
        with patch(
            "src.docking.adapters.base.subprocess.Popen",
            side_effect=delayed_popen,
        ):
            with pytest.raises(CommandCancelledError):
                adapter.run(
                    [
                        sys.executable,
                        "-c",
                        "from pathlib import Path; Path('resumed.marker').write_text('yes')",
                    ],
                    cwd=str(tmp_path),
                    timeout=5,
                    cancel_event=cancel,
                )
    finally:
        timer.cancel()

    assert spawned.wait(timeout=5)
    time.sleep(0.2)
    assert not marker.exists()


@pytest.mark.parametrize(
    ("adapter", "method", "args"),
    [
        (ADFRAdapter("tool"), "prepare_receptor", ("r.pdb", "r.pdbqt", ".")),
        (MeekoAdapter("tool"), "prepare_ligand", ("l.sdf", "l.pdbqt", ".")),
        (OpenBabelAdapter("tool"), "convert", ("l.mol2", "l.sdf", ".")),
        (VinaAdapter("tool"), "run_config", ("config.txt", ".")),
    ],
)
def test_all_command_adapters_forward_cancel_event(adapter, method, args):
    cancel = threading.Event()
    completed = subprocess.CompletedProcess([], 0, "", "")

    with patch.object(CommandAdapter, "run", return_value=completed) as run:
        getattr(adapter, method)(*args, cancel_event=cancel)

    assert run.call_args.kwargs["cancel_event"] is cancel


def test_cancelled_preparation_does_not_log_command_arguments(tmp_path, caplog):
    secret_input = tmp_path / "secret-ligand-token.sdf"
    service = MolecularDockingService()
    caplog.set_level(logging.INFO)

    class CancelledAdapter:
        @staticmethod
        def build_prepare_command(input_path, output_path):
            return ["tool", input_path, output_path]

        @staticmethod
        def prepare_ligand(*_args, **_kwargs):
            raise CommandCancelledError()

    service.ligand_adapter = CancelledAdapter()
    with pytest.raises(CommandCancelledError):
        service._run_prepare_ligand(
            str(secret_input),
            str(tmp_path / "out.pdbqt"),
            str(tmp_path),
            "ligand preparation",
            cancel_event=threading.Event(),
        )

    assert "secret-ligand-token" not in caplog.text


def _successful_service(tmp_path):
    service = MolecularDockingService()
    service.work_dir = str(tmp_path)
    calls = []

    def prepare_protein(_source, output, *, cancel_event=None):
        calls.append(("receptor", cancel_event))
        Path(output).write_text("ATOM\n", encoding="utf-8")
        return True

    def prepare_ligand(_source, output, *, cancel_event=None):
        calls.append(("ligand", cancel_event))
        Path(output).write_text("ATOM      1  C\n", encoding="utf-8")
        return True

    def run_vina(_receptor, _ligand, _config, output, *, job_dir=None, cancel_event=None):
        calls.append(("vina", cancel_event))
        Path(output).write_text(
            "REMARK VINA RESULT: -7.2 0.0 0.0\n", encoding="utf-8"
        )
        return True

    service.prepare_protein = prepare_protein
    service.prepare_ligand_from_file = prepare_ligand
    service.run_vina_docking = run_vina
    service.parse_vina_results = lambda _path: [
        DockingResult("pose-1", -7.2, 0.0, 0.0, "")
    ]
    return service, calls


def test_service_emits_fixed_phase_order_and_forwards_cancel(tmp_path):
    service, calls = _successful_service(tmp_path)
    cancel = threading.Event()
    phases = []

    with patch("src.docking.history_index.upsert_history_record"):
        result = asyncio.run(
            service.perform_docking(
                "receptor.pdbqt",
                "ligand.pdbqt",
                DockingConfig(manual_center=True),
                "file",
                job_id="task-42",
                progress_callback=lambda phase, progress: phases.append(
                    (phase, progress)
                ),
                cancel_event=cancel,
            )
        )

    assert result["success"] is True
    assert [phase for phase, _ in phases] == [
        "receptor_preparation",
        "ligand_preparation",
        "vina_running",
        "scientific_validation",
    ]
    assert all(isinstance(progress, int) and 0 <= progress <= 100 for _, progress in phases)
    assert all(event is cancel for _, event in calls)
    assert Path(result["pose_file"]).parent.name == "docking_task-42"


def test_service_preserves_co_crystal_ligand_auto_box(tmp_path):
    receptor = tmp_path / "complex.pdb"
    receptor.write_text(
        "\n".join(
            [
                "HETATM    1  C1  LIG A   1       1.000   2.000   3.000  1.00  0.00           C",
                "HETATM    2  C2  LIG A   1       5.000   8.000   9.000  1.00  0.00           C",
            ]
        ),
        encoding="utf-8",
    )
    service, _calls = _successful_service(tmp_path / "work")
    Path(service.work_dir).mkdir()
    captured = {}

    def run_vina(_receptor, _ligand, config, output, **_kwargs):
        captured["center"] = (config.center_x, config.center_y, config.center_z)
        Path(output).write_text(
            "REMARK VINA RESULT: -7.2 0.0 0.0\n", encoding="utf-8"
        )
        return True

    service.run_vina_docking = run_vina
    with patch("src.docking.history_index.upsert_history_record"):
        result = asyncio.run(
            service.perform_docking(
                str(receptor),
                "ligand.pdbqt",
                DockingConfig(),
                "file",
                job_id="auto-box",
            )
        )

    assert result["success"] is True
    assert captured["center"] == pytest.approx((3.0, 5.0, 6.0))


@pytest.mark.parametrize(
    ("cancel_phase", "expected_calls"),
    [
        ("receptor_preparation", []),
        ("ligand_preparation", ["receptor"]),
        ("vina_running", ["receptor", "ligand"]),
        ("scientific_validation", ["receptor", "ligand", "vina"]),
    ],
)
def test_service_cancellation_stops_current_and_downstream_phases(
    tmp_path, cancel_phase, expected_calls
):
    service, calls = _successful_service(tmp_path)
    cancel = threading.Event()

    def progress(phase, _value):
        if phase == cancel_phase:
            cancel.set()

    result = asyncio.run(
        service.perform_docking(
            "receptor.pdbqt",
            "ligand.pdbqt",
            DockingConfig(manual_center=True),
            "file",
            job_id=f"cancel-{cancel_phase}",
            progress_callback=progress,
            cancel_event=cancel,
        )
    )

    assert result == {
        "success": False,
        "error_code": "cancelled",
        "error": "Docking was cancelled",
        "warnings": ["Partial docking artifacts were removed."],
    }
    assert [name for name, _ in calls] == expected_calls
    assert not (tmp_path / f"docking_cancel-{cancel_phase}").exists()
    serialized = repr(result).lower()
    assert "binding_energy" not in serialized
    assert "pose_file" not in serialized


def test_service_cancellation_after_parse_removes_pose_and_skips_history(tmp_path):
    service, _calls = _successful_service(tmp_path)
    cancel = threading.Event()

    def parse(_path):
        cancel.set()
        return [DockingResult("pose-1", -9.9, 0.0, 0.0, "")]

    service.parse_vina_results = parse
    with patch("src.docking.history_index.upsert_history_record") as history:
        result = asyncio.run(
            service.perform_docking(
                "receptor.pdbqt",
                "ligand.pdbqt",
                DockingConfig(manual_center=True),
                "file",
                job_id="cancel-after-parse",
                cancel_event=cancel,
            )
        )

    assert result["error_code"] == "cancelled"
    assert not (tmp_path / "docking_cancel-after-parse").exists()
    history.assert_not_called()
    assert "-9.9" not in repr(result)


def test_partial_artifacts_are_quarantined_when_delete_fails(tmp_path):
    job_dir = tmp_path / "docking_cancel-me"
    job_dir.mkdir()
    (job_dir / "result.pdbqt").write_text("SECRET POSE", encoding="utf-8")

    with patch(
        "src.docking.molecular_docking_service.shutil.rmtree",
        side_effect=PermissionError("delete denied"),
    ):
        warnings = MolecularDockingService._discard_partial_job(str(job_dir))

    assert not job_dir.exists()
    quarantined = list(tmp_path.glob(".cancelled-*"))
    assert len(quarantined) == 1
    assert warnings == ["Partial docking artifacts were quarantined."]


def test_service_cancellation_during_history_removes_completed_index(tmp_path):
    service, _calls = _successful_service(tmp_path)
    cancel = threading.Event()

    def write_then_cancel(*_args, **_kwargs):
        cancel.set()

    with (
        patch(
            "src.docking.history_index.upsert_history_record",
            side_effect=write_then_cancel,
        ),
        patch("src.docking.history_index.remove_history_record") as remove,
    ):
        result = asyncio.run(
            service.perform_docking(
                "receptor.pdbqt",
                "ligand.pdbqt",
                DockingConfig(manual_center=True),
                "file",
                job_id="cancel-during-history",
                cancel_event=cancel,
            )
        )

    assert result["error_code"] == "cancelled"
    remove.assert_called_once_with(str(tmp_path), "cancel-during-history")
    assert not (tmp_path / "docking_cancel-during-history").exists()


def test_history_cleanup_failure_is_explicit_on_cancellation(tmp_path):
    service, _calls = _successful_service(tmp_path)
    cancel = threading.Event()

    def write_then_cancel(*_args, **_kwargs):
        cancel.set()

    with (
        patch(
            "src.docking.history_index.upsert_history_record",
            side_effect=write_then_cancel,
        ),
        patch(
            "src.docking.history_index.remove_history_record",
            side_effect=PermissionError("sensitive path"),
        ),
    ):
        result = asyncio.run(
            service.perform_docking(
                "receptor.pdbqt",
                "ligand.pdbqt",
                DockingConfig(manual_center=True),
                "file",
                job_id="cancel-history-uncertain",
                cancel_event=cancel,
            )
        )

    assert result["error_code"] == "cancelled"
    assert "Docking history cleanup could not be confirmed." in result["warnings"]
    assert "sensitive path" not in repr(result)
    assert "binding_energy" not in repr(result)


@pytest.mark.parametrize(
    "job_id",
    ["", ".", "..", "../escape", r"..\escape", "has/slash", "has\\slash", "bad\nline", "a" * 129],
)
def test_service_rejects_unsafe_job_id_without_creating_directory(tmp_path, job_id):
    service, calls = _successful_service(tmp_path)

    result = asyncio.run(
        service.perform_docking(
            "receptor.pdbqt",
            "ligand.pdbqt",
            DockingConfig(manual_center=True),
            "file",
            job_id=job_id,
        )
    )

    assert result["success"] is False
    assert result["error_code"] == "invalid_job_id"
    assert calls == []
    assert list(tmp_path.iterdir()) == []


def test_service_rejects_existing_job_directory_without_overwrite(tmp_path):
    service, calls = _successful_service(tmp_path)
    existing = tmp_path / "docking_existing-task"
    existing.mkdir()
    marker = existing / "keep.txt"
    marker.write_text("keep", encoding="utf-8")

    result = asyncio.run(
        service.perform_docking(
            "receptor.pdbqt",
            "ligand.pdbqt",
            DockingConfig(manual_center=True),
            "file",
            job_id="existing-task",
        )
    )

    assert result["error_code"] == "job_conflict"
    assert marker.read_text(encoding="utf-8") == "keep"
    assert calls == []


def test_progress_callback_failure_stops_before_science(tmp_path):
    service, calls = _successful_service(tmp_path)

    def broken_callback(_phase, _progress):
        raise RuntimeError("secret callback details")

    result = asyncio.run(
        service.perform_docking(
            "receptor.pdbqt",
            "ligand.pdbqt",
            DockingConfig(manual_center=True),
            "file",
            job_id="callback-failure",
            progress_callback=broken_callback,
        )
    )

    assert result["error_code"] == "progress_callback_failed"
    assert "secret callback details" not in repr(result)
    assert calls == []


def test_service_maps_process_ownership_uncertain_without_science_claims(tmp_path):
    service, _calls = _successful_service(tmp_path)

    def uncertain(*_args, **_kwargs):
        raise CommandOwnershipUncertainError()

    service.run_vina_docking = uncertain
    result = asyncio.run(
        service.perform_docking(
            "receptor.pdbqt",
            "ligand.pdbqt",
            DockingConfig(manual_center=True),
            "file",
            job_id="uncertain-task",
        )
    )

    assert result["success"] is False
    assert result["error_code"] == "process_ownership_uncertain"
    assert "binding" not in repr(result).lower()
    assert "pose" not in repr(result).lower()


def test_service_maps_timeout_to_stable_error_code(tmp_path):
    service, _calls = _successful_service(tmp_path)
    service.run_vina_docking = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        subprocess.TimeoutExpired("secret command", 1)
    )

    result = asyncio.run(
        service.perform_docking(
            "receptor.pdbqt",
            "ligand.pdbqt",
            DockingConfig(manual_center=True),
            "file",
            job_id="timeout-task",
        )
    )

    assert result["success"] is False
    assert result["error_code"] == "timeout"
    assert "secret command" not in repr(result)


def test_tool_forwards_controls_and_does_not_claim_real_execution_on_cancel(tmp_path):
    receptor = tmp_path / "receptor.pdbqt"
    ligand = tmp_path / "ligand.pdbqt"
    receptor.write_text("ATOM\n", encoding="utf-8")
    ligand.write_text("ATOM\n", encoding="utf-8")
    cancel = threading.Event()
    callback = lambda _phase, _progress: None
    captured = {}

    class FakeService:
        def __init__(self, config=None):
            captured["config"] = config

        def verify_environment(self):
            return True

        async def perform_docking(self, **kwargs):
            captured.update(kwargs)
            return {
                "success": False,
                "error_code": "cancelled",
                "error": "Docking was cancelled",
                "warnings": ["Partial docking artifacts were removed."],
            }

    with patch(
        "src.docking.molecular_docking_service.MolecularDockingService",
        FakeService,
    ):
        result = MolecularDocking().execute(
            {
                "receptor_path": str(receptor),
                "ligand_path": str(ligand),
                "center": [1, 2, 3],
                "size": [20, 20, 20],
            },
            job_id="task-safe",
            progress_callback=callback,
            cancel_event=cancel,
        )

    assert captured["job_id"] == "task-safe"
    assert captured["progress_callback"] is callback
    assert captured["cancel_event"] is cancel
    assert result["success"] is False
    assert result["data"]["error_code"] == "cancelled"
    assert result["quality"]["real_execution"] is False
    assert result["quality"]["execution_status"] == "cancelled"
    assert str(receptor.resolve()) not in repr(result)
    assert str(ligand.resolve()) not in repr(result)
    assert "binding_energy" not in repr(result)


@pytest.mark.parametrize("timeout", [-1, 0, float("nan"), float("inf")])
def test_command_adapter_rejects_invalid_timeout_before_spawn(tmp_path, timeout):
    adapter = CommandAdapter(sys.executable)

    with patch("src.docking.adapters.base.subprocess.Popen") as popen:
        with pytest.raises(ValueError, match="positive finite"):
            adapter.run(
                [sys.executable, "-c", "raise SystemExit(0)"],
                cwd=str(tmp_path),
                timeout=timeout,
            )

    popen.assert_not_called()


def test_command_adapter_rejects_invalid_cancel_token_before_spawn(tmp_path):
    adapter = CommandAdapter(sys.executable)

    with patch("src.docking.adapters.base.subprocess.Popen") as popen:
        with pytest.raises(TypeError, match="cancel_event"):
            adapter.run(
                [sys.executable, "-c", "raise SystemExit(0)"],
                cwd=str(tmp_path),
                timeout=1,
                cancel_event=object(),
            )

    popen.assert_not_called()


def test_command_adapter_stops_output_flood_at_fixed_limit(tmp_path):
    adapter = CommandAdapter(sys.executable)

    with pytest.raises(CommandOutputLimitError) as caught:
        adapter.run(
            [
                sys.executable,
                "-c",
                "import sys; sys.stdout.write('x' * 12000000); sys.stdout.flush()",
            ],
            cwd=str(tmp_path),
            timeout=10,
        )

    assert str(caught.value) == "Docking command output exceeded the safe limit"
    assert "xxxxxxxx" not in repr(caught.value)


def test_failed_workflow_removes_partial_directory_and_allows_retry(tmp_path):
    service, calls = _successful_service(tmp_path)
    service.prepare_protein = lambda *_args, **_kwargs: False

    first = asyncio.run(
        service.perform_docking(
            "receptor.pdbqt",
            "ligand.pdbqt",
            DockingConfig(manual_center=True),
            "file",
            job_id="retry-safe",
        )
    )
    service.prepare_protein = lambda _source, output, **_kwargs: (
        Path(output).write_text("ATOM\n", encoding="utf-8") is not None
    )
    second = asyncio.run(
        service.perform_docking(
            "receptor.pdbqt",
            "ligand.pdbqt",
            DockingConfig(manual_center=True),
            "file",
            job_id="retry-safe",
        )
    )

    assert first["error_code"] == "receptor_preparation_failed"
    assert second.get("error_code") != "job_conflict"
    assert calls


def test_service_rejects_windows_alias_job_id(tmp_path):
    service, calls = _successful_service(tmp_path)

    result = asyncio.run(
        service.perform_docking(
            "receptor.pdbqt",
            "ligand.pdbqt",
            DockingConfig(manual_center=True),
            "file",
            job_id="task.",
        )
    )

    assert result["error_code"] == "invalid_job_id"
    assert calls == []


def test_async_progress_callback_is_rejected_without_unawaited_coroutine(tmp_path):
    service, calls = _successful_service(tmp_path)

    async def unsupported_callback(_phase, _progress):
        return None

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = asyncio.run(
            service.perform_docking(
                "receptor.pdbqt",
                "ligand.pdbqt",
                DockingConfig(manual_center=True),
                "file",
                job_id="async-callback",
                progress_callback=unsupported_callback,
            )
        )

    assert result["error_code"] == "progress_callback_failed"
    assert calls == []
    assert not [warning for warning in caught if "never awaited" in str(warning.message)]


def test_tool_execute_works_inside_existing_event_loop_without_coroutine_leak(tmp_path):
    receptor = tmp_path / "receptor.pdbqt"
    ligand = tmp_path / "ligand.pdbqt"
    receptor.write_text("ATOM\n", encoding="utf-8")
    ligand.write_text("ATOM\n", encoding="utf-8")

    class FakeService:
        def __init__(self, config=None):
            pass

        def verify_environment(self):
            return True

        async def perform_docking(self, **_kwargs):
            return {
                "success": False,
                "error_code": "cancelled",
                "error": "Docking was cancelled",
            }

    async def invoke():
        with patch(
            "src.docking.molecular_docking_service.MolecularDockingService",
            FakeService,
        ):
            return MolecularDocking().execute(
                {
                    "receptor_path": str(receptor),
                    "ligand_path": str(ligand),
                    "center": [1, 2, 3],
                    "size": [20, 20, 20],
                }
            )

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = asyncio.run(invoke())

    assert result["data"]["error_code"] == "cancelled"
    assert not [warning for warning in caught if "never awaited" in str(warning.message)]


def test_tool_redacts_structured_inputs_and_success_provenance(tmp_path):
    receptor = tmp_path / "secret-receptor-name.pdbqt"
    receptor.write_text("ATOM\n", encoding="utf-8")
    secret_smiles = "CCOC(=O)SECRET"

    class FakeService:
        def __init__(self, config=None):
            pass

        def verify_environment(self):
            return True

        async def perform_docking(self, **_kwargs):
            return {
                "success": True,
                "job_id": "task-safe",
                "results": [{"pose": 1, "binding_energy": -7.0}],
                "best_pose": {"pose": 1, "binding_energy": -7.0},
                "pose_file": "safe-relative-pose.pdbqt",
                "total_poses": 1,
            }

    with patch(
        "src.docking.molecular_docking_service.MolecularDockingService",
        FakeService,
    ):
        result = MolecularDocking().execute(
            {
                "receptor_path": str(receptor),
                "smiles": secret_smiles,
                "center": [1, 2, 3],
                "size": [20, 20, 20],
            }
        )

    serialized = repr(result)
    assert result["success"] is True
    assert result["query"] == {
        "request_type": "structured_docking",
        "receptor_provided": True,
        "ligand_mode": "smiles",
    }
    assert str(receptor.resolve()) not in serialized
    assert secret_smiles not in serialized
    assert result["quality"]["docking_inputs"] == {
        "receptor_provided": True,
        "ligand_provided": True,
        "ligand_mode": "smiles",
        "center": [1.0, 2.0, 3.0],
        "size": [20.0, 20.0, 20.0],
    }


def test_receptor_preparation_never_uses_simplified_scientific_fallback(tmp_path):
    receptor = tmp_path / "receptor.pdb"
    output = tmp_path / "receptor.pdbqt"
    receptor.write_text(
        "ATOM      1  C   LIG A   1       1.000   2.000   3.000\n",
        encoding="utf-8",
    )
    service = MolecularDockingService()
    service._prepare_receptor_candidates = lambda: []

    assert service.prepare_protein(str(receptor), str(output)) is False
    assert not output.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group regression")
def test_posix_normal_parent_exit_does_not_abandon_orphaned_child(tmp_path):
    child = tmp_path / "child.py"
    launcher = tmp_path / "launcher.py"
    marker = tmp_path / "orphan.marker"
    child.write_text(
        "import sys,time\nfrom pathlib import Path\ntime.sleep(2)\n"
        "Path(sys.argv[1]).write_text('survived')\n",
        encoding="utf-8",
    )
    launcher.write_text(
        "import subprocess,sys\n"
        "subprocess.Popen([sys.executable,*sys.argv[1:]], "
        "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n",
        encoding="utf-8",
    )

    with pytest.raises(subprocess.TimeoutExpired):
        CommandAdapter(sys.executable).run(
            [sys.executable, str(launcher), str(child), str(marker)],
            cwd=str(tmp_path),
            timeout=0.3,
        )

    time.sleep(2.0)
    assert not marker.exists()


# C4a section 4.1 ONLY. Existing tests above are a byte-preserved prefix.
# Proposed internal test interface: scope(task_id, output_root, cancel_event),
# snapshot with state/command_count/pending_count/primary_error_code/error_codes.
# These are server-only lifecycle facts, never ToolResult/scientific evidence.
# Missing API lookup is deliberately inside test bodies, before resources exist.
# EXECUTION HOLD: only the separately approved API node may run. Platform tests
# call blocking scope.settle (sometimes directly); their non-daemon test threads
# are not a qualified outer retained process/resource owner or a watchdog.
def _c4a_scope(tmp_path, cancel):
    from src.docking.adapters import base

    scope_type = getattr(base, "CommandOwnershipScope", None)
    assert callable(scope_type), "missing CommandOwnershipScope API"
    return scope_type(
        task_id="c4a-command-owner",
        output_root=tmp_path,
        cancel_event=cancel,
    )


def _c4a_snapshot(scope):
    import json

    snapshot = scope.snapshot()
    assert type(snapshot) is dict
    assert set(snapshot) == {
        "state", "command_count", "pending_count", "primary_error_code", "error_codes",
    }, "snapshot must contain only closed server lifecycle facts"
    assert snapshot["state"] in {
        "reserved", "spawn_pending", "running", "cleanup_pending",
        "settled", "unresolved",
    }
    for key in ("command_count", "pending_count"):
        assert type(snapshot[key]) is int and snapshot[key] >= 0
    if snapshot["state"] == "unresolved":
        assert snapshot["pending_count"] > 0
    assert snapshot["primary_error_code"] is None or (
        type(snapshot["primary_error_code"]) is str
        and snapshot["primary_error_code"].replace("_", "").isalnum()
    )
    assert type(snapshot["error_codes"]) in (list, tuple)
    assert all(type(code) is str and code.replace("_", "").isalnum()
               for code in snapshot["error_codes"])
    encoded = json.dumps(snapshot)
    for private in ("C4A_PRIVATE", "c4a-command-owner", "python.exe", "-c"):
        assert private not in encoded
    return snapshot


def _c4a_wait(predicate, *, seconds=8):
    deadline = time.monotonic() + seconds
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.005)
    assert predicate(), "C4a finite observation expired"


class _C4aPhysicalResources:
    """Observe actual resources; never manufacture ownership/settlement facts."""

    def __init__(self, base, patcher):
        self.base = base
        self.observers = patcher
        self.patch = pytest.MonkeyPatch()
        self.cancel = threading.Event()
        self.releases = []
        self.threads = []
        self.processes = []
        self.streams = []
        self.jobs = []
        self.job_closes = []
        self.resumes = []
        self.starts = []
        self.actual_joins = []
        self.popen_calls = 0
        self.real_popen = subprocess.Popen
        self.real_start = threading.Thread.start
        self.real_run = threading.Thread.run
        self.real_join = threading.Thread.join
        self.real_process_close = base.CommandAdapter._close_process_handle
        self.popen_before = None
        self.popen_after = None
        self.start_before = None
        self.start_after = None
        self.thread_after = None
        self.job_close_fault = False
        self.config_fault = False
        if os.name == "nt":
            self.real_create = base._kernel32.CreateJobObjectW
            self.real_configure = base._kernel32.SetInformationJobObject
            self.real_close = base._kernel32.CloseHandle
            self.real_terminate = base._kernel32.TerminateJobObject
            self.real_resume = base._WindowsJob.resume

            def observe_resume(process):
                self.resumes.append(process)
                return self.real_resume(process)  # actual NtResumeProcess delegation

            patcher.setattr(base._kernel32, "CreateJobObjectW", self.create_job)
            patcher.setattr(base._kernel32, "SetInformationJobObject", self.configure_job)
            patcher.setattr(base._kernel32, "CloseHandle", self.close_handle)
            patcher.setattr(base._WindowsJob, "resume", staticmethod(observe_resume))
        patcher.setattr(base.subprocess, "Popen", self.popen)
        # Plain functions retain Thread's descriptor binding; installing bound
        # resource methods here would lose the actual Thread receiver.
        def observed_start(thread):
            return self.start(thread)

        def observed_run(thread):
            return self.run_thread(thread)

        def observed_join(thread, *args, **kwargs):
            result = self.real_join(thread, *args, **kwargs)
            if thread.name.startswith("docking-command-") and not thread.is_alive():
                self.actual_joins.append(thread)
            return result

        patcher.setattr(threading.Thread, "start", observed_start)
        patcher.setattr(threading.Thread, "run", observed_run)
        patcher.setattr(threading.Thread, "join", observed_join)

    def barrier(self):
        event = threading.Event()
        self.releases.append(event)
        return event

    def popen(self, *args, **kwargs):
        self.popen_calls += 1
        if self.popen_before is not None:
            self.popen_before()
        process = self.real_popen(*args, **kwargs)
        self.processes.append(process)
        self.streams.extend(stream for stream in (process.stdout, process.stderr)
                            if stream is not None)
        if self.popen_after is not None:
            self.popen_after(process)
        return process

    def start(self, thread):
        if thread.name.startswith(("docking-command-", "c4a-test-")):
            self.threads.append(thread)  # before real start, including failed start
        if self.start_before is not None:
            self.start_before(thread)
        self.starts.append(thread)
        result = self.real_start(thread)
        if self.start_after is not None:
            self.start_after(thread)
        return result

    def run_thread(self, thread):
        try:
            return self.real_run(thread)
        finally:
            if self.thread_after is not None:
                self.thread_after(thread)

    def create_job(self, *args):
        handle = self.real_create(*args)
        if handle:
            self.jobs.append(handle)
        return handle

    def configure_job(self, *args):
        if self.config_fault:
            self.base.ctypes.set_last_error(5)
            return 0
        return self.real_configure(*args)

    def close_handle(self, handle):
        if handle in self.jobs:
            assert handle not in self.job_closes, "double Job handle close"
            if self.job_close_fault:
                self.base.ctypes.set_last_error(5)
                return 0  # deterministic failure BEFORE the OS closes this handle
        result = self.real_close(handle)
        if result and handle in self.jobs:
            self.job_closes.append(handle)
        return result

    def invoke(self, function):
        result = {}
        done = threading.Event()

        def target():
            try:
                result["value"] = function()
            except BaseException as exc:
                result["error"] = exc
            finally:
                done.set()

        thread = threading.Thread(target=target, name="c4a-test-owner")
        thread.start()
        return thread, done, result

    def joined(self, thread):
        thread.join(10)
        assert not thread.is_alive(), "C4a actual thread did not exit"

    def assert_physically_stopped(self):
        assert all(not thread.is_alive() for thread in self.threads)
        assert all(thread in self.actual_joins for thread in self.threads
                   if thread.name.startswith("docking-command-") and thread.ident is not None)
        assert all(process.returncode is not None for process in self.processes)
        assert all(stream.closed for stream in self.streams)
        if os.name == "nt":
            assert len(self.job_closes) == len(self.jobs)
            assert all(getattr(process, "_handle", None) is None
                       for process in self.processes)
        else:
            assert all(not self.base.CommandAdapter._posix_process_group_active(process.pid)
                       for process in self.processes)

    def cleanup(self):
        # Independent teardown is deliberately outside all injected fault hooks.
        # It never modifies a scope/receipt or turns a failed assertion into proof.
        errors = []

        def attempt(label, action):
            try:
                return action()
            except BaseException as exc:
                errors.append(f"{label}:{type(exc).__name__}")
                return None

        try:
            self._cleanup_resources(attempt, errors)
        except BaseException as exc:
            errors.append(f"cleanup_dispatch:{type(exc).__name__}")
        finally:
            attempt("restore_fault_patches", self.patch.undo)
            attempt("restore_observers", self.observers.undo)
        if errors:
            pytest.fail("C4a teardown unresolved: " + ", ".join(errors), pytrace=False)

    def _cleanup_resources(self, attempt, errors):
        attempt("cancel_signal", self.cancel.set)
        for release in self.releases:
            attempt("release_barrier", release.set)
        attempt("remove_faults", self.patch.undo)
        self.job_close_fault = False
        self.config_fault = False
        self.start_before = None
        self.start_after = None
        self.thread_after = None
        # Observers remain installed until late creation/readers actually exit.
        for thread in tuple(self.threads):
            if thread.ident is not None:
                attempt("initial_join", lambda thread=thread: thread.join(2))
        if os.name == "nt":
            for handle in self.jobs:
                if handle not in self.job_closes:
                    def terminate_job(handle=handle):
                        if not self.real_terminate(handle, 1):
                            raise OSError("independent Job termination failed")
                    attempt("terminate_job", terminate_job)
        else:
            import signal

            for process in self.processes:
                def stop_group(process=process):
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                attempt("terminate_group", stop_group)
        for process in self.processes:
            if os.name != "nt" or getattr(process, "_handle", None) is not None:
                if attempt("poll_process", process.poll) is None:
                    attempt("kill_process", process.kill)
                attempt("wait_process", lambda process=process: process.wait(timeout=5))
        for thread in tuple(self.threads):
            if thread.ident is not None:
                attempt("final_join", lambda thread=thread: thread.join(10))
        # A join failure/live thread must not short-circuit remaining close calls.
        # Individual close calls can themselves block: the platform execution
        # HOLD requires an independently qualified outer owner, not this helper.
        for stream in self.streams:
            def close_stream(stream=stream):
                if not stream.closed:
                    stream.close()
            attempt("close_stream", close_stream)
        for process in self.processes:
            attempt("close_process_handle", lambda process=process: self.real_process_close(process))
        if os.name == "nt":
            for handle in self.jobs:
                if handle not in self.job_closes:
                    def close_job(handle=handle):
                        if not self.real_close(handle):
                            raise OSError("independent Job teardown failed")
                        self.job_closes.append(handle)
                    attempt("close_job_handle", close_job)
        # Aggregate only after every resource had its independent cleanup turn.
        for thread in tuple(self.threads):
            def check_thread(thread=thread):
                if thread.is_alive():
                    errors.append("live_thread")
            attempt("check_thread", check_thread)


@pytest.fixture
def c4a_resources():
    from src.docking.adapters import base

    patcher = pytest.MonkeyPatch()
    resources = _C4aPhysicalResources(base, patcher)
    try:
        yield resources
    finally:
        resources.cleanup()


def _c4a_command(tmp_path, scope, resources, *, script="print('done')", timeout=5):
    adapter = CommandAdapter(sys.executable, ownership_scope=scope)
    return adapter.run(
        [sys.executable, "-I", "-S", "-B", "-c", script],
        cwd=str(tmp_path), timeout=timeout,
        cancel_event=resources.cancel,
    )


def test_c4a_command_scope_api_present():
    from src.docking.adapters import base

    scope_type = getattr(base, "CommandOwnershipScope", None)
    assert callable(scope_type), "missing CommandOwnershipScope API"
    for method in ("reserve_command", "seal", "snapshot", "settle"):
        assert callable(getattr(scope_type, method, None)), f"missing scope.{method} API"


@pytest.mark.parametrize("denial", ["registration", "sealed", "pre-cancel"])
def test_c4a_scope_registration_and_seal_precede_creation(tmp_path, c4a_resources, denial):
    resources = c4a_resources
    scope = _c4a_scope(tmp_path, resources.cancel)
    if denial == "registration":
        def refuse(*args, **kwargs):
            raise RuntimeError("C4A_PRIVATE registration fault")
        resources.patch.setattr(scope, "reserve_command", refuse)
    elif denial == "sealed":
        scope.seal()
        scope.seal()
    else:
        resources.cancel.set()
    with pytest.raises(Exception):
        _c4a_command(tmp_path, scope, resources)
    assert resources.starts == []
    assert resources.processes == []
    assert resources.jobs == []
    assert resources.popen_calls == 0
    assert _c4a_snapshot(scope)["command_count"] == 0


def test_c4a_scope_normal_command_settles_real_resources(tmp_path, c4a_resources):
    resources = c4a_resources
    scope = _c4a_scope(tmp_path, resources.cancel)
    result = _c4a_command(tmp_path, scope, resources)
    assert result.returncode == 0 and result.stdout.strip() == "done"
    assert resources.popen_calls == len(resources.processes) == 1
    assert {t.name for t in resources.starts if t.name in {
        "docking-command-stdout", "docking-command-stderr",
    }} == {"docking-command-stdout", "docking-command-stderr"}
    scope.seal()
    scope.settle()
    snapshot = _c4a_snapshot(scope)
    assert snapshot["state"] == "settled"
    assert snapshot["command_count"] == 1 and snapshot["pending_count"] == 0
    assert snapshot["primary_error_code"] is None and not snapshot["error_codes"]
    resources.assert_physically_stopped()


@pytest.mark.skipif(os.name != "nt", reason="genuine Windows Job acquisition/configuration")
@pytest.mark.parametrize("close_fails", [False, True], ids=["close-success", "close-failure"])
def test_c4a_windows_job_configuration_failure_retains_acquired_handle(
    tmp_path, c4a_resources, close_fails,
):
    resources = c4a_resources
    scope = _c4a_scope(tmp_path, resources.cancel)
    resources.config_fault = True
    resources.job_close_fault = close_fails
    with pytest.raises(Exception):
        _c4a_command(tmp_path, scope, resources)
    scope.seal()
    _c4a_wait(lambda: all(not thread.is_alive() for thread in resources.threads))
    assert len(resources.jobs) == 1  # genuine CreateJobObjectW actually succeeded
    assert resources.processes == []
    assert resources.popen_calls == 0
    snapshot = _c4a_snapshot(scope)
    assert snapshot["primary_error_code"] == "job_configuration_failed"
    if close_fails:
        assert resources.job_closes == []
        assert snapshot["state"] == "unresolved"
        assert "job_close_failed" in snapshot["error_codes"]
    else:
        scope.settle()
        assert _c4a_snapshot(scope)["state"] == "settled"
        assert resources.job_closes == resources.jobs


@pytest.mark.skipif(os.name != "nt", reason="genuine Windows fast pre-return Job setup failure")
def test_c4a_fast_spawn_failure_registered_before_start_returns(tmp_path, c4a_resources):
    resources = c4a_resources
    scope = _c4a_scope(tmp_path, resources.cancel)
    resources.config_fault = True
    fast_exit = threading.Event()

    def before_start(thread):
        if thread.name == "docking-command-spawn":
            assert _c4a_snapshot(scope)["command_count"] == 1

    def after_start(thread):
        if thread.name == "docking-command-spawn":
            thread.join(5)  # genuine worker finishes before start wrapper returns
            assert not thread.is_alive()
            fast_exit.set()

    resources.start_before = before_start
    resources.start_after = after_start
    with pytest.raises(Exception):
        _c4a_command(tmp_path, scope, resources)
    assert fast_exit.is_set() and len(resources.jobs) == 1
    assert resources.popen_calls == 0
    assert _c4a_snapshot(scope)["primary_error_code"] == "job_configuration_failed"
    scope.seal()
    scope.settle()
    assert _c4a_snapshot(scope)["state"] == "settled"
    resources.assert_physically_stopped()


@pytest.mark.skipif(os.name != "nt", reason="genuine Windows suspended late spawn/Job")
@pytest.mark.parametrize("interrupt", ["cancel", "timeout", "normal"])
def test_c4a_windows_late_spawn_retains_actual_owner(tmp_path, c4a_resources, interrupt):
    resources = c4a_resources
    scope = _c4a_scope(tmp_path, resources.cancel)
    entered = threading.Event()
    release = resources.barrier()
    marker = tmp_path / "late-resumed.marker"

    def before_popen():
        entered.set()
        assert release.wait(15), "test did not release actual Popen"

    resources.popen_before = before_popen
    owner, done, outcome = resources.invoke(lambda: _c4a_command(
        tmp_path, scope, resources,
        script="from pathlib import Path; Path('late-resumed.marker').write_text('yes')",
        timeout=0.2 if interrupt == "timeout" else 10,
    ))
    assert entered.wait(5)
    assert len(resources.jobs) == 1 and resources.processes == []
    if interrupt == "normal":
        assert not done.is_set()
        release.set()
        resources.joined(owner)
        assert "error" not in outcome and marker.exists()
        assert resources.resumes == resources.processes and len(resources.resumes) == 1
        scope.seal()
        scope.settle()
    else:
        if interrupt == "cancel":
            resources.cancel.set()
        assert done.wait(5), "controlled Windows caller did not report interruption"
        resources.joined(owner)
        expected = (subprocess.TimeoutExpired,) if interrupt == "timeout" else (
            CommandCancelledError, CommandOwnershipUncertainError,
        )
        assert isinstance(outcome.get("error"), expected)
        scope.seal()
        before = _c4a_snapshot(scope)
        assert before["state"] != "settled" and before["pending_count"] > 0
        assert before["primary_error_code"] is not None
        settler, settled, settlement = resources.invoke(scope.settle)
        assert not settled.wait(0.1), "pending reserved spawn reported settled"
        assert not marker.exists()
        assert resources.resumes == []
        release.set()
        resources.joined(settler)
        assert "error" not in settlement
        assert len(resources.processes) == 1  # real late Popen actually returned
        assert resources.resumes == [], "actual late child reached the resume boundary"
        assert not marker.exists(), "late suspended process was resumed after stop"
        assert _c4a_snapshot(scope)["primary_error_code"] == before["primary_error_code"]
    assert _c4a_snapshot(scope)["state"] == "settled"
    assert len(resources.processes) == 1
    resources.assert_physically_stopped()


@pytest.mark.skipif(os.name != "nt", reason="genuine Windows Job/setup failure ownership")
@pytest.mark.parametrize("fault", ["reader2-start", "assign", "nested-cleanup", "process-close"])
def test_c4a_windows_setup_failure_keeps_acquired_resources(tmp_path, c4a_resources, fault):
    resources = c4a_resources
    scope = _c4a_scope(tmp_path, resources.cancel)
    fired = threading.Event()
    nested_cleanup_reached = threading.Event()
    real_assign = resources.base._WindowsJob.assign

    def fail_assign(job, process):
        fired.set()
        raise OSError("C4A_PRIVATE assignment fault")

    def fail_reader2(thread):
        if thread.name == "docking-command-stderr":
            assert any(t.name == "docking-command-stdout" and t.is_alive()
                       for t in resources.threads)
            fired.set()
            raise RuntimeError("C4A_PRIVATE reader start fault")

    if fault == "reader2-start":
        resources.start_before = fail_reader2
    else:
        resources.patch.setattr(resources.base._WindowsJob, "assign", fail_assign)
    if fault == "nested-cleanup":
        real_communicate = CommandAdapter._communicate_after_termination

        def fail_cleanup(*args, **kwargs):
            real_communicate(*args, **kwargs)
            nested_cleanup_reached.set()
            raise RuntimeError("C4A_PRIVATE nested cleanup fault")
        resources.patch.setattr(CommandAdapter, "_communicate_after_termination", staticmethod(fail_cleanup))
    elif fault == "process-close":
        def fail_process_close(process):
            raise OSError("C4A_PRIVATE process handle close fault")
        resources.patch.setattr(CommandAdapter, "_close_process_handle", staticmethod(fail_process_close))
    with pytest.raises(Exception):
        _c4a_command(tmp_path, scope, resources)
    assert fired.is_set() and len(resources.jobs) == len(resources.processes) == 1
    scope.seal()
    snapshot = _c4a_snapshot(scope)
    assert snapshot["state"] == "unresolved"
    assert snapshot["primary_error_code"] == (
        "reader_start_failed" if fault == "reader2-start" else "job_assignment_failed"
    )
    if fault == "nested-cleanup":
        assert nested_cleanup_reached.is_set(), "real cleanup delegation did not reach injected fault"
        assert "command_cleanup_failed" in snapshot["error_codes"]
        assert snapshot["primary_error_code"] == "job_assignment_failed"
    if fault == "process-close":
        assert "process_handle_close_failed" in snapshot["error_codes"]
        assert getattr(resources.processes[0], "_handle", None) is not None
    assert real_assign is not fail_assign  # original boundary, not a fake Job


@pytest.mark.skipif(os.name != "nt", reason="genuine Windows exited root/Job close failure")
def test_c4a_windows_exited_root_job_close_failure_is_unresolved(tmp_path, c4a_resources):
    resources = c4a_resources
    scope = _c4a_scope(tmp_path, resources.cancel)
    resources.job_close_fault = True
    owner, done, outcome = resources.invoke(lambda: _c4a_command(tmp_path, scope, resources))
    assert done.wait(10)
    resources.joined(owner)
    assert len(resources.processes) == len(resources.jobs) == 1
    assert resources.processes[0].returncode == 0
    assert resources.job_closes == []
    scope.seal()
    snapshot = _c4a_snapshot(scope)
    assert snapshot["state"] == "unresolved"
    assert "job_close_failed" in snapshot["error_codes"]
    # Whether run returned or raised is not the physical proof; root.poll isn't it.
    assert "value" in outcome or "error" in outcome


class _C4aStreamBoundary:
    """Delegate real pipe reads; inject only close/EOF observation boundaries."""

    def __init__(self, stream, entered, release, *, close_fails=False):
        self.stream = stream
        self.entered = entered
        self.release = release
        self.close_fails = close_fails

    def __getattr__(self, name):
        return getattr(self.stream, name)

    def read(self, size):
        value = self.stream.read(size)
        if not value and not self.close_fails:
            self.entered.set()
            assert self.release.wait(15), "test did not release real pipe EOF"
        return value

    def close(self):
        if self.close_fails:
            self.entered.set()
            raise OSError("C4A_PRIVATE stream close fault")
        return self.stream.close()


@pytest.mark.parametrize("fault", ["reader2-start", "stream-close", "join-and-repeat"])
def test_c4a_capture_exit_notification_is_not_join(tmp_path, c4a_resources, fault):
    resources = c4a_resources
    scope = _c4a_scope(tmp_path, resources.cancel)
    entered = threading.Event()
    release = resources.barrier()
    if fault == "reader2-start":
        def fail_second(thread):
            if thread.name == "docking-command-stderr":
                assert any(t.name == "docking-command-stdout" and t.is_alive()
                           for t in resources.threads)
                entered.set()
                raise RuntimeError("C4A_PRIVATE second reader start")
        resources.start_before = fail_second
    else:
        def wrap_stdout(process):
            process.stdout = _C4aStreamBoundary(
                process.stdout, entered, release, close_fails=fault == "stream-close",
            )
        resources.popen_after = wrap_stdout
    owner, done, outcome = resources.invoke(lambda: _c4a_command(
        tmp_path, scope, resources,
        script="import time; time.sleep(0.2); print('done')",
    ))
    assert entered.wait(5)
    assert done.wait(10)
    resources.joined(owner)
    scope.seal()
    snapshot = _c4a_snapshot(scope)
    assert snapshot["state"] == "unresolved"
    code = {"reader2-start": "reader_start_failed", "stream-close": "reader_close_failed",
            "join-and-repeat": "reader_join_failed"}[fault]
    assert code in snapshot["error_codes"]
    if fault == "stream-close":
        assert any(not stream.closed for stream in resources.streams)
    if fault == "join-and-repeat":
        assert any(t.name == "docking-command-stdout" and t.is_alive()
                   for t in resources.threads)
        # The legacy taken flag can produce fallback: it must not clear the receipt.
        try:
            CommandAdapter._take_captured_output(resources.processes[0], enforce_limit=False)
        except Exception:
            pass
        assert _c4a_snapshot(scope)["state"] == "unresolved"
        release.set()
        _c4a_wait(lambda: all(not t.is_alive() for t in resources.threads))
        assert code in _c4a_snapshot(scope)["error_codes"]
    assert "value" in outcome or "error" in outcome


@pytest.mark.skipif(os.name != "nt", reason="genuine Windows spawner finally versus actual exit")
def test_c4a_spawner_finally_notification_is_not_thread_exit(tmp_path, c4a_resources):
    resources = c4a_resources
    scope = _c4a_scope(tmp_path, resources.cancel)
    body_ended = threading.Event()
    release = resources.barrier()

    def after_target(thread):
        if thread.name == "docking-command-spawn":
            body_ended.set()
            assert release.wait(15), "test did not release actual spawner exit"

    resources.thread_after = after_target
    owner, done, outcome = resources.invoke(lambda: _c4a_command(tmp_path, scope, resources))
    assert body_ended.wait(5)
    assert any(t.name == "docking-command-spawn" and t.is_alive() for t in resources.threads)
    assert _c4a_snapshot(scope)["state"] != "settled"
    settler = None
    if done.wait(0.1):
        scope.seal()
        settler, settled, settlement = resources.invoke(scope.settle)
        assert not settled.wait(0.1), "worker finally notification was mistaken for join"
    release.set()
    resources.joined(owner)
    assert "error" not in outcome
    if settler is not None:
        resources.joined(settler)
        assert "error" not in settlement
    else:
        scope.seal()
        scope.settle()
    assert _c4a_snapshot(scope)["state"] == "settled"
    resources.assert_physically_stopped()


@pytest.mark.skipif(os.name == "nt", reason="genuine POSIX Popen/process-group ownership")
@pytest.mark.parametrize("interrupt", ["cancel", "timeout", "normal", "reader2-start"])
def test_c4a_posix_pending_spawn_and_setup_failure_retains_owner(tmp_path, c4a_resources, interrupt):
    resources = c4a_resources
    scope = _c4a_scope(tmp_path, resources.cancel)
    entered = threading.Event()
    release = resources.barrier()

    def before_popen():
        entered.set()
        assert release.wait(15), "test did not release POSIX Popen"

    resources.popen_before = before_popen
    if interrupt == "reader2-start":
        def fail_second(thread):
            if thread.name == "docking-command-stderr":
                raise RuntimeError("C4A_PRIVATE POSIX reader start")
        resources.start_before = fail_second
    owner, done, outcome = resources.invoke(lambda: _c4a_command(
        tmp_path, scope, resources,
        script="print('done')" if interrupt == "normal" else "import time; time.sleep(30)",
        timeout=0.2 if interrupt == "timeout" else 10,
    ))
    assert entered.wait(5)
    assert not done.is_set() and not resources.processes
    assert _c4a_snapshot(scope)["state"] != "settled"
    if interrupt == "cancel":
        resources.cancel.set()
    elif interrupt == "timeout":
        assert not done.wait(0.3), "blocked Popen caller cannot be physically finished"
    release.set()
    resources.joined(owner)
    scope.seal()
    assert len(resources.processes) == 1
    if interrupt == "reader2-start":
        assert "error" in outcome
        assert _c4a_snapshot(scope)["state"] == "unresolved"
        assert "reader_start_failed" in _c4a_snapshot(scope)["error_codes"]
    else:
        if interrupt == "normal":
            assert outcome["value"].returncode == 0
        else:
            assert isinstance(outcome.get("error"),
                              (CommandCancelledError, subprocess.TimeoutExpired))
        scope.settle()
        assert _c4a_snapshot(scope)["state"] == "settled"
        resources.assert_physically_stopped()


@pytest.mark.skipif(os.name == "nt", reason="genuine POSIX descendant process-group proof")
def test_c4a_posix_root_exit_does_not_settle_live_group(tmp_path, c4a_resources):
    resources = c4a_resources
    scope = _c4a_scope(tmp_path, resources.cancel)
    child = "import time; time.sleep(30)"
    script = (
        "import subprocess,sys; subprocess.Popen([sys.executable,'-I','-S','-B','-c',"
        + repr(child)
        + "],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)"
    )
    with pytest.raises(subprocess.TimeoutExpired):
        _c4a_command(tmp_path, scope, resources, script=script, timeout=0.3)
    assert resources.processes[0].returncode == 0
    scope.seal()
    scope.settle()
    assert _c4a_snapshot(scope)["state"] == "settled"
    resources.assert_physically_stopped()


@pytest.mark.skipif(os.name != "nt", reason="genuine Windows descendant Job ownership")
def test_c4a_windows_root_exit_does_not_settle_live_job(tmp_path, c4a_resources):
    resources = c4a_resources
    scope = _c4a_scope(tmp_path, resources.cancel)
    root_exited_with_descendants = threading.Event()
    real_active = resources.base._WindowsJob.has_active_processes

    def observe_active(job):
        active = real_active(job)  # actual QueryInformationJobObject
        if active and resources.processes and resources.processes[0].returncode == 0:
            root_exited_with_descendants.set()
        return active

    resources.patch.setattr(resources.base._WindowsJob, "has_active_processes", observe_active)
    child = "import time; time.sleep(30)"
    script = (
        "import subprocess,sys; subprocess.Popen([sys.executable,'-I','-S','-B','-c',"
        + repr(child)
        + "],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)"
    )
    with pytest.raises(subprocess.TimeoutExpired):
        _c4a_command(tmp_path, scope, resources, script=script, timeout=2)
    assert root_exited_with_descendants.is_set(), "genuine live-descendant prerequisite not reached"
    assert resources.processes[0].returncode == 0
    assert len(resources.jobs) == resources.popen_calls == 1
    scope.seal()
    scope.settle()
    assert _c4a_snapshot(scope)["state"] == "settled"
    resources.assert_physically_stopped()


# SOURCE-candidate regressions only: execution still requires a qualified outer
# owner and an exact parent grant. The existing fixture's finally independently
# rescues actual handles/processes; that rescue must not repair receipt truth.
@pytest.mark.skipif(os.name != "nt", reason="genuine Windows acquired Job before receipt attachment")
def test_c4a_job_attach_failure_after_real_acquisition_cannot_settle(tmp_path, c4a_resources):
    resources = c4a_resources
    scope = _c4a_scope(tmp_path, resources.cancel)
    attachment_reached = threading.Event()
    observed_handles = []

    def fail_before_receipt_write(receipt, job, handle):
        # The fixture's CreateJobObjectW wrapper already owns the genuine handle
        # independently. Do not delegate or mutate receipt/owner state here.
        assert receipt.scope is scope
        assert job.handle == handle and handle in resources.jobs
        observed_handles.append(handle)
        attachment_reached.set()
        raise RuntimeError("C4A_PRIVATE attach before receipt write")

    resources.patch.setattr(resources.base._CommandReceipt, "attach_job", fail_before_receipt_write)
    with pytest.raises(Exception):
        _c4a_command(tmp_path, scope, resources)

    assert attachment_reached.is_set(), "real acquisition/attachment boundary was not reached"
    assert len(resources.jobs) == 1 and observed_handles == resources.jobs
    assert resources.popen_calls == 0 and resources.processes == []
    assert resources.resumes == []
    scope.seal()
    settled = scope.settle()  # potentially blocking; no platform run grant implied
    snapshot = _c4a_snapshot(scope)
    assert settled is False, "failed acquisition registration authorized empty-receipt settlement"
    assert snapshot["state"] != "settled" and snapshot["pending_count"] == 1
    assert snapshot["primary_error_code"] is not None
    # No test-side handle closure before the verdict. Fixture finally attempts
    # every outstanding real handle exactly once, including on assertion failure.


@pytest.mark.skipif(os.name != "nt", reason="genuine suspended Windows child before Popen return failure")
def test_c4a_popen_after_real_child_failure_cannot_settle(tmp_path, c4a_resources):
    resources = c4a_resources
    scope = _c4a_scope(tmp_path, resources.cancel)
    suspended_flags_observed = threading.Event()
    child_before_return = threading.Event()
    actual_children = []
    real_popen = resources.real_popen
    marker = tmp_path / "unreturned-child-resumed.marker"
    original_error = RuntimeError("C4A_PRIVATE child created before Popen return")

    def create_real_suspended_child(*args, **kwargs):
        flags = kwargs.get("creationflags", 0)
        assert flags & getattr(subprocess, "CREATE_SUSPENDED", 0x00000004)
        assert list(args[0][:5]) == [sys.executable, "-I", "-S", "-B", "-c"]
        suspended_flags_observed.set()
        return real_popen(*args, **kwargs)

    def fail_after_real_child(process):
        # resources.popen records this actual Popen and both pipes BEFORE this
        # hook. The production caller has not received the process reference.
        assert process in resources.processes
        assert process.poll() is None
        assert all(stream in resources.streams for stream in (process.stdout, process.stderr))
        actual_children.append(process)
        child_before_return.set()
        raise original_error

    resources.patch.setattr(resources, "real_popen", create_real_suspended_child)
    resources.popen_after = fail_after_real_child
    with pytest.raises(RuntimeError) as caught:
        _c4a_command(
            tmp_path, scope, resources,
            script="from pathlib import Path; Path('unreturned-child-resumed.marker').write_text('yes')",
        )

    assert suspended_flags_observed.is_set() and child_before_return.is_set()
    assert resources.popen_calls == len(resources.processes) == 1
    assert actual_children == resources.processes and len(resources.jobs) == 1
    assert resources.resumes == [] and not marker.exists()
    scope.seal()
    settled = scope.settle()  # outer qualification remains mandatory
    snapshot = _c4a_snapshot(scope)
    assert settled is False, "unreturned real child was omitted from physical settlement"
    assert snapshot["state"] != "settled" and snapshot["pending_count"] == 1
    assert snapshot["primary_error_code"] is not None
    assert resources.resumes == [] and not marker.exists()
    startup_report = caught.value.__cause__
    assert type(startup_report) is RuntimeError
    assert startup_report.__cause__ is original_error
    assert str(startup_report) == (
        "Windows command startup failed; physical settlement is unconfirmed."
    )
    assert "terminated" not in str(startup_report).lower()
    assert "C4A_PRIVATE" not in str(caught.value) + str(startup_report)
    # The existing fixture's finally kills/waits/closes the independently saved
    # child and pipes even if receipt.process is empty or this assertion fails.


@pytest.mark.skipif(os.name != "nt", reason="genuine Windows no-scope startup-report compatibility")
def test_c4a_no_scope_popen_after_keeps_legacy_report_and_cause(tmp_path, c4a_resources):
    resources = c4a_resources
    original_error = RuntimeError("C4A_PRIVATE legacy child before Popen return")
    real_popen = resources.real_popen
    suspended_flags_observed = threading.Event()
    child_before_return = threading.Event()
    actual_children = []
    marker = tmp_path / "legacy-unreturned-child.marker"

    def create_real_suspended_child(*args, **kwargs):
        assert kwargs.get("creationflags", 0) & getattr(subprocess, "CREATE_SUSPENDED", 0x00000004)
        assert list(args[0][:5]) == [sys.executable, "-I", "-S", "-B", "-c"]
        suspended_flags_observed.set()
        return real_popen(*args, **kwargs)

    def fail_after_real_child(process):
        assert process in resources.processes and process.poll() is None
        assert all(stream in resources.streams for stream in (process.stdout, process.stderr))
        actual_children.append(process)
        child_before_return.set()
        raise original_error

    resources.patch.setattr(resources, "real_popen", create_real_suspended_child)
    resources.popen_after = fail_after_real_child
    # Deliberately no CommandOwnershipScope and no scoped command helper.
    with pytest.raises(RuntimeError) as caught:
        CommandAdapter(sys.executable).run(
            [sys.executable, "-I", "-S", "-B", "-c",
             "from pathlib import Path; Path('legacy-unreturned-child.marker').write_text('yes')"],
            cwd=str(tmp_path), timeout=5, cancel_event=resources.cancel,
        )
    assert suspended_flags_observed.is_set() and child_before_return.is_set()
    assert resources.popen_calls == len(resources.processes) == 1
    assert actual_children == resources.processes and len(resources.jobs) == 1
    assert resources.resumes == [] and not marker.exists()
    assert str(caught.value) == "Windows process creation or Job assignment failed before timeout"
    startup_report = caught.value.__cause__
    assert type(startup_report) is RuntimeError
    assert startup_report.__cause__ is original_error
    assert str(startup_report) == (
        "Windows process creation or Job assignment failed; "
        "the suspended process was terminated before execution"
    )
    assert "C4A_PRIVATE" not in str(caught.value) + str(startup_report)
    # This is compatibility evidence only, NOT endorsement of the legacy claim.
    # Existing fixture finally rescues the actual unreturned child and handles.
