import os
import importlib.util
import io
import contextlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


class DeploymentAssetsTest(unittest.TestCase):
    def test_deployment_files_exist(self):
        expected = [
            ".env.example",
            "config/deployment.yaml",
            "data/REGISTRY.md",
            "data/samples/5.sdf",
            "data/samples/MAGL_5zun.pdb",
            "deployment/medchat.service",
            "deployment/nginx-medchat.conf",
            "deployment/README.md",
            "deployment/requirements.txt",
            "deployment/docking_tools.md",
            "scripts/health_check.py",
            "src/web/static/knowledge/cadd_interactive_radial.html",
        ]

        for relative_path in expected:
            self.assertTrue((PROJECT_ROOT / relative_path).exists(), relative_path)

    def test_legacy_target_reverse_is_archived_only(self):
        legacy_dir = PROJECT_ROOT / "archive" / "legacy_target_reverse"
        self.assertTrue(legacy_dir.exists())

        forbidden_markers = ("src.target_reverse", "legacy_target_reverse")
        for path in (PROJECT_ROOT / "src").rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="ignore")
            for marker in forbidden_markers:
                self.assertNotIn(marker, text, f"{marker} should not be imported by runtime code: {path}")

    def test_app_config_expands_environment_placeholders(self):
        from src.web.app import MolecularChatApp

        with tempfile.TemporaryDirectory(prefix="medchat_config_") as tmp:
            config_path = Path(tmp) / "config.yaml"
            config_path.write_text(
                """
ollama:
  base_url: "${MEDCHAT_TEST_OLLAMA:-http://default.invalid}"
docking:
  root_dir: "${MEDCHAT_TEST_DOCKING_ROOT:-/fallback/docking}"
""",
                encoding="utf-8",
            )

            old_value = os.environ.get("MEDCHAT_TEST_OLLAMA")
            os.environ["MEDCHAT_TEST_OLLAMA"] = "http://ollama.example:11434"
            try:
                config = MolecularChatApp(config_path=str(config_path)).config
            finally:
                if old_value is None:
                    os.environ.pop("MEDCHAT_TEST_OLLAMA", None)
                else:
                    os.environ["MEDCHAT_TEST_OLLAMA"] = old_value

        self.assertEqual(config["ollama"]["base_url"], "http://ollama.example:11434")
        self.assertEqual(config["docking"]["root_dir"], "/fallback/docking")

    def test_health_check_help_runs(self):
        result = subprocess.run(
            [sys.executable, "scripts/health_check.py", "--help"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )

        self.assertIn("MedChat deployment health check", result.stdout)

    def test_main_uses_append_rotating_file_logging(self):
        source = (PROJECT_ROOT / "main.py").read_text(encoding="utf-8")

        self.assertIn("RotatingFileHandler", source)
        self.assertNotIn('FileHandler("logs/app.log", encoding=\'utf-8\', mode=\'w\')', source)

    def test_quality_workflow_covers_reproducible_offline_gates(self):
        workflow_path = PROJECT_ROOT / ".github" / "workflows" / "quality.yml"

        self.assertTrue(workflow_path.is_file())
        source = workflow_path.read_text(encoding="utf-8")
        required_commands = [
            "python -m pytest ${{ matrix.pytest_target }} -q -p no:cacheprovider",
            'pytest_target: "tests/agent"',
            'pytest_target: "tests/sandbox_broker"',
            'pytest_target: "tests/task_runtime"',
            'pytest_target: "tests --ignore=tests/agent --ignore=tests/sandbox_broker --ignore=tests/task_runtime"',
            "python -m compileall -q src scripts",
            "find tests -maxdepth 1 -type f -name '*_test.js'",
            'node "$test_file"',
            "BEGIN (RSA|OPENSSH) PRIVATE KEY",
        ]
        for command in required_commands:
            self.assertIn(command, source)
        self.assertIn('python-version: "3.10"', source)
        self.assertIn("git grep -IlE", source)
        self.assertNotIn("git grep -nE", source)
        self.assertNotIn("secrets.", source)
        self.assertNotIn("OPENAI_COMPATIBLE_API_KEY", source)

    def test_dependency_files_declare_distinct_supported_profiles(self):
        development = (PROJECT_ROOT / "requirements.txt").read_text(encoding="utf-8")
        deployment = (PROJECT_ROOT / "deployment" / "requirements.txt").read_text(
            encoding="utf-8"
        )

        self.assertIn("CI / CPU development profile", development)
        self.assertIn("not interchangeable", development)
        self.assertIn("CUDA 12.1 deployment profile", deployment)
        self.assertIn("not interchangeable", deployment)

    def test_health_check_reports_extended_deployment_categories(self):
        spec = importlib.util.spec_from_file_location(
            "health_check", PROJECT_ROOT / "scripts" / "health_check.py"
        )
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        health_check = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(health_check)

        with tempfile.TemporaryDirectory(prefix="medchat_health_") as tmp:
            root = Path(tmp)
            (root / "data" / "target_db" / "cache").mkdir(parents=True)
            (root / "data" / "reverse_target").mkdir(parents=True)
            (root / "data" / "activity" / "models").mkdir(parents=True)
            (root / "data" / "samples").mkdir(parents=True)
            (root / "deployment").mkdir()
            (root / "logs").mkdir()
            (root / "temp_docking").mkdir()
            (root / "scratch").mkdir()

            db_path = root / "data" / "target_db" / "target_database.sqlite"
            import sqlite3
            conn = sqlite3.connect(db_path)
            conn.execute("CREATE TABLE targets (id INTEGER PRIMARY KEY)")
            conn.commit()
            conn.close()

            for rel_path in [
                "data/reverse_target/chembl_data_with_fps.tsv",
                "data/reverse_target/morgan_fingerprints.npy",
                "data/reverse_target/maccs_fingerprints.npy",
                "data/activity/models/model_demo.pt",
                "data/molecular_faiss_index.index",
                "data/samples/5.sdf",
                "data/samples/MAGL_5zun.pdb",
                "deployment/medchat.service",
                "deployment/nginx-medchat.conf",
            ]:
                (root / rel_path).write_text("demo", encoding="utf-8")
            (root / "data" / "REGISTRY.md").write_text(
                "Target DB\nTarget Cache\nReverse Target Data\nActivity Models\nRAG Index\nSample Assets\n",
                encoding="utf-8",
            )

            old_root = health_check.PROJECT_ROOT
            health_check.PROJECT_ROOT = root
            try:
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    health_check.run_checks(strict=False)
            finally:
                health_check.PROJECT_ROOT = old_root

        text = output.getvalue()
        for label in [
            "Ollama",
            "ModelScope",
            "Agent Contracts",
            "Agent Tool Registry",
            "Agent Components",
            "Ligand Preparation",
            "Reverse Target Data",
            "Activity Models",
            "RAG Index",
            "Data Registry",
            "Sample Assets",
            "Writable Directories",
            "systemd Service",
            "nginx Config",
        ]:
            self.assertIn(label, text)


if __name__ == "__main__":
    unittest.main()
