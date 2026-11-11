"""Run the historical B1 acceptance matrix against the current Agent and Web profiles."""

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parents[1]
ROWS = json.loads(Path(__file__).with_name("b1_acceptance_cases.json").read_text(encoding="utf8"))
FIXTURES = ("real_agent_cases.jsonl", "golden_scientific_cases.jsonl", "diverse_scientific_cases.jsonl")
ROW_TIMEOUT = 720


class CampaignChanged(ValueError):
    """The configured inputs changed after the campaign started."""


def campaign_digest():
    """Identify the selectors, their test modules, runner and copied evaluation fixtures."""
    files = {node.split("::", 1)[0] for targets in ROWS for node in targets}
    files.update({"scripts/b1_acceptance_cases.json", "scripts/run_b1_acceptance.py"})
    files.update(f"data/agent_evals/{name}" for name in FIXTURES)
    digest = hashlib.sha256()
    for name in sorted(files):
        digest.update(name.encode("utf8") + b"\0")
        digest.update(hashlib.sha256((ROOT / name).read_bytes().replace(b"\r\n", b"\n")).digest())
    return digest.hexdigest()


def parse_rows(value):
    selected = set()
    for item in value.split(","):
        ends = item.split("-")
        if len(ends) > 2 or not all(x.isascii() and x.isdigit() for x in ends):
            raise ValueError("Expected row numbers or inclusive ranges")
        first, last = int(ends[0]), int(ends[-1])
        if not 1 <= first <= last <= len(ROWS):
            raise ValueError("Row outside the campaign")
        new = range(first, last + 1)
        if not selected.isdisjoint(new):
            raise ValueError("Duplicate row")
        selected.update(new)
    return sorted(selected)


class RowResults:
    def __init__(self):
        self.collected = self.passed = self.skipped = 0
        self.failures = []
        self.timings = {}

    def pytest_collection_finish(self, session):
        self.collected = len(session.items)

    def pytest_deselected(self, items):
        if items:
            self.failures.append(["collection", "unexpected_deselection"])

    def pytest_collectreport(self, report):
        if report.skipped or report.failed:
            self.failures.append([report.nodeid.split("[")[0], "collection"])

    def pytest_runtest_logreport(self, report):
        if report.failed:
            self.failures.append([report.nodeid.split("[")[0], report.when])
        elif report.skipped:
            self.skipped += 1
        elif report.when == "call":
            self.passed += 1
            for key, value in report.user_properties:
                if key in {"web_turn_ns", "adapter_call_ns"} and type(value) is int and value >= 0:
                    self.timings.setdefault(key, []).append(value)


def worker(number, collect, expected_digest):
    # This process starts in the parent's fresh temporary directory.
    if campaign_digest() != expected_digest:
        raise CampaignChanged("Campaign inputs changed")
    root = Path.cwd()
    for key in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "TMP", "TEMP", "TMPDIR"):
        os.environ[key] = str(root)
    os.environ.update({
        "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTEST_ADDOPTS": "", "PYTEST_PLUGINS": "",
        "PYTHONPATH": os.pathsep.join([str(ROOT), str(ROOT / "tests/agent")]),
        "MOLECULAR_CHAT_CONFIG": str(root / "missing.yaml"),
        "MEDCHAT_ENV_FILE": str(root / "not-loaded.env"),
        "MEDCHAT_USER_CONFIG_DIR": str(root / "user-config"),
        "MEDCHAT_LLM_LOCK_DIR": str(root / "locks"),
        "MEDCHAT_AGENT_SESSION_DB": str(root / "sessions.sqlite"),
        "AGENT_STATE_DB": str(root / "agent.sqlite"),
        "MEDCHAT_TASK_DB_PATH": str(root / "tasks.sqlite"),
        "TARGET_DB_PATH": str(root / "targets.sqlite"),
        "TARGET_CACHE_DIR": str(root / "target-cache"),
        "MEDCHAT_TASK_BACKEND": "local",
        "MEDCHAT_RUN_FAMILY_REAL_ACCEPTANCE": "0", "RUN_REAL_TARGET_SEARCH": "0",
    })
    sys.path[:0] = [str(ROOT), str(ROOT / "tests/agent")]
    fixtures = Path("data/agent_evals")
    fixtures.mkdir(parents=True)
    for name in FIXTURES:
        shutil.copyfile(ROOT / fixtures / name, fixtures / name)

    connect = socket.socket.connect

    def offline_connect(sock, address):
        # The Windows asyncio wakeup socketpair uses a loopback connection.
        caller = sys._getframe(1).f_code
        if caller.co_name == "_fallback_socketpair" and caller.co_filename == socket.__file__:
            return connect(sock, address)
        raise OSError("B1 acceptance is offline")

    def offline(*args, **kwargs):
        raise OSError("B1 acceptance is offline")

    socket.socket.connect = offline_connect
    socket.socket.connect_ex = socket.create_connection = offline
    import pytest

    results = RowResults()
    args = [str(ROOT / target) for target in ROWS[number - 1]]
    if collect:
        args.append("--collect-only")
    rc = int(pytest.main([*args, "-p", "no:terminal", "-p", "no:cacheprovider", "-p", "anyio.pytest_plugin"],
                         plugins=[results]))
    record = {"row": number, "campaign_sha256": campaign_digest(), "collected": results.collected, "passed": results.passed,
              "skipped": results.skipped, "failures": results.failures[:12], "pytest_exit": rc,
              "timings_ns": results.timings}
    Path("receipt.json").write_text(json.dumps(record), encoding="utf8")
    return rc


