# Temporal Worker Atomic Generation Corrective Plan

> **Status:** authoritative corrective plan for Task 8. It supersedes the tmpfiles and
> flat-install portions of `2026-08-24-temporal-worker-trust-boundary.md`.

**Goal:** Remove the verified root tmpfiles symlink ownership escalation, publish the
worker deployment as one immutable content-addressed bundle, and make signal,
concurrency, rollback, and cgroup cleanup behavior production-safe.

**Architecture:** A root-owned prepare unit runs two standard-library helpers from the
currently activated immutable generation: one validates the dedicated environment and
one creates or verifies four fixed runtime directories through descriptor-relative
no-follow operations. A no-follow installation lock serializes installers. Every source
asset is read before mutation, written into a private staging generation, fsynced and
verified, then published under `releases/<bundle-sha256>`. A single atomic `current`
symlink selects the active complete generation. Fixed systemd entry symlinks are
bootstrap-only and always resolve through `current`.

**Non-goals:** No changes to Temporal workflows, docking/Vina behavior, TaskStore,
scientific result validation, Web routes, Agent routing, Docker/Compose lifecycle, or
production environment values.

## Task 1: Replace tmpfiles with a no-follow directory preparer

**Create:**

- `deployment/libexec/prepare-temporal-worker-directories.py`
- `tests/test_temporal_worker_directory_trust.py`
- `tests/test_temporal_worker_directory_linux_unittest.py`

**Modify:**

- `deployment/medchat-temporal-worker-prepare.service`
- `tests/test_temporal_deployment_assets.py`

**Remove:**

- `deployment/tmpfiles/medchat-temporal-worker.conf`

1. Write RED parser/static and opt-in Linux root tests. Cover empty deployment,
   existing exact directories, leaf and parent symlink, FIFO, regular file, wrong
   owner/mode, concurrent replacement, missing `medchat` identity, and stable redacted
   errors. Reproduce the systemd 259 leaf-symlink behavior in an isolated `--root`
   fixture and assert the sentinel owner/mode/inode stays unchanged under the new
   prepare path.
2. Implement a standalone stdlib helper with no CLI path override. Resolve `medchat`
   UID/GID from the system account database. Traverse fixed components from `/` with
   `dir_fd`, `O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC`. Require the project parent chain to be
   root:root and not `go-w`.
3. For a missing fixed runtime directory, create with `mkdirat`, open no-follow, then
   `fchown`/`fchmod` the opened directory. For an existing entry, require directory,
   `medchat:medchat 0700` and do not repair it. Compare descriptor identity with the
   final no-follow directory entry; fail on replacement.
4. Change prepare to run environment validation then the installed directory helper.
   It must have no `EnvironmentFile`, no repo/Conda path and no tmpfiles command.
5. Run focused tests, stdlib Linux root tests and `systemd-analyze verify`; commit.

## Task 2: Introduce immutable bundle construction and no-follow locking

**Create:**

- `deployment/libexec/install-temporal-worker-bundle.py`

**Modify:**

- `deployment/install-temporal-worker.sh`
- `tests/test_temporal_deployment_assets.py`

1. Write RED tests for the exact generation layout, bounded pre-read of every source,
   manifest completeness, source mutation, parent/leaf symlink, special file,
   ownership/mode, and lock-file trust.
2. Reduce the shell wrapper to canonical DESTDIR and live-source trust checks, then
   `exec /usr/bin/python3` so the public installer PID is the writer PID before any
   destination mutation.
3. In the Python installer, acquire an exclusive `fcntl.flock` on a fixed lock opened
   no-follow through a trusted descriptor chain. Require regular file, expected
   UID/GID, exact `0600`; reject an unexpected existing object.
4. Read all fixed assets through no-follow descriptors with per-file and bundle size
   bounds. For live install require root:root and no `022` source write bits. Compute a
   deterministic bundle digest over logical path, exact mode and bytes.
5. Construct a private same-filesystem staging generation under
   `usr/lib/medchat/temporal-worker/releases`. Write units, both helpers and
   `manifest.sha256`; fsync files/directories; verify regular/no-symlink,
   owner/group/mode/inode/hash; make generation directories immutable to non-root;
   atomically rename to `releases/<bundle-sha256>` or verify an existing identical
   generation without modifying it.
6. Run focused tests and commit.

## Task 3: Add bootstrap entrypoints and single-point activation

**Modify:**

- `deployment/libexec/install-temporal-worker-bundle.py`
- `deployment/medchat-temporal-worker-prepare.service`
- `tests/test_temporal_deployment_assets.py`

1. Write RED tests that fixed unit entries are root-owned symlinks with exact targets
   through `/usr/lib/medchat/temporal-worker/current/units/`. Existing unexpected
   entries must fail and remain untouched.
