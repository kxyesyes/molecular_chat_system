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
