# Complex workflow status repair: verification record

## Scientific and runtime boundaries

The change addresses allocation waits, equivalent target wording, safe reverse-target
diagnostics, unexecuted dependencies, and mixed activity results. It does not retrain
models, edit weights/registry assets, change thresholds, reweight missing evidence,
or claim experimental validation.

Allocation defaults are 30 seconds with progress every 5 seconds. Actual tool dispatch
ends the allocation clock; scientific execution retains its existing limits. Expired
requests cannot dispatch later or publish another terminal response. Physical model
workers remain owned and capacity-bounded; timeout does not mean a thread was killed.

Family activity rows are retained only after domain validation. Raw numbers and candidate
identities survive partial batches; conflicting rows remain ineligible for activity
ranking. Alignment and checkpoint recovery use consistent evidence identities.
Dependency non-execution is certified by the runtime journal, not provider flags.
Existing public event names and ScientificReport@1's closed schema remain unchanged.

Native model-configuration refresh and reader-gate queueing are included in allocation
admission. Native dispatch reuses that scope rather than reserving another slot. A
checkpoint write must complete before the dispatch latch and `tool_started` event;
checkpoint reuse does not claim a new tool execution.

Completion and allocation expiry share the same locked state transition. A normal
`agent_result`/`complete` cannot be followed by an allocation-timeout replacement.
Before any tool runs, the latest provisional terminal event is held in a single slot
until the final result wins publication; an event emitted before a stalled persistence
write cannot suppress the deadline. This final race was observed in failing tests
before the repair; the allocation suites pass **40 tests** after that repair.

Reverse-target error details are intentionally narrowed to the allowlisted
`stage`/`reason`/`field` contract. The old `raw_result` diagnostic chain is no longer
serialized into errors, events or checkpoints; it remains private in-memory state for
the same proof checks. Consumers must not depend on that former raw diagnostic field.

For a non-cooperative provider, visible timeout precedes physical cleanup. Its final
durable timeout record is written when cleanup settles, while retained workers still
hold their model leases. Shutdown/reload may therefore wait for that provider; the
implementation does not claim forced thread termination. Existing semantic pre-intent
admission remains governed by its own established deadline.

## Production-file scope

| Area | Files |
| --- | --- |
| Allocation and ownership | `src/agent/runtime/allocation.py`, `src/agent/routing/hybrid.py`, `src/web/chat_handler.py`, `src/web/decision_runtime.py`, `src/system/model_lifecycle.py`, hooks in `src/agent/runtime/run_session.py` |
| Target wording | `src/agent/contracts/target_request.py` |
| Reverse diagnostics | `src/reverse_target/owned_source.py`, `src/reverse_target/predictor.py`, `src/agent/tools/reverse_target_tool.py`, `src/agent/tooling/target_contract.py`, `src/system/scientific_diagnostics.py`, `src/system/scientific_contracts.py`, `src/system/tool_adapter.py` |
| Partial/dependency/recovery | `src/agent/validators/partial_observations.py`, `src/agent/validators/result_validator.py`, `src/agent/validators/candidate_alignment.py`, `src/agent/contracts/result.py`, `src/agent/planning/bindings.py`, `src/agent/runtime/run_session.py`, `src/agent/orchestrators/workflow.py` |
| Ranking and display | `src/agent/tools/candidate_ranker.py`, `src/agent/presentation/evidence_report.py`, `src/web/agent_result_presentation.py`, `src/web/static/js/home/task_status.js` |

Tests exercise real adapter/runtime/binding/ranker/SQLite/transport paths with synthetic
inference or delayed-provider fixtures. They are contract tests, not real-model calibration.

## Regression and review

Failing regressions were observed before implementation and review fixes. Independent
review checked specification compliance before code quality. Additional guards cover
negated/switched/unknown targets, nested demo/fallback evidence, target-family mismatch,
safe dependency IDs, forged partial rows, checkpoint identity, and false non-execution.

