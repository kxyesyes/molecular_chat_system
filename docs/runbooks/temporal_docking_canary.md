# Temporal docking production canary runbook

This runbook is the operator procedure for the host-native Temporal docking
worker. The production target is native Linux with systemd, cgroup v2, Docker
Compose 2.17 or newer, and the repository's Python 3.10 Conda environment.
Windows and the SDK development server are validation/development targets only.

The Web service, worker, and Compose stack are separate process boundaries.
Compose is operator-managed and must be healthy before the worker is activated;
neither systemd unit starts or mutates Docker. The worker concurrency is always
`1`. Temporal, PostgreSQL, UI, Prometheus, Grafana, and worker metrics remain on
loopback or private container networks.

## 1. Host prerequisites

Perform release work from a reviewed checkout whose files and every parent are
`root:root` and not group/other writable. Do not execute the root launchers from
an ordinary user checkout. Confirm the release commit and checksums before
continuing.

```bash
test "$(uname -s)" = Linux
test -d /run/systemd/system
test -r /sys/fs/cgroup/cgroup.controllers
systemctl --version
docker compose version
/usr/bin/python3 --version
/opt/conda/envs/medchat/bin/python --version
/opt/conda/envs/medchat/bin/python scripts/health_check.py --strict
```

The activation cleanup protocol requires cgroup v2 and a usable `cgroup.kill`
file for the old worker cgroup. There is no PID-based or `systemctl kill`
fallback. Treat a missing capability, failed health check, unavailable Vina or
ADFRsuite, or an untrusted source/Conda tree as a release blocker.

Set an organization-approved positive `BACKUP_RETENTION_DAYS` in the external
backup-retention job before production. The repository backup command creates
one immutable dump and manifest and never deletes old backups; the retention job
must not delete the current verified marker or a backup still referenced by it.

## 2. Create trusted runtime configuration

Real secrets are runtime-only. Never put a password, API key, token, loader
variable, or secret value in Git, a command argument, shell history, logs, or a
report. Create `/etc/medchat` as a trusted root-owned directory, then edit the
non-secret environment files through a privileged editor:

```bash
sudo install -d -o root -g root -m 0755 /etc/medchat
sudo install -o root -g root -m 0600 \
  deployment/temporal/env.example /etc/medchat/temporal.env
sudoedit /etc/medchat/temporal.env

sudo install -o root -g root -m 0600 \
  deployment/temporal-worker.env.example /etc/medchat/temporal-worker.env
sudoedit /etc/medchat/temporal-worker.env
```

The worker file has an exact allowlist, contains no secrets, and must retain:

```text
MEDCHAT_TASK_BACKEND=temporal_canary
MEDCHAT_TEMPORAL_DOCKING_CONCURRENCY=1
MEDCHAT_TEMPORAL_NAMESPACE=default
MEDCHAT_TEMPORAL_DOCKING_QUEUE=medchat-docking
MEDCHAT_TEMPORAL_WORKER_METRICS_ADDRESS=127.0.0.1
```

The Web runtime file `/etc/medchat/medchat.env` is separate. It starts at
`MEDCHAT_TASK_BACKEND=temporal_canary` with
`MEDCHAT_TEMPORAL_CANARY_PERCENT=0`; it is the file changed by the canary
manager. Other application secrets in that file remain runtime-only. Both this
file and the dedicated worker file remain `root:root 0600`; systemd PID 1 reads
them before starting the unprivileged services.

Compose secret source files must be created by the approved host secret manager
without echoing their values. Each is a regular, non-symlink file with one value,
no trailing newline, at most 1024 bytes, and mode `0444`, inside one
`root:root 0700` directory. Set only their paths in `/etc/medchat/temporal.env`:

```text
TEMPORAL_POSTGRES_PASSWORD_FILE=<protected secret file>
TEMPORAL_POSTGRES_EXPORTER_PASSWORD_FILE=<protected secret file>
GRAFANA_ADMIN_PASSWORD_FILE=<protected secret file>
```

