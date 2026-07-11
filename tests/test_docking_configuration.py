import asyncio
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
from src.docking.molecular_docking_service import (
    DockingConfig,
    DockingResult,
    MolecularDockingService,
)
from src.docking.adapters.base import CommandAdapter
from src.docking.adapters.vina_adapter import VinaAdapter


class RecordingVinaAdapter:
    def __init__(self):
        self.calls = []

    def run_config(self, config_path, cwd, timeout=None):
        self.calls.append((Path(config_path), Path(cwd), timeout))
        return subprocess.CompletedProcess(
            [], 1, stdout="", stderr="intentional unit-test stop"
        )


class SleepingVinaAdapter(VinaAdapter):
    def build_run_command(self, config_path: str):
        return [sys.executable, "-c", "import time; time.sleep(0.5)"]


def _decoded_stream(value):
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


def _windows_process_is_active(pid: int) -> bool:
    import ctypes

    process_query_limited_information = 0x1000
    still_active = 259
    handle = ctypes.windll.kernel32.OpenProcess(
        process_query_limited_information,
        False,
        pid,
    )
    if not handle:
        return False
    try:
        exit_code = ctypes.c_ulong()
        if not ctypes.windll.kernel32.GetExitCodeProcess(
            handle,
            ctypes.byref(exit_code),
        ):
            return False
        return exit_code.value == still_active
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


