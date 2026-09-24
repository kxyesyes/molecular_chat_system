# Recovery parity regressions — specification and implementation plan

**Goal:** Close the two permanent regression gaps in package 6 E5/G3 without changing existing correct recovery behavior.

**Architecture:** Exercise real Supervisor/session/orchestrator and SQLite persistence with existing synthetic tools and temporary databases. Exercise artifact restoration twice from the same in-memory checkpoint, with nested mutation checks. These are characterization tests, not a claimed RED/product-bug fix.

**Tech stack:** Existing Python/pytest fixtures, SQLiteAgentStateStore, WorkflowOrchestrator; no new dependencies.

## Frozen scope and acceptance

- Branch: `codex/recovery-parity-regressions`; base: `3a682649c3392727f642f33f56bd3f94c7d77707`; initial worktree/index clean.
- Read-only audit source: sibling `historical-residual-disposition/docs/handoff/historical-residual-disposition.md`, E5/G3. Its historical sandbox `artifact_failed` finding remains unresolved; these tests do not reproduce or close it.
- Allowed writes: this document, `tests/agent/test_workflow_resume.py`, and `tests/agent/test_delegated_session_lifecycle.py` only.
- No production changes, weakened expectations, global sleep/timeout changes, staging, commit, push, PR, merge, models, servers, real-service switches, secrets or production assets. Deliver unstaged for parent independent review.
- On any test failure, stop and diagnose before considering further work; do not expand implementation scope.

## Existing behavior inspected

- `runtime/run_session.py::start` writes the orchestrator workflow version for owned/unowned runs; `_save_session_checkpoint` writes the same version.
- `runtime/delegated_executor.py::prepare` binds the request orchestrator version to `SpecialistDispatch.claim_run`.
- `orchestrators/workflow.py::for_request` preserves the custom version; `_compatible_checkpoint` checks it alongside input/tool/adapter/model identity.
- `persistence/sqlite_store.py` persists run/checkpoint workflow_version and reads latest checkpoints with a rowid tie-break.
- `contracts/domain.py::WorkflowArtifact.from_dict` deep-copies artifact mappings; `_result_from_checkpoint` invokes it. This does not promise deepcopy of every result field or `to_dict`.

## Implementation and verification plan

- [x] Read AGENTS, project standards, E5/G3 audit, relevant tests and the actual execution/persistence/restoration paths; verify branch/base/status.
- [x] Add parameterized delegated/nondelegated Supervisor regression in `test_delegated_session_lifecycle.py`: inject custom WorkflowOrchestrator version; assert successful run and actual persisted run/checkpoint versions; recreate store/Supervisor at the same version and assert checkpoint reuse with one tool call; restart with a different custom version and assert no reuse, a second call, fresh output and updated persisted versions while retaining old-version checkpoint history. Reuse `SinglePlanner`/`build_registry`; close registry in `finally`.
- [x] Add `test_checkpoint_double_restore_isolates_nested_artifact_metadata` in `test_workflow_resume.py`: restore the same synthetic checkpoint twice before mutating nested dictionary/list metadata; assert each restoration and original checkpoint remain independent. Keep a deepcopy snapshot to prevent a mutable expected-value oracle.
- [ ] Run the two added test node IDs, then recovery-focused suites (`test_workflow_resume.py`, `test_delegated_session_lifecycle.py`, `test_decision_protocol_recovery.py`, `test_supervisor_runtime_integration.py`), then all `tests/agent`, using the approved temporary-cwd isolation pattern from `2026-09-24-rag-service-extraction.md` and the audit E5 runner. Preserve normal conftest, disable cache/bytecode and plugin autoload; explicitly load pytest-asyncio if needed. Preserve only six OS launch variables, use temporary config/state paths and disabled real-service switches. Block network except Windows internal asyncio socketpair setup. For full Agent, copy only the three tracked offline evaluation JSONL fixtures to temporary cwd and verify their hashes; no production assets.
- [x] Compile tracked Python sources under `src`/`scripts` and changed tests in memory (no pyc); inspect diff/whitespace/scope, record exact commands/results/skips and frozen test-file hashes. Hand off uncommitted; parent performs independent review. No claim of full-repository, real-science, sandbox or packages 1–8 acceptance.

**Resource-coordination amendment:** Added tests and focused recovery verification are complete. Full Agent verification is deferred to the parent's heavy-run slot (4B active, 4C queued). The already-started full run was interrupted immediately on this instruction; do not restart it in parallel. The unchecked verification item above remains pending only for full Agent.

## Execution record

Plan written before test edits. Results and frozen handoff follow after execution.


### Commands and isolation

