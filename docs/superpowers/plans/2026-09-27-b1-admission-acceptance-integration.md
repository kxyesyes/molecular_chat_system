# B1 admitted inputs and scientific acceptance integration plan

> **For agentic workers:** Use subagent-driven-development with independent SOURCE then fresh QUALITY review. Follow the checked boundaries below; this is an existing-code integration, not a replacement Agent design.

**Goal:** Integrate server-admitted clarification inputs and deterministic evidence-based scientific acceptance on the reviewed B1 foundation.

**Architecture:** Reuse the existing immutable journal, resolver, Session and evidence contracts. Import only the approved historical implementation and its complete tests; preserve current producer contracts and fixes. Model-selected loop wiring, authenticated revision8 restoration and final publication remain separate integrations.

**Tech stack:** Python 3.10, Pydantic 2, existing Agent Session/ledger, pytest and the approved isolated launcher; real RDKit and temporary retrieval sources in tests, explicitly synthetic protocol/prediction fixtures.

## Current boundary and prerequisite

Branch `codex/b1-admission-acceptance-integration` starts at reviewed PR92 head
`7b43a1e379a94da8d8f4dbdb7edaa1e688edd3fd`. PR92 is still pending remote CI/merge
at drafting time. Only this plan may be written now. No implementation or tests
until PR92 actually lands, its exact tree is verified, and this worktree aligns
to actual main with an inspected diff. Do not substitute the local candidate for
the landed prerequisite. Original mixed checkout remains untouched.

The approved historical specification/implementation record is
`docs/superpowers/plans/2026-09-26-b1-decision-execution.md` at donor history;
Task4A input and acceptance are the selected scope. Do not copy its accumulated
handoff or unrelated later implementations into this PR.

## Exact source manifest

Input donor `e6f31150b5057a7f6dace44196e5868b14a2837a`, parent
`9360c7b459d0e5d874722e58d7ade1eff7f8002a`:

| Path | Action | Expected Git blob |
|---|---|---|
| `src/agent/harness/decision_binding_inputs.py` | add | `5fd21c6f26356a4c36a9be28c07f84ff868c7826` |
| `src/agent/harness/decision_bindings.py` | update | `be27b66ceb72e8c9d13512800209e4fb3cf6306a` |
| `src/agent/harness/decision_bounds.py` | update | `079b2315ea5baf7eee43b34ed18a32db5cf56786` |
| `tests/agent/test_decision_binding_inputs.py` | add | `8ffd78e8f06b9bf391914bc6ec252f4c372e6526` |

Acceptance donor `0d6c6e9d8627916714f087bc02269e9f08d8d5b6`, parent
`55865c6b61307792ad3e0cad87433d98bf521f26`:

| Path | Action | Expected Git blob |
|---|---|---|
| `src/agent/harness/decision_binding_acceptance.py` | add | `2b1dbc06143f548300db6557282b2ea14735e198` |
| `tests/agent/test_decision_binding_acceptance.py` | add | `f008fd3d9295dfc37b816b7fa06ce777ce1a0fe2` |

These six files plus this plan are the complete tracked write allowlist. The
ignored launcher is permitted only with verified literal REPO substitution.
Parent source inspection found both updated production baselines equal the
input donor parent. Current `rag_contract.py` and `target_contract.py` differ
from the acceptance donor parent: preserve the newer integrated files, never
overwrite them to satisfy historical tests. No new framework or global parser.

## Task 1 — re-pin and establish tests first

- [ ] Read current AGENTS/standards, this plan and the actual donor modules/tests.
- [ ] After prerequisite landing, compare reviewed/landed trees, align actual
  main, inspect scope, and independently review source/dependency compatibility.
- [ ] Check the two existing production baselines against donor parent before
  any update; new paths must not unexpectedly exist. If they diverge, inspect the
  real overlap before deciding a bounded integration, never overwrite blindly.
- [ ] Obtain complete authoritative source with these read-only commands:

```powershell
git show e6f31150b5057a7f6dace44196e5868b14a2837a:tests/agent/test_decision_binding_inputs.py
git show 0d6c6e9d8627916714f087bc02269e9f08d8d5b6:tests/agent/test_decision_binding_acceptance.py
```

- [ ] Add those exact full test files using apply_patch. Preserve imports,
  fixtures, parameter cases and every assertion; no test rewriting for green.
- [ ] Copy the approved foundation scratch launcher using apply_patch; change
  only literal REPO. Its source hash is
  `8822EA04BBBAC0B60058F70431B2C0098AD925630A25E4CFC9941B80271A01FF`.
  Compare full normalized bytes, not selected snippets. Never use raw pytest,
  application-import probes or host asset/config/secret discovery.
- [ ] Acquire the sole local scientific test slot and run the two new modules
  through MedChat Python `-I -S -B scratch/ordinary_chat_offline_runner.py`.
  Expected missing-feature failures include the explicit missing input-journal
  and acceptance-API assertions. Record actual failures; collection/fixture or
  isolation failures do not demonstrate a missing behavioral feature.

## Task 2 — integrate the bounded historical implementation

- [ ] Read complete implementation bytes from the immutable donor paths:

