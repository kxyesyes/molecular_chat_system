# Temporal Worker Trusted Install Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the unsafe privileged worker preflight with root-owned installed assets, a dedicated non-secret worker environment, descriptor-relative validation, and cgroup-safe lifecycle tests.

**Architecture:** A root-owned oneshot prepare unit validates `/etc/medchat/temporal-worker.env` without consuming it, then applies an installed tmpfiles policy. The worker unit remains fully unprivileged, consumes only the dedicated allowlisted environment, revalidates runtime semantics before side effects, and is writable only in four private state/output directories. Repository code, Conda, units, helper, and tmpfiles assets are root-owned and never executed as root from a service-user-writable checkout.

**Tech Stack:** Python 3.10+ standard library, systemd 259-compatible units, systemd-tmpfiles, POSIX descriptor APIs, Bash/POSIX deployment tooling, pytest, unittest, WSL/Linux integration checks.

---

## Baseline and non-goals

- Start from commit `10e82b7`; retain its validated `src/task_runtime/production_worker.py` and worker-entrypoint ordering unless a new test proves a defect.
- Follow the approved design revision in `docs/superpowers/specs/2026-08-21-temporal-production-canary-readiness-design.md` at commit `d9e2802`.
- Do not change docking algorithms, Vina arguments, Temporal workflow/activity semantics, Web behavior, TaskStore schema, Agent routing, or scientific validation.
- Remove the intermediate `deployment/medchat-temporal-worker.tmpfiles` after the trusted installed replacement exists.
- A Windows/WSL skip is not production evidence. Linux descriptor/cgroup checks must remain explicit release blockers until actually executed.

## File map

### Create

- `deployment/libexec/validate-temporal-worker-env.py`: standalone root-owned parser and descriptor-relative validator; imports no repository code.
- `deployment/medchat-temporal-worker-prepare.service`: root oneshot with no `EnvironmentFile`.
- `deployment/tmpfiles/medchat-temporal-worker.conf`: trusted installed tmpfiles policy.
- `deployment/temporal-worker.env.example`: exact non-secret worker allowlist.
- `deployment/install-temporal-worker.sh`: tested staging/live installation with fixed destinations and modes.
- `tests/test_temporal_worker_trust_boundary.py`: cross-platform parser/static/install contracts.
- `tests/test_temporal_worker_trust_linux_unittest.py`: stdlib-only real Linux no-follow and ownership tests.

### Modify

- `deployment/medchat-temporal-worker.service`: remove privileged prefixes and depend on prepare unit.
- `scripts/validate_temporal_worker_production.py`: retain only non-privileged injected-config validation.
- `tests/task_runtime/test_production_worker.py`: move environment-file trust tests to the standalone helper contract.
- `tests/test_temporal_deployment_assets.py`: assert prepare/worker/install trust boundaries.
- `tests/test_temporal_systemd_integration.py`: replace numeric-PID cleanup with cgroup-scoped cleanup and identity checks.
- `deployment/README.md`: replace recursive service-user ownership with root-owned source/runtime instructions.

### Remove

- `deployment/medchat-temporal-worker.tmpfiles`: unsafe repository-path tmpfiles source used by privileged worker preflight.

## Task 1: Build the standalone environment parser

**Files:**
- Create: `deployment/libexec/validate-temporal-worker-env.py`
- Create: `tests/test_temporal_worker_trust_boundary.py`

- [ ] **Step 1: Write failing parser and redaction tests**

Load the helper with `importlib.util.spec_from_file_location()` so tests do not require
`deployment` to be a Python package. Define the exact allowed key set in the test:

