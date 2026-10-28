# Agent Result Summary and Details Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development for the isolated persistence sidecar and requesting-code-review for independent checks. The parent handles the coupled presentation/transport integration. Steps use checkbox syntax for tracking.

**Goal:** Deliver real Agent results as a compact summary with one owner-scoped, immutable, lazily read scientific detail snapshot, while retaining legacy clients.

**Architecture:** A pure presentation projection produces summary and canonical details from verified results. A private file-backed artifact stores details once; the existing owner-scoped HTTP route reads it. Per-turn capability negotiation selects the new delivery shape and a small standalone frontend module mounts the disclosure after final rendering.

**Tech Stack:** Existing Python 3.10 / FastAPI / SQLite, native JavaScript and CSS; no new dependencies.

**Spec:** `docs/superpowers/specs/2026-10-26-agent-result-summary-details-design.md`.

---

## Execution rules

Use the existing task branch `codex/feat/pi-baseline-migrate-worktree`; do not recreate its dirty checkout. Preserve unrelated changes. Use `apply_patch` for edits and stage only task-owned files or reviewed task-owned hunks. A component commit must not sweep earlier uncommitted work into this task.

Use the existing MedChat Conda interpreter for Python checks. Tests needing Windows asyncio/network setup require the normal local execution permission because the restricted sandbox's socketpair stalls; keep TEMP in the ordinary user temporary directory, not under this Git repository. Never print configuration or key values.

After every task: confirm its spec tests, independently review risky security/compatibility edges, fix findings, then proceed. Final acceptance additionally uses the running frontend and real tools. Record actual pass/fail/skip, not planned outcomes.

### Execution checkpoint — 2026-10-26

**Superseding comparison checkpoint:** the nine retained comparison failures
below are now resolved by the shared input-boundary follow-up. See
[comparison boundary acceptance](2026-10-26-comparison-input-boundaries.md)
for the final 1,684-pass expanded regression, actual frontend results and review
status. The older withdrawal notes below remain historical evidence, not the
current code/server state. Subsequent
[responsive and cancellation acceptance](2026-10-26-home-chat-responsive.md)
verified 390/768/1280 layouts and cooperative Stop/recovery. It also records
remaining comparison-prose and task-panel-position findings; reverse-target
data health is still unavailable.

