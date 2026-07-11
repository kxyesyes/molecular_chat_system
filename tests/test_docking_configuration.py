import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
from src.docking.molecular_docking_service import MolecularDockingService
from src.docking.molecular_docking_service import DockingConfig
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

    def test_vina_timeout_can_be_configured_from_environment(self):
        with patch.dict(
            os.environ,
            {"MOLECULAR_DOCKING_VINA_TIMEOUT_SECONDS": "17.5"},
        ):
            service = MolecularDockingService()

        self.assertEqual(service.vina_timeout_seconds, 17.5)


if __name__ == "__main__":
    unittest.main()
