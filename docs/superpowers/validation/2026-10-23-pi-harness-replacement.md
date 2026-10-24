# Pi runtime replacement validation — 2026-10-23

## Release status — engineering recovery verified in scoped batches; live acceptance incomplete

The initial cross-entrypoint regression exposed a capability gap: replacing the old default Web receiver with the former four-tool decision admission did not preserve the full former Web surface. The explicit `medchat-web-v1` profile now integrates generation, optimization, ranking, ADMET, reverse-target and RAG through individually bound existing tools. The latest mounted-entry/browser-lab/retirement/reference batch passed **66 tests**, and the browser-business/reverse-ranking batch passed **158 tests**. These scoped results close the reproduced failures below; they are not a final all-suite or live-model certification.

The offline browser-lab and main-integration regressions retain their original generation/reference and scientific-result expectations; they have not been replaced with preseeded candidates or weakened assertions to obtain a pass. No old harness fallback or whole-Agent tool wrapper has been added. The user explicitly approved restoring all Web capabilities before delivery on 2026-10-23. Live cloud/local compatibility and full deployment health remain unresolved; no release completion is claimed. Historical failures below are retained as intermediate evidence, with superseding results explicitly identified.

## Scope and release boundary

### PR #215 CI follow-up — baseline 8141b755

The maintainer requested CI/review repair on the same draft PR, explicitly without merging and without converting the unresolved live-model items into passes. At inspection, GitHub review submissions, inline review threads and conversation comments were empty.

