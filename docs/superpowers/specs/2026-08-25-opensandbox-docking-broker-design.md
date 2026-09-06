# OpenSandbox Docking Broker Design

**Date:** 2026-08-25

**Status:** Approved for implementation planning

## 1. Objective

Add a production-oriented sandbox boundary for MedChat molecular docking without
replacing the existing Supervisor, workflow planner, Temporal runtime, docking
domain service, or `ToolResult` contract. The first delivery moves only the
Vina/Meeko docking execution path behind a dedicated MedChat Sandbox Broker,
which provisions an OpenSandbox instance backed by Docker and gVisor.

The QEMU Ubuntu 24.04 environment is the first deployment and acceptance target.
Production Linux rollout follows only after the real sample docking and security
gates pass.

## 2. Non-goals

- Do not migrate RDKit property calculations, ADMET, RG-MPNN, Ollama, target
  search, RAG, or reverse-target prediction in this phase.
- Do not replace Temporal with OpenSandbox lifecycle management.
- Do not expose arbitrary code, shell, image, entrypoint, environment, mount, or
  network configuration to users or the Agent.
- Do not add Kubernetes, Kata Containers, Firecracker, a sandbox warm pool, or
  multi-node scheduling.
- Do not silently fall back to host-native Vina when the OpenSandbox backend was
  selected.

## 3. Selected approach

Use a dedicated MedChat Sandbox Broker between the Temporal docking activity and
OpenSandbox:

```text
Web / Supervisor
        |
Temporal Workflow / Docking Activity
        |
MedChat Sandbox Broker (Unix socket)
        |
OpenSandbox Python SDK
        |
OpenSandbox Server (loopback only)
        |
Docker + gVisor runsc
        |
Pinned medchat-docking image
        |
Vina / Meeko
```

The Broker is intentionally narrower than OpenSandbox. It exposes one operation,
`molecular_docking`, and owns the MedChat-specific policy, validation, persistence,
provenance, and artifact contracts. OpenSandbox owns sandbox lifecycle and Docker
interaction. Only OpenSandbox Server may access the Docker socket.

## 4. Trust boundaries

### 4.1 Web, Agent, and Temporal Worker

These components may construct a typed docking request and submit it to the
Broker. They cannot choose an image, command, entrypoint, mount, runtime, network
mode, secret, or environment variable. They never receive the OpenSandbox API
key and never access the Docker socket.

### 4.2 MedChat Sandbox Broker

The Broker:

- accepts only the docking API described below;
- validates receptor, ligand, docking box, file sizes, finite numbers, and hashes;
- assigns server-generated file names and sandbox metadata;
- selects a configured image pinned by digest;
- enforces resource, concurrency, timeout, and retention policy;
- persists job state in its own SQLite database;
- validates scientific output before publishing artifacts;
- redacts secrets and host paths from responses and logs;
- always attempts sandbox cleanup and records cleanup status.

The Broker cannot accept arbitrary Docker or OpenSandbox lifecycle requests.

### 4.3 OpenSandbox Server

OpenSandbox Server listens on loopback, authenticates the Broker, and exclusively
owns Docker access. Its server configuration selects Docker mode and gVisor:

```toml
[runtime]
type = "docker"

[secure_runtime]
type = "gvisor"
docker_runtime = "runsc"
```

Startup must fail if `runsc` is unavailable. Standard `runc` is not an accepted
runtime for the real acceptance environment.

### 4.4 Docking sandbox

The sandbox runs as a non-root user from a pinned image with a read-only root
filesystem. It receives only normalized input files and a generated request JSON.
It has no project source, `.env`, task database, Docker socket, host credentials,
or general network access. A writable task workspace contains input and output
only.

## 5. Broker API

The Broker serves HTTP over an access-controlled Unix socket.

### 5.1 Submit

`POST /v1/docking/jobs`

- Requires an `Idempotency-Key` header.
- Accepts receptor and ligand file bodies plus structured box data.
- Returns `202 Accepted` with `job_id`, `trace_id`, `status`, and timestamps.
- Repeating the same key and same canonical input returns the existing job.
- Reusing the same key with different input returns a stable conflict error.

### 5.2 Observe

`GET /v1/docking/jobs/{job_id}` returns the current state, phase, elapsed time,
warnings, provenance, cleanup state, and a sanitized structured error when
applicable.

`GET /v1/docking/jobs/{job_id}/manifest` becomes available only after validated
success. It contains the pose count, finite best energy, artifact identifiers,
sizes, hashes, and scientific provenance.

`GET /v1/docking/jobs/{job_id}/artifacts/{artifact_id}` returns only a registered
artifact. The route never accepts or resolves caller-provided filesystem paths.

### 5.3 Cancel

`POST /v1/docking/jobs/{job_id}/cancel` requests sandbox termination and records
`cancelled`. Repeated cancellation is idempotent.

## 6. State model

```text
queued -> provisioning -> uploading -> running -> validating -> succeeded
   |           |             |          |            |
   +-----------+-------------+----------+------------+-> failed
   +-----------+-------------+----------+------------+-> cancelled
   +-----------+-------------+----------+------------+-> expired
```

Terminal states never transition back to active states. A Temporal retry with the
same idempotency key observes or reuses the existing Broker job rather than
creating another sandbox.

The Broker uses a dedicated SQLite database under its state directory. It records
job, trace, and idempotency IDs; canonical input hash; state transitions; sandbox
ID; image URI and digest; secure runtime; Vina and Meeko versions; exit code;
warnings; error code; artifact metadata; and cleanup result. It does not store API
keys, user-provided host paths, or secrets.

