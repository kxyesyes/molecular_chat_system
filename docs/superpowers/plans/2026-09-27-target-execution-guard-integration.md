# Typed target execution guard integration

## Scope and source pin

Continue package7, not a new Agent design. Start from mainfb62f5322e1e299e6e871970476e46379817fc23.
PR89 source hooks are independently pending CI; align its actual landing before
publication if it lands. No production activation, live provider or host assets.

Independent Hubble source-only dependency review approves the next integration
boundary: historical b907e8c owned-call extraction and 5bf3344 typed target guard
belong in one prerequisite. The target tests import the owned-call helper; the
full prepared-evidence journal also needs unpublished binding contracts and must
not be pulled in accidentally. Both behaviors already have historical TDD and
reviews. New integration still needs current-source regression and fresh reviews.

## Exact extraction

Only these three production/test files, plus this plan:

1. src/agent/harness/decision_execution.py: b907e8c helper extraction hunk and
   5bf3344 target guard hunk. Preserve settle_action's named advance closure and
   journal-only retry. Helper never retries callbacks; keep existing actual worker
   reservation, repeated cancellation, task creation cleanup and drain semantics.
2. tests/agent/test_decision_target_status.py: unchanged complete 5bf3344 blob.
3. tests/agent/test_decision_owned_call.py: dedicated extraction of b907e8c
   test_decision_binding_session.py owned_helper and four generic owned-call tests
   (historical lines419-422 and510-618), necessary imports and existing signalled/
   pending helpers only. Preserve all test bodies/assertions and parameter cases.

Do not import Session preparation/proof parser, replace ownership with a stub,
copy accumulated handoff docs or alter existing tests. Later journal integration
must account for these carried tests without collecting duplicate copies.

Target guard is restricted to exact TargetToolAdapter and canonical target name.
Bound the entire untouched producer envelope before copying. Translate only
resolved/not_found success status in a detached generic-validation view, then
retain typed validation of the original envelope and canonical result. No global
status exception, fake target match or relaxation of errors/native limits.

## Verification

- Check current executor blob equals donor b907e8c parent before extraction;
  compare resulting executor with 5bf3344 blob and exact target test blob.
- Independently inspect extracted ownership test bodies and current imports.
- No tests before explicit release of the sole local scientific test slot.
- Copy approved scratch/ordinary_chat_offline_runner.py using apply_patch; only
  literal REPO may change. Compare normalized full source. No raw pytest/import
  probes, environment/config/model discovery, or network execution.
- Current integration ordered union, with approved Python -I -S -B launcher:

```text
tests/agent/test_decision_target_status.py
tests/agent/test_decision_owned_call.py
tests/agent/test_target_tool_contract.py
tests/agent/test_worker_ownership.py
tests/agent/test_decision_loop.py
tests/agent/test_decision_adapter_retry.py
tests/agent/test_decision_inputs.py
tests/agent/test_workflow_run_session.py
```

- Fresh SOURCE/SPEC then QUALITY; independent exact union after parent terminal.
  Before/after hashes, compile in memory, diff and narrow secret-pattern checks.
- Exact-file commit and draft PR; fresh exact-head required CI checks, all review
  pages and unchanged base/head; SHA-guarded squash only after gates pass, then
  fetch and compare reviewed/landed trees. No direct main edits.

This is offline integration only. Contracts/prepared evidence/resolver, normal
Web continuation and remaining B1/B2/C, followed by package8 live acceptance, are
still required. A passing prerequisite is not package7 or8 completion.

## Current evidence

Mechanical extraction verified executor bd06d6cebe8f0e70b0752ed8bd80dc783e675682
equals5bf3344, target test83df224daddf03bf81c5e2d7858a831c038e23a4 is exact,
and ownership test25683ce00eb0594988e30987dca2f2059e5ab971 retains donor helper
419-422 and test ranges510-546,549-565,568-595,598-618 including decorators.
Sagan independent SOURCE/SPEC approves all three files/current dependencies.
Runner SHA256507bdc0ac25abad62d4fbe32025ac7831e21e35f60fbacb6575e46bb550f3bd6
differs from approved source only in literal REPO.

Parent exact eight-module command above, MedChat Python -I -S -B approved runner,
handle47363/summaryb5ce38:437 passed26.25s, ORDINARY_PYTEST_EXIT=0. The result
did not expose a separate process exit field; subsequent poll says unknown handle,
confirming no live process. No restart. Fresh QUALITY and independent repeat remain.
That slot transfer was historical; the Task5 correction has since terminated.

Fresh Bernoulli QUALITY approves the exact three-file source freeze and donor
test extraction. Independent exact eight-module run61002 completed437 passed
57.02s, runner and process exit0, with no warnings/errors reported. Runner and
all three Git blob hashes above were unchanged before/after; diff check passed.
The reviewer released the sole local test slot. This completes local independent
review/repeat, not remote CI or merge. Before publishing, align the separately
reviewed CI capacity partition once landed and require all nine resulting checks;
the older eight-check gate is not sufficient after that change.

## Landed source-hook alignment

PR89 landed as7a3309e3bbe90d5f52f6953e959b8a357205b0ec. Localac34413 merges
it into previously reviewedf1fc421. Confucius independent SOURCE approves the
composition: first-parent delta is exactly PR89's five files, and all three
theme blobs above remain identical. No new source-hook caller or production
activation is introduced. Old remote CI36261062523 passed9/9 onf1fc421 only;
it is not evidence for this aligned head.

Current ordered union is the eight modules above followed by:

```text
tests/agent/test_current_source_tool_hooks.py
tests/agent/test_rag_receipt_consumption.py
tests/agent/test_reverse_receipt_consumption.py
tests/test_reverse_target_invocation_receipts.py
tests/agent/test_rag_tool_contract.py
tests/test_rag_retrieval_outcome.py
tests/agent/test_rag_current_eligibility.py
tests/test_rag_owned_generation.py
```

Parent used the same approved MedChat Python -I -S -B launcher with these exact
16 ordered paths. Run54858 (summary814338, terminalf9dfb8) completed2031 passed,
3 SWIG deprecation warnings203.63s; runner/process exit0. Launcher hash above is
unchanged, working tree clean before this evidence addition, diff check passed.
Fresh independent QUALITY/repeat is now released to McClintock; no result is
claimed yet. Current-head CI, paginated review audit and merge gates remain.

Fresh McClintock QUALITY approves the exact aligned composition. Independent
ordered16 run97501/62a661 completed2031 passed,3 SWIG warnings163.97s with runner
and process exit0. All three pinned Git blobs and launcher hash unchanged;
diff check passed. Only this parent-authored plan evidence changed. The reviewer
released the sole local scientific slot. Both local runs are offline integration
evidence, not live-model acceptance. Fresh remote CI remains mandatory.