def validate_receipt(record, number, collect, returncode, expected_digest):
    expected = {"row", "campaign_sha256", "collected", "passed", "skipped", "failures", "pytest_exit", "timings_ns"}
    if not isinstance(record, dict) or set(record) != expected:
        raise ValueError("Invalid row receipt")
    if any(type(record[k]) is not int or record[k] < 0
           for k in ("row", "pytest_exit", "collected", "passed", "skipped")):
        raise ValueError("Invalid row counts")
    if record["campaign_sha256"] != expected_digest:
        raise CampaignChanged("Campaign inputs changed during row")
    if record["row"] != number or record["pytest_exit"] != returncode:
        raise ValueError("Unexpected row or pytest exit")
    if not isinstance(record["failures"], list) or not isinstance(record["timings_ns"], dict):
        raise ValueError("Invalid pytest result")
    ran = record["passed"] + record["skipped"]
    if returncode or not record["collected"] or record["failures"] or record["skipped"]:
        return "failed"
    if collect:
        return "collected" if ran == 0 else "failed"
    return "passed" if record["passed"] and ran == record["collected"] else "failed"


def command_owner_module():
    # Importing src.docking also constructs its global service. This supervisor
    # only needs the standalone process primitives before entering the isolated child.
    spec = importlib.util.spec_from_file_location("b1_command_owner", ROOT / "src/docking/adapters/base.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def run_row(number, collect, expected_digest):
    commands = command_owner_module()
    directory = Path(tempfile.mkdtemp(prefix="medchat-b1-"))
    owner = commands.CommandOwnershipScope(task_id=f"b1-{number}", output_root=directory)
    cmd = commands.CommandAdapter(sys.executable, ownership_scope=owner)
    entry = ("import runpy,sys; mod=runpy.run_path(sys.argv[1]); "
             "sys.exit(mod['worker'](int(sys.argv[2]), sys.argv[3]=='collect', sys.argv[4]))")
    try:
        proc = cmd.run([sys.executable, "-I", "-B", "-c", entry, str(Path(__file__).resolve()),
                        str(number), "collect" if collect else "run", expected_digest],
                       cwd=str(directory), timeout=ROW_TIMEOUT)
        with (directory / "receipt.json").open("rb") as stream:
            payload = stream.read(16385)
        if len(payload) > 16384:
            raise ValueError("Oversized row receipt")
        record = json.loads(payload)
        record["status"] = validate_receipt(record, number, collect, proc.returncode, expected_digest)
        return record
    finally:
        owner.seal()
        if not owner.settle():
            raise RuntimeError("B1 child ownership unresolved")
        shutil.rmtree(directory)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", default=f"1-{len(ROWS)}", help="For example 1,26-37")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--list", action="store_true", help="Print current pytest selectors")
    action.add_argument("--collect-only", action="store_true")
    options = parser.parse_args()
    try:
        rows = parse_rows(options.rows)
    except ValueError as error:
        parser.error(str(error))
    sys.path.insert(0, str(ROOT))
    expected_digest = campaign_digest()
    passed = True
    for number in rows:
        if options.list:
            print(json.dumps({"row": number, "targets": ROWS[number - 1]}))
            continue
        started = time.monotonic()
        try:
            if campaign_digest() != expected_digest:
                raise CampaignChanged("Campaign inputs changed between rows")
            result = run_row(number, options.collect_only, expected_digest)
        except Exception as error:
            print(json.dumps({"row": number, "status": "runner_error", "error": type(error).__name__}),
                  flush=True)
            return 1
        result["seconds"] = round(time.monotonic() - started, 2)
        print(json.dumps(result), flush=True)
        passed = passed and result["status"] in {"collected", "passed"}
    return int(not passed)


if __name__ == "__main__":
    raise SystemExit(main())