Final allocation review passed five actual mounted `/ws` controlled-clock probes and
three buffering invariants, in addition to the 40 allocation tests. The external model
transport in those probes was synthetic; no live inference is claimed. A separate
read-only review confirmed that the updated compatibility tests retain their scientific
proof, raw-value, zero-dispatch and physical-cleanup assertions.

A broad run found that new ranker diagnostics broke the old report's exact-key schema.
The report now projects only established fields without modifying its source observation.
Its regression and existing report/snapshot/transport suites pass together: **169 passed**.

## Verification

Commands run with the MedChat Conda interpreter and required native-library PATH. Tests
isolate user configuration, environment-file loading, and SQLite. No real API credential
was saved or included in reports.

- Core partial/dependency/alignment/resume/session/contracts suites: **111 passed** after
  checkpoint-identity and runtime-authority fixes.
- Final combined core/target/ranking/report/transport regression: **1,394 passed**.
- Combined allocation/transport/lifecycle/reverse/shared-contract regression:
  **1,143 passed** before the final native queue-boundary review repair.
- Final allocation/decision/session/cancellation pinpoint rerun: **138 passed**.
- Final allocation deadline/handoff suites after completion-race repair: **40 passed**.
- Final combined allocation/native-and-legacy transport/lifecycle/session regression:
  **647 passed**.
- Reference cleanup/execution plus allocation suites: **96 passed**. These use a
  test-owned pool with the production capacity; all slots must be returned before
  shutdown, and existing thread-join, lease, source and replay checks remain intact.
- Native reference-projection/transport/handoff suites: **69 passed**. Fault injection
  now waits for the actual outer supervised turn; inner exception and cancellation
  assertions are explicit, and final-frame counts and durable-state checks are unchanged.
- Entrypoint/dependency compatibility and synthetic family acceptance rerun:
  **627 passed, 3 skipped**. Valid partial observations now expect `partial` and
  `tool_completed`, not execution failure; genuine invalid inputs still fail.
- Report/transport/snapshot suites: **169 passed**.
- `python -m compileall -q src scripts`: passed.
- `node tests/home_agent_task_panel_test.js`: passed.
- `node tests/reverse_target_broad_recall_test.js`: passed.
- `node tests/reverse_target_pagination_test.js`: passed.
- All existing `tests/**/*_test.js` Node regression scripts: **41 passed**, including
  the allocation-vs-computation timeout label checks.
- `node --check` over non-Ketcher/non-backup JavaScript: **54 files passed**.
- `python scripts/run_agent_acceptance.py --mode contract`: **34/34**, offline.
- Broad Agent run: **13,501 passed, 12 failed, 3 skipped** before the final test-contract
  and ownership-fixture updates. All 12 failures are covered by passing reruns below.
- Broad non-Agent run excluding `c4a`: **9,174 passed, 2 failed, 263 skipped,
  83 deselected; 173 subtests passed**. Both failures are covered by the passing
  synthetic family-chain rerun. These broad runs are not labelled all-green.

### Full-suite limitation

The initial `python -m pytest tests -q -p no:cacheprovider --tb=short` run stopped
making progress at `tests/task_runtime/test_docking_consent.py::test_c4a_pending_command_retains_real_lease_and_capacity[normal]`.
It began while implementation was still in progress, contained failure markers, and
was interrupted without a final summary; it is not a passing verification result.

The same single test was reproduced independently on unchanged `main` and on this
branch with `-o faulthandler_timeout=30`. Both diagnostic stacks retained execution at
`src/task_runtime/docking_execution.py`'s `_retain`/`finish` cleanup path. Only the
three verified test-owned Python processes were stopped, after checking they had no
live child processes. Neither docking implementation nor its tests were modified.
This establishes a baseline reproducibility limitation, not a successful C4a run or
a claim that the underlying cause has been fixed.

Broad reruns use a local, uncommitted reporting plugin that prints test
module starts and failures immediately; it does not alter fixtures or test outcomes:

