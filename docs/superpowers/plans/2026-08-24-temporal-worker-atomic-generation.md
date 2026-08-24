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

## Review corrections that govern every task

- Installation and activation are separate. `install-temporal-worker.sh` only creates
  an immutable generation. A separate operator command owns stop/switch/reload/verify/
  start and rollback; the installer never changes a running service's `current`.
- Live and DESTDIR locks use the exact relative path
  `run/medchat-temporal-worker/install.lock`. Creating the `0700` lock directory and
  `0600` lock file through trusted no-follow descriptors is the only permitted target
  mutation before all source assets have been pre-read. The parent and lock inode,
  owner and mode are fsynced and revalidated before `flock` is trusted.
- Bundle framing is exactly `MEDCHAT_TEMPORAL_BUNDLE_V1\0`, followed for each fixed
  asset in ASCII path order by 4-byte big-endian unsigned path length, path bytes,
  4-byte big-endian unsigned mode (`0..07777`), 8-byte big-endian unsigned bounded
  payload length and payload. Exact assets/modes are worker unit `0644`, prepare unit
  `0644`, environment helper `0755`, directory helper `0755`, using the installed
  logical paths in the design. Manifest first line is exactly
  `MEDCHAT_TEMPORAL_BUNDLE_V1 <64-lower-hex>\n`; four sorted rows are exactly
  `<asset-sha256> <4-octal-mode> <canonical-decimal-size> <logical-path>\n`, with one
  final LF. Generation name, header digest and recomputed digest must match. Duplicate,
  unknown, missing, unsorted, CR, blank or malformed content fails closed.
- `current` is only a relative `releases/<64-lower-hex>` symlink. Old and requested
  generations must fully validate before activation or rollback.
- Existing flat deployments require the explicit legacy migration in Task 3A. The
  verified-dangerous tmpfiles policy is never restored on migration failure.

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
   `exec /usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin /usr/bin/python3 -I` so
   the public installer PID is the isolated writer PID before any destination mutation.
3. In the Python installer, bootstrap `run/medchat-temporal-worker` with descriptor-
   relative `mkdirat` as exact `0700`, then bootstrap `install.lock` with
   `O_CREAT|O_EXCL|O_NOFOLLOW` as exact `0600`. Concurrent losers reopen no-follow and
   verify the winner's object. Verify owner/mode/inode, fsync the directory, then take
   exclusive `fcntl.flock`. This lock namespace is the only pre-read mutation.
4. Read all fixed assets through no-follow descriptors with per-file and bundle size
   bounds. For live install require root:root and no `022` source write bits. Compute
   the exact V1 framed digest defined above.
5. Construct a private same-filesystem staging generation under
   `usr/lib/medchat/temporal-worker/releases`. Write units, both helpers and
   canonical `manifest.sha256` (version+bundle digest header, then sorted
   `asset_sha256 mode size logical_path` rows); reject duplicate/unknown/missing or
   malformed rows; fsync files/directories; verify regular/no-symlink,
   owner/group/mode/inode/hash; make generation directories immutable to non-root;
   atomically rename to `releases/<bundle-sha256>` or verify an existing identical
   generation without modifying it.
6. Run focused tests and commit.

## Task 3: Add bootstrap entrypoints and transactional activation

**Modify:**

- `deployment/libexec/install-temporal-worker-bundle.py`
- `deployment/medchat-temporal-worker-prepare.service`
- `tests/test_temporal_deployment_assets.py`

**Create:**

- `deployment/activate-temporal-worker-generation.sh`

1. Write RED tests that fixed unit entries are root-owned symlinks with exact targets
   through `/usr/lib/medchat/temporal-worker/current/units/`. Existing unexpected
   entries must fail and remain untouched.
2. Bootstrap missing fixed symlinks with same-directory temporary symlinks and atomic
   replace; track only entries created by this invocation for rollback. Existing exact
   entries are verified, never rewritten.
3. The installer stops after publishing and validating a generation; it does not write
   `current`. The activation wrapper source-guards itself then `exec`s the Python
   operator path so its public PID is signal-aware. It accepts only one 64-lower-hex
   generation argument (rollback uses the same command with an older existing digest).
4. Under the same lock, validate the requested and current generation. Reject external,
   absolute, dangling, dot-component or malformed current targets. Record whether both
   services were active, stop worker then prepare and confirm inactive. Only then switch
   current atomically, run fixed `/usr/bin/systemctl daemon-reload`, verify loaded unit
   content against the target generation, and start prepare then worker.
5. On reload/verification/start failure, stop the new units, restore old current,
   daemon-reload again, and restart old services only if they were active before. With
   no old generation, remove current and leave services inactive. Tests cover automatic
   restart attempts during the stopped window and reload failure.
6. Ensure prepare resolves both root helpers from `current/libexec`; no privileged
   process executes repo, Conda, service-user-writable or flat copied helper paths.
7. Test first install, idempotent reinstall, upgrade, rollback and unexpected existing
   objects; commit.

8. Implement an explicit signal state machine for stopping, pre-switch, switched,
   reloaded, prepare-starting and worker-starting phases. Each absolute-path systemctl
   child runs in its own process group and remains owned by a live `Popen`. First
   HUP/INT/TERM blocks further install signals, terminates and bounded-waits/reaps the
   current child, then performs phase-aware rollback and old-service restoration while
   still holding the lock. Test HUP/INT/TERM at switch, reload, verify, prepare-start
   and worker-start; no child, lock or partial activation may remain.

## Task 3A: Migrate the known flat legacy deployment safely

**Modify:**