## 7. Docking image and command contract

The image contains fixed Vina, Meeko, conversion dependencies, and
`/opt/medchat/run_docking.py`. The command is fixed by Broker policy. User text is
never interpolated into a shell command.

Inputs:

```text
/workspace/input/receptor
/workspace/input/ligand
/workspace/input/request.json
```

Outputs:

```text
/workspace/output/result.json
/workspace/output/poses/*
```

`request.json` contains a schema version, canonical input hashes, finite center
and size vectors, and fixed execution options. `result.json` contains schema
version, echoed input hashes, status, pose count, best energy, tool versions,
warnings, and relative pose names.

No scientific result is successful unless the sandbox command exits with zero,
`pose_count` is positive, best energy is a finite number, every declared pose is
present and bounded, the pose format passes validation, and echoed input hashes
match the request.

## 8. Resource and retention policy

Initial policy:

- maximum 2 CPU cores;
- maximum 4 GiB memory;
- maximum 128 PIDs;
- receptor maximum 50 MiB;
- ligand maximum 10 MiB;
- total published output maximum 100 MiB;
- docking execution deadline 180 seconds;
- sandbox lifetime deadline 300 seconds;
- one concurrent docking job;
- no sandbox network;
- task files retained for 24 hours;
- audit metadata retained for 30 days.

Limits are configured by the operator within bounded ranges. Request callers
cannot raise them.

## 9. Error contract

Stable Broker error codes distinguish invalid input, idempotency conflict,
unauthorized caller, queue saturation, OpenSandbox unavailable, provisioning
failure, upload failure, execution timeout, command failure, invalid scientific
output, artifact failure, cancellation, expiration, and cleanup failure.

The MedChat adapter maps these to existing `AgentErrorCode` and `ToolResult`
fields. OpenSandbox exceptions, Docker details, API keys, raw commands, and host
paths are not returned to the Agent. Missing dependencies and failed tools are
never converted into successful prose or fabricated binding energies.

## 10. Project components

```text
src/sandbox_broker/
  app.py                 FastAPI and Unix-socket API assembly
  config.py              bounded environment configuration
  models.py              request, state, manifest, provenance, errors
  store.py               dedicated SQLite state and idempotency
  service.py             async state machine and job lifecycle
  opensandbox_client.py  narrow OpenSandbox SDK adapter
  validation.py          input and scientific output validation
  artifacts.py           atomic artifact publication and retention

src/docking/sandbox_runner.py
                         docking-domain Broker client adapter

deployment/opensandbox/
  Dockerfile.docking
  run_docking.py
  sandbox.example.toml
  medchat-sandbox-broker.service
  medchat-opensandbox.service
  README.md
```

OpenSandbox dependencies live in a dedicated requirements file. Ordinary Web
development does not require the sandbox server packages.

## 11. Migration

The docking execution backend is explicit: `local` or `opensandbox`. There is no
automatic fallback. Existing development remains compatible during the POC, but
the QEMU acceptance configuration forces `opensandbox`. A later production
validator rejects `local` in production mode.

Rollout sequence:

1. Contract tests with a fake OpenSandbox client.
2. QEMU installation and gVisor runtime validation.
3. Build and pin the docking image.
4. Run the repository MAGL sample at least three times.
5. Compare pose count, energy range, latency, and artifacts with the trusted
   host-native baseline without requiring identical stochastic output.
6. Verify escape, file, network, resource, timeout, cancellation, cleanup, and
   idempotency gates.
7. Enable a small production canary only after all required evidence is current.
8. Make OpenSandbox the production default after canary stability is accepted.

## 12. Test strategy and acceptance gates

### Unit tests

- request and finite-number validation;
- file type and size limits;
- state transition legality;
- idempotency reuse and conflict;
- stable error mapping and redaction;
- result schema, pose, energy, hash, and output-size validation;
- artifact identifier safety and atomic publication.

### Broker API tests

- Unix-socket authorization boundary;
- submit, observe, manifest, artifact, cancel, and repeated requests;
- queue saturation, timeout, malformed uploads, path attacks, and oversized data;
- no arbitrary image, command, environment, mount, network, or filesystem path.

### OpenSandbox contract tests

An injected fake SDK verifies provisioning, upload, fixed execution, observation,
download, cancellation, cleanup, TTL, and provenance without Docker.

### Real QEMU acceptance

Required evidence:

- OpenSandbox Server startup passes with `secure_runtime.type = "gvisor"`;
- Docker inspection reports `Runtime: runsc`;
- the sandbox cannot read the project, `.env`, or task databases;
- external network access fails;
- CPU, memory, PID, wall-time, and output limits take effect;
- cancellation and timeout leave no running sandbox container or child process;
- the same idempotency key creates at most one sandbox;
- three repository MAGL sample runs produce positive pose counts, finite energies,
  existing pose artifacts, complete hashes, and real tool provenance;
- no OpenSandbox, gVisor, Vina, or artifact failure is reported as success;
- the selected path does not execute host-native Vina.

## 13. References

- OpenSandbox project and Python SDK:
  <https://github.com/opensandbox-group/OpenSandbox>
- OpenSandbox secure container runtime guide:
  <https://github.com/opensandbox-group/OpenSandbox/blob/main/docs/guides/secure-container.md>
- gVisor security architecture:
  <https://gvisor.dev/docs/architecture_guide/intro/>
- gVisor installation and Docker runtime configuration:
  <https://gvisor.dev/docs/user_guide/install/>
