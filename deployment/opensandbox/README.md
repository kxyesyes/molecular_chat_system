# OpenSandbox docking on an Ubuntu 24.04 QEMU host

This guide is the production acceptance path for MedChat docking through the
worker-side `SandboxDockingRunner`, the access-controlled Broker Unix socket,
OpenSandbox, Docker, and gVisor (`runsc`). It is intended for a dedicated
Ubuntu 24.04 QEMU virtual machine, not Docker Desktop, WSL, or the development
Windows host. The acceptance remains opt-in and a skip is never a pass.

## Preconditions and safety gates

- Use a disposable or snapshotted QEMU VM with hardware virtualization and at
  least 4 vCPU, 12 GiB RAM, and 40 GiB free disk.
- Work from a clean checkout at `/opt/medchat/molecular_chat_system` and keep
  runtime databases, registry storage, reports, and image layers out of Git.
- Docker Engine Server must be version 25.0.5 or newer. Older engines fail the
  deployment validator because this isolation policy depends on patched Docker
  behavior.
- The OpenSandbox API key must never enter Git, shell history, a report, logs,
  chat, a command-line argument, or copied terminal output. Acceptance reports
  expose only stable codes and approved provenance.
- Do not run the real test until the firewall, OpenSandbox, Broker, and worker
  services are active and the runtime validator passes.

Take a VM snapshot before installation. Commands below assume the repository
root is the current directory.

## 1. Install Docker Engine and verify the server

Install Docker Engine from Docker's official Ubuntu repository, including the
daemon and CLI. Do not use the Ubuntu `docker.io` package when it resolves to a
server older than 25.0.5. Then verify the server (not merely the client):

```bash
docker version --format '{{.Server.Version}}'
```

The value must compare as `>=25.0.5`. Start and enable Docker before continuing:

```bash
sudo systemctl enable --now docker.service
sudo systemctl is-active --quiet docker.service
```

## 2. Install gVisor and register `runsc`

Use the signed gVisor apt repository on Ubuntu 24.04:

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg openssl python3-venv
curl -fsSL https://gvisor.dev/archive.key | sudo gpg --dearmor -o /usr/share/keyrings/gvisor-archive-keyring.gpg
echo 'deb [signed-by=/usr/share/keyrings/gvisor-archive-keyring.gpg] https://storage.googleapis.com/gvisor/releases release main' | sudo tee /etc/apt/sources.list.d/gvisor.list >/dev/null
sudo apt-get update
sudo apt-get install -y runsc
sudo runsc install
sudo systemctl restart docker
runsc --version
docker info --format '{{json .Runtimes}}'
```

The final JSON must contain `runsc`. Do not configure `runsc` as Docker's
global default runtime; the OpenSandbox policy selects it explicitly.

## 3. Build and pin the QEMU-only docking image

The approved workflow uses a registry reachable only inside the QEMU host. A
loopback local registry avoids mutable local image tags crossing host
boundaries. This registry is an operator dependency and is not attached to a
sandbox container.

```bash
sudo docker run -d --restart=always --name medchat-registry -p 127.0.0.1:5000:5000 registry:2
sudo docker build --pull --no-cache -f deployment/opensandbox/Dockerfile.docking -t 127.0.0.1:5000/medchat-docking:approved .
sudo docker push 127.0.0.1:5000/medchat-docking:approved
DIGEST=$(sudo docker image inspect --format '{{index .RepoDigests 0}}' 127.0.0.1:5000/medchat-docking:approved)
case "$DIGEST" in 127.0.0.1:5000/medchat-docking@sha256:????????????????????????????????????????????????????????????????) ;; *) exit 1 ;; esac
sudo docker pull "$DIGEST"
```

`DIGEST` is the immutable `@sha256:` reference to place in the Broker runtime
environment. It is not secret, but the digest must not be replaced by a tag.
This local registry/image digest workflow is QEMU-only; do not substitute a
developer workstation daemon or copy its image ID.

## 4. Create the root-only runtime secret without shell history

Create the file through a root-owned process and feed the generated key over
stdin. The key is never expanded into the interactive command line, and
`openssl rand -hex 32` writes directly into the protected file. Temporarily
disable shell tracing if the operator shell enabled it.

```bash
set +x
sudo install -d -o root -g root -m 0700 /etc/medchat
openssl rand -hex 32 | sudo sh -c 'umask 077; IFS= read -r key; printf "OPEN_SANDBOX_API_KEY=%s\n" "$key" > /etc/medchat/opensandbox.env'
printf 'MEDCHAT_SANDBOX_IMAGE=%s\n' "$DIGEST" | sudo tee -a /etc/medchat/opensandbox.env >/dev/null
sudo chown root:root /etc/medchat/opensandbox.env
sudo chmod 0600 /etc/medchat/opensandbox.env
sudo stat -c '%U %G %a' /etc/medchat/opensandbox.env
unset DIGEST
```

Expected metadata is `root root 600`. Never use `export KEY=...`, `echo KEY=...`
with a literal key, `sudo -E`, `set -x`, a here-document containing the key, or
paste the key into Git, shell history, a report, logs, chat, or a test command.
Do not display the file. The installer projects the secret to the separate
root-owned service files required by the final Broker architecture.

## 5. Install and enable the service chain

Install the pinned Python environment and OpenSandbox package according to the
repository deployment lock before running the transactional installer. The
installer validates identities, modes, configuration, and the image digest:

```bash
sudo deployment/opensandbox/install.sh
sudo systemctl daemon-reload
sudo systemctl enable medchat-opensandbox-firewall.service medchat-opensandbox.service medchat-sandbox-broker.service medchat-temporal-worker.service
sudo systemctl restart medchat-opensandbox-firewall.service
sudo systemctl start medchat-opensandbox.service
sudo systemctl start medchat-sandbox-broker.service
sudo systemctl start medchat-temporal-worker.service
```

The firewall service is a required dependency of `medchat-opensandbox.service`,
not an optional ordering hint. Confirm all four gates without printing their
environment:

```bash
sudo systemctl is-active --quiet medchat-opensandbox-firewall.service
sudo systemctl is-active --quiet medchat-opensandbox.service
sudo systemctl is-active --quiet medchat-sandbox-broker.service
sudo systemctl is-active --quiet medchat-temporal-worker.service
```

## 6. Validate runtime and run real acceptance

First run the static/runtime deployment validator. It checks Docker >=25.0.5,
the registered gVisor runtime, root-owned internal network
`medchat-opensandbox`, firewall rules, service state, UDS ownership, and pinned
image digest.

```bash
/opt/conda/envs/medchat/bin/python scripts/validate_opensandbox_deployment.py --static
sudo /opt/conda/envs/medchat/bin/python scripts/validate_opensandbox_deployment.py --runtime
```

The real fixture is `data/samples/MAGL_5zun.pdb` plus
`data/samples/5.sdf`, centered at `[5.99, 3.01, 17.345]` with size
`[20, 20, 20]`. Both entry points require the explicit gate:

```bash
sudo env MEDCHAT_RUN_OPENSANDBOX_ACCEPTANCE=1 \
  MEDCHAT_SANDBOX_BROKER_SOCKET=/run/medchat-sandbox/broker.sock \
  /opt/conda/envs/medchat/bin/python -m pytest \
  tests/sandbox_broker/test_real_opensandbox_acceptance.py -q -p no:cacheprovider