```python
ALLOWED_KEYS = {
    "MEDCHAT_TASK_BACKEND",
    "MEDCHAT_TEMPORAL_CANARY_PERCENT",
    "MEDCHAT_TEMPORAL_ADDRESS",
    "MEDCHAT_TEMPORAL_NAMESPACE",
    "MEDCHAT_TEMPORAL_DOCKING_QUEUE",
    "MEDCHAT_TEMPORAL_DOCKING_CONCURRENCY",
    "MEDCHAT_TASK_STAGING_ROOT",
    "MEDCHAT_TASK_DB_PATH",
    "MEDCHAT_TEMPORAL_WORKER_METRICS_ADDRESS",
    "MEDCHAT_TEMPORAL_WORKER_METRICS_PORT",
    "MEDCHAT_TEMPORAL_BASELINE_P95_SECONDS",
    "MEDCHAT_TEMPORAL_BACKUP_STATE",
    "MOLECULAR_DOCKING_ROOT",
    "MOLECULAR_DOCKING_VINA",
    "MOLECULAR_DOCKING_ADFR_BIN",
    "MOLECULAR_DOCKING_PREPARE_RECEPTOR",
    "MOLECULAR_DOCKING_PREPARE_LIGAND",
    "MOLECULAR_DOCKING_VINA_TIMEOUT_SECONDS",
}


def test_parser_accepts_only_canonical_allowlisted_assignments(helper):
    payload = b"# worker only\nMEDCHAT_TASK_BACKEND=temporal_canary\n"
    assert helper.parse_environment_payload(payload) == {
        "MEDCHAT_TASK_BACKEND": "temporal_canary"
    }
    assert helper.ALLOWED_KEYS == frozenset(ALLOWED_KEYS)


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        (b"LD_PRELOAD=/tmp/pwn.so\n", "environment_key_forbidden"),
        (b"PYTHONHOME=/tmp/python\n", "environment_key_forbidden"),
        (b"PYTHONPATH=/tmp/modules\n", "environment_key_forbidden"),
        (b"OPENAI_COMPATIBLE_API_KEY=secret\n", "environment_key_forbidden"),
        (b"UNDECLARED=value\n", "environment_key_not_allowed"),
        (b"MEDCHAT_TASK_BACKEND=a\nMEDCHAT_TASK_BACKEND=b\n", "environment_key_duplicate"),
        (b" MEDCHAT_TASK_BACKEND=value\n", "environment_line_not_canonical"),
        (b"MEDCHAT_TASK_BACKEND='value'\n", "environment_line_not_canonical"),
        (b"MEDCHAT_TASK_BACKEND=value\\\ncontinued\n", "environment_line_not_canonical"),
        (b"MEDCHAT_TASK_BACKEND=bad\x00value\n", "environment_file_invalid_nul"),
        (b"\xff\n", "environment_file_invalid_utf8"),
    ],
)
def test_parser_rejects_unsafe_content_without_echo(helper, payload, code):
    with pytest.raises(helper.TrustBoundaryError) as error:
        helper.parse_environment_payload(payload)
    assert error.value.code == code
    assert repr(payload) not in str(error.value)
```

Add tests for empty/comments-only content, missing `=`, multiple `=`, control
characters, payloads larger than 65,536 bytes, and stable CLI failure output. No error
may contain a path, key value, payload fragment, secret, or traceback.

- [ ] **Step 2: Verify RED**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\test_temporal_worker_trust_boundary.py -q -p no:cacheprovider
```

Expected: FAIL because the standalone helper does not exist.

- [ ] **Step 3: Implement the bounded canonical parser**

The helper must be executable with system Python and import only standard-library
modules. Define this public contract:

```python
ENVIRONMENT_FILE = Path("/etc/medchat/temporal-worker.env")
MAX_ENVIRONMENT_BYTES = 65_536
NAME_RE = re.compile(r"[A-Z][A-Z0-9_]*\Z", re.ASCII)
FORBIDDEN_KEYS = frozenset({
    "LD_PRELOAD", "LD_LIBRARY_PATH", "PYTHONHOME", "PYTHONPATH",
    "BASH_ENV", "ENV", "IFS", "GCONV_PATH", "SSLKEYLOGFILE",
    "OPENAI_COMPATIBLE_API_KEY", "MODELSCOPE_API_KEY",
})


class TrustBoundaryError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(f"temporal worker trust validation failed: {code}")


