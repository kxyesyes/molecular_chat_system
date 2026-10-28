# Server-owned scientific execution for the normal Agent entry

Date: 2026-10-25
Status: Proposed design for the next implementation cycle
Scope: normal Web Agent interaction and explicit molecular-analysis requests

## 1. Decision summary

MedChat will keep Pi as the Agent runtime and will keep the existing native
tool-calling path for the model selected in the frontend Agent/LLM settings
when that model supports it. However, a scientific request
whose required inputs and outputs can be compiled by the server will not depend
on the model voluntarily selecting tools.

The server will own the following boundary:

```text
user request
  -> server admission and scientific obligation compilation
  -> trusted, versioned workflow steps
  -> WorkflowRunSession / WorkflowOrchestrator
  -> normalized observations, evidence and provenance
  -> model summary constrained by accepted observations
  -> structured Agent events and result card
```

The configured frontend Agent model remains responsible for language
understanding, clarification when
the request is genuinely incomplete or ambiguous, and human-readable
summarization. It is not authoritative for whether an already-valid,
explicitly requested scientific calculation runs, nor may it invent a result
when a tool was not executed.

This is an extension of the existing Pi/Web decision boundary, not a legacy
runtime fallback. `MEDCHAT_CHAT_PROFILE=legacy` remains rejected. Existing
workflow, evidence, reference and cancellation contracts remain the source of
truth. The local Ollama `gmm-llama:latest` model is the dedicated molecular
generation model and is never substituted for the frontend-configured Agent
model.

## 2. Evidence and problem statement

The real local acceptance path established the following facts:

1. The application constructs two independent model roles: `self.model` from
   the frontend-persisted main LLM configuration, and
   `self.molecular_generator_model` from the dedicated Ollama configuration.
   The latter defaults to `gmm-llama:latest`.
2. A prior browser/JSON experiment configured `gmm-llama:latest` on the Agent
   decision path. It showed that this generation model does not support the
   native tools request and may choose clarification in JSON mode. This is a
   configuration-boundary finding, not a requirement that the generator model
   become the Agent model.
3. The real RDKit-backed `property_calculator` and
   `drug_likeness_assessment` tools execute successfully for CCO. Their
   registered adapters also return normalized `ToolResult` values.
4. The browser currently shows an honest waiting/failure state when the
   configured Agent model cannot complete its decision path;
   it does not display fabricated scientific values.

Therefore the primary defect is not RDKit or tool registration. It is both an
authority mismatch and an interaction/configuration hazard: the server must
use the frontend-selected Agent model, while the model must not be allowed to
withhold an execution that the server can already prove is well-formed and
scientifically scoped.

## 3. Goals and non-goals

### Goals

- Make explicit, valid molecular-analysis requests execute through real tools
  even when the configured Agent model lacks native tool calling.
- Preserve the frontend-selected Agent model/provider capability information
  and expose it in connection
  and run diagnostics without leaking credentials.
- Keep the dedicated `gmm-llama:latest` molecular generation model isolated;
  changing the Agent model must not rebind the generator, and generator
  provenance must not be reported as Agent-model provenance.
- Preserve tool status, warnings, evidence, artifacts, provenance, model
  identity and failure reasons through the existing Agent contracts.
- Keep ambiguous or incomplete requests clarifying instead of guessing a
  molecule, metric, target or experimental conclusion.
- Make the browser result understandable from a user perspective: show the
  accepted request, planned tools, live state, evidence-bound values and any
  incomplete or failed step.
- Keep the implementation compatible with the current FastAPI/Jinja/native
  JavaScript architecture and current Pi runtime.

### Non-goals

- Replacing Pi, adding LangChain/LangGraph, or adding a second workflow engine.
- Making every registered tool available to normal chat. Registration is not
  permission, and high-risk or side-effecting tools need their own reviewed
  admission and consent contracts.
- Treating model text, a static fallback answer, or a demo result as scientific
  evidence.
- Enabling docking, generation/ranking, reverse-target, ADMET or target-aware
  workflows merely by adding their names to a tool allowlist. Each requires a
  separate obligation schema, binding contract and real acceptance.
- Automatically changing a user's configured provider or model.

## 4. Alternatives considered

### A. Server-owned compiled execution — selected

The server parses bounded obligations from the request, validates trusted
inputs, compiles only reviewed steps, and executes them through the existing
workflow session. The model summarizes accepted observations afterward.

This is the only option that supports the configured Agent model regardless of
whether it has native tool calling, while making a valid scientific request
deterministic, auditable and independent of a model's tool-calling preference.
The cost is that each supported workflow needs a reviewed obligation compiler
and acceptance predicate.

### B. Require a native tool-calling model