- Storage/ownership and pure-projection reviews passed before transport integration. Post-fix frontend, transport/capacity and compiled-ADMET preview SPEC/QUALITY reviews passed, including the exact optional-failure wrapper cleanup. Comparison admission did not pass final compatibility review; this is not a release approval.
- Real frontend acceptance with the configured `deepseek-v4-pro` Agent completed a two-molecule property + ADMET + BuChE activity comparison. A request without ranking preserved input order. The explicit ranking follow-up reused the prior results, explained the excluded inconsistent candidate, and returned to the idle state.
- The real ADMET backend is **ADMET-AI 1.4.0**, with **49 endpoint entries per molecule** and separate reference percentiles. The 140-endpoint projection fixture is synthetic capacity-test data, not a claim about the installed model's output count. The displayed risk denominator of 27 belongs to the risk-screening subset, not all endpoint entries.
- Real detail expansion retained both molecule records, exact original activity values, the inconsistency flag, endpoint values/units, and provenance. Details were initially collapsed; keyboard expansion was verified. A persisted snapshot remained byte-identical across a server restart and a browser read-only retrieval.
- Real missing-structure clarification followed by the Continue button produced the property result and a collapsed detail control. The exact bare-subject wording (`计算分子的分子量和 logP。`) passed the same real UI flow after its fix. Appending an aspirin subject or a non-molecular patient subject now rejects without enabling Continue. The long-form IC50 comparison rejects directly. Short-form IC50 variants passed temporarily, but their new admission patch was subsequently withdrawn for a ranked-comparison regression; those short-form cases remain unresolved in the current checkout.
- Real single-molecule ADMET acceptance now shows eight model endpoints (rather than the leading calculated descriptors), neutral numeric column labels, the full endpoint count and a collapsed detail control. Raw values, units and complete snapshots remain unchanged by preview selection.
- Parent regression runs: six result-detail/follow-up/compiled-ADMET modules **326 passed, 2 skipped**; subsequent compiled-ADMET/display/projection/transport rerun **188 passed**; runtime/recovery/Web-profile/scientific-execution **149 passed**. These runs overlap and must not be summed. Each Python integration run reported seven existing dependency deprecation warnings; the two skips are Windows symlink creation limitations. Node detail and runtime contracts passed **38/38** and **79/79**, with the relevant task-panel checks also passing.
- Comparison/obligation/clarification checkpoint: **267 passed, 7 existing deprecation warnings**, before the no-ADMET positive probes were added. The preceding comparison/execution/follow-up/compiled-ADMET/optional-warning run passed **137**. These are overlapping suites. The short-form regression first failed in four assertions (candidate ownership and premature execution), then passed after the comparison branch made its admission decision before the activity fallback.
- **Pre-withdrawal suite was not green: 272 passed, 2 failed, 7 existing deprecation warnings.** The two added English no-ADMET positives passed comparison admission but failed later during input binding. Scoped diagnosis reached `WebBindingResolver._context` → `parse_activity_input`: a sentence-final period on the last molecule causes target ambiguity. The same pure parser input without a period, or with a semicolon, resolves BuChE and both molecules. The real frontend reproduced the failure (`Decision publication invalidated`); no scientific tool was attempted in the instrumented offline case. This remains a shared-parser defect, even if an earlier admission check rejects the request first after withdrawal.
- Final independent compatibility review found that the new immediate-return admission patch also rejected established complex-SMILES ranked comparisons. The parent withdrew only that new production patch, preserving the prior long-form IC50/bare-subject fixes and all new regression tests. The existing evidence-follow-up, Web requirements and hybrid-router suite then returned **156 passed**. Short-form IC50 handling must be redesigned without rejecting validated molecular spans or reviewed presentation clauses; do not reapply the withdrawn branch in isolation.
- **Current post-withdrawal comparison test file: 91 passed, 9 failed, 3 dependency warnings.** Failures cover short-form IC50/unknown-metric admission and English no-ADMET comparisons. These failing tests are retained intentionally as unresolved regression requirements, not skipped or marked expected. The actual frontend subsequently completed the established aspirin/paracetamol properties + ADMET + BuChE ranked comparison again, with real predictions and a collapsed details control. The current test server has the withdrawn-patch version loaded.
- Chinese no-ADMET comparison passed actual tool execution. However, its comparison table still incorrectly displays an ADMET-unavailable column although ADMET was not requested. This presentation defect, short-form IC50 admission and English shared-parser failure are outstanding. A broader shared-input/admission design decision is required before another production patch, rather than accumulating more sentence-specific exceptions.
- Narrow-viewport acceptance has not yet been performed. CSS/DOM contracts are not a substitute for that visual check. The latest strict health check reported 23/24, with the local reverse-target data requirement unmet; this result must not be reported as a full health pass. A four-molecule ADMET-only natural-language request was rejected without execution; broader batch admission is outside this result-delivery component. The attempted real Stop check raced with normal completion, so it is not cancellation acceptance evidence.

Final focused commands (run in the existing scientific Python environment):

