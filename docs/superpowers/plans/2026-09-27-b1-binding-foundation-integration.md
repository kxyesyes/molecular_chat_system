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
