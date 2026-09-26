# Reverse receipt consumer clean-main integration

## Current verified checkpoint

Corrected SOURCE/SPEC and independent QUALITY approve this slice. Parent exact
15-module regression: 1802 passed,7 warnings,2 subtests passed,74.96s,exit0
(76189/94fdce). Independent Pauli exact repeat:1802 passed,7 warnings,2 subtests
passed,75.07s,exit0 (65275/4a8497). Warnings are three SWIG and four FastAPI
deprecations. Both corrected file hashes and the approved launcher hash below
remained unchanged. The local test slot is released; no active process remains.
These are offline temporary-source/real-tool/SQLite checks, not live P8 acceptance.

Command used by both runs (MedChat Python with -I -S -B):

```text
scratch/ordinary_chat_offline_runner.py tests/agent/test_reverse_receipt_consumption.py tests/test_reverse_target_invocation_receipts.py tests/test_reverse_target_popcounts.py tests/test_reverse_target_health.py tests/test_reverse_target_pharmacophore.py tests/agent/test_reverse_target_complete_input.py tests/agent/test_target_tool_contract.py tests/agent/test_domain_result_validators.py tests/agent/test_registration_consistency.py tests/agent/test_rag_receipt_consumption.py tests/test_rag_owned_generation.py tests/test_rag_retrieval_outcome.py tests/test_rag_index_manifest.py tests/test_rag_service_boundary.py tests/agent/test_rag_tool_contract.py
```

PR87 has now landed as b33e2c044e4866a9f6228b28f4c734c22565eda9. Its current-source
changes must be aligned and reviewed before publication/new CI. Earlier staging
statements below are historical, not current unperformed-test claims.

## Scope and prior evidence

Extract the already reviewed consumer slice66039eb onto actual main72ce2bf
(PR86 owned source landed). All nine historical design/plan/code/test blobs are
identical to66039eb; parent verified each with git hash-object/rev-parse. Do not
copy the accumulated branch or its ledger. The later source-revalidation hooks,
typed target not-found exception, B decision loop and Web assembly remain separate.

The original two real-tool behavioral REDs and worker942/independent942 passing
regression remain recorded in the imported plan. They are historical evidence,
not current-main verification. No new scientific behavior or scoring change is
proposed by this extraction. Keep PR86's strict TSV/preflight and health-test
module-identity corrections unchanged; all current RAG source/receipt changes
must also remain intact.

## Current gates

1. Independent SOURCE/SPEC checks nine exact historical blobs, actual current
   producer/Session/adapter compatibility and fixture boundaries, plus launcher.
2. Parent affected regression using the historical ten-module union, plus current
   source/health tests if not already included. No local tests before explicit
   transfer from normal-Web Task4, which presently owns the sole test slot.
3. Fresh QUALITY review and independent exact union after explicit handover.
4. In-memory compile/diff checks, scoped commit, fresh-main alignment, draft PR,
   complete exact-head CI and review gates before SHA-guarded squash merge.

Offline launcher is the existing reviewed B1 launcher with only its literal
REPO changed to this dedicated worktree (newline normalization only). Parent
verified the complete normalized source comparison. SHA256:
72DA52ACA9A93102D57D460882E5189C543FFE651CD5BB76D58F4E16CC3A406C.
Invoke with MedChat Python -I -S -B and explicit test modules. It clears inherited
configuration before imports and uses temporary assets/socket denial. Do not use
raw pytest, discover host secrets/models/assets, call live providers, or alter
the launcher/thresholds/assertions/CI deadlines. No deployment or activation.

Current state: extraction only; independent integration review and new regression
have NOT run. Source/QUALITY approval and CI cannot be inferred from old counts.

## SOURCE integration finding: evidence-only security normalization

Helmholtz traced a P2 in the extracted code: inherited adapter normalization
redacts data but not evidence. Session accepts/seals evidence which SQLite later
redacts; a genuine credential-shaped source filename or generic evidence extension
can therefore alter the persisted receipt/digest after acceptance. Existing tests
cover scientific-row redaction, not this evidence-only case. This is source-based
until a new actual adapter/Session behavioral RED establishes it.

Parent verified current-main RAG already compares the complete native-coded
data/evidence against redact_sensitive(..., spec.sensitive_fields). Reuse this
existing security policy inside reverse optional-proof validation at every nested
snapshot. No new redactor, hash repair, persistence exception or receipt bypass.
Generic receipt-free adapters remain unchanged. Correction scope is only
target_contract.py and test_reverse_receipt_consumption.py, within original scope.

TDD: generate real temporary writer sources, prove hits and verified-empty positive
baselines, inject evidence-only synthetic sensitive filenames/extensions and
nested diagnostic observations. Test actual adapter and Session persistence:
changed proof cannot be accepted/sealed as successful science; safe originals
retain identical evidence/receipt through SQLite. Preserve caller error/status,
real one-call count and worker drain. First observe RED, then add full covered
redaction comparison before publication, then same-node GREEN. SOURCE re-review
and fresh QUALITY plus exact15-module union follow. The15 are historical10 plus
test_rag_owned_generation.py, test_rag_retrieval_outcome.py,
test_rag_index_manifest.py, test_rag_service_boundary.py and
agent/test_rag_tool_contract.py. No tests before explicit slot grant; no merge.

After explicit slot transfer, Pascal reproduced24 failures in7.87s (terminal
573f7c, exit1):8 direct observations incorrectly succeeded;16 nested outcomes
retained altered proof diagnostics. Safe producer baselines passed first. The
minimal full covered-data/evidence redaction comparison then passed the same24
plus three existing companion nodes:32 passed7.15s (cbcbd7, exit0). Tests were
unchanged between RED/GREEN; no active handles and slot explicitly released.
Target contract SHA F597826CA34AA43B20BBCBBCDEAD5C2F8D0DC563F0D2F1F76D5CD1B470423991;
consumer test SHA18961BBA384091A609C6FA19FD8EF42BC67CCAAEE5D680AA1B630450B17455F9.
Launcher unchanged. Corrected SOURCE, fresh QUALITY and15-module union remain
required. Rawls receives the slot next for normal-Web Task4; no parallel tests.

Helmholtz corrected SOURCE/SPEC approves both frozen corrected files and the
unchanged seven imported blobs; no remaining P1/P2. Parent independently checked
the hashes, diff and in-memory compilation of the two corrected files. Fresh
Pauli QUALITY is source-only until explicitly granted the slot. Full15-module
worker/parent regression and independent repeat are still not run; do not promote
32 focused passes to whole integration evidence or publish this worktree yet.

Pauli fresh QUALITY source review also approves the corrected seven-file scope,
confirming both new hashes and launcher plus seven unchanged imported blobs.
No independent test execution yet; parent-first15-module regression remains
pending until Rawls releases the local slot. No new receipt-consumer PR exists.