def parse_environment_payload(payload: bytes) -> dict[str, str]:
    if len(payload) > MAX_ENVIRONMENT_BYTES:
        raise TrustBoundaryError("environment_file_too_large")
    if b"\x00" in payload:
        raise TrustBoundaryError("environment_file_invalid_nul")
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise TrustBoundaryError("environment_file_invalid_utf8") from None
    values: dict[str, str] = {}
    for line in text.splitlines():
        if not line or line.startswith(("#", ";")):
            continue
        if line != line.strip() or line.endswith("\\") or line.count("=") != 1:
            raise TrustBoundaryError("environment_line_not_canonical")
        name, value = line.split("=", 1)
        if NAME_RE.fullmatch(name) is None or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise TrustBoundaryError("environment_line_not_canonical")
        if value != value.strip() or value.startswith(("'", '"')) or value.endswith(("'", '"')):
            raise TrustBoundaryError("environment_line_not_canonical")
        if name in values:
            raise TrustBoundaryError("environment_key_duplicate")
        if name in FORBIDDEN_KEYS or any(token in name for token in ("KEY", "TOKEN", "PASSWORD", "SECRET")):
            raise TrustBoundaryError("environment_key_forbidden")
        if name not in ALLOWED_KEYS:
            raise TrustBoundaryError("environment_key_not_allowed")
        values[name] = value
    if not values:
        raise TrustBoundaryError("environment_file_empty")
    return values
```

The real code must spell out `ALLOWED_KEYS` exactly as Step 1; do not import it from
tests or the application. CLI success prints only
`temporal_worker_environment_validation=passed`; failure prints only
`temporal_worker_environment_validation=failed code=<allowlisted-code>` to stderr.

- [ ] **Step 4: Verify GREEN and commit**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\test_temporal_worker_trust_boundary.py -q -p no:cacheprovider
git add -- deployment/libexec/validate-temporal-worker-env.py tests/test_temporal_worker_trust_boundary.py
git commit -m "feat(deploy): validate temporal worker environment syntax"
```

Expected: parser/redaction tests pass.

## Task 2: Enforce descriptor-relative Linux trust checks

**Files:**
- Modify: `deployment/libexec/validate-temporal-worker-env.py`
- Create: `tests/test_temporal_worker_trust_linux_unittest.py`

- [ ] **Step 1: Write real Linux no-follow tests**

Use `unittest` so WSL can run the file with system Python without pytest. Skip the
class unless POSIX, effective UID 0, and
`MEDCHAT_RUN_TEMPORAL_TRUST_INTEGRATION=1`. Build each fixture beneath a private
root-owned directory under `/root`, never `/tmp`. Cover:

- secure parent chain and root:root `0600` regular leaf passes;
- parent symlink, leaf symlink, FIFO/device, wrong directory/leaf owner, `0640`/`0666`,
  and group/world-writable parent fail with stable codes;
- oversize and mutation during read fail closed;
- a thread alternating a valid regular file with a symlink to disallowed content never
  returns a key outside `ALLOWED_KEYS`;
- cleanup stays under the private root path and never follows a symlink.

- [ ] **Step 2: Verify RED on Linux/WSL**

```bash
MEDCHAT_RUN_TEMPORAL_TRUST_INTEGRATION=1 python3 tests/test_temporal_worker_trust_linux_unittest.py
```

Expected: FAIL because `validate_environment_file()` has not implemented descriptor
traversal.

- [ ] **Step 3: Implement no-follow traversal and stable identity reads**

Use `os.open()` from `/` through every component with `dir_fd`, `O_DIRECTORY`,
`O_NOFOLLOW`, and `O_CLOEXEC`. Require every directory to be root:root and have no
`0o022` write bits. Open the leaf with
`O_RDONLY|O_NONBLOCK|O_NOFOLLOW|O_CLOEXEC`, then require regular, root:root, `0600`.
Read at most 65,537 bytes through the descriptor. Compare `(st_dev, st_ino, st_size,
st_mtime_ns, st_ctime_ns)` before and after reading; reject mutation. Close every fd in
reverse order. Do not use `Path.resolve()`, `Path.open()`, path-based `stat()`, or a
second path open. Map OS failures to stable codes without serializing the exception.

The CLI accepts no path argument and always validates
`/etc/medchat/temporal-worker.env`.

