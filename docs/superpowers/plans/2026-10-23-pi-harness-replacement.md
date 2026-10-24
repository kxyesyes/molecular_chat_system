# Pi Harness Replacement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. The user approved the replacement boundaries in the implementation request; this plan executes that scope, not a new framework selection.

**Goal:** Make official Pi the sole general-purpose Web Agent decision runtime and retire the custom harness without deleting scientific capabilities or safety invariants.

**Architecture:** An isolated Node process owns the official Pi AgentSession and its decision loop. Python owns scientific session callbacks, authorization, durable task state and model transport. A custom Pi provider delegates one bounded model transaction to the existing Python model adapter (preserving native/JSON compatibility and pinned-network policy); it never calls an old Agent or executes a Python decision loop. Pi's custom tools dispatch one validated domain action, clarification or evidence-checked finish, with Pi's native `terminate` tool-result semantics ending the run. Existing deterministic workflows execute their existing executor directly.

**Tech Stack:** Python 3.10/FastAPI, existing domain tooling and SQLite state store, Node >=22.19.0, official `@earendil-works/pi-coding-agent@1.4.0`, Node's built-in test runner; no frontend build system.

**Execution status: incomplete.** The Pi host/bridge and harness retirement are implemented, but final cross-entrypoint verification found Task 3/5 completeness gaps: the old default Web supported scientific capabilities outside the four-tool decision admission now used by default. Keeping their tools/deterministic APIs does not preserve those Web capabilities. No preseeded-candidate workaround or weakened scientific assertion is accepted. The user explicitly approved complete Web restoration; implementation follows the [full-Web restoration addendum](2026-10-23-pi-full-web-restoration.md), with a distinct versioned domain profile rather than a silent B1 upgrade. Task 6 release remains held until those contracts and baseline flows pass. The checklist below records the original work breakdown; exact results and limits are in [the validation record](../validation/2026-10-23-pi-harness-replacement.md).

## Baseline

- Upstream main: `616d8e9e`; dedicated branch: `codex/pi-harness-replacement`.
- Existing focused baseline: 194 passed, 7 existing deprecation warnings (65.74 s).
- Baseline command: `python -m pytest tests/agent/test_harness_legacy.py tests/agent/test_supervisor_harness_integration.py tests/agent/test_decision_loop.py tests/agent/test_web_decision_runtime.py tests/agent/test_worker_ownership.py -q -p no:cacheprovider --tb=short` with a unique temporary directory.
- The first sandboxed run failed at pytest temporary-directory creation (194 setup errors); it was rerun with normal Windows permissions. This is not a product regression.

## Task 1: Official Pi host contract

**Files:** create `runtime/pi/package.json`, lockfile, `runtime/pi/host.mjs`, `runtime/pi/session.mjs`, and `runtime/pi/test/session.test.mjs`.

- [ ] Add an architectural/behavioral test before production code. Verify the module is absent with an assertion rather than an accidental import error; then test the actual exported session using a scripted single-request provider:

```js
assert.equal(existsSync(new URL('../session.mjs', import.meta.url)), true,
  'the official Pi session host must exist');
```

- [ ] Add a test in which the model requests a domain tool, consumes its observation, then finishes; assert the official Pi session emits tool lifecycle events and calls the provider twice. Add invalid tool, terminal finish, abort, tool failure and concurrent-run isolation cases.
- [ ] Implement one official AgentSession per admitted run with in-memory credentials/settings/session manager and a resource loader that exposes no discovered resources. Explicit tool allowlist excludes built-in read/write/shell tools. Register the MedChat provider through the public registration API.

```js
const runtime = await ModelRuntime.create({
  credentials: new InMemoryCredentialStore(), modelsPath: null,
  allowModelNetwork: false, refreshOnCreate: false,
});
const manager = SessionManager.inMemory(cwd);
const settings = SettingsManager.inMemory({
  compaction: { enabled: false }, retry: { enabled: false },
});
```

- [ ] Use bounded JSONL requests with run/request IDs, one active run per child, fixed error codes and no raw error/credential logging. Node only requests model transactions and authorized tools over stdio; no cloud credentials are passed to Node. Use native tool result `{content, details, terminate: true}` for validated finish/clarification rather than an extra custom loop.
- [ ] Run `node --test runtime/pi/test/*.test.mjs` and `node --check` for host modules. Pin all direct dependencies and lock transitive dependencies. Installation scripts remain disabled unless an audited required dependency proves necessary.

## Task 2: Python bridge and provider

**Files:** create `src/agent/pi_runtime/{__init__,bridge,provider}.py` and `tests/agent/test_pi_bridge.py`, `tests/agent/test_pi_provider.py`.

- [ ] Write failure-first tests for missing host, protocol version, oversized frames, malformed response IDs, child exit, cancellation and model timeouts. The child cannot select a URL or supply credentials; the captured Python model owns transport configuration.
- [ ] Provider converts Pi-owned history to the existing single-model-transaction protocol, and a validated provider response into one Pi tool call. It preserves native/JSON modes without implementing iteration; test both modes with the existing model HTTP mock transport.
- [ ] Bridge dispatches callbacks, awaits physical settlement through existing owners and closes the process deterministically. An EOF/error never becomes successful completion, and an unknown operation is never replayed.
- [ ] Run the focused Python bridge/provider tests plus the real Node-session contract tests. Tests that use scripted provider output remain explicitly non-live acceptance.