```powershell
python -m pytest tests/agent/test_browser_comparison_without_ranking.py tests/agent/test_scientific_obligations.py tests/agent/test_web_scientific_execution.py -q
python -m pytest tests/agent/test_browser_comparison_without_ranking.py tests/agent/test_web_scientific_execution.py tests/agent/test_result_followup_delivery.py tests/agent/test_compiled_admet_summary.py tests/agent/test_optional_failure_summary.py -q
python -m pytest tests/agent/test_browser_evidence_followup.py tests/agent/test_web_profile_requirements.py tests/agent/test_hybrid_router.py -q
python -m pytest tests/agent/test_browser_comparison_without_ranking.py -q
node tests/home_result_details_test.js
node tests/home_decision_runtime_test.js
node tests/home_agent_task_panel_test.js
node --check src/web/static/js/home/result_details.js
node --check src/web/static/js/home/task_status.js
python -m compileall -q src/web/scientific_obligations.py src/web/decision_runtime.py src/web/decision_chat.py src/web/result_summary.py tests/agent/test_browser_comparison_without_ranking.py tests/agent/test_web_scientific_execution.py tests/agent/test_compiled_admet_summary.py tests/agent/test_optional_failure_summary.py
git diff --check
```

The compile, syntax and whitespace checks passed; Git reported only the existing controller CRLF normalization warning. No runtime snapshots, screenshots, credentials, model/data assets or earlier mixed work were staged. Implementation is uncommitted on `codex/feat/pi-baseline-migrate-worktree`, with HEAD `95c29f8b` (the earlier design/plan-only commit); no PR or merge.

## Task 1 — Immutable private detail artifacts and owner-scoped route

**Create:** `src/agent/persistence/result_details.py`, `tests/agent/test_result_details_store.py`, `tests/agent/test_result_details_routes.py`.

**Modify:** `src/web/routes/agent_run_routes.py` only for the new endpoint; keep existing routes unchanged.

Public server-only API:

```python
publish_result_details(store, trace_id, *, session_id, payload) -> dict | None
read_result_details(store, trace_id, *, session_id, view_id, revision) -> dict | None
```

The returned reference is exactly `{view_id, revision, byte_length}`. The payload is a JSON mapping with `format: "summary_details_v1"`, `trace_id`, `status`, `tool_result_sequence`, `error`, `warnings`, and `display_redacted`. HTTP data is `{**reference, "payload": payload}`. Runtime envelope fields outside this payload stay in the small WebSocket result.

- [x] Write the immutable-read and owner-denial failing tests using a real temporary `SQLiteAgentStateStore` and ordinary `create_run` fixtures:

```python
ref = publish_result_details(store, "trace-one", session_id="owner", payload=payload)
assert set(ref) == {"view_id", "revision", "byte_length"}
got = read_result_details(store, "trace-one", session_id="owner", **{k: ref[k] for k in ("view_id", "revision")})
assert got["payload"] == payload
assert read_result_details(store, "trace-one", session_id="foreign", **{k: ref[k] for k in ("view_id", "revision")}) is None
```

- [x] Run `python -m pytest tests/agent/test_result_details_store.py -q`; verify the failure is the absent feature before implementation.
- [x] Implement canonical `json.dumps(..., ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))`, 4 MiB/65,536-node validation with 32 observation layers plus two envelope layers, SHA-256, server UUID, private sibling `result_details/`, temporary write plus atomic publish, owner-checked `workflow_artifacts` registration. Do not accept client paths. Use fixed artifact kind/media type; metadata freezes both owner fields. `read_result_details` validates bounded input and record metadata, exact derived path, resolved containment, length/hash/schema and both current/frozen owners before returning the decoded payload.
- [x] Add red/green cases for ownership mismatch, wrong trace/view/revision, path traversal, symlink escape, byte/hash corruption, orphan file, restart, repeated publication, run updates, oversized/cyclic/nonfinite/deep payloads, read size bounds and write/registration failure cleanup. Do not delete existing artifacts.
- [x] Add the endpoint to existing setup with exact route `/api/agent/runs/{trace_id}/result-details/{view_id}`. Read authenticated session from scope. Map absent/foreign/invalid snapshot to the same 404. Return the standard API success envelope with private/no-store headers. Keep model/tool functions out of this path.
- [x] Run `python -m pytest tests/agent/test_result_details_store.py tests/agent/test_result_details_routes.py tests/agent/test_web_decision_runtime_recovery.py -q`; compile changed Python. Review Task 1's security boundary before integration.