- [ ] **Step 4: Verify GREEN and commit**

```bash
MEDCHAT_RUN_TEMPORAL_TRUST_INTEGRATION=1 python3 tests/test_temporal_worker_trust_linux_unittest.py
```

```powershell
git add -- deployment/libexec/validate-temporal-worker-env.py tests/test_temporal_worker_trust_linux_unittest.py tests/test_temporal_worker_trust_boundary.py
git commit -m "fix(deploy): enforce no-follow worker environment trust"
```

## Task 3: Install root-owned prepare and worker assets

**Files:**
- Create: `deployment/medchat-temporal-worker-prepare.service`
- Create: `deployment/tmpfiles/medchat-temporal-worker.conf`
- Create: `deployment/temporal-worker.env.example`
- Create: `deployment/install-temporal-worker.sh`
- Modify: `deployment/medchat-temporal-worker.service`
- Remove: `deployment/medchat-temporal-worker.tmpfiles`
- Modify: `scripts/validate_temporal_worker_production.py`
- Modify: `tests/task_runtime/test_production_worker.py`
- Modify: `tests/test_temporal_deployment_assets.py`
- Modify: `tests/test_temporal_worker_trust_boundary.py`

- [ ] **Step 1: Write failing unit, allowlist, and installer tests**

Assert the worker unit has this exact relationship:

```python
worker = parse_systemd_directives(WORKER_SYSTEMD_UNIT)
assert worker["Unit"]["Requires"] == ["medchat-temporal-worker-prepare.service"]
assert "medchat-temporal-worker-prepare.service" in worker["Unit"]["After"][0].split()
assert worker["Service"]["EnvironmentFile"] == ["/etc/medchat/temporal-worker.env"]
assert worker["Service"]["ExecStartPre"] == [
    "/opt/conda/envs/medchat/bin/python scripts/validate_temporal_worker_production.py --check-config"
]
assert all(
    not value.startswith(("+", "!"))
    for key, values in worker["Service"].items()
    if key.startswith("Exec")
    for value in values
)
assert worker["Service"]["ProtectSystem"] == ["strict"]
assert worker["Service"]["KillMode"] == ["mixed"]
assert worker["Service"]["TimeoutStopSec"] == ["90"]
assert worker["Service"]["UMask"] == ["0077"]
assert worker["Service"]["ReadWritePaths"] == [
    "/opt/medchat/molecular_chat_system/scratch /opt/medchat/molecular_chat_system/temp_docking"
]
```

Assert the prepare unit has no `User`, `Environment`, or `EnvironmentFile`; has
`Before=medchat-temporal-worker.service`, `PartOf=medchat-temporal-worker.service`,
`Type=oneshot`, and `RemainAfterExit=yes`; and its only commands are:

```text
/usr/libexec/medchat/validate-temporal-worker-env
/usr/bin/systemd-tmpfiles --create /usr/lib/tmpfiles.d/medchat-temporal-worker.conf
```

Assert the source tmpfiles file contains exactly the four `0700 medchat medchat`
directory records from the design, and the old repository-path file is absent. Assert
the env example's non-comment keys equal `ALLOWED_KEYS`, contains no values matching
secret/token/key/password patterns, and excludes relay/network and loader variables.

Run `deployment/install-temporal-worker.sh --destdir <temporary-root>` under WSL/Linux
and assert fixed destinations, byte equality, executable/helper mode `0755`, other
asset modes `0644`, a SHA-256 manifest, no generated `/etc/medchat/temporal-worker.env`,
and no `systemctl start/restart/enable` side effects. Live `--destdir /` must require
effective UID 0 and a root-owned, non-group/other-writable source tree.

- [ ] **Step 2: Verify RED**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\test_temporal_worker_trust_boundary.py tests\test_temporal_deployment_assets.py tests\task_runtime\test_production_worker.py -q -p no:cacheprovider
```

Expected: old privileged unit/tmpfiles and missing prepare/install assets fail.

- [ ] **Step 3: Create the trusted assets and unprivileged worker unit**

Create the prepare unit:

```ini
[Unit]
Description=Prepare MedChat Temporal Docking Worker Trust Boundary
Before=medchat-temporal-worker.service
PartOf=medchat-temporal-worker.service