Before every start, verify the files and parent directory metadata through the
approved host provisioning process. Do not use `echo`, repository `.env` files,
or command-line secret values. An ordinary operator cannot read these files and
must not be added to a broadly privileged group as a workaround. Compose is run
through the fixed root command below: root reads the root-only env and secret
files, while `/usr/bin/env -i` prevents the caller's environment from crossing
the privilege boundary. Do not use `sudo -E` or `sudo --preserve-env`.

## 3. Render and activate the metrics relay before Prometheus

Run from the reviewed release root. All published ports in the committed Compose
file bind to `127.0.0.1`; do not add public port mappings. Render the metrics
relay first. Use its generation-specific override only to stop/recreate the
Prometheus container without starting it; this makes Compose create the private
`worker-metrics-scrape` bridge with the required ownership labels, subnet, and
gateway. Activate the relay and nginx while Prometheus remains stopped. Only
then start Prometheus with the published live override.

Production mode accepts only the fixed paths shown here. In particular,
`--worker-env-file` is `/etc/medchat/medchat.env`, and
`--nginx-command` is `/usr/sbin/nginx`. The existing
`/etc/medchat/generated` parent must be `root:root` and not group/other writable;
the relay creates only its final `temporal-metrics` child.

```bash
cd /usr/local/src/medchat-release
release_root=/usr/local/src/medchat-release
relay_root=/etc/medchat/generated/temporal-metrics
relay_nginx=/etc/nginx/conf.d/medchat-temporal-worker-metrics.conf

sudo install -d -o root -g root -m 0755 /etc/medchat/generated

relay_render="$(
  sudo /usr/bin/env -i \
    HOME=/root PATH=/usr/sbin:/usr/bin:/sbin:/bin \
    /opt/conda/envs/medchat/bin/python -I \
    "$release_root/scripts/configure_temporal_metrics_relay.py" \
    --render \
    --env-file /etc/medchat/temporal.env \
    --worker-env-file /etc/medchat/medchat.env \
    --output-dir "$relay_root" \
    --nginx-output "$relay_nginx" \
    --nginx-command /usr/sbin/nginx
)"
printf '%s\n' "$relay_render"
generation_id=${relay_render#OK E_RELAY_RENDERED generation=}
case "$generation_id" in *[!0-9a-f]*|'') exit 1;; esac
[ "${#generation_id}" -eq 24 ] || exit 1

compose_override="$relay_root/generations/$generation_id/compose.override.yml"
sudo /usr/bin/test -f "$compose_override"

sudo /usr/bin/env -i \
  HOME=/root PATH=/usr/sbin:/usr/bin:/sbin:/bin \
  /usr/bin/docker compose \
  --env-file /etc/medchat/temporal.env \
  -f deployment/temporal/docker-compose.yml \
  -f "$compose_override" \
  config --quiet

sudo /usr/bin/env -i \
  HOME=/root PATH=/usr/sbin:/usr/bin:/sbin:/bin \
  /usr/bin/docker compose \
  --env-file /etc/medchat/temporal.env \
  -f deployment/temporal/docker-compose.yml \
  -f "$compose_override" \
  stop --timeout 30 prometheus

sudo /usr/bin/env -i \
  HOME=/root PATH=/usr/sbin:/usr/bin:/sbin:/bin \
  /usr/bin/docker compose \
  --env-file /etc/medchat/temporal.env \
  -f deployment/temporal/docker-compose.yml \
  -f "$compose_override" \
  create --no-deps --force-recreate prometheus

running_prometheus="$(
  sudo /usr/bin/env -i \
    HOME=/root PATH=/usr/sbin:/usr/bin:/sbin:/bin \
    /usr/bin/docker compose \
    --env-file /etc/medchat/temporal.env \
    -f deployment/temporal/docker-compose.yml \
    -f "$compose_override" \
    ps --status running --services prometheus
)"
[ -z "$running_prometheus" ] || exit 1

sudo /usr/bin/env -i \
  HOME=/root PATH=/usr/sbin:/usr/bin:/sbin:/bin \
  /opt/conda/envs/medchat/bin/python -I \
  "$release_root/scripts/configure_temporal_metrics_relay.py" \
  --activate "$generation_id" \
  --env-file /etc/medchat/temporal.env \
  --worker-env-file /etc/medchat/medchat.env \
  --output-dir "$relay_root" \
  --nginx-output "$relay_nginx" \
  --nginx-command /usr/sbin/nginx

sudo /usr/bin/env -i \
  HOME=/root PATH=/usr/sbin:/usr/bin:/sbin:/bin \
  /usr/sbin/nginx -t

sudo /usr/bin/env -i \
  HOME=/root PATH=/usr/sbin:/usr/bin:/sbin:/bin \
  /usr/bin/docker compose \
  --env-file /etc/medchat/temporal.env \
  -f deployment/temporal/docker-compose.yml \
  -f "$relay_root/live/compose.override.yml" \
  config --quiet

sudo /usr/bin/env -i \
  HOME=/root PATH=/usr/sbin:/usr/bin:/sbin:/bin \
  /usr/bin/docker compose \
  --env-file /etc/medchat/temporal.env \
  -f deployment/temporal/docker-compose.yml \
  -f "$relay_root/live/compose.override.yml" \
  up -d --wait --wait-timeout 180 temporal

if ! sudo /usr/bin/env -i \
  HOME=/root PATH=/usr/sbin:/usr/bin:/sbin:/bin \
  /usr/bin/docker compose \
  --env-file /etc/medchat/temporal.env \
  -f deployment/temporal/docker-compose.yml \
  -f "$relay_root/live/compose.override.yml" \
  run --rm --no-deps --no-TTY temporal-namespace
then
  exit 1
fi

sudo /usr/bin/env -i \
  HOME=/root PATH=/usr/sbin:/usr/bin:/sbin:/bin \
  /usr/bin/docker compose \
  --env-file /etc/medchat/temporal.env \
  -f deployment/temporal/docker-compose.yml \
  -f "$relay_root/live/compose.override.yml" \
  up -d --wait --wait-timeout 180 prometheus
```

