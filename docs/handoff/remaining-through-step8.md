# Remaining work through step 8

User objective: complete work packages 1–8 from the remaining-work inventory; do not perform step 9 (server deployment). Subsequent PR merges are authorized by default, and recommended choices are delegated to Codex. Independent branches, scientific protections, review and passing CI remain mandatory. Do not read or publish secrets.

## Completion ledger

| Package | Required end state / evidence | Current state |
|---|---|---|
| 1. Publish terminal-label fix | PR #62 merged after exact-head checks; reviewed and merged trees equal | DONE: squash 6af7292bcd4280d597e2f5a124332fb91264a400; CI 7/7, no unresolved review threads, tree equals fc99a6e |
| 2. ChatHandler responsibilities | Pure result presentation and prompt/RAG assembly extracted; compatibility, frames, numerical text, errors and lifecycle tests pass | DONE: PR #63 squash aa86377c60ff4c8a0457dc28ab6a9d6b4856c194; CI7/7; merged tree equals d4860d7; independent SPEC/QUALITY approved; joint5728 passed/2 skipped and final boundary99 passed |
| 3. Domain API routes | Domain-specific setup functions replace the mixed registrar; URL, HTTP/response contracts and shared service lifetimes verified | DONE: PR #64 squash ecd6ccad5ffdd155f5944c4136107004e2ea00be; exact-head0e54a1e CI8/8, no unresolved reviews; reviewed/merged tree b7a060b7cf559bd76d833a4c0372aee23403fa6a. First timeout retained; infrastructure PR #65 separately reviewed/merged. See domain-api-separation.md |
| 4. Remaining tool contracts | Inventory every registered tool; explicit reusable input/output contracts or justified non-executable helper boundaries; status/evidence retained | DONE: inventory PR #75 merged `7b5611f`, closing the former publication gate. Earlier evidence retained: 4A #67 merged1bba025; 4B #69 merged16b91575229be987b0c5cf3d8d9039135d8ade29, exact-headd1575f3 CI8/8/unresolved0, tree491b2a3d11fd7609ab741ee9c558f0737a32ccdc equal, full6318 passed/2 skipped. 4C #73 merged0295e9960b15c850bb212e434da66824181ef546, headbeaf9cf CI8/8/unresolved0, tree869d6f1592e1135797e176ee807c8c394b245857 equal; full6676 passed/2 skipped, G2 integration525 passed/162 subtests. 4D historical RED4 failed/12 passed →16 passed on merged4C; joint310 passed. RAG/activity/docking already typed |
| 5. Planner separation | Selection, request parsing and pure step templates have separate responsibilities; same plan/data bindings/clarifications verified | DONE: PR #71 squash5db56b0c79ac30da1ba4646c2c567e7d2dd71cc5, exact-head7f9abdc CI8/8, unresolved0; reviewed/merged treed10464ad002088622e8f5d1ef93ffddc2aa1c9a1. Integrated full6351 passed/2 skipped, final651 focused passed. Separate snapshot fix PR #68 mergede173d432f767fe76e1c1f101d9cd8824ffee612d after CI8/8 including Linux. Historical failed runs retained; controlled ancestor-mtime repro is not unique attribution of every historical sandbox failure |
| 6. Historical residual audit | Each original code-only residual classified as superseded, migrated or intentionally retained, with evidence; no dirty-tree reset | DONE: ADMET unknown evidence #74 merged `c3195f9` and evidence-bound report/cards #77 merged `be0219e`, together with existing #66/#70/#72. Earlier evidence retained: #66 merged3a68264; #70 mergedbb11ded0bdb62d919ca64969730cfd2b85d77980 (CI8/8, tree6ce2a90faaf01d319c853344262cf1bf9b37af79 equal); #72 merged27170d95b17ccae224b95b493ec8a9276944f421 (head548baca, CI8/8, tree789bb5fc17ba6db3db6b9e09596e09cd2ef37a50 equal). G2 full6412 passed/2 skipped before Planner integration; final639 passed/162 subtests. No wholesale dirty-tree merge or asset deletion |
| 7. Production-entry dynamic decisions | Normal Web entry actually supports the approved model-driven decision path, using existing loop/Session/authorization/evidence and bounded recovery; integration/browser evidence | PENDING overall. A1 #78 merged `782cd13129cb4c2328c398a3930f61172ea3ec62`; G1 satisfied and A2 implementation released after this docs freeze. A2 implementation/evidence, B bindings and C integration remain pending; isolated admission/bridge is not completion. Final ordinary-chat coverage remains required; deployment excluded |
| 8. Current real scientific acceptance | Latest integrated code tested with real configured providers/local generation/scientific tools and requested artifacts/data flow; repeats and honest failed/partial reasons reported | PENDING live/final acceptance. 8A offline aggregator #76 merged `5ad08ac`, with `live_execution_verified=false` and `final_acceptance=false`; not a live run. Historical real PDE/BuChE verification remains evidence, not a replacement for current integrated end-to-end testing |

The final audit must check all eight rows against current code, PR state and real reports. Passing offline CI is not proof of model quality, real Vina operation or server readiness. A missing dependency does not become a scientific pass.

Historical 2026-09-25 checkpoint (superseded status, retained chronology): package7 A1 was in independent finding closure; A2 design awaited landed A1; package8-A offline aggregator was under review. Those dependency/publication states are updated below, not evidence that the historical failures never occurred. No production-entry switch, external provider activation or deployment occurred in those batches.

## A2 G1 release and documentation freeze — 2026-09-25

Parent confirms packages 4 and 6 DONE through the merged PRs above, and 8A #76 as offline-only. Local Git ancestry includes #75/#74/#76/#77/#78; this freeze does not independently rerun their tests or query remote review/CI state.