## Task 2 — Pure scientific summary and single-copy details projection

**Create:** `src/system/scientific_public_data.py` (shared privacy-preserving, non-truncating JSON projection), `src/web/result_details_projection.py` (single-copy DTO), `src/web/result_summary.py` (bounded summary frames), `tests/agent/test_result_details_projection.py`.

**Modify:** `src/agent/evidence_followup.py`, `src/web/property_comparison.py`, `src/web/web_business.py`, `src/web/decision_chat.py` at their existing result rendering boundaries.

Projection API:

```python
project_result_details(result, *, summary, status, error, warnings) -> dict
```

`result` is the validated `AgentResult`, not a browser envelope. Return the Task 1 payload. Keep only a single ordered tool-result list; drop no scientific fields, do not copy embedded raw-answer strings into details. Preserve explicit failed/partial state and structured provenance. Perform existing secret/path redaction without old silent per-field truncation. Reject invalid/over-budget data rather than publishing incomplete details.

- [x] Add failing tests that a three-tool/two-molecule fixture preserves all 140 ADMET endpoints, excluded activity rows, units/errors/provenance, while no `tool_results`, `tool_results_by_id` or raw audit answer exists in the DTO:

```python
assert projected["tool_result_sequence"] == expected_public_sequence
assert not {"tool_results", "tool_results_by_id", "final_answer", "message"} & projected.keys()
assert source.to_legacy_dict() == before
```

- [x] Run `python -m pytest tests/agent/test_result_details_projection.py -q` and observe RED, then implement the pure projection and bounds.
- [x] In evidence comparison rendering, capture a separate `summary_answer` before source footers and raw JSON. Keep old `final_answer` unchanged. The concise variant keeps accepted ordering, exclusion reasons and concrete issues, omits generic caveats/internal IDs, and uses business tool names. Carry it separately through `WebTurnProjection.capture`.
- [x] Extend property/ADMET/target result renderers with explicit concise presentation selection rather than deleting arbitrary lines by regex. Preserve existing default callers. Bound concise tables by row count/UTF-8 bytes; count omitted summary rows and retain their full details.
- [x] Add red/green tests for legitimate 32-layer observations, secrets, malformed/cyclic objects, missing scientific acceptance, prediction disagreement, tied rankings, business partial status, tool failures, no tool results and source immutability. Run projection, display, browser evidence-followup and safe render regressions.

## Task 3 — Negotiated per-turn transport and snapshot publication

**Modify:** `src/web/decision_runtime.py`, `src/web/decision_chat.py`.

**Test:** `tests/agent/test_result_details_transport.py`, `tests/agent/test_web_decision_runtime.py`, `tests/agent/test_web_decision_runtime_recovery.py`, `tests/agent/test_decision_result_display.py`.

- [x] Write failing full-Web tests for advertised capability, opt-in chat/resume, default legacy shape and invalid format before execution. Assert `result_format` never enters requirements, tool arguments, frozen continuation or request identity.
- [x] Implement strict transport-only format extraction, `_Turn.result_format`, and explicit sender wrapper forwarding. Only full-Web `chat`/`resume` accept `summary_details_v1`; no field means legacy. Unknown/type-invalid values fail closed.
- [x] Pass the format, trusted session and store to result preparation. On opt-in, obtain concise summary, project/publish details before sending, attach only its reference, preserve minimal error/partial/waiting/control fields. Keep raw `AgentResult` and ordinary history authority separate from this browser DTO.
- [x] Enforce 24 KiB summary and 64 KiB serialized-frame bounds. Publish errors produce an explicit details-unavailable state with no giant fallback. Existing 256 KiB frontend limit remains unchanged.
- [x] Cover all three `_result_frame` callers and `_ProjectionSender`. Preserve result → business/RAG/reference projections → complete order, existing correlation and cancellation checks, continuation ACK and terminal semantics.
- [x] Run runtime/recovery/display/input-budget/Web-Pi tests. Assert the large representative legacy fixture exceeds the old frame size but the opted-in result/complete frames remain below their new bound and the HTTP payload exactly retains the single public sequence.