```powershell
git show e6f31150b5057a7f6dace44196e5868b14a2837a:src/agent/harness/decision_binding_inputs.py
git show e6f31150b5057a7f6dace44196e5868b14a2837a:src/agent/harness/decision_bindings.py
git show e6f31150b5057a7f6dace44196e5868b14a2837a:src/agent/harness/decision_bounds.py
git show 0d6c6e9d8627916714f087bc02269e9f08d8d5b6:src/agent/harness/decision_binding_acceptance.py
```

- [ ] Apply only those four production paths. Check all six resulting Git blobs
  against the manifest. A full-blob donor reference is the exact implementation,
  not permission to copy other historical files or omit current fixes.
- [ ] Retain full-context freezing, exact native types, sequential admitted
  inputs, prefix commitments, atomic journal installation and shared512KiB
  snapshot limit. A checksum/journal is not authentication or scientific proof.
- [ ] Preserve default context_value serialized-query behavior; the explicitly
  Boolean query_content_bytes option bounds actual UTF-8 query content for this
  admitted-input path without weakening whole-context validation.
- [ ] Preserve complete-input scientific checks and mandatory cited ancestry
  for all seven tools. No disconnected RAG citation may discharge a missing
  property result; demo/fallback, missing model, sparse ADMET or unsupported
  family results stay scientifically unsatisfied. Rendering preserves truthful
  partial/failure, warnings, evidence and artifacts without fabricating values.
- [ ] Keep acceptance load-free and ownership-neutral: callers still owe owned
  execution, deadlines and final source/publication barriers. Do not activate
  a Web entry, trust browser history or imply that revision8 auth is included.
- [ ] Re-run the two exact modules under the same launcher. Diagnose failures
  with bounded TDD; no weakening scientific assertions or historical gap controls.

## Task 3 — source-selected complete regression

- [ ] Run the following exact ordered38-module union after explicit slot grant.
  Use `C:/Users/xkx52/.conda/envs/MedChat/python.exe -I -S -B
  scratch/ordinary_chat_offline_runner.py` followed by these paths; no omitted,
  reordered or substituted targets and no competing local scientific process.

```text
tests/agent/test_decision_binding_inputs.py
tests/agent/test_decision_binding_acceptance.py
tests/agent/test_decision_binding_profiles.py
tests/agent/test_decision_binding_arguments.py
tests/agent/test_decision_binding_requirements.py
tests/agent/test_decision_binding_session.py
tests/agent/test_decision_dynamic_bindings.py
tests/agent/test_decision_owned_call.py
tests/agent/test_decision_target_status.py
tests/agent/test_ordinary_capabilities.py
tests/agent/test_evidence_ledger.py
tests/agent/test_decision_contract.py
tests/agent/test_decision_requirements.py
tests/agent/test_decision_inputs.py
tests/agent/test_binding_resolver.py
tests/agent/test_candidate_alignment.py
tests/agent/test_activity_tool_contract.py
tests/agent/test_analysis_contract.py
tests/agent/test_scientific_reference_store.py
tests/agent/test_workflow_run_session.py
tests/agent/test_dynamic_run_session.py
tests/agent/test_run_session_ownership.py
tests/agent/test_worker_ownership.py
tests/agent/test_delegated_session_lifecycle.py
tests/agent/test_delegated_session_parity.py
tests/agent/test_decision_adapter_retry.py
tests/agent/test_decision_loop.py
tests/agent/test_target_tool_contract.py
tests/agent/test_domain_result_validators.py
tests/agent/test_reverse_target_complete_input.py
tests/agent/test_current_source_tool_hooks.py
tests/agent/test_rag_receipt_consumption.py
tests/agent/test_reverse_receipt_consumption.py
tests/agent/test_rag_tool_contract.py
tests/agent/test_rag_current_eligibility.py
tests/test_rag_retrieval_outcome.py
tests/test_rag_owned_generation.py
tests/test_reverse_target_invocation_receipts.py
```

- [ ] Record exact command, source/launcher hashes before/after, actual terminal
  process handle, exit/duration/warnings/skips. Never restart on observation
  timeout or represent synthetic prediction fixtures as live science.
- [ ] Retain known rejection characterizations for unrelated expired selection:
  those are existing gaps for later loop integration, not successful independent
  RAG/target behavior. Do not claim they are solved by this extraction.

## Task 4 — independent review and publication

- [ ] Independent SOURCE/SPEC then fresh QUALITY with exact38 repeat, source
  compilation in memory, diff check and scoped filename-only credential scan.
- [ ] Commit only the six source/test paths and this plan. Publish one draft PR
  against actual main after all prerequisite checks; no entire-history merge.
- [ ] Require exact-head all9 CI checks and actual full Agent collection proof.
  Paginate REST reviews using a flattened response array and GraphQL threads
  using pageInfo; no unresolved/changes-requested review may be waived.
- [ ] Before authorized squash, recheck head/base/main/checks and reviewed tree;
  after merge fetch and compare landed tree. No direct main commits/deployment.

P7 remains incomplete until loop/continuation/publication and normal-Web/B2/C
integration; P8 still requires current repeated real-provider/scientific/UI
acceptance. This plan neither narrows that objective nor authorizes live assets.