## Task 3: Extract scientific session and switch dynamic runtime

**Files:** create `src/agent/scientific_session/` for preserved domain policies/bindings/continuation, create `src/agent/pi_runtime/runtime.py`, update Web decision runtime and the direct/lab callers; adapt decision/domain tests without dropping safety assertions.

- [ ] Add a test asserting the replacement runtime invokes the Pi bridge and cannot call `ModelDecisionLoop` or construct LangGraph. Before refactoring, retain tests proving observations affect next action, unsafe finish is rejected, worker settlement precedes terminal publication and duplicate decisions do not re-execute science.
- [ ] Extract preparation, domain-action validation/execution and terminal-publication safeguards from the old loop. Remove Python model iteration and graph construction, not just the old class name. The domain controller exposes callbacks to Pi; it cannot choose the next tool itself.
- [ ] Keep evidence validation, immutable task requirements, scientific answer rendering, cancellation invalidation and worker ownership. Store only validated state, and mark the new runtime/checkpoint format so old checkpoints are not silently replayed.
- [ ] Let Pi own model history. Python may retain validated wire projections for audit/continuation, but must not independently choose or append the next model action. Restore only an owner-bound, version-compatible waiting run, never an unknown in-flight operation.
- [ ] Update all scientific regression imports to their preserved owners and rerun the original decision/continuation/binding suites. Any changed generic protocol assertion needs an explicit replacement proving the same boundary in Pi.

## Task 4: Retire deterministic-workflow harness wrappers (delegable)

**Files:** modify `src/agent/supervisor.py`, `tests/agent/test_supervisor_harness_integration.py`, `tests/agent/test_run_session_ownership.py`; create `tests/agent/test_supervisor_direct_execution.py`.

- [ ] Add a failure-first dependency guard and exact-once deterministic executor contract:

```python
def test_supervisor_has_no_backend_factory():
    import inspect
    from src.agent import supervisor
    source = inspect.getsource(supervisor)
    assert 'HarnessFactory' not in source
    assert 'harness_factory' not in source
```

- [ ] Replace factory/wrapper selection with direct `executor.execute(**kwargs)` after the existing idempotent-context claim. Preserve authoritative execution identity, delegated authorization, errors, events and persistence; no Pi call is added to deterministic workflows.
- [ ] Replace backend-specific tests with deterministic execution and ownership assertions. Preserve non-replay and failure propagation coverage; obsolete shadow/canary metadata is not a product fallback.
- [ ] Run Supervisor, delegation, run-session ownership and workflow executor regressions. Parent reviews specification compliance and code quality before integration.

## Task 5: Web default, cleanup and deployment

**Files:** Web composition/profile configuration, startup/health checks, dependency manifests, evaluation scripts and current documentation; delete obsolete harness wrappers and old loop after references are migrated.

- [ ] Write default-Web tests proving Pi receives ordinary and scientific requests and legacy dispatch is unreachable. Keep deterministic workflow APIs and explicit docking consent flows unchanged.
- [ ] Make the Pi runtime the default; remove old backend switches/fallbacks and give explicit migration diagnostics for obsolete configuration. Missing Pi is an actionable readiness error, not a legacy fallback.
- [ ] Inventory imports and references before deleting old modules. Remove LangGraph dependencies only when no supported consumer remains. Historical docs may retain dated references; current commands must describe Pi.
- [ ] Run import/compile checks, reference scans, affected Node UI contracts and the strict health check. Avoid claiming optional scientific assets/services are available when the environment lacks them.

## Task 6: Live acceptance, review and release artifacts

- [ ] Add/run a bounded live acceptance path for one cloud compatible model and one local tool-capable model, with true RDKit observation consumed by Pi. Check credential presence without printing credentials. Missing services are recorded as unverified, never replaced by a mock success.
- [ ] Run the affected Agent/task/Web regression suite, full applicable tests, `python -m compileall -q src scripts`, Node syntax/contracts and health checks. Capture exact counts, warnings and skipped/blocked conditions.
- [ ] Review removal completeness, model/protocol compatibility, security/isolation, domain preservation and migration/rollback instructions independently. Resolve important findings before claiming completion.
- [ ] Update `docs/AGENT_MAINTENANCE.md`, deployment/current setup docs and `docs/handoff/latest.md` with a single authoritative Pi runbook and evidence table. No user data, keys, runtime caches or generated test outputs in Git.
- [ ] Stage explicit task files, commit, create and attach a draft PR targeting main. Do not merge. Final completion audit covers every user requirement, including live verification limits and the absence of old executable harness paths.

## Decision points

Pause and request a decision for a necessary model capability that cannot be preserved, breaking public contracts or irreversible data migration. Ordinary scoped implementation and reversible dependency installation proceed under the existing request. No separate legacy runtime is retained to hide an unresolved compatibility gap.