## Task 4 — Lazy frontend details and clean task labels

**Create:** `src/web/static/js/home/result_details.js`, `tests/home_result_details_test.js`.

**Modify:** `src/web/static/js/home/config.js` (central API base URL), `src/web/static/js/home/main.js`, `src/web/static/js/home/task_panel.js`, `src/web/templates/index.html`, relevant existing frontend contract tests. The homepage has no generic API-client module; the dedicated detail module owns its bounded reader and consumes the central config URL.

Module API:

```javascript
HomeResultDetails.mount(container, {traceId, reference, load});
HomeResultDetails.dispose(container);
```

`load` is the owner-scoped, same-origin API reader. `mount` returns a native details/summary control, initially closed. It must not fetch before user expansion. Attach controls after final message rendering and tie each asynchronous update to its original connected instance.

- [x] Write Node DOM-contract failing tests for no initial fetch, exactly one concurrent read, successful reuse, read-only retry, dispose abort and hostile text as literal text. Run `node tests/home_result_details_test.js` for RED.
- [x] Implement the isolated disclosure using `textContent`, human tool names, nested tables/field lists, safe formatted text for unknown fields, and no remote HTML. Unknown labels can retain original keys only inside details.
- [x] Implement streamed bounded response reading and exact format/trace/view/revision checks, including absent/fake Content-Length and maximum 4 MiB + 64 KiB response bytes. API calls use same-origin credentials and AbortController.
- [x] Negotiate only when advertised, include opt-in in chat/resume, consume new reference/summary without legacy result-index assumptions, mount after `completeLastMessage`, and invalidate pending instances on re-render/disconnect without corrupting historical-message identity.
- [x] Humanize workflow step/tool labels in the task panel; preserve concrete failed/waiting/partial states, accessibility labels and existing event ordering. Remove technical IDs from default displayed labels only, not structured events.
- [x] Bump only affected template cache tags; update exact contract expectations. Run `node --check` on all changed JS plus `home_result_details`, `home_decision_runtime`, task-panel, safe-render, comparison-table, reference-controls, tool-toggle and settings tests.

## Task 5 — Real frontend acceptance, review and handoff

**Modify:** `docs/PI_RUNTIME.md` for negotiated presentation and read-only detail lifecycle. No production key/config edits.

- [x] Run focused and impacted Python/Node regressions, `compileall`, `node --check`, `git diff --check`; run strict health check if runtime-directory changes affect its checks, reporting any environmental failure accurately.
- [x] Restart only the identified local test server after checking its live process handle. Keep configured main Agent model and generator separation; open the real existing local frontend.
- [x] Run a real two-molecule property + ADMET + activity request, inspect actual result delivery and default-collapsed UI, expand and verify original endpoints, provenance and disagreement/partial status. No mocked browser result is acceptance evidence.
- [x] Test a real target query, continuation interaction, same-trace old snapshot behavior and restart/read-only retrieval without rerunning science. Use UI and scoped diagnostics for evidence; no secret/log dump.
- [ ] Save local ignored screenshots; verify narrow-screen/keyboard operation and task labels. Preserve current scientific source/status in explanations, omit generic disclaimers.
- [ ] Request independent spec and code review; repair findings with failing tests and repeat affected real frontend checks. Do not mark the broad Agent goal complete based on this component alone.
- [ ] Stage only task-owned work, report exact validation/results and branch/commit/PR status; no merge without specific authorization. Final handoff states user, developer and Agent experience improvements and remaining verified limitations.