[Service]
Type=oneshot
ExecStart=/usr/libexec/medchat/validate-temporal-worker-env
ExecStart=/usr/bin/systemd-tmpfiles --create /usr/lib/tmpfiles.d/medchat-temporal-worker.conf
RemainAfterExit=yes
```

Change the worker unit to require/after prepare, consume only
`/etc/medchat/temporal-worker.env`, and keep only the non-privileged config
`ExecStartPre`. Declare fixed `PATH` and `PYTHONDONTWRITEBYTECODE=1` using root-owned
unit `Environment=` directives; retain `ProtectSystem=strict`, `UMask=0077`,
`KillMode=mixed`, `KillSignal=SIGTERM`, `SendSIGKILL=yes`, `TimeoutStopSec=90`, and the
two exact writable roots. Add explicit read-only paths for the source tree and Conda.

Move the four tmpfiles records to
`deployment/tmpfiles/medchat-temporal-worker.conf`. Remove the old file. Restrict
`scripts/validate_temporal_worker_production.py` to `--check-config`; delete its
environment metadata mode/imports/tests because root trust now belongs exclusively to
the standalone installed helper.

Implement the installer with `set -eu`, fixed source/destination arrays, `install -D`,
fixed modes, SHA-256 manifest generation, `--destdir`, and a live-install guard that
rejects non-root execution or a source file/parent owned by non-root or writable by
group/other. It must never create the production env file or start/reload/enable a
service.

- [ ] **Step 4: Verify GREEN, systemd syntax, and commit**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\test_temporal_worker_trust_boundary.py tests\test_temporal_deployment_assets.py tests\task_runtime\test_production_worker.py -q -p no:cacheprovider
wsl.exe -u root -- bash -lc "systemd-analyze verify /mnt/d/MedChat/molecular_chat_system_worktrees/industrial-agent-temporal-docking/deployment/medchat-temporal-worker-prepare.service /mnt/d/MedChat/molecular_chat_system_worktrees/industrial-agent-temporal-docking/deployment/medchat-temporal-worker.service"
```

Use temporary executable stubs only when `systemd-analyze` reports the absent
production Conda path; do not edit the committed units for validation. Then commit:

```powershell
git add -- deployment/medchat-temporal-worker.service deployment/medchat-temporal-worker-prepare.service deployment/libexec/validate-temporal-worker-env.py deployment/tmpfiles/medchat-temporal-worker.conf deployment/temporal-worker.env.example deployment/install-temporal-worker.sh scripts/validate_temporal_worker_production.py tests/task_runtime/test_production_worker.py tests/test_temporal_deployment_assets.py tests/test_temporal_worker_trust_boundary.py
git add -u -- deployment/medchat-temporal-worker.tmpfiles
git commit -m "fix(deploy): isolate temporal worker root preparation"
```

## Task 4: Make lifecycle tests cgroup-safe and document ownership

**Files:**
- Modify: `tests/test_temporal_systemd_integration.py`
- Modify: `deployment/README.md`
- Modify: `tests/test_temporal_worker_trust_boundary.py`

- [ ] **Step 1: Write failing cleanup and documentation tests**

Add static/behavioral assertions that cleanup has no direct numeric-PID signal path:

```python
source = Path("tests/test_temporal_systemd_integration.py").read_text(encoding="utf-8")
cleanup_source = source[
    source.index("def _cleanup_unit"):source.index("def _start_process_tree")
]
assert "os.kill(" not in cleanup_source
assert "ControlGroup" in cleanup_source
assert "cgroup.procs" in cleanup_source
assert '"systemctl", "kill"' in cleanup_source
```

Assert `deployment/README.md` contains root ownership/non-writability for source and
Conda, private `medchat` ownership only for the four runtime directories, the trusted
staging/install flow, dedicated worker environment creation, and prepare+worker
restart after configuration changes. Assert the old recursive
`chown -R medchat:medchat /opt/medchat` command is absent.