- `deployment/libexec/install-temporal-worker-bundle.py`
- `deployment/activate-temporal-worker-generation.sh`
- `tests/test_temporal_deployment_assets.py`

1. Add an explicit `--migrate-legacy <generation>` operator path. Stop worker/prepare,
   confirm inactive and inspect loaded state before touching files.
2. No-follow validate legacy regular unit files, flat environment helper and dangerous
   `/usr/lib/tmpfiles.d/medchat-temporal-worker.conf` against exact root owner, modes
   and a source-code allowlist of hashes for the supported predecessor commits. Unknown
   hash/type/symlink/mode/owner fails with no mutation.
3. First atomically move the verified dangerous tmpfiles file outside every tmpfiles
   search directory to the fixed same-filesystem path
   `usr/lib/medchat/temporal-worker/quarantine/legacy-tmpfiles.disabled`. Create the
   quarantine parent as root:root `0700`, install the file as root:root `0600`, and
   fsync both parents. Retry accepts only source-present/quarantine-absent or
   source-absent/quarantine-present-with-known-hash; all other combinations fail.
   Then transactionally create/activate the
   generation, convert the two known unit regular files to exact bootstrap symlinks,
   daemon-reload and verify loaded unit content before starting.
4. On failure, roll back only entries/current created by this invocation. Never restore
   the dangerous tmpfiles policy; keep services inactive and emit a stable manual-
   recovery-required status. Cover old prepare already active/loaded, unknown hashes,
   retry combinations, failure after every migration boundary and reload failure. After
   a failed migration, execute real global systemd-tmpfiles in an isolated root and
   prove the symlink sentinel owner/mode/inode/content is unchanged.

## Task 4: Prove failure, signal and concurrency invariants

**Modify:**

- `tests/test_temporal_deployment_assets.py`

**Create:**

- `tests/run_temporal_live_install_isolated.sh`

1. Add a POSIX runner: native Linux executes the shell directly; Windows uses WSL.
   Stage-only tests must run in either environment. Live `/` tests require
   `MEDCHAT_RUN_TEMPORAL_LIVE_INSTALL_TESTS=1`. The runner must create a private mount
   namespace, verify its namespace inode differs from PID 1, bind private roots over
   every touched `/etc`, `/usr` and lock target, and verify those mount sources before
   running. Real systemd activation/migration instead requires a root-owned disposable-
   environment marker plus an isolated VM/distro/container; otherwise refuse, not run.
2. In temporary installer copies, inject deterministic failures at every generation
   write/publish/bootstrap/current-replace/daemon-reload/start boundary without adding production hooks.
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

1. Require cgroup v2 and an openable `cgroup.kill`; otherwise emit an explicit
   production blocker with no fallback. Query `LoadState`, `ControlGroup`,
   `InvocationID` and pending `Job` together, then open the old cgroup directory and
   `cgroup.kill` no-follow and record device/inode.
2. Establish a start mutex for the test-owned random unit before force cleanup: open
   `/run/systemd/system` as a trusted root descriptor, create `<unit> -> /dev/null` with
   `O_EXCL` symlink semantics, record its lstat inode, fsync, and daemon-reload. Confirm
   `LoadState=masked`, no pending start/restart job, and the original InvocationID and
   cgroup identity. A mask collision or unexpected existing entry aborts cleanup.
3. Force cleanup writes only to the already-open old `cgroup.kill` fd. It never calls
   `systemctl kill <unit>` and never signals stored numeric PIDs. Because the verified
   mask rejects future starts, a new invocation cannot join the old inode after the
   final check. On exit, remove only the exact mask symlink whose inode and `/dev/null`
   target match this invocation, fsync and daemon-reload; otherwise report a blocker.
4. Add tests for unload before cleanup, cgroup movement and same-path reload with a new
   InvocationID and/or cgroup inode. At the synchronization point after final recheck
   but before fd write, attempt a same-name start/reload and prove the runtime mask
   rejects it and no new invocation joins the inode. Preserve
   the original test failure instead of masking it with cleanup assertions.
5. Rewrite the worker install runbook around trusted source staging, generation
   preview, explicit legacy migration, transactional activate/rollback,
   current/manifest verification, environment creation and prepare+worker restart.
   Remove tmpfiles instructions and all recursive chown guidance.
6. Document that live-root tests are destructive opt-in and must run only in an
   isolated disposable Linux environment.
7. Commit.

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

Run native POSIX staging and the technically isolated live-root suite:

```bash
python3 -m pytest tests/test_temporal_deployment_assets.py -q -p no:cacheprovider -k 'installer or generation or legacy_migration'
sudo /usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin \
  MEDCHAT_RUN_TEMPORAL_LIVE_INSTALL_TESTS=1 \
  tests/run_temporal_live_install_isolated.sh
```

The second command must create and verify its private mount namespace and bind roots;
an environment flag without those checks is a failure, not a skip. For real activation,
legacy migration and cgroup lifecycle, use a disposable systemd VM/distro/container
with `/run/medchat-disposable-systemd-test` pre-provisioned as root:root `0400`:

```bash
MEDCHAT_RUN_TEMPORAL_DEPLOYMENT_INTEGRATION=1 \
python3 -m pytest tests/test_temporal_systemd_integration.py \
  tests/test_temporal_deployment_assets.py -q -p no:cacheprovider \
  -k 'systemd or activation or legacy_migration'
```

Expected production-ready result: all three Linux groups execute and pass; a missing
marker, unavailable private mounts, missing pytest or unavailable systemd produces an
explicit skip/blocker and keeps Task 8 incomplete. Then request strict spec review
followed by independent security quality review. Any finding returns to the
implement/fix/re-review loop.

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