Render prints exactly `OK E_RELAY_RENDERED generation=<24 lowercase hex>` and
stages `compose.override.yml`, `file_sd/worker-targets.json`, `nginx.conf`, and a
hash-bound manifest. Compose consumes the staged override first only to
force-recreate a stopped Prometheus container and the expected
project-owned/labeled bridge. The explicit running-service check must remain
empty; activation is forbidden while Prometheus is running against an
unpublished generation. Prometheus must never be started from the base Compose
file alone.

Activate re-reads both env files, rechecks routes and Docker networks, and
refuses to proceed unless that exact private bridge exists. It then publishes
the nginx config, runs the pinned `/usr/sbin/nginx -t`, reloads nginx, and only
after both succeed atomically publishes the live override, manifest, and worker
target. The explicit post-activation `nginx -t` above is an additional operator
check; activation has already performed both test and reload. The final Compose
commands use `live/compose.override.yml`. First, `up ... temporal` waits for
PostgreSQL, schema setup, and Temporal health. The synchronous, dependency-free
`run --rm --no-deps --no-TTY temporal-namespace` then executes the bounded namespace
setup against that healthy server; any nonzero exit stops the procedure. Only
after this one-shot succeeds does `up ... prometheus` start and wait for its
remaining `temporal-exporter-role-sync` and `postgres-exporter` dependencies.
It does not start unrelated `temporal-ui` or `grafana` at this gate.

The schema, namespace, Temporal server, and exporter role-sync wrappers mounted
by Compose are part of the validated deployment contract. Do not replace their
entrypoints or commands. Do not manually create the bridge, nginx file,
file-SD target, or live override.

## 4. Stage and activate an immutable worker generation

`deployment/install-temporal-worker.sh` only stages a content-addressed
generation. It does not create `current`, install a production environment file,
reload systemd, enable a unit, or start a service.

First stage into an operator-owned `0700` preview directory and record the exact
64-character digest printed by the installer:

