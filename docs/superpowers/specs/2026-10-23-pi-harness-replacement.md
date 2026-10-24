# Pi harness replacement

## Approved scope

Replace the custom general-purpose Agent harness with Pi as the sole Web Agent
decision runtime. Remove the old decision loop, harness backend selection,
Legacy/Shadow/Canary/LangGraph wrappers and their exclusive dependencies. Do not
retain an executable fallback. Rollback is a previous release, not a second
runtime. Keep FastAPI, the existing frontend, Python scientific dependencies,
scientific contracts and deterministic domain/workflow APIs.

## Baseline and ownership

Implementation starts from upstream main `616d8e9e` in an isolated worktree on
`codex/pi-harness-replacement`. The original checkout contains unrelated local
changes and is not an implementation target. Pi package metadata was checked at
the official npm registry: `@earendil-works/pi-coding-agent@1.4.0`, MIT, Node
`>=22.19.0`. The development host currently provides Node 24.15.0.

| Existing responsibility | Disposition | New owner |
| --- | --- | --- |
| ModelDecisionLoop model requests / graph / control flow | Remove | Official Pi session / Agent loop |
| HarnessFactory / legacy / shadow / canary / LangGraph wrappers | Remove | Direct deterministic workflow execution or Pi, according to entry point |
| Scientific input binding, evidence verification, finish validation | Extract and preserve | Python scientific session / tool bridge |
| Tool registry, adapters, result validators, provenance | Preserve | Existing domain tooling |
| TaskRuntime, worker ownership, approvals, idempotency | Preserve | Existing task and execution authorities |
| Web progress, result presentation, run ownership | Adapt | Pi event/result boundary and existing Web runtime |
| Old continuation checkpoints | Explicitly version and reject unsafe replay | Existing state store plus Pi session lifecycle |

## Integration boundaries

Python owns identity, permissions, scientific execution and durable task state.
A controlled Node subprocess hosts the official Pi runtime with only approved
MedChat custom tools. Pi owns model iteration and context. No shell/file tools,
automatic extension discovery or personal Pi credentials are allowed. Requests
and responses use bounded, correlated messages; credentials are transient and
never persisted or placed in argv. Process exit never proves worker settlement.

The provider adapter must preserve OpenAI-compatible and local tool-capable
models. A required protocol incompatibility is a decision point, not permission
to recreate a decision loop or silently discard existing capability. Scientific
answers remain grounded in validated domain observations, not model prose.

## Acceptance

- Existing Web Agent requests run through Pi, with feedback-dependent tool choice.
- Old general-purpose harness classes, executable modes and imports are retired.
- Domain validation, partial/failed outcomes, provenance, approvals and ownership remain enforced.
- Cancellation, timeout, process failure and continuation cannot duplicate effects or publish premature success.
- Native contracts are tested deterministically; cloud and local live acceptance are separately reported.
- Focused and affected regression tests, compilation, Node checks and applicable health checks are recorded accurately.
- Versioned dependencies, startup/migration/rollback instructions and draft PR are delivered; no automatic merge.

User data, credentials, generated outputs and runtime state must not be committed.
