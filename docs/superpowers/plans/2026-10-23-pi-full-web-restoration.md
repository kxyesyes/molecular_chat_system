# Pi Full Web Restoration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Restore the existing Web scientific capability matrix through official Pi, with no old general-agent fallback and no weakened business or safety assertions.

**Architecture:** Introduce the explicit `medchat-web-v1` domain profile. Reuse deterministic SkillRouter/TaskPlanner/PlanCompiler to compile immutable permitted actions and dependencies, not to execute a model loop. Pi selects one action at a time using `{"step_ref":"compiled-step-name"}`; the existing dynamic WorkflowRunSession executes exactly one existing ToolAdapter after server-side binding, current-evidence validation and authorization. Existing B1 profiles remain closed and unchanged.

**Tech Stack:** Existing FastAPI/Jinja/native JavaScript, Python scientific environment, official pinned Pi Node SDK, strict Pydantic contracts and existing SQLite journal.

**Current checkpoint (2026-10-23):** The implementation and scoped business/mounted-Web regressions are recorded in [validation](../validation/2026-10-23-pi-harness-replacement.md). Web snapshot revision is now **13** with authenticated stable action IDs and owner-fenced reference retries; the revision12 mentions below describe the original plan, not the currently accepted restore format. Reference presentation retries retain an immutable full-run archive and require a new ACK. B1 stays revision11. Final integration/review and cloud/local live acceptance remain release gates; unchecked original steps must not be treated as a completion certificate.

## Scope and interfaces

The user explicitly approved complete Web restoration after discovering the A1-only default-entry regression. This is required migration work, not an optional expansion. Preserve ordinary chat, generation, properties, drug-likeness, activity, ADMET, reverse targets, retrieval, target lookup, composite evaluation, ranking, lead optimization, candidate ACK/restore/followups and separately approved docking. Deterministic APIs remain deterministic.

- `src/agent/contracts/web_requirements.py`: `WEB_PROFILE_REVISION`, `WEB_TOOLS`, bounded immutable `WebTaskRequirements`, `WebActionArguments`, `WebBindingProof`; parsers reject unknown versions/fields, mutation and invalid native JSON.
- `src/agent/scientific_session/web_requirements.py`: `compile_web_requirements(context, *, allowed_tools)` returns a requirements value; `prepare_web_requirements(value, *, context, request_kind, allowed_tools, required_tools)` validates it without executing science; `required_web_tools(requirements, required_tools=())`; reconstruct exact WorkflowStep values from immutable compiled records. Requirements expose `forbidden_tools`, `model_dump(mode='json')`, steps/dependencies/metadata and a committed plan digest. No arbitrary executable callbacks in contracts.
- `src/agent/scientific_session/web_bindings.py`: `WebBindingResolver` implements the existing B1 resolver lifecycle interface, without inheriting B1 policy: constructor, resolve, register_action, guard_dispatch, prepare_observation, verify_binding_closure, export_records, restore_records, find_reusable, record_descriptors and sanitized preparation failure. Returned `ResolvedBindingAction.record_json` contains context/input-turn commitments, original decision arguments, resolved input, action digest and proof roles. Additional `step_metadata(action)` supplies immutable compiled-domain metadata where required. Action references are server-issued plan step names; tool name must match; raw model-provided SMILES, target, count, scores or free-form inputs are rejected.
- `src/agent/scientific_session/web_acceptance.py`: `evaluate_web_acceptance(resolver, *, required_tools, evidence_ids=None)` and `render_web_scientific_answer(resolver, report)` match B1 call conventions; preserve all required actions, batch identities/counts/metrics, genuine partial/fail and original obligation completeness.
- `src/agent/scientific_session/binding_policy.py`: exact profile selection for preparation, tools, resolver, acceptance, rendering and continuation revision. Small static composition boundary, no new execution loop.
- `controller.py`, `decision_policy.py`, `decision_binding_continuation.py`, `pi_runtime/runtime.py`: select explicit domain policy; keep existing owner drain, deadlines, budgets, CAS, replay, seals and publication barriers. B1 revision11 stays distinct; Web revision12 cannot upgrade B1 checkpoints implicitly.
- `src/agent/evidence/ledger.py` (verify actual module path before editing): explicitly recognize the new strict proof type at the existing proof boundary, never accept arbitrary dictionaries or bypass proof validation.
- Web admission/runtime/handler modules: build full requirements from the effective server-side context and authorized tool toggles. Reuse pure followup, context-resolution and presentation helpers; never call the old whole `_process_message_body`, Supervisor.execute or WorkflowExecutor.execute as a Pi tool. Docking retains C approval authority and is excluded from generic WEB_TOOLS.

## Task 1 — Closed compiled Web obligations (worker Tesla)