- [ ] **Step 2: Verify RED**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\test_temporal_worker_trust_boundary.py tests\test_temporal_systemd_integration.py -q -p no:cacheprovider
```

Expected: numeric PID cleanup and old deployment ownership fail.

- [ ] **Step 3: Replace cleanup with cgroup identity**

Read `ControlGroup` through checked `systemctl show`, constrain it beneath
`/sys/fs/cgroup`, and read `cgroup.procs` for liveness. Cleanup order is:

```python
stop = _run(["systemctl", "stop", unit], timeout=10)
if stop.returncode != 0 and _cgroup_has_processes(control_group):
    kill = _run([
        "systemctl", "kill", "--kill-whom=all", "--signal=SIGKILL", unit,
    ])
    assert kill.returncode == 0, kill.stderr
assert _wait_until(lambda: not _cgroup_has_processes(control_group), timeout=5)
reset = _run(["systemctl", "reset-failed", unit])
assert reset.returncode == 0, reset.stderr
```

Capture `(pid, /proc/<pid>/stat starttime, cgroup)` only to identify the original
fixture process in assertions. PID reuse or movement outside the unit cgroup means the
original process is gone. Never call `os.kill()` on a stored PID during cleanup.

- [ ] **Step 4: Correct deployment ownership guidance**

Replace recursive `/opt/medchat` service-user ownership with root-owned source, Conda,
unit/helper/tmpfiles assets and `go-w` protection. Use `install -d -o medchat -g
medchat -m 0700` only for the four approved directories. Document that the dedicated
worker environment is root:root `0600`, no-secret, created manually from the example,
and that config changes require restarting prepare then worker. Docker/Compose remains
operator-managed and starts first.

- [ ] **Step 5: Verify GREEN and commit**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\test_temporal_worker_trust_boundary.py tests\test_temporal_systemd_integration.py -q -p no:cacheprovider
git add -- tests/test_temporal_systemd_integration.py deployment/README.md tests/test_temporal_worker_trust_boundary.py
git commit -m "fix(test): make temporal worker cleanup cgroup-safe"
```

## Task 5: Run the complete corrective release gate

**Files:** none unless a failing test exposes an in-scope defect.

- [ ] **Step 1: Run Windows unit/static regression**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\task_runtime\test_production_worker.py tests\test_temporal_worker_trust_boundary.py tests\test_temporal_deployment_assets.py tests\test_temporal_systemd_integration.py -q -p no:cacheprovider
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\task_runtime\test_temporal_config.py tests\task_runtime\test_temporal_prometheus.py tests\task_runtime\test_temporal_workflow.py -q -p no:cacheprovider
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m compileall -q src scripts deployment\libexec
```

Expected: all unit/static tests pass; only explicitly Linux/root/opt-in tests skip.

- [ ] **Step 2: Run real Linux trust and systemd gates**

```bash
MEDCHAT_RUN_TEMPORAL_TRUST_INTEGRATION=1 python3 tests/test_temporal_worker_trust_linux_unittest.py
MEDCHAT_RUN_TEMPORAL_SYSTEMD_INTEGRATION=1 python -m pytest tests/test_temporal_systemd_integration.py -q -p no:cacheprovider
systemd-analyze verify deployment/medchat-temporal-worker-prepare.service deployment/medchat-temporal-worker.service
```

Expected: all pass on the Linux production candidate. If pytest is unavailable in
WSL, the stdlib trust suite and `systemd-analyze` still run, while the real cgroup
pytest remains truthfully unexecuted and blocks production readiness.

- [ ] **Step 3: Audit the final diff**

```powershell
git diff --check 10e82b7..HEAD
git diff --name-status 10e82b7..HEAD
git status --short
```

Expected: only plan-listed files changed, no secret/env runtime file, no untracked
output, and a clean worktree. Do not squash the RED/GREEN commits before review.

- [ ] **Step 4: Two-stage review**

First run a spec-compliance review against design commit `d9e2802`; then run a code
quality/security review emphasizing root execution, `EnvironmentFile` ordering,
descriptor races, installation ownership, and cgroup cleanup. Any finding requires a
fix by the implementer followed by the same reviewer re-review.
