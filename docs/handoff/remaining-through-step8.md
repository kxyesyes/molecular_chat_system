# Remaining work through step 8

User objective: complete work packages 1–8 from the remaining-work inventory; do not perform step 9 (server deployment). Subsequent PR merges are authorized by default, and recommended choices are delegated to Codex. Independent branches, scientific protections, review and passing CI remain mandatory. Do not read or publish secrets.

## Completion ledger

| Package | Required end state / evidence | Current state |
|---|---|---|
| 1. Publish terminal-label fix | PR #62 merged after exact-head checks; reviewed and merged trees equal | DONE: squash 6af7292bcd4280d597e2f5a124332fb91264a400; CI 7/7, no unresolved review threads, tree equals fc99a6e |
| 2. ChatHandler responsibilities | Pure result presentation and prompt/RAG assembly extracted; compatibility, frames, numerical text, errors and lifecycle tests pass | IN PROGRESS: two-module design selected; pre-change six-file baseline 271 passed, 4 existing warnings, 15.26s |
| 3. Domain API routes | Domain-specific setup functions replace the mixed registrar; URL, HTTP/response contracts and shared service lifetimes verified | PENDING |
| 4. Remaining tool contracts | Inventory every registered tool; explicit reusable input/output contracts or justified non-executable helper boundaries; status/evidence retained | PENDING; RAG/activity/docking already typed, do not duplicate them |
| 5. Planner separation | Selection, request parsing and pure step templates have separate responsibilities; same plan/data bindings/clarifications verified | PENDING; five templates already extracted |
| 6. Historical residual audit | Each original code-only residual classified as superseded, migrated or intentionally retained, with evidence; no dirty-tree reset | PENDING; do not merge historical trees wholesale or remove user assets |
| 7. Production-entry dynamic decisions | Normal Web entry actually supports the approved model-driven decision path, using existing loop/Session/authorization/evidence and bounded recovery; integration/browser evidence | PENDING; isolated decision bridge alone is not completion; server deployment excluded |
| 8. Current real scientific acceptance | Latest integrated code tested with real configured providers/local generation/scientific tools and requested artifacts/data flow; repeats and honest failed/partial reasons reported | PENDING; historical real PDE/BuChE verification is evidence, not a replacement for current end-to-end testing |

The final audit must check all eight rows against current code, PR state and real reports. Passing offline CI is not proof of model quality, real Vina operation or server readiness. A missing dependency does not become a scientific pass.

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