sudo install -d -o root -g root -m 0700 outputs/agent_evaluation
sudo env MEDCHAT_RUN_OPENSANDBOX_ACCEPTANCE=1 \
  MEDCHAT_SANDBOX_BROKER_SOCKET=/run/medchat-sandbox/broker.sock \
  /opt/conda/envs/medchat/bin/python scripts/run_opensandbox_docking_acceptance.py \
  --repeat 3 --report outputs/agent_evaluation/opensandbox_docking_acceptance.json
```

Expected gates are: static and runtime validation pass; each actual sandbox is
observed with Docker `Runtime=runsc`; the owned network is exactly
`medchat-opensandbox`, internal, correctly labelled, and the container has no
second network; all three real docking runs have verified pose bytes/hash/count,
finite energy, allowlisted Vina/Meeko versions, immutable image digest, real
provenance, and successful cleanup. Idempotency must create one sandbox;
cancellation, timeout, PID exhaustion, and the setsid escaped-descendant probe
must leave no running sandbox. Host data, `.env`, task SQLite, Docker socket,
outbound DNS/direct-IP/host services, and non-loopback data-plane access must be
denied. `/etc` and `/opt/medchat` must be read-only while `/workspace/output`
is writable. CPU, memory, and PID limits must match the fixed Broker policy.

A missing environment gate produces an honest `skipped` report and exit code 2.
A missing dependency or failed security probe produces `failed` with a stable
reason; neither condition can produce a passing report. This procedure has not
run merely because unit/contract tests pass on Windows.

## Cleanup and rollback

Preserve a failed report only if it contains no sensitive material. Never copy
service environment files or journal output into an issue or chat. To rollback
the QEMU deployment:

```bash
sudo systemctl disable --now medchat-temporal-worker.service medchat-sandbox-broker.service medchat-opensandbox.service medchat-opensandbox-firewall.service
sudo docker ps -aq --filter label=opensandbox.io/id | xargs -r sudo docker rm -f
sudo docker network rm medchat-opensandbox || true
sudo docker rm -f medchat-registry || true
sudo rm -f /etc/systemd/system/medchat-opensandbox.service
sudo rm -f /etc/systemd/system/medchat-sandbox-broker.service
sudo rm -f /etc/systemd/system/medchat-opensandbox-firewall.service
sudo rm -f /etc/medchat/opensandbox.env /etc/medchat/sandbox-broker.env /etc/medchat/opensandbox.toml
sudo systemctl daemon-reload
```

Remove `/var/lib/opensandbox` and `/var/lib/medchat-sandbox` only after an
operator confirms they contain no evidence required for incident review. For a
full rollback, restore the pre-install QEMU snapshot; do not reuse a possibly
exposed API key.