For A1 #78, parent reports exact reviewed head `c4aafe2e3b7386a3653865396c5514a70310fee9`, independent Galileo SPEC and Peirce QUALITY approval with all eight A1 source/test files unchanged, complete CI **8/8 passed**, unresolved threads **0**, squash `782cd13129cb4c2328c398a3930f61172ea3ec62`. Local read-only Git confirms reviewed/merged trees both equal `a5eaff6d64e658224a96cba4616abaadf638c79f`.

Keep verification environments/results separate:

- **Linux CI complete Agent, parent-read GitHub evidence:** run `36073938886`, job `107880850355`, exact `c4aafe2e`: **7781 passed, 1 skipped, 11337 warnings in 270.12s**. This is complete Agent CI success within all 8 passing checks, not a local rerun. Preserve the high warning count; A2 does not include incidental warning cleanup.
- **Windows historical local full, still failed:** **7777 passed, 3 failed, 2 skipped, 7 warnings in 388.24s**, exit 1; three tracked evaluation fixtures were omitted by the isolation wrapper. Fixture-only recovery gave 3 exact-node passes and 32 module passes; it was not a passing local full. The earlier 1406 focused passes and subsequent Linux CI do not erase this failure. Full chronology remains in the landed A1 spec/plan, including prior SPEC/QUALITY findings and repairs.

Parent created `codex/web-decision-runtime-integration` on landed #78 and cherry-picked only the approved A2 documents through `20aeef5998153b99ed401c6ac0e350a44cd47a0b` (planning freeze `15a903d9`). This worker changes only this ledger plus the [A2 spec](../superpowers/specs/2026-09-25-web-decision-runtime-integration-design.md) and [A2 plan](../superpowers/plans/2026-09-25-web-decision-runtime-integration.md), explicitly stages them, commits locally and stops. No code/tests/compile/network/env/key/model assets/services/push in this freeze.

**G1 is satisfied and implementation release is OPEN after the freeze; no further user confirmation is required to begin.** The next code worker stays on this A2 branch, ultimately one A2 PR:

1. Batch 1: Task 5 low-level owned-worker helper/two submit hooks/decision execution and necessary loop binding, then Task 6 pure bounded history/prefix plus coherent loop/continuation validator/revision changes; focused TDD only, no Web wiring.
2. Batch 2: Tasks 2–4 app/WS/admission/epoch, Task 5 route cancellation/drain, Task 6 socket continuation/history, Task 7 references/#77 report, Task 8 UI; actual-route/ACK/strict report-order and offline review evidence remain required.

The three readiness bindings are frozen in the linked spec/plan: shared app store/reference/registry ownership and common writer publication; real bridge/loop signatures and revision-5 prefix gaps; candidate → optional strict report → complete with no added `turn_id` in strict presentation DTOs, no receive-as-ACK and no report restore. A2/C still differ: A2 retains lease/ownership until real descendant drain and executor join, with no finite terminal/graceful-shutdown guarantee for a worker that never exits; C requires finite UI terminal plus retained cleanup owner. Later C integration must reconcile this, not infer compatibility from release.

**P7 final ordinary-chat coverage is a mandatory pending gate.** The A2 `Explain logP` → `解释分子生成的概念` history pair proves incremental exact transmission through real admission/loop/adapter with an offline provider transport; it is not complete chat or real-model understanding. Preserve the original package-8 full capability-description questions and normal multi-turn cases. A1 rejecting an original real case means an unresolved capability gap, never permission to change that case to expect rejection, narrow P7 to whitelist chat, or skip the gate. Required improvements/classifier expansion need separate design; no new classifier is authorized by this freeze. A2/B/C and final/live acceptance remain pending.

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

### A2 local verification follow-up (2026-09-25)

A2 is implemented locally on `codex/web-decision-runtime-integration`; it has not been published or merged. Production source is unchanged since `2769e1e`. The later user-config fixture correction is independently reviewed; the ignored offline harness now permits only its one test-owned metrics endpoint, not general networking. Detailed original failures, scoped fixes and commands are retained in sections 21–25 of the A2 implementation plan.

At `5ad17c8`, local static checks passed (15 Node entrypoints, four changed-JS syntax checks, 335 Python files compiled, no credential-scan filename matches), followed by a single complete task-runtime run: **1549 passed, 25 skipped, one warning, zero failures, 122.75s**. Resource cleanup succeeded. Local Node 24 differs from CI Node 20. Earlier guard/startup timeout causes remain UNKNOWN; no timeout or scientific assertion was relaxed.

The subsequent complete affected root run at `9540516` passed **2060 cases and 173 subtests**, with 143 skips, five warnings and no failures in 324.08s. It excludes all 30 separately verified activity files and the three other test partitions.

Remaining A2 gates: complete Agent confirmation, final snapshot review and all CI gates before the unique PR can merge. P7 ordinary-chat, B/C integrations and P8 live acceptance remain pending; this follow-up does not redefine them as complete. The historical verification below is preserved rather than presented as current full success.

PR #62 merge was rechecked through GitHub API and local fetch; merge tree matched the reviewed head. New step-2 branch is based on origin/main 6af7292.
Six baseline files: test_chat_handler_partial_results, test_chat_handler_agent_events, test_chat_input_budget, test_agent_audit_regressions, test_chat_local_cleanup, test_scientific_reference_web (all under tests/agent).
Executed with MedChat Python and the existing isolated runner documented in docs/superpowers/plans/2026-09-24-rag-service-extraction.md, replacing only the worktree path. No model API or production assets used.