```bash
preview="$(mktemp -d)"
chmod 0700 "$preview"
deployment/install-temporal-worker.sh --destdir "$preview"
test ! -e "$preview/usr/lib/medchat/temporal-worker/current"
cat "$preview/usr/lib/medchat/temporal-worker/releases/<digest>/manifest.sha256"
```

After review, place the identical release in the trusted root-owned staging tree
and run live staging. A live destination alias such as `/./`, `/tmp/..`, or `///`
is normalized to `/` and cannot bypass root/source trust checks.

```bash
cd /usr/local/src/medchat-release
release_root=/usr/local/src/medchat-release
sudo "$release_root/deployment/install-temporal-worker.sh" --destdir /
```

Activate the digest in a separate transaction:

```bash
sudo "$release_root/deployment/activate-temporal-worker-generation.sh" <digest>
sudo systemctl enable medchat-temporal-worker.service
sudo systemctl status medchat-temporal-worker-prepare.service \
  medchat-temporal-worker.service
```

After the worker is active, wait for one file-SD refresh and scrape interval and
require the exact worker job to report one target with value `1`:

```bash
/opt/conda/envs/medchat/bin/python -I - <<'PY'
import json
import time
import urllib.parse
import urllib.request

query = 'up{job="medchat-temporal-worker"}'
url = "http://127.0.0.1:9090/api/v1/query?" + urllib.parse.urlencode(
    {"query": query}
)
deadline = time.monotonic() + 90
while time.monotonic() < deadline:
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            payload = json.load(response)
        result = payload.get("data", {}).get("result", [])
        if (
            payload.get("status") == "success"
            and len(result) == 1
            and result[0].get("metric", {}).get("job")
            == "medchat-temporal-worker"
            and result[0].get("value", [None, None])[1] == "1"
        ):
            print("temporal_worker_target_up=1")
            break
    except (OSError, ValueError):
        pass
    time.sleep(2)
else:
    raise SystemExit("temporal_worker_target_not_up")
PY
```

Failure to reach exactly one `up` target blocks deployment and canary preflight.
After that gate passes, start the two operator-facing services with the same
base and live override; their declared dependencies reuse the already healthy
Temporal and Prometheus services:

```bash
cd /usr/local/src/medchat-release
relay_root=/etc/medchat/generated/temporal-metrics
sudo /usr/bin/env -i \
  HOME=/root PATH=/usr/sbin:/usr/bin:/sbin:/bin \
  /usr/bin/docker compose \
  --env-file /etc/medchat/temporal.env \
  -f deployment/temporal/docker-compose.yml \
  -f "$relay_root/live/compose.override.yml" \
  up -d --wait --wait-timeout 180 temporal-ui grafana
```

Activation verifies the directory digest, canonical manifest, and all four
assets; switches `current`; reloads systemd; verifies the actually loaded unit
content; then starts prepare before the worker. The prepare service runs only the
generation's root-owned environment and directory helpers. Those helpers create
or validate exactly `scratch`, `scratch/task_inputs`,
`scratch/temporal_backups`, and `temp_docking` as `medchat:medchat 0700`.

Never manually copy a worker unit or helper, alter `current`, or edit a release.
Doing so bypasses the bundle digest/manifest and loaded-unit identity checks.
There is no supported root `systemd-tmpfiles` path for this worker.

After changing `/etc/medchat/temporal-worker.env`, re-run the trust boundary in
this order:

```bash
sudo systemctl restart medchat-temporal-worker-prepare.service
sudo systemctl restart medchat-temporal-worker.service
```

## 5. Legacy migration and generation rollback

Use legacy migration exactly once, and only for a known flat predecessor whose
hash is in the committed allowlist:

```bash
sudo /usr/local/src/medchat-release/deployment/activate-temporal-worker-generation.sh \
  --migrate-legacy <digest>
```

Unknown assets fail closed. The migration permanently quarantines the dangerous
legacy tmpfiles policy outside systemd's search path; never restore it.

Generation rollback is an ordinary activation of a previously staged and fully
verified digest:

```bash
sudo /usr/local/src/medchat-release/deployment/activate-temporal-worker-generation.sh \
  <old-digest>
```