This keeps the current decision authority model simple, but makes any Agent
configuration without native tool calling unusable for normal scientific
execution and turns a provider capability mismatch into a product-level
blocker. It is useful as an optional capability for higher-level dynamic
workflows, not as the only path.

### C. Native then JSON protocol fallback

This improves provider compatibility but still allows a JSON-capable model to
choose clarification, omit a required tool, or return an invalid action. It
also creates more retry and fingerprint combinations. It may remain as a
transport capability for model-driven workflows, but it cannot be the authority
for compiled scientific obligations.

## 5. Architecture

### 5.1 Components and ownership

Add a narrow Web-facing admission/compilation seam, reusing existing domain
contracts:

- `src/web/decision_runtime.py` (or a small adjacent module) owns request
  classification and invokes the compiler. It does not execute tools itself.
- A typed `ScientificObligation`/compiled-plan DTO records the original query,
  trusted subject identity, requested metrics, required tool names, workflow
  version and immutable negative requirements.
- Existing domain parsers validate SMILES and metric names. No model-generated
  SMILES or target identifier becomes authoritative input.
- Existing `WorkflowOrchestrator.create_session()` and
  `WorkflowRunSession` execute the compiled `WorkflowStep` sequence, journal
  each attempt, and publish normalized `ToolResult` observations.
- Existing `AgentEventBus` and Web Agent presentation code project plan,
  observation, partial, failure and terminal events. The browser does not
  reconstruct scientific status from free-form text.
- Pi remains the model lifecycle, continuation and ordinary-chat runtime for
  the frontend-selected Agent model. Native tool calling remains available
  only when that provider advertises and passes the existing decision probe.
- The molecular generation service continues to receive only the dedicated
  `molecular_generator_model`; the compiled property profile must never route
  Agent requests through that model.

The compiler is a policy boundary, not a general-purpose planner. It accepts
only reviewed request kinds and bounded fields; it emits no arbitrary Python,
expressions, filesystem paths or tool keyword arguments.

### 5.2 Request classification

Classification produces one of these explicit states:

- `chat`: explanation or conversation without a requested scientific result;
- `scientific_ready`: molecule/input and requested outputs are complete and
  compile to a reviewed workflow;
- `scientific_clarification`: the user requests science but a required input is
  missing or ambiguous;
- `scientific_rejected`: the request requires an unsupported or unapproved
  capability;
- `provider_unavailable`: the configured model cannot support the selected
  model-driven portion of the request.

Classification must not downgrade an explicit scientific request to ordinary
chat simply because the model lacks tools. For example, a request to calculate
LogP for an invalid or missing SMILES asks for the SMILES; it must not receive
a generic estimated LogP answer.

### 5.3 Initial compiled workflow profile

The first profile supports only explicit single-molecule property analysis:

| User obligation | Required tool | Acceptance condition |
|---|---|---|
| requested molecular properties | `property_calculator` | succeeded observation for the exact validated molecule and requested metrics |
| requested drug-likeness analysis | `drug_likeness_assessment` | succeeded observation for the exact validated molecule |

The same request may compile both steps. Input is a validated SMILES resolved
from the user request or a server-owned scientific reference. The exact input
and canonical identity are recorded separately. The plan must retain the
original query and must not replace it with a model paraphrase.

The compiler must reject unsupported combinations instead of silently dropping
obligations. In particular, “properties and ADMET”, “generate and rank”,
“find targets”, “reverse-target”, and docking are not satisfied by a property
calculation unless a future profile explicitly declares those obligations and
their acceptance rules.

## 6. Execution and model interaction

### 6.1 Ready request

For `scientific_ready`:

1. Acquire the existing model/request ownership and snapshot provider
   capability/config generation.
2. Validate the request, reference ownership/revision and exact molecule again
   at dispatch time.
3. Emit a plan event containing only nonsecret tool names, requested metrics,
   workflow version and trace ID.
4. Execute the compiled steps through `WorkflowRunSession` with existing
   timeout, cancellation, idempotency and worker-ownership rules.
5. Accept only succeeded observations satisfying the obligation predicate.
6. Emit evidence/provenance-bound result data to the frontend.
7. Ask the model for a bounded summary using accepted observations as data, not
   as executable instructions. If summary generation fails, preserve the
   scientific observations and show an explicit “summary unavailable” state;
   never replace them with model guesses.

The model may be skipped for a purely deterministic result card, but if the
product displays a model-written interpretation it must mark that interpretation
as separate from tool-derived values.

### 6.2 Clarification request

For `scientific_clarification`, do not run tools. Return a structured waiting
state with the exact missing field(s), for example “请提供有效 SMILES”。 A
follow-up must be bound to the original request fingerprint and cannot change
the immutable obligations without starting a new request.

### 6.3 Rejection and provider failure

