# Remaining work through step 8

User objective: complete work packages 1–8 from the remaining-work inventory; do not perform step 9 (server deployment). Subsequent PR merges are authorized by default, and recommended choices are delegated to Codex. Independent branches, scientific protections, review and passing CI remain mandatory. Do not read or publish secrets.

## Completion ledger

| Package | Required end state / evidence | Current state |
|---|---|---|
| 1. Publish terminal-label fix | PR #62 merged after exact-head checks; reviewed and merged trees equal | DONE: squash 6af7292bcd4280d597e2f5a124332fb91264a400; CI 7/7, no unresolved review threads, tree equals fc99a6e |
| 2. ChatHandler responsibilities | Pure result presentation and prompt/RAG assembly extracted; compatibility, frames, numerical text, errors and lifecycle tests pass | DONE: PR #63 squash aa86377c60ff4c8a0457dc28ab6a9d6b4856c194; CI7/7; merged tree equals d4860d7; independent SPEC/QUALITY approved; joint5728 passed/2 skipped and final boundary99 passed |
| 3. Domain API routes | Domain-specific setup functions replace the mixed registrar; URL, HTTP/response contracts and shared service lifetimes verified | DONE: PR #64 squash ecd6ccad5ffdd155f5944c4136107004e2ea00be; exact-head0e54a1e CI8/8, no unresolved reviews; reviewed/merged tree b7a060b7cf559bd76d833a4c0372aee23403fa6a. First timeout retained; infrastructure PR #65 separately reviewed/merged. See domain-api-separation.md |
| 4. Remaining tool contracts | Inventory every registered tool; explicit reusable input/output contracts or justified non-executable helper boundaries; status/evidence retained | IN PROGRESS: 4A PR #67 merged1bba0256409a06317486530e5c1cfa6598b8e381, latesthead9f3ce84 CI8/8 unresolved0, reviewed/merged tree71b1a064862faa8f0711d5e74e1faf67386ffedd; full6068 passed/2 skipped and final505 passed retained. 4B post-timing-fix dual review approved, full5852 passed/2 skipped, combined integration pending. 4C generator/ranker TDD underway; 4D helper audit pending. RAG/activity/docking already typed, do not duplicate them |
| 5. Planner separation | Selection, request parsing and pure step templates have separate responsibilities; same plan/data bindings/clarifications verified | LOCAL REVIEWED, NOT RELEASED: selector/parsing dual-reviewed595 focused passed; full failures retained. Controlled reproduction found separate secure snapshot ancestor-mtime false rejection. Separate codex/snapshot-directory-identity fix dual-reviewed with new RED3 failures, GREEN506 passed/47 skipped, full5643 passed/5 skipped and latest-main692 passed/3 skipped; native Linux CI and subsequent integrated Planner full regression pending. Historical failures are not erased |
| 6. Historical residual audit | Each original code-only residual classified as superseded, migrated or intentionally retained, with evidence; no dirty-tree reset | IN PROGRESS: 6A reverse whole-input PR #66 merged as3a682649c3392727f642f33f56bd3f94c7d77707 after exact-head37b66a1 CI8/8 and unresolved0 gate; reviewed/merged trees ff24ffd342f3ab7ab31bfa9a8d82514eb2d01a84 equal. Full5664 passed/2 skipped and final118 passed retained. Report/property residuals and unsupported ADMET labels remain open; do not merge historical trees wholesale or remove user assets |
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