All three invocations use the same in-memory runner below from the specified worktree. The runner is not a repository script. No extra pytest plugin was needed: these suites use their existing synchronous/asyncio runners; plugin autoload remained disabled. Normal `tests/conftest.py` ran. The full invocation copies only the three tracked offline JSONL fixtures; those names do not enable real acceptance.

```powershell
$runner = @'
import hashlib, logging, os, shutil, socket, sys, tempfile
from pathlib import Path
repo = Path(r"D:/MedChat/molecular_chat_system_worktrees/recovery-parity-regressions")
keep = {key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "COMSPEC") if key in os.environ}
os.environ.clear()
os.environ.update(keep)
with tempfile.TemporaryDirectory(prefix="medchat-g3-") as temporary:
    root = Path(temporary)
    os.environ.update({
        "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8",
        "PYTHONPATH": str(repo), "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "AGENT_HARNESS_MODE": "legacy",
        "MOLECULAR_CHAT_CONFIG": str(root / "missing.yaml"),
        "MEDCHAT_ENV_FILE": str(root / "not-loaded.env"),
        "MEDCHAT_USER_CONFIG_DIR": str(root / "user-config"),
        "MEDCHAT_LLM_LOCK_DIR": str(root / "locks"),
        "MEDCHAT_AGENT_SESSION_DB": str(root / "sessions.sqlite"),
        "AGENT_STATE_DB": str(root / "agent.sqlite"),
        "MEDCHAT_TASK_DB_PATH": str(root / "tasks.sqlite"),
        "TARGET_DB_PATH": str(root / "targets.sqlite"),
        "TARGET_CACHE_DIR": str(root / "target-cache"),
        "MEDCHAT_FRAGMENT_DB_PATH": str(root),
        "MEDCHAT_TASK_BACKEND": "local", "MEDCHAT_TEMPORAL_CANARY_PERCENT": "0",
        "AGENT_LANGGRAPH_CANARY_PERCENT": "0",
        "MEDCHAT_RUN_FAMILY_REAL_ACCEPTANCE": "0",
        "MEDCHAT_RUN_OPENSANDBOX_ACCEPTANCE": "0",
        "MEDCHAT_RUN_OPENSANDBOX_STABILITY_SOAK": "0",
        "RUN_REAL_TARGET_SEARCH": "0",
    })
    os.chdir(root)
    sys.path.insert(0, str(repo))
    original_connect = socket.socket.connect
    def guarded_connect(sock, address):
        caller = sys._getframe(1)
        if (caller.f_code.co_name == "_fallback_socketpair"
                and caller.f_code.co_filename == socket.__file__
                and isinstance(address, tuple)
                and address[0] in ("127.0.0.1", "::1")):
            return original_connect(sock, address)
        raise AssertionError("G3 offline runner blocks network connections")
    def blocked(*args, **kwargs):
        raise AssertionError("G3 offline runner blocks network connections")
    socket.socket.connect = guarded_connect
    socket.socket.connect_ex = blocked
    socket.create_connection = blocked
    try:
        if "tests/agent" in sys.argv[1:]:
            fixtures = root / "data/agent_evals"
            fixtures.mkdir(parents=True)
            for name in ("real_agent_cases.jsonl", "golden_scientific_cases.jsonl", "diverse_scientific_cases.jsonl"):
                source = repo / "data/agent_evals" / name
                destination = fixtures / name
                shutil.copy2(source, destination)
                assert hashlib.sha256(source.read_bytes()).digest() == hashlib.sha256(destination.read_bytes()).digest()
        import pytest
        result = pytest.main([str(repo / path) for path in sys.argv[1:]] +
            ["-q", "-p", "no:cacheprovider", "--tb=short", "-rs"])
    finally:
        logging.shutdown()
        os.chdir(repo)
    print("G3_PYTEST_EXIT=" + str(result))
    sys.exit(result)
'@
$python = 'C:/Users/xkx52/.conda/envs/MedChat/python.exe'
# New characterization tests:
$runner | & $python -B -c "import sys; exec(sys.stdin.read())" tests/agent/test_workflow_resume.py::test_checkpoint_double_restore_isolates_nested_artifact_metadata tests/agent/test_delegated_session_lifecycle.py::test_custom_workflow_version_persists_and_controls_resume
# Recovery-focused:
$runner | & $python -B -c "import sys; exec(sys.stdin.read())" tests/agent/test_workflow_resume.py tests/agent/test_delegated_session_lifecycle.py tests/agent/test_decision_protocol_recovery.py tests/agent/test_supervisor_runtime_integration.py
# Full Agent (not full repository):
$runner | & $python -B -c "import sys; exec(sys.stdin.read())" tests/agent
```

### Results