Unsupported domain requests return a stable, user-readable rejection with
missing capability names. Provider failures identify the configured Agent
model/service capability without exposing raw URLs, credentials or stack
traces. A failed model probe cannot be reported as a successful connection
test.

## 7. Frontend behavior

The normal chat UI will render structured Agent states in this order when
available:

```text
已识别请求 -> 已生成工具计划 -> 工具执行中 -> 结果/部分结果/失败
```

The card must show:

- the molecule identity presented to the user (original and canonical form
  where the existing contract permits both);
- the actual frontend-configured Agent provider/model, not a stale header
  default; the header model label must be refreshed from `/api/llm/config`;
- a separate readiness state for the Agent decision interface. A green
  WebSocket transport indicator alone must not claim that the model is ready;
  readiness is shown only after the same Agent connection/protocol probe used
  by the settings action succeeds;
- requested outputs and the actual executed tool names;
- status for every required step;
- scientific values only when the corresponding observation is succeeded and
  evidence-bound;
- provenance/model/tool version and warnings through existing safe formatting;
- a clear distinction between waiting for missing input, provider failure,
  tool failure, timeout, cancellation and completed execution;
- retry/clarify affordances that preserve the trace and do not silently reuse
  revoked references.

Existing ordinary chat, target-search, docking and molecular-design pages keep
their current contracts. This change only adds the common Agent presentation
states needed by the normal entry.

## 8. Safety, provenance and compatibility

- No fabricated values, scores, binding energies, pIC50 values or literature
  claims are permitted. Missing or failed evidence remains missing or failed.
- Every scientific claim in the final projection must point to accepted
  evidence IDs from the executed observation.
- Tool attempts, warnings, artifacts, provenance and model state remain in the
  existing state store/session journal. Do not add an untracked browser-only
  result cache.
- The plan fingerprint includes original query digest, exact trusted molecule,
  requested metrics, tool/profile versions, provider/model identity and
  configuration generation. Credential values are never included.
- Existing native model-driven decision requests retain their protocol and
  rejection semantics. A provider capability probe selects the allowed mode;
  it does not silently route a failed native request through an unreviewed
  legacy path.
- Existing database schemas and wire event names remain compatible. New event
  fields are optional and additive; terminal status must remain explicit.
- Rollback is configuration-level for new requests only. In-flight sessions
  finish, cancel, or fail under their captured fingerprint and are not
  reinterpreted by a later configuration change.

## 9. Verification plan

### Unit and contract tests

- Compile valid `CCO` requests for LogP/TPSA, drug-likeness, and both together;
  assert exact required tools and immutable metrics.
- Reject missing/invalid SMILES, unsupported obligations, unrequested tool
  names, model-provided authoritative fields and mismatched references.
- Assert the compiler never executes a tool and the orchestrator receives only
  typed, reviewed workflow steps.
- Assert accepted values carry evidence/provenance and failed/partial results
  cannot satisfy a requirement.
- Assert provider capability negotiation distinguishes native tools, structured
  JSON and unsupported decision interfaces.
- Assert continuation, cancellation, timeout, stale reference and config
  generation changes preserve the existing session semantics.

### Browser acceptance

With the frontend-configured Agent model available and the real RDKit
dependencies:

1. Open the real page and confirm WebSocket connection.
2. Submit `请分析 CCO 的分子性质，返回 LogP 和 TPSA。`.
3. Verify the browser shows a plan containing
   `property_calculator`, executes the real tool, and renders the returned
   LogP/TPSA values with status and provenance.
4. Submit a request without a molecule and verify it asks for SMILES without
   running a tool.
5. Submit an unsupported mixed request and verify it reports the unsupported
   obligation instead of returning a partial property-only success.
6. Stop or make the provider unavailable and verify the UI reports the
   provider/tool failure without claiming a scientific result.
7. Separately exercise a molecular-generation request and verify its
   provenance identifies `gmm-llama:latest`; changing the frontend Agent model
   must not change that generator binding.

The acceptance record must include the server/model/tool versions and trace
IDs, but no credentials, chat dumps or prohibited runtime outputs.

## 10. Delivery and rollout

Implementation must be staged:

1. Add the typed obligation compiler and failing unit tests.
2. Integrate it with the existing Web decision runtime and workflow session.
3. Add structured plan/observation presentation and browser contract tests.
4. Run focused Python tests, Node tests, `compileall`, and real browser
   acceptance with the local model/toolchain.
5. Enable the compiled property profile for new normal-Web requests only after
   the browser acceptance passes. Keep unsupported profiles explicitly gated.

Each stage must remain independently reviewable. No broad refactor, dependency
upgrade, frontend framework migration or automatic activation of high-risk
scientific tools is part of this design.
