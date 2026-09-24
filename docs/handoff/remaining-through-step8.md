# Remaining work through step 8

User objective: complete work packages 1–8 from the remaining-work inventory; do not perform step 9 (server deployment). Subsequent PR merges are authorized by default, and recommended choices are delegated to Codex. Independent branches, scientific protections, review and passing CI remain mandatory. Do not read or publish secrets.

## Completion ledger

| Package | Required end state / evidence | Current state |
|---|---|---|
| 1. Publish terminal-label fix | PR #62 merged after exact-head checks; reviewed and merged trees equal | DONE: squash 6af7292bcd4280d597e2f5a124332fb91264a400; CI 7/7, no unresolved review threads, tree equals fc99a6e |
| 2. ChatHandler responsibilities | Pure result presentation and prompt/RAG assembly extracted; compatibility, frames, numerical text, errors and lifecycle tests pass | DONE: PR #63 squash aa86377c60ff4c8a0457dc28ab6a9d6b4856c194; CI7/7; merged tree equals d4860d7; independent SPEC/QUALITY approved; joint5728 passed/2 skipped and final boundary99 passed |
| 3. Domain API routes | Domain-specific setup functions replace the mixed registrar; URL, HTTP/response contracts and shared service lifetimes verified | LOCAL COMPLETE, RELEASE PENDING: all30 operations moved, independent SPEC/QUALITY approved, joint5965 passed/3 skipped/11 subtests; each of3 framework profiles68 passed; see domain-api-separation.md |
| 4. Remaining tool contracts | Inventory every registered tool; explicit reusable input/output contracts or justified non-executable helper boundaries; status/evidence retained | PENDING; RAG/activity/docking already typed, do not duplicate them |
| 5. Planner separation | Selection, request parsing and pure step templates have separate responsibilities; same plan/data bindings/clarifications verified | PENDING; five templates already extracted |
| 6. Historical residual audit | Each original code-only residual classified as superseded, migrated or intentionally retained, with evidence; no dirty-tree reset | PENDING; do not merge historical trees wholesale or remove user assets |
| 7. Production-entry dynamic decisions | Normal Web entry actually supports the approved model-driven decision path, using existing loop/Session/authorization/evidence and bounded recovery; integration/browser evidence | PENDING; isolated decision bridge alone is not completion; server deployment excluded |
| 8. Current real scientific acceptance | Latest integrated code tested with real configured providers/local generation/scientific tools and requested artifacts/data flow; repeats and honest failed/partial reasons reported | PENDING; historical real PDE/BuChE verification is evidence, not a replacement for current end-to-end testing |

The final audit must check all eight rows against current code, PR state and real reports. Passing offline CI is not proof of model quality, real Vina operation or server readiness. A missing dependency does not become a scientific pass.

## Read-only scope inventory (2026-09-25)

This inventory is not implementation or test evidence. Reconfirm each boundary in its own batch.

- **3:** The mixed registrar contains docking execution/history, reverse-target/pharmacophore, activity inference, training/model management, molecular utilities/properties and metrics. Preserve registration order, injected service/runtime lifetime, upload/threadpool/resource limits and existing response/error semantics. The properties endpoint's heuristic ADMET labels are a separate scientific issue, not fixed by moving routes.
- **4:** Eleven canonical tools remain beyond the existing RAG/activity/molecular-docking contracts: target search, reverse-target, generation, ranking, properties, drug-likeness, ADMET and four staged docking helpers. Suggested bounded increments: properties/likeness/ADMET, target/reverse, generator/ranker, then four restricted helper exceptions and registry audit. Reuse non-projecting raw-envelope and normalized-result checks; do not coerce/default away scientific fields. Read-only inspection flagged target lookup status words versus ObservationStatus for actual reproduction, nullable unknown structure counts, sparse ADMET backends and ranker's optional evidence shapes. These findings are not yet runtime-verified fixes. The helpers are restricted compatibility interfaces, not permission to expose new executable actions. Retain scientific extension fields and all status/evidence/warnings/artifacts.
- **5:** Separate workflow selection and request parsing from the already extracted five step templates. Preserve clarification versus exception behavior, metadata precedence, helper overrides and resolved-scientific-reference bindings.
- **6:** Reconcile the old Chinese target-design presentation, independent-property card alignment, remaining whole-SMILES callers, recovery version/artifact independence and historical sandbox timing findings. Do not restore the superseded `complete.molecules` protocol or move dirty patches wholesale. Untracked code still needs individual inspection; user assets stay untouched.
- **7:** Reuse `ModelDecisionLoop`, decision transport, `decision_chat`, shared Session, ownership and scientific-reference storage. The harness backend selector alone does not activate model-driven decisions. Verify normal HTTP/WebSocket routing, clarification/continuation, cancellation, model switching, partial and candidate ACK; no server deployment.
- **8:** Reuse decision-chat acceptance, browser lab, real family-weight acceptance and scientific acceptance runner. Historical real family-weight checks used scripted decisions and retained scientific partials. The scientific runner's zero exit on partial and optional external-main-model probe cannot establish full real acceptance. Inspect structured outcomes and actual provenance on the final integrated revision.

## Work boundaries

- Each independently reviewed package gets its own branch/PR; sub-increments may be used but do not redefine the end state.
- Preserve main; merge only through reviewed PRs with passing required checks and no unresolved findings.
- The original checkout remains at its existing dirty state. Do not stage its unrelated changes.
- Keys only from runtime environment or the authorized local secret/config boundary; never print, commit or include credentials in reports.
- No new parallel Agent framework, DSL, replacement frontend or arbitrary model-executed code.
- Step 9 (server provisioning, HTTPS and public deployment) is excluded, not silently counted as done.

## Current verification

PR #62 merge was rechecked through GitHub API and local fetch; merge tree matched the reviewed head. New step-2 branch is based on origin/main 6af7292.
Six baseline files: test_chat_handler_partial_results, test_chat_handler_agent_events, test_chat_input_budget, test_agent_audit_regressions, test_chat_local_cleanup, test_scientific_reference_web (all under tests/agent).
Executed with MedChat Python and the existing isolated runner documented in docs/superpowers/plans/2026-09-24-rag-service-extraction.md, replacing only the worktree path. No model API or production assets used.
