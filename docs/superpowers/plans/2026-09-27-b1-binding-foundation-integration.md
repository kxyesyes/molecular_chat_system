# B1 binding foundation integration

This is the next dependency-cohesive package7 integration, not a redesign,
activation or completion claim. Original working tree and production settings
remain untouched. Branch `codex/b1-binding-foundation-integration` starts at
the locally reviewed target-execution prerequisite `b706d253888039c51fc99681f3626c6f1c5982eb`.
That prerequisite is not yet on main. No implementation is copied at this freeze.

## Dependency and source boundary

The earlier independent dependency review grouped closed capability/argument
contracts, immutable scientific requirements/proofs, prepared Session journal,
ledger integrity and current-source resolver as one coherent foundation.
Do not import the entire accumulated B1 branch. Before implementation/release,
align the actual landed CI partition, current-source hooks PR89 and target guard;
inspect their real deltas and preserve their RAG/reverse source corrections.

Selected donor history, in order:

- `b9f97597fa9b3501fc9dbf7280aae0f47c9fe2b8`: capability profiles.
- `fd5031801efc6c53d0b38828c7d463a30f1bf7a5`: closed tool-reference arguments.
- `62e32aadeaae62b35f256d3c399b68f9cd3ed870`: immutable requirements/proofs.
- `b907e8c8570570e6586476a3fd5c827c1a3d9dc9`: prepared Session/ledger journal.
- `33f2847048f6a7ba55f664dd06a6252dd7ff203f`: role/input and current-source resolver.

Use only these seven production paths' selected deltas, after comparing their
donor parents with the current integrated base:

1. `src/web/ordinary_capabilities.py`
2. `src/agent/contracts/decision_bindings.py`
3. `src/agent/contracts/binding_requirements.py`
4. `src/agent/harness/decision_bindings.py`
5. `src/agent/harness/decision_inputs.py`
6. `src/agent/evidence/ledger.py`
7. `src/agent/runtime/run_session.py`

Do not overwrite current tool implementations, target/RAG contracts, execution
guard, Web entry, loop, continuation, tests or unrelated earlier fixes using the
historical tree. The seven-tool loop/acceptance, admitted-input journal, revision8,
publication and normal-Web changes belong to later integrations.

Carry five donor test modules: `test_decision_binding_profiles.py`,
`test_decision_binding_arguments.py`, `test_decision_binding_requirements.py`,
`test_decision_binding_session.py`, `test_decision_dynamic_bindings.py` under
`tests/agent`. In the Session module, remove only the exact helper and four
owned-call tests already moved unchanged to `test_decision_owned_call.py` by the
prerequisite. Compare AST signatures/bodies/decorators and parameter cases to
prove no assertion disappeared and no duplicate collection was introduced.
Preserve imports/reexports used by downstream tests; unused-import cleanup is
not permission to change public compatibility.

## Verification and publication gates

1. Read current AGENTS, standards and donor source/specifications; obtain fresh
   independent source review of this extraction boundary before copying code.
2. Record original/current/donor blob identities. Use apply_patch and exact
   allowlists; no main edits or whole-history cherry-pick.
3. Use the approved isolated launcher only; copy with literal REPO substitution,
   verify normalized source equality, and acquire the sole local test slot.
4. Run the five new test modules plus owned-call/target-status controls, existing
   ordinary capability, evidence ledger, decision input, Session/worker and
   RAG/reverse current-source tests. Derive exact ordered module list from source
   before execution; no invented test filenames or count-only evidence.
5. Existing scientific/permission/source assertions cannot be weakened to make
   integration pass. Any new behavioral defect requires a reproduced failing
   case and bounded repair, followed by independent review.
6. Independent SOURCE then fresh QUALITY/repeat, in-memory compilation, diff
   check and filename-only credential scanning before exact-file commit.
7. Publish one draft PR against actual main after prerequisites land. All nine
   current CI checks and full collection proof, fully paginated review state,
   exact head/base checks and reviewed/merged tree equality remain mandatory.

This foundation alone cannot satisfy package7 or8. Normal-Web clarification,
dataflow/presentation/resources, generation/ranking, docking consent/cleanup and
the full repeated real-model/scientific/UI acceptance remain required. No live
provider, host key/config/model lookup or deployment is part of this extraction.

## Independent source plan audit

Goodall approves the seven-production/five-test boundary and confirms no hidden
extra module extraction. This is plan approval only; implementation stays HOLD
until the source-hook and target prerequisites actually land and align.
PR90 CI prerequisite has now landed as35b82705e76b504e329d43b4cee6c5a8b5d37101;
its reviewedfdd7680 and landed trees both equal8d3c4a92462752d2f0be9e792ec52ec99df9428e.
CI36260272170 passed all9 gates, actual collection9802 =9578 +224 with no overlap.

The four existing production starting blobs match the donor parents. Preserve
execution blobbd06d6ce already supplied by target prerequisite; do not copy it
again. PR89 supplies both validate_current_observation and the dynamic suite's
sources/forbid_work fixtures. Its candidate test blobdce02d84 matches the donor.
Fourteen imported helper definitions match by AST; current initialized_service
adds a compatible optional source_name and must remain current.

Selected donor parents are6bc6dfc,78675e4,e10df12,62e32aa,5bf3344 respectively.
Final expected production blob prefixes in the seven-file order above:
82bf4f6b6f1a,8265f19ca5cb,9d651e5e0d61,23ad07ccdfab,b92b0880e62f,
893f3cd40dbf,488994ce2784. Verify full identities from Git before copying.
The five duplicate definitions' ASTs including Boolean decorators match the
prerequisite; remove exactly those, not the intervening Session tests or imports.

Exact ordered verification targets (all test filenames gain .py):

```text
tests/agent/test_decision_binding_profiles
tests/agent/test_decision_binding_arguments
tests/agent/test_decision_binding_requirements
tests/agent/test_decision_binding_session
tests/agent/test_decision_dynamic_bindings
tests/agent/test_decision_owned_call
tests/agent/test_decision_target_status
tests/agent/test_ordinary_capabilities
tests/agent/test_evidence_ledger
tests/agent/test_decision_contract
tests/agent/test_decision_requirements
tests/agent/test_decision_inputs
tests/agent/test_binding_resolver
tests/agent/test_candidate_alignment
tests/agent/test_activity_tool_contract
tests/agent/test_analysis_contract
tests/agent/test_scientific_reference_store
tests/agent/test_workflow_run_session
tests/agent/test_dynamic_run_session
tests/agent/test_run_session_ownership
tests/agent/test_worker_ownership
tests/agent/test_delegated_session_lifecycle
tests/agent/test_delegated_session_parity
tests/agent/test_decision_adapter_retry
tests/agent/test_decision_loop
tests/agent/test_target_tool_contract
tests/agent/test_domain_result_validators
tests/agent/test_reverse_target_complete_input
tests/agent/test_current_source_tool_hooks
tests/agent/test_rag_receipt_consumption
tests/agent/test_reverse_receipt_consumption
tests/agent/test_rag_tool_contract
tests/agent/test_rag_current_eligibility
tests/test_rag_retrieval_outcome
tests/test_rag_owned_generation
tests/test_reverse_target_invocation_receipts
```

This is future execution scope, not a run or permission to edit existing
regressions. Current landed pins, full blobs and approved-launcher equality still
must be checked at release; no local candidate is substituted for main.