2. Bootstrap missing fixed symlinks with same-directory temporary symlinks and atomic
   replace; track only entries created by this invocation for rollback. Existing exact
   entries are verified, never rewritten.
3. Activate a complete release using one temporary symlink plus `os.replace` of
   `current`. Capture and verify the previous current target. On ordinary exception or
   HUP/INT/TERM before completion, restore old current or remove the newly created
   current when there was no predecessor. A crash after current replacement is allowed
   to leave the complete new generation active, never a mixed bundle.
4. Ensure prepare resolves both root helpers from `current/libexec`; no privileged
   process executes repo, Conda, service-user-writable or flat copied helper paths.
5. Test first install, idempotent reinstall, upgrade, rollback and unexpected existing
   objects; commit.

## Task 4: Prove failure, signal and concurrency invariants

**Modify:**

- `tests/test_temporal_deployment_assets.py`

1. Add a POSIX runner: native Linux executes the shell directly; Windows uses WSL.
   Stage-only tests must run in either environment. Live `/` tests require
   `MEDCHAT_RUN_TEMPORAL_LIVE_INSTALL_TESTS=1` and an explicitly documented disposable
   distro/container/mount namespace; they are skipped by default.
2. In temporary installer copies, inject deterministic failures at every generation
   write/publish/bootstrap/current-replace boundary without adding production hooks.
   Assert the active state is a complete old or complete new generation, manifest and
   current agree, sentinels survive, and no staging/temp/release residue from the failed
   invocation remains.
3. At a synchronization point after destination mutation begins, signal only the
   public installer PID with HUP, INT and TERM. Assert nonzero redacted failure, no
   child process, no partial active bundle and old-or-new invariants.
4. Run two installers built from different source bundles concurrently against one
   DESTDIR. Assert serialization and that final current, units, helpers and manifest
   all belong to exactly one bundle.
5. Commit tests and any minimal fixes.

## Task 5: Harden cgroup cleanup and deployment documentation

**Modify:**

- `tests/test_temporal_systemd_integration.py`
- `deployment/README.md`
- `tests/test_temporal_worker_trust_boundary.py`

1. Query `LoadState` and `ControlGroup` together. Before `systemctl kill`, query again
   and require the same non-empty cgroup. If the unit is unloaded and the original
   cgroup is absent/empty, return safely. Never signal stored numeric PIDs and never
   kill a newly loaded same-name unit.
2. Add tests for unload before cleanup, cgroup movement and same-name reload; preserve
   the original test failure instead of masking it with cleanup assertions.
3. Rewrite the worker install runbook around trusted source staging, generation
   preview, root live install, current/manifest verification, explicit daemon-reload,
   environment creation and prepare+worker restart. Remove tmpfiles instructions and
   all recursive chown guidance.
4. Document that live-root tests are destructive opt-in and must run only in an
   isolated disposable Linux environment.
5. Commit.

## Task 6: Final verification and dual review

Run with the MedChat Conda Python unless the command explicitly requires system Linux:

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\test_temporal_worker_trust_boundary.py tests\test_temporal_worker_directory_trust.py tests\test_temporal_deployment_assets.py tests\test_temporal_systemd_integration.py tests\task_runtime\test_production_worker.py -q -p no:cacheprovider
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\task_runtime tests\test_task_runtime_routes.py tests\test_temporal_docking_routes.py -q -p no:cacheprovider
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m compileall -q src scripts deployment\libexec
```

On real Linux/WSL root:

```bash
MEDCHAT_RUN_TEMPORAL_TRUST_INTEGRATION=1 python3 tests/test_temporal_worker_trust_linux_unittest.py
MEDCHAT_RUN_TEMPORAL_DIRECTORY_INTEGRATION=1 python3 tests/test_temporal_worker_directory_linux_unittest.py
systemd-analyze verify deployment/medchat-temporal-worker-prepare.service deployment/medchat-temporal-worker.service
```

Run the opt-in real systemd/cgroup suite where pytest and systemd are available. Any
missing real Linux evidence remains a release blocker and must be reported, not
converted to passed. Then request strict spec review followed by independent security
quality review. Any finding returns to the implement/fix/re-review loop.

## Acceptance invariants

- Root never invokes `systemd-tmpfiles` on service-user-writable descendants.
- Root never executes repo, Conda or service-user-writable code from systemd.
- A leaf symlink cannot change sentinel owner, mode, inode or contents.
- The active deployment is selected by one atomic `current` pointer and always has a
  matching immutable manifest.
- Concurrent, failed and signaled installs end in a complete old or complete new
  generation; no mixed executable bundle is reachable.
- The public installer PID is the signal-aware writer PID.
- Native Linux runs POSIX tests directly; destructive live-root tests are explicit
  opt-in and isolated.
- Cgroup cleanup revalidates unit identity immediately before kill and never signals a
  cached numeric PID.