[Quality run 40049269829](https://github.com/kxyesyes/molecular_chat_system/actions/runs/40049269829) completed with eight successful jobs, two failed Python partitions and the failed aggregate. `root-activity` recorded **1958 passed, 1 failed, 6 skipped (844.91 s)**: missing Node was reported as `chain_mismatch` instead of `dependency_unavailable`. The Agent job collected **15215 tests**, recorded six browser-quality failures, then exceeded its unchanged **1200 s** command deadline at 24%; it did not finish and has no final passing count. The log shows continued progress, including about 410 s in binding continuation and 156 s in binding loop, rather than proving the last interrupted test hangs.

- The exact seven observed failures reproduced locally: **7 failed, 16 passed (57.97 s)**. Six were stale browser success-terminal expectations (`succeeded` instead of the actual Web `completed` contract). Only these expected values and the missing existing cleanup fixture import were corrected; all molecular values, evidence/session isolation, helper equivalence, tool-call counts and partial-result assertions remain. The quality/evidence/S02/context combination then passed **120 tests (242.06 s)** using real Pi and RDKit, with scripted model/activity fixtures, not live inference.
- The maintainer approved five additional bounded Agent partitions. Browser/chat, decision bindings, remaining decision, Pi and remaining Web tests now have separate 1200-second command budgets; the existing general core, Web lifecycle, docking/publication, root and other groups retain their limits. No production deadline, scientific assertion or runtime process model changed.
- New partition contract first failed because the groups were absent (**1 failed / 43 deselected**), then passed. Complete workflow contracts: **49 passed, 2 platform skips (114.80 s)**. Synthetic tests execute the actual inline verifier and Bash expansion, including omission/overlap, nested future files, docking collisions, duplicate/unknown/missing groups, child errors and deadline faults. The unchanged 180 s/60 s actual repository collector also passed: **17754** original Agent IDs, distributed exactly once as core 10535, browser/chat 811, binding 926, decision 1360, Pi 703, Web 880, lifecycle 225, docking 2045, publication 269.
- Pi now requires Node before the later DOM stage in the acceptance helper. A narrow preflight at `decision_session` returns fixed `dependency_unavailable` only when Node is missing. An available Node with injected launch failure still attempts the real bridge, produces `chain_mismatch` / `decision_runtime_failed`, calls no model, reaches no DOM and exposes no raw exception. The original environment/Torch/cache-restoration assertions remain, with stage assertions added. Focused RED: **2 failed / 1 passed (16.05 s)**; same three cases GREEN: **3 passed (14.80 s)**. Full acceptance-chain module plus Pi bridge: **127 passed, 2 existing PyTorch/PyG warnings (851.85 s)**.
- Independent SPEC then QUALITY review passed for both slices without actionable findings. The CI reviewer independently reran **49 passed / 2 platform skips**; a Windows temporary-directory cleanup issue occurred after its test result and is not counted as an additional clean launcher pass. Parent compileall and whitespace checks passed. No production source code changed in this follow-up.
- New-head remote CI must be checked after publication; these local checks do not establish remote CI or deployment acceptance. The cloud DNS rejection, local model protocol failures and 21/24 health limitations remain unchanged.

Commands use isolated temporary state and no live-model credentials:

```powershell
python -m pytest tests/agent/test_browser_quality_boundaries.py tests/test_activity_family_acceptance_chain.py::test_settings_and_caches_restore_even_when_node_is_missing -q -p no:cacheprovider --show-capture=no --durations=10
python -m pytest tests/agent/test_browser_quality_boundaries.py tests/agent/test_browser_evidence_followup.py tests/agent/test_browser_s02_context.py tests/agent/test_browser_context_isolation.py -q -p no:cacheprovider --show-capture=no --durations=10
python -m pytest tests/test_quality_workflow_contract.py -q -p no:cacheprovider --show-capture=no --durations=10
python -m pytest tests/test_activity_family_acceptance_chain.py::test_decision_session_requires_node_before_pi_execution tests/test_activity_family_acceptance_chain.py::test_available_node_launch_failure_is_not_missing_dependency tests/test_activity_family_acceptance_chain.py::test_settings_and_caches_restore_even_when_node_is_missing -q -p no:cacheprovider
python -m pytest tests/test_activity_family_acceptance_chain.py tests/agent/test_pi_bridge.py -q -p no:cacheprovider
python -m compileall -q src scripts tests/test_quality_workflow_contract.py tests/agent/test_browser_quality_boundaries.py
```

### Final integration sweep — draft handoff, not production acceptance

The maintainer explicitly chose to retain the live-model unverified items and deliver a draft PR. All reproduced failures have a subsequent passing scoped regression. The final 44-agent-file prefix plus lifecycle rerun passed **2558 tests, 7 existing warnings (1162.59 s)**; the complete lifecycle module separately passed **39 tests, 7 warnings (19.59 s)**. No production code changed after the second 127-file sweep: only the lifecycle test synchronization and documentation changed. The complete 127-file sweep was **not rerun** after that fixture correction, and the entire repository/remote CI is not certified green. Original worktree changes and untracked assets remain untouched. Live cloud/local compatibility and deployment health remain release gates, not blockers to the authorized draft handoff.

The 127 changed/new Python test-file sweep completed with **68 failed, 6613 passed, 5 skipped, 4 errors, 7 warnings (3929.95 s)**. This is a historical failing run, not a full-suite pass. The failures were grouped rather than hidden: ordinary assembly (3), planning review (6), Web admission review (3 plus 4 cleanup errors), prompt matrix (8), target clarification (1), reverse-rank Web review (43), model request lifecycle (2), and Web profile default (2).

- Assembly/default migration retained explicit closed-profile behavior and removed an obsolete private-entry refusal stub: **84 passed** in the focused assembly/entry/retirement run.
- Planning fixtures now observe the real Pi registry, ordered observations and socket history rather than mutating a removed Supervisor after assembly. Existing assertions remain. Comparison binding validates complete molecular fields before canonical deduplication and retains the raw candidate cap. Unsupported explicit ordering is rejected; generation politeness cannot be mistaken for a molecular subject. Negative review cases first recorded **6 failed / 1 passed**, followed by the comparison/planning/admission/default batch: **247 passed, 7 existing warnings (101.51 s)**.
- Bounded ordinary explanatory lookup retains RAG permission and rejects appended actions. Missing molecular input is checked before endpoint coverage; ambiguous targets retain a fixed safe clarification, without forwarding arbitrary exception text. Lookup tests first recorded **2 failed / 5 passed**. The first integrated run recorded **207 passed / 1 failed** because the long target explanation triggered existing redaction; fixed concise wording preserved the filter. Latest obligation/prompt/target batch: **208 passed, 3 existing warnings (27.37 s)**.
- Independent read-only review of these latest binding/admission changes found no remaining actionable issue, including full-field validation, canonical deduplication, 16/17 raw-count boundary, RAG permissions and additional-action rejection.
- The two original lifecycle failures have not reproduced: its full module passed **37 tests**; ordered entrypoint/design-switch combinations passed **56** each; an expanded nine-module run passed **359, 3 skipped**. The original three-second gate wait and all ownership/frozen-configuration assertions remain unchanged. The full sweep cannot be declared green based on these reruns.
- Reverse-rank observer migration first reduced its original **43 failed / 127 passed** to **18 failed / 152 passed (209.24 s)** without production changes. The remaining failures were fresh-query routing and a Chinese display suffix in the sealed molecular input. The existing fresh-routing helper is now shared without changing its behavior; execution/history keep the original query, and only the exact display-clause comma is normalized before strict molecular parsing. Failure-first binding tests recorded **2 failed / 10 passed**, then **53 passed**; the original reverse Web/source/follow-up combination passed **312 tests, 3 warnings (311.14 s)**.
- Independent review found comma-separated additional scientific actions could escape coverage. Three new negative cases failed before the action-boundary correction, then the follow-up/obligation/requirements combination passed **142 tests, 3 warnings (9.72 s)**. The fix splits commas only before recognized independent imperative verbs, not arbitrary molecular lists. The finding was independently rechecked as closed; reverse-helper quality review passed.
- The two lifecycle cases also passed with all **127 changed test files collected**: **2 passed / 6717 deselected / 7 warnings (33.52 s)**. There is still no demonstrated root cause for the earlier intermittent failure; no timing or ownership assertion was relaxed.
- The second full 127-file integration run ended with **6720 passed, 2 failed, 5 skipped, 7 warnings (4119.79 s)**, without concurrent test workers. Only the two model-switch startup waits remained. The later 44-agent-file prefix plus lifecycle module reproduced **2554 passed / 2 failed (1136.41 s)**. Diagnostics showed an unfinished chat with one valid model decision and no decision error; the full-run synthetic database confirmed both requests ultimately succeeded, with physical tool-start events 5.156 s and 5.864 s after run creation. This is not evidence of a model-switch ownership failure. Python compilation, JavaScript syntax, staged/unstaged whitespace checks and all six official Pi SDK contracts passed again.
- A deliberately delayed first model reply (3.2 s) independently reproduced both wait failures (**2 failed, 14.41 s**). The fixture now synchronizes after a successful, correctly typed first property-tool decision (15 s startup guard), then retains the original **3 s physical tool-entry wait** and every switch/frozen-configuration/cancellation/drain assertion. Both zero-delay and delayed replies are tested. This removes the implicit end-to-end three-second performance assumption; it does **not** explain the broad batch's slower execution or establish that a profiler/resource leak is absent. No production timing limit was increased. Independent review accepted this boundary, with that caveat. Final focused/prefix results are recorded above; compileall, JavaScript syntax and staged/unstaged whitespace checks passed again before commit.

```powershell
python -m pytest tests/agent/test_pi_web_followup_admission.py tests/agent/test_review_planning_route_gates.py tests/agent/test_review_web_admission_gates.py tests/test_web_chat_profile.py -q -p no:cacheprovider
python -m pytest tests/agent/test_pi_web_obligation_admission.py tests/agent/test_routing_prompt_matrix.py tests/agent/test_target_identity_alignment.py -q -p no:cacheprovider
python -m pytest tests/agent/test_web_reverse_rank_review.py tests/agent/test_chat_reverse_rank_followup.py tests/agent/test_reverse_target_rank_followup.py tests/agent/test_pi_web_followup_admission.py -q -p no:cacheprovider
python -m pytest tests/agent/test_pi_web_followup_admission.py tests/agent/test_pi_web_obligation_admission.py tests/agent/test_web_profile_requirements.py -q -p no:cacheprovider
python -m pytest tests/test_model_request_lifecycle.py -q -p no:cacheprovider
# Final reproduced-prefix selection, relative to the migration baseline:
$prefix = @(git diff 616d8e9e --name-only --diff-filter=ACM -- tests/agent) | Where-Object { $_ -match '/test_.*\.py$' -and $_ -ge 'tests/agent/test_pi_web_admet_projection.py' } | Sort-Object -Unique
python -m pytest @prefix tests/test_model_request_lifecycle.py -q -p no:cacheprovider --show-capture=no
```

### Confirmed-reference replay regression — historical failures and current repair

The reference-resilience tests now enter the real Pi/Web runtime instead of the removed private handler. The first migrated run recorded **10 passed, 3 failed (28.05 s)**. The three failures are not suppressed: a repeated request for the same confirmed candidate has no preserved request idempotency key, and property calls occur again after reopening the store or after cancellation/drain. Subsequent inspection found that the old MW-only fixture did not satisfy the typed property adapter; normal execution recorded invalid output and cancelled execution had only a running checkpoint. That initial run therefore does **not** prove that verified, committed successful evidence was repeated. The fixture is being corrected without removing the original replay assertions. Restoring safe owner/source/configuration-bound replay remains required before release; no fallback to the old whole Supervisor executor has been added.

The retained assertions cover source TTL, invalid-source fail-closed behavior, SQLite read/write/uncertain-commit behavior, cancelled physical-worker draining and model-lease ordering, partial-candidate ACK/restore, and non-mutation of original scientific source records. Both cancellation cases passed their drain/lease assertions before failing at replay. This evidence does not establish replay completeness.

After replacing the property fixture with actual RDKit output, correcting its unavailable-error enum and adding three real-adapter checks, the reference execution/resilience batch recorded **56 passed, 3 failed** with all original assertions retained. The normal first request now has a succeeded property checkpoint and execution record, but still lacks an idempotency key; its test stops before reopening. The cancellation cases still have a cancelled public terminal, failed persisted run and only a running checkpoint, followed by a new run and repeated physical property call. Safe retry must distinguish verified durable success from physically drained but uncommitted execution; simply reopening a dynamic trace or allowing cancelled claims is not an adequate repair.

A read-only cancellation trace localized the missing receipt: the property adapter returns succeeded, but Session replaces the result with cancelled before scientific validation/sealing. After observation preparation, subsequent cancellation replacements drop the result's binding proof while the action journal retains it. Ledger correctly rejects the mismatch; settlement retry does not redispatch but cannot commit, and the controller records an incomplete action journal. This is not deletion of a committed success by publication correction. A completed scientific receipt and a publicly cancelled turn require separate, verified handling; disabling ledger checks or copying a success proof onto an empty cancelled result would be unsafe.

### Cancelled-turn execution receipts (before cross-request claim integration)

- The completed-call loss above is now repaired for the full-Web dynamic session. A physically returned tool result passes the same scientific validators, owner/configuration/current-source preparation and evidence sealing even when cancellation has arrived. Its execution receipt/checkpoint records the verified tool outcome; the public request remains cancelled. Failed or stale-source output is not promoted to success.
- Repeated cancellation no longer strips an already prepared cancellation proof. Late cancellation is rechecked before warning and tool-terminal publication, including cancellation during capture, execution/checkpoint commit, warning callbacks and commit-then-raise settlement retry. No second tool call, scientific success event after cancellation, or fabricated completion is introduced.
- New real Pi/Web tests first reproduced **2 failures / 1 pass**, then passed all three cases: genuine RDKit completion, malformed output and expired source. Five additional Session lifecycle tests cover late-cancellation boundaries; four initially failed, then passed with the publication recheck.
- Adjacent generation cancellation, dynamic binding, ownership, owned-call and allocation regression: **401 passed, 7 warnings (99.30 s)**. Final reviewed receipt/shared-session partition: **183 passed (25.42 s)**. Focused compilation and diff checks passed. These batches overlap and do not certify live model compatibility.
- The broader Session/publication/reference batch recorded **215 passed, 3 failed (114.37 s)**. The remaining failures are still the unchanged cross-request identity/replay assertions: a missing idempotency key and repeated property calls on a newly created retry run. Correctly retaining a cancelled turn's execution receipt is necessary but does not by itself authorize restoring it in another request.

```powershell
python -m pytest tests/agent/test_pi_web_cancel_receipts.py tests/agent/test_dynamic_run_session.py tests/agent/test_workflow_run_session.py tests/agent/test_decision_binding_session.py -q -p no:cacheprovider
python -m pytest tests/agent/test_dynamic_run_session.py tests/agent/test_workflow_run_session.py tests/agent/test_decision_binding_publication.py tests/agent/test_scientific_reference_resilience.py tests/agent/test_pi_web_cancel_receipts.py -q -p no:cacheprovider
```

### Cross-request reference claim and receipt replay

- Added an owner-bound SQLite claim with a unique request identity, retained first context/trace, generation/token fencing and explicit READY/BLOCKED settlement. Running or uncertain claims cannot be taken over on a timeout. The ordinary workflow claim API is not relaxed; a dedicated atomic start consumes each reference generation once.
- Full-Web preparation restores the original context after a successful claim. New transport traces and growing model history do not change the scientific identity; the selected source trace, full pointer, query, permissions, plan and configuration remain identity inputs. Pi still owns action selection; no Supervisor execution fallback or Python decision loop was added.
- Web action step IDs now derive from the authenticated action digest, allowing the existing Session checkpoint path to find completed work. Checkpoint verification authenticates old provenance, data, proofs, source identity and envelope before any preparation can replace fields. Web snapshot revision is **13**, B1 stays **11**; revision12 snapshots are rejected without deletion.
- The first integrated reference batch passed **15 tests (56.94 s)**, including the three original replay failures. Cancellation replay assertions now also require a real partial completion and the failed assessment's attempted retry, so a rejected retry cannot masquerade as successful reuse.
- Added same-socket history-growth, active-worker cancellation/concurrent retry, damaged receipt, waiting/nonce and identity tests. Waiting recovery exposed noncanonical JSON key order in the Web prompt; a minimal prompt test failed before Web-only canonical serialization. The resulting eight-test batch passed **8 tests (50.95 s)**.
- Independent review found two earlier decode-error paths bypassing the guarded checkpoint validator. Two real-Web tests first repeated physical work, then exposed READY release after a local exception. Both paths now enter the same guarded failure latch before terminating; final focused result **2 passed (18.01 s)**, including no physical redispatch and rejection of a third request. Ordinary workflow/B1 decoding behavior remains unchanged.
- An earlier 13-file integrated run recorded **617 passed / 1 failed (261.21 s)**, with only the waiting prompt-order failure above. It imported code before that repair and the later decoder changes, so it is not claimed as a final full-suite pass. The latest twelve-file combined reference/archive/validation/resilience/ownership/Web-binding/B1-continuation regression now passes **610 tests, 3 existing SWIG warnings (755.47 s)**; its exact selection is below.
- SQLite claim tests: **136 passed**; related persistence/session batch **641 passed**. Independent Web identity/profile/continuation batch **52 passed** and full B1 continuation batch **175 passed**. Checkpoint validation tests **63 passed**, related domain binding batch **359 passed**. These overlapping offline batches are not additive and do not establish live-model compatibility.
- Review additionally caught a reference claim marker substituting for the one-time continuation nonce. The marker now must agree with the actual restored continuation; it cannot authorize resume by itself. Failure-first coverage and actual waiting recovery passed **20 tests (80.27 s)**. The latest mounted-entry batch below also includes these tests.
- Same-trace presentation recovery now atomically archives the complete prior run, consumes the claim generation and replaces active metadata. The append-only `agent_reference_run_history` preserves the original raw metadata/TTL; previous confirmed pointers cannot revive, newly displayed candidates require a new ACK, and input source A is unchanged. Archive tests first failed all **16**, then the archive/claim/source-store batch passed **214**. A real-Pi Web generation/retry test also passed, with one generation call and exact archived-row equality. Related persistence/continuation/contracts/binding/resilience/ownership/retry regression passed **589 tests, 3 warnings (209.95 s)**.
- A pre-latch-fix run recorded **361 passed / 2 failed (494.32 s)**; its only failures were the damaged-checkpoint decode cases described above. It is retained as intermediate evidence, not a final pass. Final mounted entry/reference/profile/identity/retirement regression passed **66 tests, 7 existing deprecation warnings (162.89 s)**. Scientific generation/provider replies are offline fixtures; actual Pi, SQLite and RDKit are used where specified.

```powershell
python -m pytest tests/agent/test_pi_reference_retry.py tests/agent/test_scientific_reference_resilience.py -q -p no:cacheprovider
python -m pytest tests/agent/test_pi_reference_claims.py tests/agent/test_pi_web_step_identity.py tests/agent/test_pi_web_checkpoint_validation.py -q -p no:cacheprovider
python -m pytest tests/agent/test_pi_reference_retry.py tests/agent/test_pi_reference_claims.py tests/agent/test_pi_reference_archive.py tests/agent/test_pi_web_checkpoint_validation.py tests/agent/test_pi_web_step_identity.py tests/agent/test_pi_web_cancel_receipts.py tests/agent/test_scientific_reference_resilience.py tests/agent/test_run_session_ownership.py tests/agent/test_web_profile_bindings.py tests/agent/test_web_profile_session.py tests/agent/test_decision_continuation.py tests/agent/test_decision_binding_continuation.py -q -p no:cacheprovider
```

### Web compatibility corrections and current regression evidence

- Input-budget, mounted-Web input-budget, presentation, transport and local-cleanup files: **149 passed, 4 existing deprecation warnings (119.43 s)**. Allocation deadline/handoff files: **44 passed, 7 warnings**. These are separate overlapping engineering batches, not live-model certification.
- Generation request preparation now distinguishes an omitted/null count from an explicitly supplied count, retaining canonical prose parsing and explicit-count precedence. Agent-event/generation admission, generation config, optimization count and planner regressions: **429 passed, 7 warnings**; frontend generation-count contract, focused compilation and diff checks also passed.
- Four nounless malformed-generation cases initially failed because their migrated tests had dropped the original explicit molecular-skill premise. Baseline source showed no raw-Web routing guarantee for those phrases. The tests now exercise real contextual compilation with that premise and retain ordinary non-molecular counterexamples; production grammar was not broadened to force every creation request into molecular generation.
- Missing-structure property requests now preserve the closed `molecular_input_required` admission reason and targeted SMILES guidance. Bare valid SMILES with RAG enabled creates a required retrieval action. The scoped admission/runtime regression recorded **82 passed**, including **37 obligation tests**; tools remain undispatched on rejected missing-input requests. Unknown exception text is not forwarded.
- A business-context/reverse-ranking run recorded **115 passed, 13 failed (225.32 s)**. Two failures were overbroad ranking admission for evidence comparisons; eleven cascaded from reverse-receipt validation receiving the full query instead of bound molecular input. Correcting these uncovered two further failures (**126 passed / 2 failed**): typed ADMET projection omitted endpoint-only results, and allocation-busy rejection retained an old ranking pointer. Both were repaired with failure-first coverage, strict endpoint projection and socket-local history cleanup. The original four files plus the new ADMET/allocation files now pass **158 tests (285.84 s)**; no business safety assertion was removed. Related ADMET/Web/B1 batch: **248 passed / 1 skipped / 7 warnings**; the skip lacks the real ADMET backend and is not scientific acceptance.

Latest reproducible scoped selections (same isolated environment as the earlier batches):

```powershell
python -m pytest tests/agent/test_browser_s02_context.py tests/agent/test_browser_evidence_followup.py tests/agent/test_browser_context_isolation.py tests/agent/test_chat_reverse_rank_followup.py tests/agent/test_pi_allocation_history.py tests/agent/test_pi_web_admet_projection.py -q -p no:cacheprovider
python -m pytest tests/agent/test_pi_reference_retry.py tests/agent/test_pi_profile_error_contract.py tests/agent/test_pi_web_step_identity.py tests/agent/test_main_integration_acceptance.py tests/agent/test_scientific_reference_browser_lab.py tests/agent/test_pi_full_web_entry.py tests/agent/test_pi_retirement.py -q -p no:cacheprovider
```

Latest syntax/SDK/frontend checks: `python -m compileall -q src scripts`, `node --check src/web/static/js/home/main.js`, and `npm test --prefix runtime/pi` all passed (Pi SDK: **6 tests**). The four changed frontend contracts passed: `home_decision_runtime_test.js` (**71**), `home_llm_settings_test.js` (**17**), `home_reference_controls_test.js`, and `home_tool_toggle_visibility_test.js`. No cloud/local real acceptance or strict deployment-health rerun is implied by these checks.

The full local Node contract selection also passed: **44 `tests/*_test.js` files plus `activity_family_acceptance_dom.js` (17 checks)**. `git diff --check HEAD` passed. A filename-only scan of **220 changed/new task text paths** found no matches for the CI credential patterns; this bounded pattern scan is not a general secret-detection guarantee. The original checkout still retains its unrelated dirty branch/files and was not modified.

Final independent read-only review of the reference claim/controller/Session/SQLite archive lifecycle found **no actionable correctness findings**. It specifically covered physical drain, guarded decode rejection, one-time nonce authority, immutable history and old-presentation revocation; the reviewer did not run tests or change files. The latest combined reference/B1 regression passed **610 tests**. The run of all **127 changed/new Python test files** (6,686 collected cases before the correction below) ended with failures; its result and subsequent repairs are recorded in the final-integration section above.

That integrated run exposed three assembly failures, independently reproduced as **3 failed**. Two old assertions still expected the closed four-tool default when the policy was omitted; the test now checks the requested full-Web default while preserving explicit `a1_closed` assertions for every mode spelling, including `pi`. The third found semantic assembly recreating the retired `_process_message` attribute as a refusal stub. The stub did not execute the old Harness, but violated the retirement contract; its assignment and now-unused refusal helper were removed. The actual direct Pi decision bridge still rejects missing server-owned semantic admission with HTTP 503, and that assertion is unchanged. Assembly/full-Web-entry/retirement regression then passed **84 tests, 7 existing warnings (25.55 s)**. The integrated process imported the older code, so its final result remains an intermediate result rather than a pass for this correction.

```powershell
python -m pytest tests/agent/test_ordinary_app_assembly.py tests/agent/test_pi_full_web_entry.py tests/agent/test_pi_retirement.py -q -p no:cacheprovider
```

Explicit staging excluded temporary helpers, runtime output and installed dependencies. The first staging attempt included already-staged deleted paths and failed without a commit; selecting only remaining unstaged/new task paths succeeded. The complete staged whitespace check then found surplus EOF blank lines in 17 new/extracted files; only those trailing blank lines were removed, and `git diff --cached --check` subsequently passed. This was formatting-only, with no runtime logic changes.

### Partial evidence and reconnect corrections

- A dependency refusal no longer automatically poisons valid prior observations. Only the exact full-Web `web_dependency_unusable` refusal can take the bounded recovery path, and the complete binding/source closure is verified again before leaving the owned guard. Configuration, deadline, current-source or integrity failures still latch and invalidate publication. No rejected child tool is dispatched.
- Server recovery renders only authenticated typed observations, distinguishes displayed closure IDs from model citations, and retains unsatisfied/ineligible acceptance. Missing requested subjects are now distinguished from foreign subjects for display: only a unique, valid strict subset is shown as incomplete. Foreign, duplicate and invalid subject data remain excluded; no missing values are fabricated.
- The migrated partial-alignment test had accidentally introduced a no-ranker assertion unsupported by the original baseline. It now restores the original dispatched-but-failed-ranker behavior with schema-valid, deliberately incomplete real RDKit output. A separate real-Pi test covers actually blocked dependencies and mutation rejection. Original alignment and missing-candidate assertions remain intact.
- An independent focused review found no actionable issues in these changes; **20 tests passed, 120 deselected**. The broader five-file run recorded **277 passed, 1 failed (194.25 s)**: the remaining failure revealed durable reconnect turning an omitted generation count into an explicit default.
- Durable full-Web request metadata now preserves count presence separately from the normalized count. Omitted/null and explicit counts reconstruct the original context and compiled requirements; missing or non-boolean presence metadata is rejected without modifying history. Ambiguous older experimental waiting records are not automatically converted. Count/budget reconnect plus generation-admission/config regression: **66 passed, 7 warnings (22.52 s)**. The complete five-file rerun then recorded **280 passed, 7 warnings (191.85 s)**. Focused compileall and whitespace checks passed; no all-suite pass is claimed.

```powershell
python -m pytest tests/agent/test_web_profile_acceptance.py tests/agent/test_chat_handler_partial_results.py tests/agent/test_pi_web_input_budget.py tests/agent/test_decision_binding_publication.py tests/agent/test_web_profile_bindings.py -q -p no:cacheprovider
python -m pytest tests/agent/test_pi_web_input_budget.py::test_durable_web_continuation_pins_admitted_model_budgets tests/agent/test_pi_web_generation_admission.py tests/agent/test_chat_generation_request_config.py -q -p no:cacheprovider
```

### Actual-input budget and durable-settings checkpoint (still not a release)

- Four initial mounted-Web failures showed actual outbound input exceeding the configured 7000-character budget and an oversized complete protocol request still dispatching HTTP. The transport now checks final message content plus native decision arguments after protocol construction. It can omit only exact frozen ordinary user/assistant prefix pairs, oldest first; an omission notice also spends budget. Immutable requirements remain unchanged; their prompt projection no longer duplicates optional history. Full questions, molecular strings, evidence, proof and repair instructions are never sliced.
- Web preparation freezes input/history/output limits and temperature. A reconnect test found default-budget restoration, then identified substring credential redaction of the old output-count metadata key. Numeric output limits now use neutral persisted field names; global secret filtering was not relaxed. A real mounted reconnect/one-shot continuation uses the originally admitted output limit even after handler configuration changes.
- A failure-first real-RDKit case verified one successful scientific dispatch followed by zero additional HTTP when its complete observation would exceed input budget. The controller preserves partial work through the existing independent evidence renderer. The audit explicitly distinguishes server-displayed closure IDs from model citations, keeps citation_checked false and satisfied/finish_eligible false, and retains provenance, error and actual values. Mutable/forged science still fails its binding checks. The UI includes the budget-stop explanation; no extra model summary is generated.
- Focused budget/acceptance/resolved-property/business-review regression: **65 passed, 7 existing deprecation warnings (62.37 s)**. Earlier budget/profile/Web-lifecycle regression: **173 passed, 7 warnings (306.82 s)**, before the added partial-display checks. Focused compileall and diff checks passed. Counts overlap; neither is a full-suite, live-model or deployment pass.
- Broader model/provider/full-Web execution/profile/presentation regression: **323 passed, 7 existing warnings (122.21 s)**. This run includes the actual-RDKit overflow case and does not substitute for live cloud/local acceptance.
- Retired-entry tests in input-budget, local-cleanup, allocation, event/partial and reference-resilience partitions are still being ported. No completion, commit, draft PR or production release is claimed by this checkpoint.

```powershell
python -m pytest tests/agent/test_web_profile_acceptance.py tests/agent/test_pi_web_input_budget.py tests/agent/test_resolved_property_evidence.py tests/agent/test_pi_web_business_review.py -q -p no:cacheprovider
python -m pytest tests/agent/test_pi_web_input_budget.py tests/agent/test_pi_web_profile_runtime.py tests/agent/test_web_decision_runtime_lifecycle.py -q -p no:cacheprovider
python -m pytest tests/agent/test_pi_web_input_budget.py tests/agent/test_web_profile_acceptance.py tests/agent/test_pi_web_presentation.py tests/agent/test_pi_full_web_execution.py tests/agent/test_pi_web_profile_runtime.py tests/agent/test_ollama_decision_compat.py tests/agent/test_chat_generation_request_config.py tests/agent/test_pi_provider.py tests/test_agent_decision_model.py -q -p no:cacheprovider
```

### Request configuration and failure-frame checkpoint (still not a release)

- Full-Web model decisions now carry the admitted request temperature and provider-specific output budget through Pi's single-transaction Python adapter, including a protocol-repair request. B1 and ordinary-intent defaults remain separate. Invalid transport temperatures and invalid prepared output budgets are rejected before HTTP dispatch.
- The retired-private-entry failure tests now execute the real Pi/Web runtime with the real Ollama adapter and an offline HTTP boundary. They retain one-dispatch, cleanup, error classification, redaction, failed-send propagation and socket-local history assertions. Pi consumes a complete structured decision; interrupted wire fragments are not published as browser prose.
- A new failure-first presentation check found that `agent_result` retained a structured error while the full-Web `complete` frame omitted it. The terminal now copies the same already-sanitized error and warnings. Existing B1 serialization is unchanged; no provider response body is exposed.
- Configuration/local compatibility/model/profile regression: **198 passed, 7 existing deprecation warnings (40.70 s)**. Failure/presentation/transport regression: **63 passed, 7 existing deprecation warnings (45.51 s)**. These batches overlap other evidence and are not a full-suite or live-model pass. Focused `compileall` and `git diff --check` passed.
- Additional transport/ordinary-intent/Pi-provider/protocol-repair/Web-session regression: **528 passed (30.25 s)**. No existing protocol adapter required an incompatible call-signature fallback.

Exact test selections (using the isolated local regression environment):

```powershell
python -m pytest tests/agent/test_chat_generation_request_config.py tests/agent/test_ollama_decision_compat.py tests/test_agent_decision_model.py tests/test_openai_compatible_model.py tests/agent/test_web_profile_requirements.py tests/agent/test_pi_web_profile_runtime.py -q -p no:cacheprovider
python -m pytest tests/agent/test_chat_runtime_failures.py tests/agent/test_pi_web_presentation.py tests/agent/test_decision_chat_transport.py -q -p no:cacheprovider
python -m pytest tests/agent/test_decision_transport_boundaries.py tests/agent/test_ordinary_intent_transport.py tests/agent/test_ordinary_intent_protocol.py tests/agent/test_pi_provider.py tests/agent/test_decision_protocol_recovery.py tests/agent/test_web_profile_session.py -q -p no:cacheprovider
```

### Full-Web integration checkpoint (still not a release)

- The mounted Web default now retains ordinary chat, configured question admission, full registered capability reporting, evidence-only scientific display and honest partial diagnostics. `final_answer` remains the structured audit; `display_answer` is the human-facing projection. RAG cards consume only the accepted audit data, not arbitrary raw tool columns.
- Candidate generation, ACK, durable restore, selected second-candidate calculation and foreign-owner rejection passed the unchanged offline browser-lab assertions. The generator/provider is scripted; RDKit and Pi are real. No candidate preseed was used.
- Socket-local property comparison and invalid-structure correction now use the existing pure business helpers. Scientific pointers are kept separately from plain model history and published only after terminal delivery. The same authenticated owner on a new socket cannot inherit that temporary evidence.
- A failure-first retirement guard exposed the remaining private Web whole-agent path. `_process_message`, `_process_message_body` and `_execute_agent` were removed; pure scientific and presentation helpers remain. Tests that called those private methods still require migration to Pi or the appropriate pure boundary; this is an outstanding release gate, not permission to drop their safety assertions.
- Before private-method removal, actual Web/business/main-integration tests passed **25**. After removal, retirement/business/reference tests passed **13**. A requirements/reference/presentation partition passed **50**. These overlapping batches are not summed and do not represent a full-suite pass.
- A broader pre-removal transport/lifecycle/allocation partition recorded **200 passed, 6 failed**. Five failures involve scientific-report snapshot/projection integration (under repair); the serialization-keyword compatibility failure subsequently passed all **3** cases. New accepted-data-only presentation checks passed **4**. Independent review and migrated business regressions are still in progress.

- Baseline: `616d8e9e`; branch: `codex/pi-harness-replacement`.
- Work is isolated from the original checkout and its pre-existing changes.
- Official `@earendil-works/pi-coding-agent` and `@earendil-works/pi-ai` are fixed at `1.4.0`; Node `24.15.0`, Python `3.10` were used locally.
- Draft PR only; no merge or production deployment is authorized by this change.
- The authoritative setup, model configuration, checkpoint policy and rollback instructions are in [PI_RUNTIME](../../PI_RUNTIME.md).

## Removal, extraction and retained responsibilities

Removed: `ModelDecisionLoop`, `HarnessFactory`, legacy/shadow/canary/LangGraph backends, backend-only tests, old backend environment selection and `requirements-agent-harness.txt`. No Python or script imports of the removed package remain.

Extracted: scientific preparation, bounded evidence/history projections, domain binding validation, immutable requirements, clarification, continuation and terminal publication into `src/agent/scientific_session/`. Extraction does not make these callbacks another model loop.

Added: the official Pi session host in `runtime/pi/` and Python process/provider/checkpoint adapters in `src/agent/pi_runtime/`. The actual public WebSocket entry requires Pi; missing composition fails closed. Deterministic Supervisor/workflow and evaluation execution calls the existing executor directly.

Retained: scientific tools and validators, RDKit calculations, activity/ADMET models, target/reverse-target/RAG provenance, docking approval and task dispatch, durable execution identity, idempotency, cancellation/physical worker settlement, user ownership and partial/failure rendering. No scientific scores or experimental conclusions were manufactured.

The Pi child has an explicit MedChat tool catalog, no built-in shell/file tools, no discovered extensions/skills/project instructions, no inherited credentials and no independent model-network transport. Model calls remain single transactions in the existing Python transport. Local Ollama uses a separately owned loopback-pinned client without changing the generation model or client.

## Checkpoint compatibility

Pi protocol revision is `1`; current domain snapshot revisions are `9`, `10`, `11` and `13`. Web revision13 remains distinct from B1 revision11; experimental Web revision12 is rejected without deletion. New continuation validates both Pi-owned session history and the existing domain evidence/owner/CAS requirements. Unsupported old checkpoints remain readable as history but cannot execute. There is no data deletion, automatic checkpoint conversion or blind replay of scientific actions.

## Local verification evidence

Counts below are separate, sometimes overlapping batches, **not an additive unique total**. Scripted provider responses and HTTP mock transports are engineering contracts, not live model acceptance.

| Check | Observed result |
| --- | --- |
| Pre-migration focused behavior baseline | 194 passed; 7 existing deprecation warnings |
| Pi runtime/provider/checkpoint/bridge after independent-review fixes | 143 passed (22.52 s) |
| Long-history limit classification plus Pi runtime/provider | 123 passed (96.00 s); no HTTP dispatch on rejected input |
| Shared decision codec and docking approval/retention contracts | 243 passed (179.52 s) |
| Bridge malformed peer/exit/request identity plus retirement guard | 28 passed (16.16 s) |
| Web privacy/history/secret cleanup fixtures on actual Pi | 134 passed; 7 deprecation warnings (27.02 s) |
| Canonical Ollama adapter, CLI, actual Pi/Web and local network contracts, including owned DNS cancellation | 107 passed, 1 skipped |
| Independent final DNS/security review | 67 passed; no remaining P0/P1/P2 in its scope |
| Expanded-feedback history ceiling, runtime/provider/protocol recovery | 143 passed; input rejection retains zero model attempts |
| Forbidden-tool priority and adjacent Pi gate contracts | 48 passed |
| Remaining Web lifecycle partition plus ordinary runtime and real-route contracts | 220 passed; 7 deprecation warnings (479.94 s) |
| Docking app assembly/lifecycle after profile fixture migration | 72 passed (60.70 s) |
| Docking receiver and focused lab lifecycle migration | 33 passed; 7 deprecation warnings |
| Root user-config/Web refresh contracts on actual Pi | 10 passed; 7 deprecation warnings |
| Root design-model isolation and shared instance contracts | 6 passed |
| Root model request ownership/cancellation/probe lifecycle after registry fixture migration | 37 passed; 7 deprecation warnings |
| Root authenticated session entrypoint contracts on actual Pi | 13 passed; related batch 202 passed, 2 platform skips |
| Independent Pi follow-up review regression | 869 passed; no remaining P0/P1/P2 in its four-fix scope |
| Terminal callback source revalidation, both returned/raised paths | 10 passed (39.97 s) |
| Root health/profile/quality workflow contracts | 81 passed, 2 skipped; 7 deprecation warnings |
| Official SDK `npm test --prefix runtime/pi` | 6 passed |
| `tests/*_test.js` contracts | 44 files passed |
| `node tests/activity_family_acceptance_dom.js` | 17 checks passed; engineering-only |
| Node syntax checks: host, session, homepage main | Passed |
| `python -m compileall -q src scripts` | Passed again after local adapter and history-classification integration |
| Strict local health | 21/24 passed; see limitations below |
| Large Agent partition before full-Web implementation | 3117 passed, 1 registration-fixture failure; focused fixture migration subsequently passed |
| Remaining Agent partition before full-Web implementation | 6579 passed, 15 failed (1008.90 s): 7 actual Web/lab composition failures and 8 obsolete legacy-summary fixture calls; not a passing release gate |
| Remaining root partition before fixture repairs | 4676 passed, 146 skipped, 18 failed; 164 subtests passed; model/import/download fixture failures retained in intermediate evidence |
| Full-Web prebound generation envelope + Session/binding regression | RED reproduced double canonicalization; repair: 115 passed (12.02 s) |
| Static-page/import-only Pi registration fixtures | RED 8 failures; lazy registered core tools with no-dispatch guards: 18 passed (35.81 s) |
| Structure download offline DNS fixture isolation | RED 5 failures; complete target-search/fallback files: 293 passed, 2 skipped (35.43 s); production DNS protections unchanged |
| Counted RAG input, typed/source/receipt regressions | RED counted-input/schema failures; 793 passed, 3 warnings (38.10 s); current receipt binds exact requested k |
| Full-Web resolver plus real Pi generation/feedback/resume | 21 passed, 3 warnings (23.75 s); scripted generation/model, real RDKit; resume does not regenerate |
| Full-Web changed-authority clarification guard plus resolver | RED 19 failures / 6 passed; repair: 42 passed, 3 warnings (8.93 s); unknown/changed instructions rejected before reuse or dispatch |
| Target-design ranking and eight-step lead optimization binding | 2 passed (9.00 s); actual RDKit/ranker/verifier, explicit generation/target fixtures, optional model-unavailable results preserved |

Focused commands (repository root; installed Python scientific environment):

```powershell
python -m pytest tests/agent/test_pi_runtime.py tests/agent/test_pi_provider.py tests/agent/test_pi_checkpoint.py tests/agent/test_pi_bridge.py -q -p no:cacheprovider
python -m pytest tests/agent/test_decision_contract.py tests/agent/test_web_docking_consent.py tests/agent/test_web_docking_approval_http.py tests/agent/test_web_docking_approval_deadlines.py tests/agent/test_docking_consent_retention.py -q -p no:cacheprovider
python -m pytest tests/agent/test_pi_bridge.py tests/agent/test_pi_retirement.py -q -p no:cacheprovider
python -m pytest tests/agent/test_decision_binding_publication.py::test_actual_loop_rechecks_source_after_each_terminal_callback_even_if_it_raises -q -p no:cacheprovider
npm test --prefix runtime/pi
node --check runtime/pi/host.mjs
node --check runtime/pi/session.mjs
node --check src/web/static/js/home/main.js
node tests/activity_family_acceptance_dom.js
python -m compileall -q src scripts
python scripts/health_check.py --strict --env-file .env.pi-acceptance-unused
```

Pytest runs used unique temporary roots, temporary configuration/state databases, disabled real-family acceptance and no cloud credentials. Initial sandbox temp-directory/bytecode-write failures were rerun with required Windows permissions; those failures are not product passes or scientific results.

Intermediate broad runs exposed obsolete legacy-Web test setup, old checkpoint revision literals and old private-loop monkeypatch seams. Those tests were migrated to the actual Pi Web/runtime path while preserving their business assertions. Interrupted/failed intermediate runs are not reported as all-suite success. The arbitrary cold-start allowance in generic fixtures was separated from the existing short cancellation/deadline assertions; worker settlement and no-late-publication expectations were retained.

## Independent review and failure-first repairs

Four Pi integration findings were reproduced before correction:

1. Raw Pi bookkeeping messages prematurely consumed the domain history count limit. Raw history has a separately bounded allowance; the existing projected-domain limits remain unchanged.
2. A bridge failure/cancellation after a terminal clarification callback could retain waiting state without an accepted Pi result. That state is invalidated before publication.
3. Cancellation during worker settlement after an accepted clarification had the same stale waiting-state risk. Settlement is still awaited; the waiting state is invalidated, not bypassed.
4. The bridge envelope consumed the decision DTO's existing depth allowance. The transport envelope now has a bounded depth of 32; decision DTO parsing retains depth 16, strict JSON, duplicate-key/nonfinite/surrogate rejection. This is a transport-contract check, not authority to execute arbitrary nested scientific arguments.

A later full-history regression correctly rejected the thirteenth continuation with 20 retained conversation pairs, but classified the projection limit as a generic runtime failure. The controller now records the original failed-input contract (`INVALID_INPUT`, zero request attempts, no continuation) without truncation or a network call. Native/JSON failure evidence preceded the correction; both boundary cases and adjacent runtime/provider tests passed (123 tests).

Follow-up failure-first checks closed three more integration gaps: expanded schema-feedback projection now preserves the same failed-input journal; a task-forbidden tool retains the original task-restriction diagnostic before catalog rejection; and local DNS resolution is owned asynchronous work instead of blocking the event loop. Cancellation/timeout prevents HTTP dispatch, while cleanup still waits for the system resolver to finish before closing its pool. Repeated cancellation does not release ownership early; this is not a promise of a hard wall-clock DNS shutdown limit. Independent review found no remaining P0/P1/P2 in these fixes.

An activity-family synthetic-worker partition recorded 418 passed, 2 skipped and one PDE child timeout at the unchanged 120-second budget. The isolated failed case subsequently passed in 102.78 seconds with the same budget. Neither result is live scientific acceptance; the initial timeout is retained as a loaded-environment limitation rather than hidden by raising the scientific timeout.

A root regression partition recorded 344 passed, 8 skipped and one Windows `os.replace` access-denied failure in the unchanged docking-history multiprocess test. The complete file then passed all 5 tests, and the failing case passed three further isolated runs; each verified six successful children and all 120 records. The interfering handle/ACL condition was not identified. This is suspected unrelated Windows I/O intermittency, not an established known flake or an all-suite pass.

## Live services and deployment limitations

Cloud credentials/configuration were present (values were never printed or saved). The bounded acceptance attempted the configured approved endpoint, but all four cases stopped with `model_unavailable` / `decision_endpoint_rejected` before scientific dispatch. A sanitized DNS diagnostic showed resolution succeeded but not all answers were globally routable. HTTPS/DNS/pinning protections were not bypassed. **Cloud model/scientific acceptance remains unverified**, not passed by mocked responses.

The bounded real local JSON acceptance used installed `gmm-llama:latest` through canonical `OllamaModel` and actual Pi. **All four cases failed**: chat hit a task deadline, the two-molecule case hit allocation timeout, clarification/resume encountered invalid decision schemas, and the invalid-SMILES case ended with `unusable_input_reference` rather than its required rejection path. One schema correction produced a valid clarification, but that does not certify the resumed workflow. No scientific tool was dispatched and no RDKit result was manufactured. The local report remained in ignored `outputs/agent_evaluation/`, not Git. This model's successful end-to-end compatibility is **not verified**; finding/installing a model and adapter contract tests do not substitute for it.

Strict health passed the actual Pi Node/SDK check and the available local scientific/tool checks, but failed on missing isolated-worktree reverse-target data, RAG FAISS index and scratch directory. Production assets were not copied merely to make this report green. Full deployment health is not certified.

No full browser/production deployment, real docking, model training, Temporal/multi-worker recovery or new scientific prediction result is claimed by this runtime migration. Complete CI must be checked against the eventual PR head; no historic CI result applies automatically.