class DockingConfigurationTest(unittest.TestCase):
    def test_configure_accepts_direct_prepare_receptor_command(self):
        with tempfile.TemporaryDirectory(prefix="docking_config_") as tmp:
            root = Path(tmp)
            vina = root / "vina.exe"
            prepare_receptor = root / "prepare_receptor"
            prepare_ligand = root / "mk_prepare_ligand.py"
            for path in (vina, prepare_receptor, prepare_ligand):
                path.write_text("", encoding="utf-8")

            service = MolecularDockingService(
                {
                    "vina_exe": str(vina),
                    "prepare_receptor_cmd": str(prepare_receptor),
                    "prepare_ligand_cmd": str(prepare_ligand),
                }
            )

        self.assertEqual(service.vina_exe, str(vina.resolve()))
        self.assertEqual(service.prepare_receptor_cmd, str(prepare_receptor.resolve()))
        self.assertEqual(service.prepare_ligand_cmd, str(prepare_ligand.resolve()))

    def test_vina_config_is_isolated_to_each_job_directory(self):
        with tempfile.TemporaryDirectory(prefix="docking_jobs_") as tmp:
            root = Path(tmp)
            shared_work_dir = root / "shared"
            shared_work_dir.mkdir()
            service = MolecularDockingService({"vina_timeout_seconds": 12.5})
            service.work_dir = str(shared_work_dir)
            adapter = RecordingVinaAdapter()
            service.vina_adapter = adapter
            config_paths = []

            for job_name in ("docking_job_a", "docking_job_b"):
                job_dir = root / job_name
                job_dir.mkdir()
                receptor = job_dir / "receptor.pdbqt"
                ligand = job_dir / "ligand.pdbqt"
                output = job_dir / "result.pdbqt"
                receptor.write_text("", encoding="utf-8")
                ligand.write_text("", encoding="utf-8")

                success = service.run_vina_docking(
                    str(receptor),
                    str(ligand),
                    DockingConfig(manual_center=True),
                    str(output),
                    job_dir=str(job_dir),
                )

                self.assertFalse(success)
                config_paths.append(job_dir / "config.txt")

            self.assertNotEqual(config_paths[0], config_paths[1])
            self.assertTrue(all(path.is_file() for path in config_paths))
            self.assertFalse((shared_work_dir / "config.txt").exists())
            self.assertEqual(
                adapter.calls,
                [
                    (config_paths[0], config_paths[0].parent, 12.5),
                    (config_paths[1], config_paths[1].parent, 12.5),
                ],
            )

    def test_vina_timeout_is_an_explicit_docking_failure(self):
        with tempfile.TemporaryDirectory(prefix="docking_timeout_") as tmp:
            job_dir = Path(tmp)
            receptor = job_dir / "receptor.pdbqt"
            ligand = job_dir / "ligand.pdbqt"
            output = job_dir / "result.pdbqt"
            receptor.write_text("", encoding="utf-8")
            ligand.write_text("", encoding="utf-8")
            service = MolecularDockingService({"vina_timeout_seconds": 0.05})
            service.vina_adapter = SleepingVinaAdapter(sys.executable)

            with self.assertRaises(subprocess.TimeoutExpired):
                service.run_vina_docking(
                    str(receptor),
                    str(ligand),
                    DockingConfig(manual_center=True),
                    str(output),
                    job_dir=str(job_dir),
                )
            self.assertFalse(output.exists())

    def test_command_adapter_honors_cwd_and_float_timeout(self):
        adapter = CommandAdapter(sys.executable)
        with tempfile.TemporaryDirectory(prefix="command_adapter_") as tmp:
            result = adapter.run(
                [sys.executable, "-c", "import os; print(os.getcwd())"],
                cwd=tmp,
                timeout=1.0,
            )
            self.assertEqual(Path(result.stdout.strip()), Path(tmp))

            with self.assertRaises(subprocess.TimeoutExpired):
                adapter.run(
                    [sys.executable, "-c", "import time; time.sleep(0.5)"],
                    cwd=tmp,
                    timeout=0.05,
                )

    @unittest.skipUnless(os.name == "nt", "Windows process-tree regression")
    def test_command_adapter_timeout_terminates_windows_process_tree(self):
        with tempfile.TemporaryDirectory(prefix="command_tree_") as tmp:
            root = Path(tmp)
            child_script = root / "delayed_marker.py"
            child_pid_file = root / "child.pid"
            marker_file = root / "survived.marker"
            batch_file = root / "launch_child.bat"
            child_script.write_text(
                "\n".join(
                    [
                        "import os",
                        "import sys",
                        "import time",
                        "from pathlib import Path",
                        "Path(sys.argv[1]).write_text(str(os.getpid()), encoding='utf-8')",
                        "print('child-started', flush=True)",
                        "print('child-stderr', file=sys.stderr, flush=True)",
                        "time.sleep(2.0)",
                        "Path(sys.argv[2]).write_text('survived', encoding='utf-8')",
                    ]
                ),
                encoding="utf-8",
            )
            batch_file.write_text(
                (
                    "@echo off\r\n"
                    f'"{sys.executable}" "{child_script}" "%~1" "%~2"\r\n'
                ),
                encoding="utf-8",
            )
            adapter = CommandAdapter(str(batch_file))

            with self.assertRaises(subprocess.TimeoutExpired) as caught:
                adapter.run(
                    adapter.wrap_command(str(child_pid_file), str(marker_file)),
                    cwd=str(root),
                    timeout=0.75,
                )

            self.assertIn("child-started", _decoded_stream(caught.exception.stdout))
            self.assertIn("child-stderr", _decoded_stream(caught.exception.stderr))
            self.assertTrue(child_pid_file.is_file())
            child_pid = int(child_pid_file.read_text(encoding="utf-8"))
            time.sleep(2.25)
            self.assertFalse(marker_file.exists())
            self.assertFalse(_windows_process_is_active(child_pid))

    @unittest.skipUnless(os.name != "nt", "POSIX process-group regression")
    def test_command_adapter_timeout_terminates_posix_process_group(self):
        with tempfile.TemporaryDirectory(prefix="command_group_") as tmp:
            root = Path(tmp)
            child_script = root / "delayed_marker.py"
            launcher_script = root / "launcher.py"
            child_pid_file = root / "child.pid"
            marker_file = root / "survived.marker"
            child_script.write_text(
                "\n".join(
                    [
                        "import os, sys, time",
                        "from pathlib import Path",
                        "Path(sys.argv[1]).write_text(str(os.getpid()), encoding='utf-8')",
                        "print('child-started', flush=True)",
                        "time.sleep(2.0)",
                        "Path(sys.argv[2]).write_text('survived', encoding='utf-8')",
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
            adapter = CommandAdapter(sys.executable)

            with self.assertRaises(subprocess.TimeoutExpired):
                adapter.run(
                    [
                        sys.executable,
                        str(launcher_script),
                        str(child_script),
                        str(child_pid_file),
                        str(marker_file),
                    ],
                    cwd=str(root),
                    timeout=0.75,
                )

            time.sleep(2.25)
            self.assertFalse(marker_file.exists())

    def test_history_persistence_failure_returns_traceability_warning(self):
        with tempfile.TemporaryDirectory(prefix="docking_history_warning_") as tmp:
            root = Path(tmp)
            service = MolecularDockingService()
            service.work_dir = str(root)

            def prepare_file(_input_path, output_path):
                Path(output_path).write_text("", encoding="utf-8")
                return True

            service.prepare_protein = prepare_file
            service.prepare_ligand_from_file = prepare_file
            service.run_vina_docking = lambda *args, **kwargs: True
            service.parse_vina_results = lambda _output_path: [
                DockingResult(
                    ligand_id="pose-1",
                    binding_energy=-7.25,
                    rmsd_lb=0.0,
                    rmsd_ub=0.0,
                    pose_data="",
                )
            ]

            with patch(
                "src.docking.history_index.upsert_history_record",
                side_effect=OSError("history lock unavailable"),
            ):
                result = asyncio.run(
                    service.perform_docking(
                        receptor_file=str(root / "input_receptor.pdbqt"),
                        ligand_input=str(root / "input_ligand.pdbqt"),
                        config=DockingConfig(manual_center=True),
                        input_type="file",
                    )
                )

            self.assertTrue(result["success"])
            self.assertEqual(result["best_pose"]["binding_energy"], -7.25)
            warning = result["warnings"][0]
            self.assertEqual(warning["code"], "history_persistence_failed")
            self.assertEqual(warning["traceability"]["job_id"], result["job_id"])
            self.assertEqual(
                Path(warning["traceability"]["job_dir"]).parent,
                root,
            )

    def test_vina_timeout_can_be_configured_from_environment(self):
        with patch.dict(
            os.environ,
            {"MOLECULAR_DOCKING_VINA_TIMEOUT_SECONDS": "17.5"},
        ):
            service = MolecularDockingService()

        self.assertEqual(service.vina_timeout_seconds, 17.5)


if __name__ == "__main__":
    unittest.main()