Activation and rollback stop the worker/prepare pair, switch and verify the
generation transactionally, and restore only services that were previously
active. If rollback verification fails, both units remain inactive and require
operator investigation.

## 6. Validate the deployment

Create the report parent beforehand; the validator will not follow or create an
untrusted parent chain.

```bash
/opt/conda/envs/medchat/bin/python -I \
  scripts/validate_temporal_deployment.py \
  --output scratch/temporal_backups/deployment-validation.json

systemd-analyze verify \
  deployment/medchat.service \
  deployment/medchat-temporal-worker.service
```

The validator checks Compose, all mounted bootstrap scripts, both committed
systemd units, Prometheus config/rules/tests, and Grafana provisioning. Its
`systemd-analyze` command intentionally verifies the Web and worker units; the
prepare unit is validated by the static generation contract.

## 7. Create a backup and verify an isolated restore

Run these commands as the `medchat` service account from an operator session in
which the secret manager has injected `TEMPORAL_POSTGRES_PASSWORD` into the
process environment. Do not place the password in argv or a report, and unset it
when the session ends. Use the trusted, version-specific server tool directory;
do not rely on `PATH`.

```bash
backup_dir=/opt/medchat/molecular_chat_system/scratch/temporal_backups
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
manifest="$backup_dir/temporal-$stamp.manifest.json"
postgres_bin=/usr/lib/postgresql/16/bin

/opt/conda/envs/medchat/bin/python -I \
  scripts/backup_temporal_postgres.py \
  --host 127.0.0.1 --port 5433 \
  --database temporal --user temporal \
  --output-dir "$backup_dir" \
  --manifest-output "$manifest" \
  --postgres-bin-dir "$postgres_bin"

/opt/conda/envs/medchat/bin/python -I \
  scripts/restore_temporal_postgres.py \
  --manifest "$manifest" \
  --source-database temporal \
  --target-database temporal_verify \
  --host 127.0.0.1 --port 5433 --user temporal \
  --postgres-bin-dir "$postgres_bin" \
  --drop-verification-database

unset TEMPORAL_POSTGRES_PASSWORD
```

The restore checks the PostgreSQL server major against the manifest before any
database mutation, restores through the held dump descriptor with
`--exit-on-error --single-transaction`, checks required Temporal tables, drops
only `temporal_verify` when explicitly requested, and then atomically publishes
`latest-verified.json`. It never drops or overwrites the source `temporal`
database. A failed final verification or cleanup must not publish a passed
marker.

## 8. Run production preflight at 0%

Run preflight as `medchat` with the exact validated runtime environment. The
parent process runs contract repeat 3 and real scientific repeat 3; the real
child receives an isolated `canary=100` override without editing either
production environment file.

```bash
/opt/conda/envs/medchat/bin/python -I \
  scripts/run_temporal_production_preflight.py \
  --prometheus-url http://127.0.0.1:9090 \
  --output scratch/temporal_backups/preflight-level-0.json
```

Only `status=passed` is promotion evidence. `partial` means at least one required
host, infrastructure, alert, backup/restore, or real-science check was skipped or
unavailable; it blocks promotion just like failed evidence. Contract/replay and
fake runners never count as real Vina, Temporal, or PostgreSQL evidence.

## 9. Promote and observe each level

The only legal sequence is `0 -> 5 -> 10 -> 25`; there is no force option and no
level may be skipped. The manager atomically edits the Web runtime file. Restart
the Web service after each change so only new submissions use the new level.

```bash
sudo /opt/conda/envs/medchat/bin/python -I \
  scripts/manage_temporal_canary.py promote \
  --env-file /etc/medchat/medchat.env \
  --to 5 \
  --evidence scratch/temporal_backups/preflight-level-0.json \
  --reason "approved level 5 canary"
sudo systemctl restart medchat.service
```

For each nonzero level, choose an explicit UTC window and generate observation
evidence from the real TaskStore, Prometheus, and latest verified backup marker:

