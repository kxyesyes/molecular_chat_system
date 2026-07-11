# MedChat Scientific Runtime Hardening Design

## Goal

Harden the existing MedChat platform so that browser rendering, Agent execution,
RAG retrieval, activity prediction, docking, and background-task reporting share
one truthful and traceable runtime contract. The implementation must preserve the
current FastAPI, Jinja2, native JavaScript, RDKit, Ollama, RG-MPNN, and Vina
architecture and must not turn unavailable scientific dependencies into success.

## Scope and delivery strategy

The work is split into four independently testable stages on one dedicated branch.
Each stage must leave the repository green before the next stage starts.

1. Browser safety and task-status truthfulness.
2. Agent and docking request isolation.
3. RAG and scientific-model provenance contracts.
4. Deployment reproducibility, CI, and removal of superseded code.

This design intentionally avoids a frontend framework migration, database platform
migration, unconstrained LLM planner, or unrelated visual redesign.

## Stage 1: Browser safety and status semantics

### Safe rendering boundary

All runtime strings from uploaded files, API responses, model metadata, job IDs,
errors, and notifications are untrusted. The activity page will load the shared
`safe_render.js` helper before its feature modules. Dynamic text will use DOM APIs
and `textContent`; small fixed templates may use `trustedTemplate`, with every
interpolated value escaped. Inline event-handler strings will be replaced by
`addEventListener` where touched.

The first pass covers:

- activity preflight headers, cells, file names, target columns, metrics, model
  metadata, prediction rows, and errors;
- home chat toast and notification messages;
- the activity-model and docking-history management callers.

Protected management callers will consistently use `MedChatAdminAuth.fetch`.
Backend authorization rules remain unchanged.

### Background-task terminal state

`TaskManager` will normalize handler results before assigning a terminal state.
An exception remains `failed`; a mapping with `success: false` or
`status: "failed"` becomes `failed`; `status: "partial"` remains a successful
task transport with a structured partial result and explicit task metadata. A
returned failure must never be persisted as `succeeded`.

## Stage 2: Agent and docking isolation

### Request-scoped Agent runtime

`WorkflowExecutor` will never mutate a shared orchestrator's `event_bus`. Each
execution gets a request-scoped event bus and orchestrator instance while sharing
only immutable collaborators such as validators and the state store. Concurrent
traces must not exchange events or callbacks.

Chat and management execution will converge on the same internal workflow
execution semantics. Delegation remains optional, but tool ownership and result
contracts must be identical. `reverse_target_predictor` will receive a declared
specialist owner.

### Timeout and cancellation semantics

Thread timeouts will not be described as cancellation. Python-only tools may use
cooperative cancellation; external scientific commands will run as managed
subprocesses with a timeout and process termination. Timeout results remain
structured failures and cannot publish late successful artifacts.

### Docking job isolation

Every docking run owns its receptor, ligand, Vina config, logs, and result files
inside `temp_docking/docking_<job_id>/`. No run writes a shared `config.txt`.
Direct HTTP docking work will be moved off the event loop. History-index updates
will be serialized and written atomically. Legacy methods that synthesize docking
scores, binding modes, or residue interactions without tool evidence will be
removed.

## Stage 3: RAG and scientific-model contracts

### RAG index manifest

The active RAG implementation will persist a manifest containing:

- schema version;
- source CSV relative path and SHA256;
- embedding model and base URL identity without credentials;
- vector dimension and vector count;
- an explicit vector-index-to-source-row mapping;
- creation time and builder version.

Index loading must reject an incompatible or incomplete manifest. Failed embedding
rows are omitted together with their source-row mapping, so FAISS positions cannot
drift from the DataFrame. `is_initialized` is true only when the index, mapping,
and source data pass validation. `enable_rag=false` is propagated into Agent
context and removes RAG tools from the allowed tool set.

Semantic embedding similarity will be labelled as semantic similarity. Chemical
structure similarity will use a fingerprint/Tanimoto implementation and will not
reuse the semantic score label.

### Activity-model registry

Every usable activity checkpoint must have metadata for task type, target or
endpoint, units, model format, model hash, dataset identity, split strategy,
random seed, metrics, and model configuration. Prediction must refuse a checkpoint
whose scientific endpoint is unknown.

Regression returns a named endpoint value with units. Classification applies the
correct probability transform and returns a class probability. The system will no
longer assign `confidence: 1.0` or generic High/Medium/Low pIC50 labels to arbitrary
models. UI-selected split strategy must reach the trainer; scaffold split is the
recommended default for molecular datasets. Model switching is restricted to
registered files resolved inside the model directory, and checkpoint loading uses
the safest format supported by the installed PyTorch version.

### Other scientific provenance

- ADMET results always identify `adme_py`, `rdkit_rules`, or unavailable status.
- Reverse-target records include target identifiers, assay type, relation, units,
  similarity evidence, and explicit low-confidence labelling.
- Target search expands recommended structures instead of returning only counts.
- Molecular generation records the configured local generator model and validates
  requested count, canonical uniqueness, and RDKit validity.
- Hit-to-lead and target-driven design add an explicit candidate merge/rank step;
  ranking inputs and missing metrics remain visible.

## Stage 4: Deployment and maintainability

Root and deployment dependency sets will be reconciled into documented profiles
with one compatibility matrix. CI will run Python tests, Node static tests, syntax
checks, and secret scanning without requiring real scientific assets. Real-tool
acceptance remains an explicitly provisioned job.

Large modules will be split only along boundaries touched by this work. Dead RAG,
Ollama, route, and static backup implementations will be removed only after import
and route-coverage tests prove they are unused. Logging will use rotation and
request/trace identifiers instead of truncating the log on each start.

## Error and provenance contract

Every scientific step must preserve:

- `trace_id`, skill, step and tool identity;
- sanitized input summary or hash;
- tool/model version and execution method;
- success, partial, unavailable, invalid-input, timeout, or failed state;
- warnings, evidence and validated artifacts;
- elapsed time and retry count.

The final answer may include only successful validated scientific claims. Partial
and failed steps remain visible and cannot be rewritten as success by a chat model.

## Testing strategy

All behavior changes use red-green-refactor TDD. Required new coverage includes:

- activity-page and chat-renderer XSS payloads;
- browser management callers attaching the admin token;
- handler-returned failure mapping to failed task state;
- concurrent Agent traces with isolated event streams;
- reverse-target specialist ownership and chat/management parity;
- docking config isolation, subprocess timeout, and history concurrency;
- RAG skipped-row mapping and stale-manifest rejection;
- RAG-disabled tool denial;
- regression/classification activity output semantics;
- scaffold-split propagation and model-path confinement;
- end-to-end provenance completeness and actual upstream-data consumption.

The final gate is the complete Python suite, all Node tests, Python syntax checks,
JavaScript syntax checks for changed files, and focused real-tool acceptance when
the local scientific dependencies are available.

## Compatibility and rollout

Existing response fields remain available during migration. New provenance and
status fields are additive. Any field whose old meaning was scientifically
ambiguous is retained only as a deprecated alias with an explicit warning. Each
stage is committed separately so it can be reviewed or reverted without losing
later independent work.
