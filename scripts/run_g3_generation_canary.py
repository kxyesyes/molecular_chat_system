"""Run the portable G3 generation check in one owned, cold Python child."""
import asyncio
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parents[1]
SOURCES = {
    "canary": "scripts/g3_generation_canary.py",
    "launcher": "scripts/run_g3_generation_canary.py",
    "client": "src/system/ollama_model.py",
    "generator": "src/agent/tools/llm_molecular_generator.py",
    "adapter": "src/agent/tooling/generation_ranking_contract.py",
    "ownership": "src/agent/runtime/worker_ownership.py",
    "harness": "src/agent/scientific_session/decision_execution.py",
}
REPORT_LIMIT = 64 * 1024
HELP = """Usage: python scripts/run_g3_generation_canary.py [--real]
Default: one real adapter invocation with scripted HTTP and RDKit, offline.
--real   Send one generation campaign (up to five POSTs) to an existing
         Ollama at 127.0.0.1:11434. Ollama may load gmm-llama:latest.
Exit 0 requires ten valid, unique candidates and settled local ownership.
"""


def _source_hashes():
    return {key: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
            for key, path in SOURCES.items()}


def _child(real, started, deadline):
    # Discard diagnostic output including native fd writes before scientific
    # imports. Only the encoded, closed report is sent to the parent's pipe.
    with os.fdopen(os.dup(1), "wb") as output, open(os.devnull, "wb") as sink:
        os.dup2(sink.fileno(), 1)
        os.dup2(sink.fileno(), 2)
        workdir = Path.cwd()
        for key in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "TMP", "TEMP", "TMPDIR"):
            os.environ[key] = str(workdir)
        for key, name in (("MEDCHAT_USER_CONFIG_DIR", "config"), ("MEDCHAT_LLM_LOCK_DIR", "locks"),
                          ("MEDCHAT_ENV_FILE", "absent.env"), ("MEDCHAT_AGENT_SESSION_DB", "sessions.sqlite")):
            os.environ[key] = str(workdir / name)

        def restrict_network(event, args):
            if event == "socket.connect":
                address = args[1]
                if not real or address != ("127.0.0.1", 11434):
                    raise PermissionError("Disallowed canary connection")
            elif event == "socket.getaddrinfo":
                if not real or args[:2] != ("127.0.0.1", 11434):
                    raise PermissionError("Disallowed canary lookup")

        async def campaign():
            # Windows asyncio initializes its own private socketpair at loop
            # creation, before any application/provider import or dispatch.
            sys.addaudithook(restrict_network)
            sys.path.insert(0, str(ROOT))
            from scripts.g3_generation_canary import encode_report, run_canary

            report = await run_canary(real=real, clock=time.monotonic,
                                      started_at=started, outer_deadline=deadline,
                                      cancelled=lambda: False, source_hashes=_source_hashes())
            return encode_report(report)

        output.write(asyncio.run(campaign()))


def validated_output(child, *, real):
    from scripts.g3_generation_canary import encode_report

    if child.returncode or child.stderr or not child.stdout or len(child.stdout) > REPORT_LIMIT:
        raise ValueError("Invalid canary frame")
    report = json.loads(child.stdout)
    encoded = encode_report(report)
    expected = "local_ollama" if real else "scripted"
    if report["transport_kind"] != expected or report["source_hashes"] != _source_hashes():
        raise ValueError("Invalid canary origin")
    return encoded


def _command_owner():
    # Import just the public process owner. Importing src.docking constructs
    # its unrelated global docking service and probes the user's toolchain.
    spec = importlib.util.spec_from_file_location("g3_command_owner", ROOT / "src/docking/adapters/base.py")
    commands = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(commands)
    # This module instance belongs only to this launcher. Keep G3's combined
    # raw pipe budget at 64 KiB before decoding on either OS.
    commands._COMMAND_OUTPUT_LIMIT_BYTES = REPORT_LIMIT
    return commands


def run(*, real):
    started = time.monotonic()
    deadline = started + 210.0
    commands = _command_owner()
    directory = tempfile.mkdtemp(prefix="medchat-g3-")
    scope = commands.CommandOwnershipScope(task_id="g3", output_root=directory)
    adapter = commands.CommandAdapter(sys.executable, ownership_scope=scope)
    args = ["-I", "-B", "-c",
            "import runpy,sys; runpy.run_path(sys.argv[1])['_child']("
            "sys.argv[2]=='real',float(sys.argv[3]),float(sys.argv[4]))",
            str(Path(__file__).resolve()), "real" if real else "offline", str(started), str(deadline)]
    failure = None
    try:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired("canary", 210)
        child = adapter.run(adapter.wrap_command(*args), cwd=directory, timeout=remaining)
        encoded = validated_output(child, real=real)
    except subprocess.TimeoutExpired:
        failure = "deadline"
    except (Exception, KeyboardInterrupt):
        failure = "child_failed"
    finally:
        scope.seal()
        if scope.settle():
            shutil.rmtree(directory)
        else:
            failure = "ownership_unconfirmed"
    if failure is None and time.monotonic() >= deadline:
        failure = "deadline"
    if failure:
        return json.dumps({"status": failure, "remote_state": "uncertain" if real else "not_contacted"}).encode()
    return encoded


def main(argv):
    if argv in (["-h"], ["--help"]):
        print(HELP, end="")
        return 0
    if argv not in ([], ["--real"]):
        print("Invalid arguments. Use --help.", file=sys.stderr)
        return 2
    sys.path.insert(0, str(ROOT))
    try:
        encoded = run(real=bool(argv))
    except (Exception, KeyboardInterrupt):
        encoded = b'{"status":"launcher_failed"}'
    print(encoded.decode("ascii"))
    return 0 if json.loads(encoded)["status"] == "complete10" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