- [ ] Add failing tests in `tests/agent/test_web_profile_requirements.py`: generation count, targets, temperature, original subject, property/ADMET/activity/ranking/optimization dependencies, unknown tools/fields, disabled capabilities, forbidden clauses and whole-request coverage.
- [ ] Run `python -m pytest tests/agent/test_web_profile_requirements.py -q` and record failures before implementation.
- [ ] Implement contracts and compile/prepare/required functions above, reusing compiler semantics; preserve each WorkflowStep field and original metadata through JSON roundtrip. Example decision fixture: `ToolDecision(type='tool', tool_name='llm_molecular_generator', arguments={'step_ref': 'generate'})` (use actual decision discriminator when writing tests).
- [ ] Add acceptance tests for incomplete required actions, partial generation counts, missing ranking evidence, optimization claims and finish subsets. Implement Web acceptance with independently validated owned results, not model prose.
- [ ] Run focused tests and existing planning/compiler tests. No commit while integration is incomplete.

## Task 2 — Profile hooks and exact replay (worker Hilbert)

- [ ] Add failing profile selection/replay tests in `tests/agent/test_pi_web_profile_runtime.py`; demonstrate Web no longer rejected, B1 permissions unchanged and cross-profile checkpoints rejected before writes.
- [ ] Implement static policy selection through the existing controller and continuation codec; adapt dynamic WorkflowStep metadata using the resolver's optional server metadata method. No duplication of lifecycle algorithms.
- [ ] Keep historical action replay reconstructing the exact server action/input/proof at each prefix. Add Web revision12 validation and preserve revision11 B1 behavior.
- [ ] Run binding runtime, continuation, publication and cancellation regressions, including source changes around CAS and publication.

## Task 3 — Authenticated single-action binding (parent)

- [ ] Add failing tests in `tests/agent/test_web_profile_bindings.py` for forged step/tool, premature dependencies, cross-owner results, altered plan, replayed side effects, changed current RAG/reverse sources, invalid complete molecular batches and forged output digest.
- [ ] Bind only from original/current authenticated input journal and eligible same-run producer outputs using existing WorkflowOrchestrator input resolution. Map compiled step -> decision step -> output key -> evidence identity explicitly. Validate dependencies and preconditions before dispatch.
- [ ] Seal full observation after existing domain validators; verify candidate alignment does not conceal producer digest tampering. Preserve generation model independence and budget/receipts, never route generation through chat model configuration.
- [ ] Integrate strict Web proof decoding in the actual EvidenceLedger module and add bounded, version-specific proof tests.
- [ ] Run focused and adversarial binding tests plus existing B1 regression suites.

## Task 4 — Actual Web entry and business baseline (worker Raman)

- [ ] Add failing actual-entry tests for every retained capability; keep existing integration and browser-lab business assertions, generation flow and real RDKit validation. Script model decisions only, not scientific success.
- [ ] Switch default Web preparation to Web requirements/profile, retaining ordinary chat and isolated continuation ownership. Restore pure rank/evidence/reference followup preparation and final presentation.
- [ ] Preserve generation -> candidate display -> ACK -> durable restore -> selected followup flow, partial/error rendering, toggles and cross-session rejection.
- [ ] Verify docking stays approval-controlled and cannot enter generic action catalog; preserve all C consent and cancellation tests.
- [ ] Run Web integration/browser tests and relevant JavaScript contracts.

## Task 5 — Independent review and release gate

- [ ] Independent reviewer checks no legacy whole-agent path, no Python model iteration, no silent B1/B2 expansion, complete capabilities, receipt/owner/budget and checkpoint semantics.
- [ ] Run impacted partitions, Python compileall, Node syntax and runtime tests, health strict check; report actual failures/skips instead of treating test counts as release authority.
- [ ] Repeat cloud/local live acceptance where available. Existing unsuccessful attempts remain unverified, never substitute mocks.
- [ ] Update migration validation, runbook, handoff and original plan with full matrix and exact remaining limits. Remove task-owned temporary helpers only after tests finish.
- [ ] Review staged paths and secret/data boundaries, commit only task files on `codex/pi-harness-replacement`, create draft PR against main; do not merge. Never label replacement complete with remaining required Web gaps.

## Review of plan coverage

All original scientific tools remain existing implementations. New code supplies domain authorization and immutable evidence mapping only. This plan does not introduce arbitrary shell/files/extensions, alter scientific algorithms, migrate user data or expand docking consent. Source receipts remain current at dispatch, replay and terminal publication. Full request obligations cannot be weakened by a later model decision or clarification. An unsupported combined obligation must be explicitly rejected rather than silently omitted; regression baseline functionality must still be restored before release.