| Check | Actual result |
|---|---|
| Added tests | 3 passed, 0 skipped, 0 warnings; 1.85s; exit 0 |
| Recovery-focused four files | 77 passed, 0 skipped, 0 warnings; 11.75s; exit 0 |
| Full Agent | Interrupted at user resource-coordination request; last emitted progress 59%, no final pytest summary; terminal exit 1 after Ctrl-C. No completed pass/skip count and not a test-failure diagnosis or passing run. Full Agent remains pending parent scheduling. |
| In-memory compilation | 321 tracked Python files under `src`/`scripts` plus both changed test files passed; no pyc |
| Existing test/helper AST comparison against HEAD | All pre-existing function/class ASTs identical; only the two new test definitions and imports added |
| `git diff --check` | exit 0 |

Static compilation used `git ls-files src scripts`, selected `.py`, and `compile(Path(path).read_bytes(), path, 'exec')` under the same interpreter with `-B`; no product import or service startup. AST comparison used `git show HEAD:<test-path>` and `ast.dump(..., include_attributes=False)`. This is an in-memory substitute for compileall to avoid generated files, not a claim that literal `python -m compileall` ran. No JavaScript, deployment or data changes, so Node and deployment health checks are outside this bounded run.

### Frozen test diff for parent review

Base and HEAD remain `3a682649c3392727f642f33f56bd3f94c7d77707`. Test diff: **100 insertions, 1 deletion**; the deletion only extends an import. Raw-file SHA-256:

```text
tests/agent/test_delegated_session_lifecycle.py ee1b702f52c4da126d555784fc77b2b13c5ef3e4102ca523851d2fed4dbf71cc
tests/agent/test_workflow_resume.py 778bf1e30c1b2e10969f4ea308708bf26207441ec5cb33309c70fafbd08d0732
```

Normalized test-diff SHA-256: `afb51fd5d2a539d3854f7f098f270e5e5cb8596d75917f8a5dbb587f4452ad10`. Reproduce by taking `git diff --binary --no-ext-diff -- tests/agent/test_delegated_session_lifecycle.py tests/agent/test_workflow_resume.py`, joining output lines with LF, adding one final LF, then SHA-256 of UTF-8 bytes. The untracked plan is excluded to avoid a self-referential hash. Raw-file hashes came from `Get-FileHash -Algorithm SHA256`.

Independent review is for the parent; implementer self-checks are not independent SPEC/QUALITY approval. Review focus: custom version survives both Supervisor entry paths and request cloning; real persisted rows agree; same version restores old output without re-execution; changed version executes and retains history; double restore cannot share nested artifact dictionaries/lists with the other result or original checkpoint.

### Final handoff state

- Exactly the two allowed test files are modified and this plan is untracked; index empty, branch/base unchanged. No staging, commit, push, PR or merge.
- Focused tests and static checks apply to the frozen hashes above; no test edits followed verification. Only this record was updated afterward.
- Full Agent is pending, not passed. The interrupted output showed two skip markers but no final reasons/counts; they are not reported as completed skip evidence. No test failures were reported before interruption.
- Historical sandbox `artifact_failed` remains unresolved. No sandbox rerun, soak, real model/scientific acceptance, service startup or production asset access was performed by this task.
- Parent next actions: independent SPEC/QUALITY review of this unstaged diff; schedule full Agent against the same frozen tests when the heavy slot is free. Do not infer package 6 feature completion or packages 1–8 acceptance from this bounded handoff.

## Parent review checkpoint

Independent SPEC approved with its own3 passing cases1.61s; independent QUALITY approved with3 passing cases1.82s. Both verified frozen hashes, unchanged pre-existing test ASTs, direct SQLite version checks, both entrypaths and nested-object independence. No production changes or weakened assertions. The interrupted full run is retained; full integrated Agent verification will be supplied by required exact-head CI, not counted as a local completed run. Latest reviewed main must be integrated and focused tests repeated before publication. Historical sandbox artifact_failed remains a separate unresolved record.

## Integrated publication checkpoint

The reviewed test-only implementation was committed as `23bdaf9`. Main through snapshot PR #68 (`e173d43`) was integrated without conflicts: the two modified recovery test files passed all 56 cases in 7.01 seconds. Main through target-contract PR #69 (`16b91575229be987b0c5cf3d8d9039135d8ade29`) was subsequently integrated without conflicts; the same two files passed all 56 cases in 6.48 seconds, exit 0. No implementation or test assertions changed during integration.

Command: the repository's isolated offline runner from `2026-09-24-rag-service-extraction.md`, with only its repository path replaced, invoked `tests/agent/test_delegated_session_lifecycle.py tests/agent/test_workflow_resume.py`. No production model, network service, or user asset was activated. Required exact-head CI remains the publication gate for full Agent regression; the interrupted local full run above is not reclassified as passed.
