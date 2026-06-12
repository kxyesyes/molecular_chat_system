#!/usr/bin/env python3
"""MedChat deployment health check."""

from __future__ import annotations

import argparse
import importlib
import os
import sqlite3
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def load_env_file(env_path: str | Path = ".env") -> None:
    path = Path(env_path)
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def status_line(ok: bool, label: str, detail: str) -> str:
    marker = "OK" if ok else "FAIL"
    return f"[{marker}] {label}: {detail}"


def check_rdkit() -> tuple[bool, str]:
    try:
        import rdkit  # noqa: F401
        return True, "RDKit import succeeded"
    except Exception as exc:
        return False, f"RDKit import failed: {exc}"


def check_vina() -> tuple[bool, str]:
    configured = os.environ.get("MOLECULAR_DOCKING_VINA", "").strip()
    candidates = [configured] if configured else []
    vina_in_path = shutil_which("vina")
    if vina_in_path:
        candidates.append(vina_in_path)
    if not candidates:
        return False, "No Vina path found (set MOLECULAR_DOCKING_VINA)"

    vina_path = Path(candidates[0]).expanduser()
    if not vina_path.exists():
        return False, f"Vina executable not found: {vina_path}"

    try:
        result = subprocess.run(
            [str(vina_path), "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode not in (0, 1):
            return False, f"Vina exists but '--help' returned {result.returncode}"
        return True, f"{vina_path}"
    except Exception as exc:
        return False, f"Vina check failed at {vina_path}: {exc}"


def check_adfrsuite() -> tuple[bool, str]:
    direct_prepare = os.environ.get("MOLECULAR_DOCKING_PREPARE_RECEPTOR", "").strip()
    if direct_prepare:
        prepare_path = Path(direct_prepare).expanduser()
        if prepare_path.exists():
            return True, f"{prepare_path}"
        return False, f"MOLECULAR_DOCKING_PREPARE_RECEPTOR not found: {prepare_path}"

    adfr_bin = os.environ.get("MOLECULAR_DOCKING_ADFR_BIN", "").strip()
    if not adfr_bin:
        return False, "MOLECULAR_DOCKING_ADFR_BIN is empty (or set MOLECULAR_DOCKING_PREPARE_RECEPTOR)"
    adfr_dir = Path(adfr_bin).expanduser()
    if not adfr_dir.exists():
        return False, f"ADFR bin directory not found: {adfr_dir}"

    candidates = [
        adfr_dir / "prepare_receptor",
        adfr_dir / "prepare_receptor.py",
        adfr_dir / "prepare_receptor.bat",
    ]
    existing = next((path for path in candidates if path.exists()), None)
    if not existing:
        return False, f"prepare_receptor not found in: {adfr_dir}"
    return True, f"{existing}"


def check_ligand_preparation() -> tuple[bool, str]:
    configured = os.environ.get("MOLECULAR_DOCKING_PREPARE_LIGAND", "").strip()
    candidates = [configured] if configured else []
    for cmd in ("mk_prepare_ligand.py", "mk_prepare_ligand"):
        found = shutil_which(cmd)
        if found:
            candidates.append(found)
    if not candidates:
        return False, "mk_prepare_ligand not found (set MOLECULAR_DOCKING_PREPARE_LIGAND)"

    command_path = Path(candidates[0]).expanduser()
    if not command_path.exists():
        return False, f"ligand preparation command not found: {command_path}"
    return True, f"{command_path}"


def check_target_db() -> tuple[bool, str]:
    db_path = os.environ.get("TARGET_DB_PATH", "data/target_db/target_database.sqlite")
    db_file = (PROJECT_ROOT / db_path).resolve() if not Path(db_path).is_absolute() else Path(db_path)
    if not db_file.exists():
        return False, f"SQLite DB not found: {db_file}"
    try:
        conn = sqlite3.connect(str(db_file))
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='targets'")
        row = cur.fetchone()
        conn.close()
        if not row:
            return False, f"targets table missing: {db_file}"
        return True, f"{db_file}"
    except Exception as exc:
        return False, f"SQLite open failed: {exc}"


def check_target_cache() -> tuple[bool, str]:
    cache_path = os.environ.get("TARGET_CACHE_DIR", "data/target_db/cache")
    cache_dir = (PROJECT_ROOT / cache_path).resolve() if not Path(cache_path).is_absolute() else Path(cache_path)
    if not cache_dir.exists():
        return False, f"cache dir not found: {cache_dir}"
    if not cache_dir.is_dir():
        return False, f"cache path is not a directory: {cache_dir}"

    files = [p for p in cache_dir.rglob("*") if p.is_file()]
    return True, f"{cache_dir} ({len(files)} files)"


def check_ollama() -> tuple[bool, str]:
    base_url = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
    url = f"{base_url}/api/tags"
    try:
        with urllib.request.urlopen(url, timeout=3) as response:
            if response.status != 200:
                return False, f"{url} returned HTTP {response.status}"
            return True, f"{url}"
    except urllib.error.URLError as exc:
        return False, f"Ollama not reachable at {url}: {exc}"
    except Exception as exc:
        return False, f"Ollama check failed: {exc}"


def check_modelscope() -> tuple[bool, str]:
    api_key = os.environ.get("MODELSCOPE_API_KEY", "").strip()
    base_url = os.environ.get(
        "MODELSCOPE_BASE_URL",
        "https://api-inference.modelscope.cn/v1/chat/completions",
    ).strip()
    if not api_key:
        return True, "MODELSCOPE_API_KEY not configured (optional)"
    if not base_url:
        return False, "MODELSCOPE_BASE_URL is empty while MODELSCOPE_API_KEY is set"
    return True, f"API key configured; endpoint: {base_url}"


def check_agent_contracts() -> tuple[bool, str]:
    try:
        contracts = importlib.import_module("src.agent.contracts")
        context = contracts.AgentContext(query="health check", trace_id="health-check")
        result = contracts.ToolResult.success_result(
            tool_name="health_check",
            data={"ok": True},
            message="contract ok",
        )
        if context.trace_id != "health-check" or not result.success:
            return False, "Agent contract construction returned unexpected values"
        return True, "AgentContext and ToolResult are available"
    except Exception as exc:
        return False, f"Agent contracts import failed: {exc}"


def check_agent_tool_registry() -> tuple[bool, str]:
    try:
        from src.agent.tools.base_tool import execute_tool_compat

        class _HealthTool:
            name = "health_tool"

            def execute(self, query: str) -> dict:
                return {"success": True, "message": "ok", "data": {"query": query}}

        result = execute_tool_compat(_HealthTool(), "CCO")
        if not result.success or result.tool_name != "health_tool":
            return False, "Tool compatibility wrapper returned unexpected result"
        return True, "legacy tool compatibility wrapper is available"
    except Exception as exc:
        return False, f"Agent tool registry check failed: {exc}"


def check_agent_components() -> tuple[bool, str]:
    try:
        from src.agent.contracts import AgentContext
        from src.agent.orchestrators import WorkflowOrchestrator
        from src.agent.planning import TaskPlanner
        from src.agent.runtime.event_bus import AgentEventBus
        from src.agent.skills.skill_registry import SkillRegistry

        registry = SkillRegistry()
        target_skill = registry.get_skill_by_name("target_driven_design")
        if target_skill is None:
            return False, "target_driven_design skill is not registered"

        context = AgentContext(
            query="基于 PDE5 设计 20 个类药候选分子",
            trace_id="health-agent",
            active_skill="target_driven_design",
        )
        plan = TaskPlanner().plan(context)
        if plan.workflow_name != "target_driven_design" or len(plan.steps) < 5:
            return False, "TaskPlanner did not produce the target-driven workflow"

        WorkflowOrchestrator(event_bus=AgentEventBus())
        return True, f"planner, orchestrator and {len(registry.skills)} skills are available"
    except Exception as exc:
        return False, f"Agent component check failed: {exc}"


def check_task_runtime() -> tuple[bool, str]:
    try:
        from src.task_runtime import TaskManager

        with tempfile.TemporaryDirectory(prefix="medchat_task_health_") as tmp:
            manager = TaskManager(db_path=Path(tmp) / "tasks.sqlite", max_workers=1)
            record = manager.submit(
                "health_check",
                {"value": 1},
                lambda payload: {"value": payload["value"], "artifacts": []},
            )
            import time

            for _ in range(50):
                finished = manager.get(record.task_id)
                if finished.status.value in {"succeeded", "failed"}:
                    break
                time.sleep(0.02)
            else:
                return False, "task runtime did not complete a local health task"
            if finished.status.value != "succeeded":
                return False, f"health task failed: {finished.error}"
        return True, "SQLite task runtime is available"
    except Exception as exc:
        return False, f"Task runtime check failed: {exc}"


def check_supervisor_agent() -> tuple[bool, str]:
    try:
        from src.agent.supervisor import SupervisorAgent

        supervisor = SupervisorAgent(tools={})
        plan = supervisor.plan(
            "Design PDE5 drug-like molecules and evaluate docking",
            skill_name="target_driven_design",
        )
        if len(plan.get("steps", [])) < 5:
            return False, "Supervisor did not create a multi-step target-driven plan"
        return True, f"Supervisor plan has {len(plan['steps'])} steps"
    except Exception as exc:
        return False, f"Supervisor Agent check failed: {exc}"


def check_reverse_target_data() -> tuple[bool, str]:
    data_dir = resolve_project_path(os.environ.get("REVERSE_TARGET_DATA_DIR", "data/reverse_target"))
    required_any = [
        data_dir / "chembl_data_with_fps.tsv",
        data_dir / "chembl_training_data.tsv",
    ]
    required_fps = [
        data_dir / "morgan_fingerprints.npy",
        data_dir / "maccs_fingerprints.npy",
    ]
    if not data_dir.exists():
        return False, f"reverse target data dir not found: {data_dir}"
    has_table = any(path.exists() for path in required_any)
    missing_fps = [path.name for path in required_fps if not path.exists()]
    if not has_table:
        return False, f"missing ChEMBL table in {data_dir}"
    if missing_fps:
        return False, f"missing fingerprint files in {data_dir}: {', '.join(missing_fps)}"
    return True, f"{data_dir}"


def check_activity_models() -> tuple[bool, str]:
    models_dir = resolve_project_path(os.environ.get("ACTIVITY_MODEL_DIR", "data/activity/models"))
    if not models_dir.exists():
        return False, f"activity model dir not found: {models_dir}"
    patterns = ("*.pt", "*.pkl", "*.joblib", "*.json")
    model_files = []
    for pattern in patterns:
        model_files.extend(models_dir.glob(pattern))
    if not model_files:
        return False, f"no activity model files found in {models_dir}"
    return True, f"{models_dir} ({len(model_files)} files)"


def check_rag_index() -> tuple[bool, str]:
    index_path = resolve_project_path(os.environ.get("RAG_INDEX_PATH", "data/molecular_faiss_index.index"))
    if not index_path.exists():
        return False, f"RAG FAISS index not found: {index_path}"
    if not index_path.is_file():
        return False, f"RAG index path is not a file: {index_path}"
    return True, f"{index_path}"


def check_data_registry() -> tuple[bool, str]:
    registry_path = PROJECT_ROOT / "data" / "REGISTRY.md"
    if not registry_path.exists():
        return False, f"data registry not found: {registry_path}"
    text = registry_path.read_text(encoding="utf-8", errors="ignore")
    required_terms = ["Target DB", "Reverse Target Data", "Activity Models", "RAG Index"]
    missing = [term for term in required_terms if term not in text]
    if missing:
        return False, f"{registry_path} missing terms: {', '.join(missing)}"
    return True, f"{registry_path}"


def check_sample_assets() -> tuple[bool, str]:
    sample_dir = PROJECT_ROOT / "data" / "samples"
    expected = [sample_dir / "5.sdf", sample_dir / "MAGL_5zun.pdb"]
    missing = [path.name for path in expected if not path.exists()]
    if missing:
        return False, f"missing sample assets in {sample_dir}: {', '.join(missing)}"
    return True, f"{sample_dir} ({len(expected)} files)"


def check_writable_directories() -> tuple[bool, str]:
    dirs = [
        resolve_project_path(os.environ.get("MEDCHAT_LOG_DIR", "logs")),
        resolve_project_path(os.environ.get("MEDCHAT_TEMP_DOCKING_DIR", "temp_docking")),
        resolve_project_path(os.environ.get("MEDCHAT_SCRATCH_DIR", "scratch")),
    ]
    failures = []
    for directory in dirs:
        if not directory.exists():
            failures.append(f"{directory} missing")
            continue
        if not directory.is_dir():
            failures.append(f"{directory} is not a directory")
            continue
        try:
            with tempfile.NamedTemporaryFile(prefix=".health_", dir=directory, delete=True):
                pass
        except Exception as exc:
            failures.append(f"{directory} not writable: {exc}")
    if failures:
        return False, "; ".join(failures)
    return True, ", ".join(str(path) for path in dirs)


def check_systemd_service() -> tuple[bool, str]:
    service_path = resolve_project_path(os.environ.get("MEDCHAT_SYSTEMD_SERVICE", "deployment/medchat.service"))
    if not service_path.exists():
        return False, f"systemd service file not found: {service_path}"
    text = service_path.read_text(encoding="utf-8", errors="ignore")
    required = ["WorkingDirectory=", "EnvironmentFile=", "ExecStart="]
    missing = [item for item in required if item not in text]
    if missing:
        return False, f"{service_path} missing keys: {', '.join(missing)}"
    return True, f"{service_path}"


def check_nginx_config() -> tuple[bool, str]:
    nginx_path = resolve_project_path(os.environ.get("MEDCHAT_NGINX_CONFIG", "deployment/nginx-medchat.conf"))
    if not nginx_path.exists():
        return False, f"nginx config file not found: {nginx_path}"
    text = nginx_path.read_text(encoding="utf-8", errors="ignore")
    required = ["proxy_pass", "location /ws", "Upgrade", "Connection"]
    missing = [item for item in required if item not in text]
    if missing:
        return False, f"{nginx_path} missing Web/proxy keys: {', '.join(missing)}"
    return True, f"{nginx_path}"


def resolve_project_path(path_value: str | Path) -> Path:
    path = Path(path_value).expanduser()
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


def shutil_which(cmd: str) -> str | None:
    # Keep this local to avoid importing shutil in the module scope only for one use.
    import shutil

    return shutil.which(cmd)


def run_checks(strict: bool) -> int:
    checks: list[tuple[str, Callable[[], tuple[bool, str]]]] = [
        ("RDKit", check_rdkit),
        ("AutoDock Vina", check_vina),
        ("ADFRsuite", check_adfrsuite),
        ("Ligand Preparation", check_ligand_preparation),
        ("Target DB", check_target_db),
        ("Target Cache", check_target_cache),
        ("Ollama", check_ollama),
        ("ModelScope", check_modelscope),
        ("Agent Contracts", check_agent_contracts),
        ("Agent Tool Registry", check_agent_tool_registry),
        ("Agent Components", check_agent_components),
        ("Task Runtime", check_task_runtime),
        ("Supervisor Agent", check_supervisor_agent),
        ("Reverse Target Data", check_reverse_target_data),
        ("Activity Models", check_activity_models),
        ("RAG Index", check_rag_index),
        ("Data Registry", check_data_registry),
        ("Sample Assets", check_sample_assets),
        ("Writable Directories", check_writable_directories),
        ("systemd Service", check_systemd_service),
        ("nginx Config", check_nginx_config),
    ]

    failures = 0
    print("MedChat deployment health check")
    print(f"Project root: {PROJECT_ROOT}")

    for label, check_func in checks:
        ok, detail = check_func()
        print(status_line(ok, label, detail))
        if not ok:
            failures += 1

    print(f"Summary: {len(checks) - failures}/{len(checks)} checks passed")
    if failures and strict:
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="MedChat deployment health check")
    parser.add_argument("--env-file", default=os.environ.get("MEDCHAT_ENV_FILE", ".env"))
    parser.add_argument("--strict", action="store_true", help="Return non-zero if any check fails")
    args = parser.parse_args()

    load_env_file(args.env_file)
    return run_checks(strict=args.strict)


if __name__ == "__main__":
    sys.exit(main())