```bash
/opt/conda/envs/medchat/bin/python -I \
  scripts/observe_temporal_canary.py \
  --db-path scratch/tasks.sqlite \
  --level 5 \
  --window-start <UTC-ISO-8601> \
  --window-end <UTC-ISO-8601> \
  --prometheus-url http://127.0.0.1:9090 \
  --backup-state scratch/temporal_backups/latest-verified.json \
  --baseline-p95-seconds <approved-positive-baseline> \
  --output scratch/temporal_backups/observation-level-5.json
```

An observation passes only after either 20 real Temporal-accepted tasks have
reached terminal state, or a full 24 hours has elapsed with at least 3 such
tasks. Zero, contract, replay, local, demo, fallback, or fabricated tasks do not
count. All failure-rate, p95, heartbeat, queue, release-blocker alert, backup,
Vina-attempt, terminal-event, artifact/hash, and provenance gates must pass.

Promote only with the immediately preceding level's unexpired passed evidence:

```bash
sudo /opt/conda/envs/medchat/bin/python -I \
  scripts/manage_temporal_canary.py promote \
  --env-file /etc/medchat/medchat.env \
  --to 10 \
  --evidence scratch/temporal_backups/observation-level-5.json \
  --reason "approved level 10 canary"
sudo systemctl restart medchat.service

# Repeat observation with --level 10 and a new window/output, then:
sudo /opt/conda/envs/medchat/bin/python -I \
  scripts/manage_temporal_canary.py promote \
  --env-file /etc/medchat/medchat.env \
  --to 25 \
  --evidence scratch/temporal_backups/observation-level-10.json \
  --reason "approved level 25 canary"
sudo systemctl restart medchat.service
```

Check the active level without mutation:

```bash
sudo /opt/conda/envs/medchat/bin/python -I \
  scripts/manage_temporal_canary.py status \
  --env-file /etc/medchat/medchat.env
```

## 10. Incident response

Any release blocker, duplicate Vina attempt, multiple terminal event, artifact
or hash failure, stale worker, queue mismatch, backup failure, or scientific
provenance failure first routes new work to 0%:

```bash
sudo /opt/conda/envs/medchat/bin/python -I \
  scripts/manage_temporal_canary.py rollback \
  --env-file /etc/medchat/medchat.env \
  --reason "release blocker firing"
sudo systemctl restart medchat.service

/opt/conda/envs/medchat/bin/python -I \
  -m src.task_runtime.temporal.reconcile
```

Rollback changes only new-flow routing. It does not delete Temporal history,
TaskStore rows, staging/completion records, reports, or artifacts, and it never
locally replays a task already accepted by Temporal. Keep the worker available so
accepted workflows can converge unless worker execution itself is the incident;
in that case stop both worker units and preserve all evidence for recovery.

If the worker release is defective, canary rollback comes first, followed by
generation rollback to a previously verified digest. Do not restore legacy
tmpfiles or bypass activation with manual unit copies.

## 11. Evidence and report locations

- Task projections: `scratch/tasks.sqlite`.
- Durable inputs, completion authority, and verified pose artifacts:
  `scratch/task_inputs/<task-id>/` using only relative artifact paths.
- Per-execution Vina workspace: `temp_docking/`; it is not promotion evidence.
- Backup dumps, canonical manifests, and `latest-verified.json`:
  `scratch/temporal_backups/`.
- Deployment, preflight, and observation reports: operator-selected regular files
  under a pre-existing trusted `0700` directory; the examples above use
  `scratch/temporal_backups/`.

Reports contain canonical SHA-256 values and redacted projections. Never copy
task payloads, SMILES, absolute artifact paths, environment dumps, or secret
values into a report or incident ticket.

## 12. Truthful status semantics

Every deployment check is explicitly `passed`, `failed`, or `skipped`.
Unavailable Linux, systemd, Docker, promtool, Temporal, PostgreSQL, Prometheus, or
Vina evidence is `skipped`/`partial`, never `passed`. A preflight containing any
required skipped evidence cannot authorize 5%. Run native systemd, cgroup,
isolated live-install, trust-boundary, backup/restore, and real scientific gates
on the production candidate host before declaring release readiness.