```powershell
python -m pytest tests/agent -q -p no:cacheprovider -p pytest_live_report --tb=short -o faulthandler_timeout=300
python -m pytest tests --ignore=tests/agent -k 'not c4a' -q -p no:cacheprovider -p pytest_live_report --tb=short -o faulthandler_timeout=90
```

`pytest_live_report` is diagnostic-only; omit `-p pytest_live_report` to reproduce the
same test selections without the local progress printer. The excluded C4a group is
explicitly unverified by these reruns.

The broad runs also exposed older test expectations for the intentionally changed
partial/dependency contract. These expectations were updated without changing raw
values, provenance checks, error rejection, evidence identity, or scoring. The focused
compatibility rerun includes the actual synthetic RGNN conflict chain for PDE/BuChE
and a new negative check rejecting a false `tool_failed` event on observed partial
evidence. The later atomic terminal-publication repair is verified separately from
the already-running broad processes; no single post-repair all-green full run is claimed.

| Broad-run failures | Cause and retained verification | Passing rerun |
| --- | --- | --- |
| 2 partial-entrypoint + 1 blocked-dependency cases | Align the approved statuses; retain raw values, provenance, durable state and zero tool calls | 627-case compatibility suite |
| 2 synthetic conflict-chain cases | Expect settled `tool_completed`/partial rather than execution failure; retain scientific review warnings and add event-tamper rejection | 627-case compatibility suite |
| 2 reference cancellation cases | Test-owned bounded pool replaces the old per-event-loop thread assumption; assert capacity returned before shutdown and retain join/replay checks | 96-case reference/allocation suite |
| 7 projection scheduling/cancellation cases | Await the outer turn that owns delivery, rather than its new allocation child; preserve exact terminal counts and assert child fault propagation | 69-case projection/transport/handoff suite |

```powershell
python -m pytest tests/agent/test_allocation_deadline.py tests/agent/test_allocation_handoff.py -q -p no:cacheprovider --tb=short
python -m pytest tests/agent/test_entrypoint_outcome_parity.py tests/agent/test_molecular_agent_adapter_science.py tests/test_activity_family_acceptance_support.py tests/test_activity_family_acceptance_chain.py::test_synthetic_conflict_forward_preserves_partial_across_full_chain -q -p no:cacheprovider --tb=short
python -m pytest tests/agent/test_allocation_deadline.py tests/agent/test_allocation_handoff.py tests/agent/test_web_decision_runtime.py tests/agent/test_web_decision_runtime_lifecycle.py tests/agent/test_decision_chat_transport.py tests/agent/test_decision_binding_loop.py tests/agent/test_workflow_run_session.py tests/agent/test_chat_local_cleanup.py tests/agent/test_chat_presentation_boundary.py tests/agent/test_chat_runtime_failures.py -q -p no:cacheprovider --tb=short
python -m pytest tests/agent/test_scientific_reference_resilience.py tests/agent/test_scientific_reference_execution.py tests/agent/test_allocation_deadline.py tests/agent/test_allocation_handoff.py -q -p no:cacheprovider --tb=short
python -m pytest tests/agent/test_web_decision_runtime_references.py tests/agent/test_decision_chat_transport.py tests/agent/test_allocation_handoff.py -q -p no:cacheprovider --tb=short
```

Replay acceptance requires a real recorded report. No contract fixture was relabelled as
real replay evidence. Real/all acceptance and model recalibration were not performed.
No deployment/data-asset change is included; a deployment health check is not claimed.

## Delivery

Branch: `codex/fix-complex-workflow-status`. No tracked changes were made in the primary
`main` checkout; unrelated assets were not staged or removed. Only named task files are staged. Delivery is one
draft PR against `main`; no merge is authorized or performed.

Implementation commit: `6c90df58`. Draft PR:
[kxyesyes/molecular_chat_system#208](https://github.com/kxyesyes/molecular_chat_system/pull/208).
The remote repository owner and authenticated account were verified read-only before
the approved push. No branch protection bypass or merge was performed.
