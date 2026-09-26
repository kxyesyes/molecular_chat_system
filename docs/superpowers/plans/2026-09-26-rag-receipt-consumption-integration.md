# Focused RAG receipt-consumption integration plan

> **For agentic workers:** Use subagent-driven-development with independent SPEC then QUALITY gates. Parent owns this focused extraction and publication; no parallel test processes.

**Goal:** Preserve verified same-invocation retrieval evidence through the actual RAG tool, adapter and existing Session without replacing the Agent architecture.

**Architecture:** Reuse the reviewed R3 implementation from `2d9fbdc992ee98954ef2f186bfe448f46290338a`, its pure receipt validator, existing RAGSearchTool/RAGToolAdapter and Session ledger. Preserve PR83's newly reproduced legacy source-coherence and exact-dtype-copy repairs. No new scientific calculation, Web activation or model access.

**Tech stack:** Python, Pydantic2, actual temporary FAISS/CSV/SQLite, pytest, synthetic HTTPX transport fixtures.

## Base and scope

Preparation started at reviewed PR83 head `950880a95ae138dcc07452d53da2f2020b1edc70` while that PR's CI ran. Its subsequent test-only repair43ea6fd is now fully incorporated with exact upstream blobs. PR83 merged as29502ca560ae1844f98290f84d85f2ab6b6fc750; reviewed/landed tree equality was verified. After R3 gates, commit its complete scoped delta, then transplant the clean branch onto that actual squash and require identical complete source trees. No edits to the original mixed checkout or accumulated B1 production files.

Pre-extraction source-parent comparison is empty for the existing tool/adapter, three modified compatibility test files, Session, generic adapter and test conftests. `src/rag/service.py` intentionally differs because of PR83's two integration repairs.

Selected production files:

- `src/rag/receipt.py` (new pure native-JSON validator and existing digest codec).
- `src/agent/tools/rag_search_tool.py` (strict producer required, honest empty/partial/errors).
- `src/agent/tooling/rag_contract.py` (verify proof before/after normalization; no repair of altered proof).
- `src/rag/service.py` (only R3 digest extraction/import and manifest source-path spelling).

Selected tests:

- `tests/agent/test_rag_receipt_consumption.py` (new, actual tool/adapter/Session).
- `tests/agent/test_rag_tool_contract.py`.
- `tests/agent/test_registration_consistency.py`.
- `tests/test_rag_index_manifest.py`.

Documents are this plan and the exact historical R3 specification. Its initial design-release statements are historical; this is integration of previously reviewed code, not a claim that old tests ran on this branch.

## Task 1: Exact extraction with repair preservation

- [x] Copy the seven non-service source/test files from commit `2d9fbdc` through apply_patch, checking Git blobs. Copy no accumulated handoff or unrelated runtime modules. New review-driven corrections are separately recorded below.
- [x] Apply only the service delta: remove its local `_canonical_digest` and `json` import, import `canonical_digest as _canonical_digest` from `src.rag.receipt`, and change receipt `source_path` from `str(config.source)` to captured `manifest.source_path`.
- [x] Verify `_copy_frame` still explicitly constructs object Series with their original dtype; `_legacy_candidate` still hashes/parses same CSV bytes and rejects mismatched detached frame before load/build/transport. Keep all PR83 tests byte-identical to its final43ea6fd.
- [x] Repin only the existing isolated launcher's REPO path to this new worktree using apply_patch. SHA256220D8B2D07BE845898ED495ACE8474D5B6F9390A93FA3530373263275C3EEDFA; no host environment/configuration/model/asset reads by tests.

## Task 2: Fresh integration review and regression

- [x] Independent SOURCE/SPEC review against the approved R3 spec and this base. For any new defect, reproduce RED before minimal repair; preserve failures and do not weaken contracts, assertions or deadlines. Huygens approves the final ten Python hashes; no actionable findings. This is not an independently executed union.
- [x] After explicit sole-slot handover, execute the actual-tool focused positives and the ten-module union below. Existing source TDD is historical evidence, not a reason to fabricate a new RED for unchanged integration. Final union59479:1138 passed/7 warnings/61.92s, exit0, chunk4f5452; includes strict positives and expanded fault controls.
- [x] Independent fresh QUALITY reviewer checks the frozen scope and reruns the same union; report exact counts, skips/warnings, durations and terminal handles. Epicurus APPROVE:1138 passed/0 skipped/7 warnings/60.31s, exit0, handle18301 terminal chunk5e7757; no rerun.

```powershell
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -I -S -B scratch/ordinary_chat_offline_runner.py tests/agent/test_rag_receipt_consumption.py::test_actual_tool_refuses_list_only_service tests/agent/test_rag_receipt_consumption.py::test_actual_empty_tool_retains_receipt
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -I -S -B scratch/ordinary_chat_offline_runner.py tests/agent/test_rag_receipt_consumption.py tests/test_rag_owned_generation.py tests/test_rag_retrieval_outcome.py tests/test_rag_index_manifest.py tests/test_rag_service_boundary.py tests/agent/test_rag_tool_contract.py tests/agent/test_registration_consistency.py tests/agent/test_dynamic_run_session.py tests/agent/test_decision_inputs.py tests/agent/test_decision_spec_findings.py
```

Historical R3 source evidence: initial two behavioral RED nodes failed because list-only services were accepted and actual empty retrieval lost its receipt. Corrected source independent ten-module union was1068 passed/0 skipped/7 warnings/133.91s. PR83 added14 preserved tests; this is not a prediction or a passing result for the new union.

## Task 3: Focused publication

- [ ] In-memory compile the eight Python files, diff-check, original-worktree preservation check, exact staging and filename-only credential pattern check. Never stage scratch launchers or runtime assets.
- [ ] Require PR83 actual merged tree equals final43ea6fd, then rebase only clean new commits onto that squash and reconfirm identical complete source tree. Parent verified this prerequisite equality; the R3 clean transplant is still pending. If base changed beyond that equality, re-review/retest affected integration instead.
- [ ] Create one draft PR to main, attach it, require latest complete CI and no unresolved review findings, then delegated squash merge and verify reviewed/merged tree equality.

## Non-goals and remaining work

No current-source eligibility activation in this slice; no B dispatch/reuse/resume, normal Web generation/ranking or final live P8 acceptance. Pure receipt validation is not producer authentication, does not verify embedding weights and does not retroactively authenticate old index files. Missing capability or invalid proof must remain unavailable/failed/partial, not successful fallback.

The sole local test slot is currently held by the B1 continuation implementer. Preparation/review may proceed without tests; wait for explicit handover before starting pytest.

## Integration finding: evidence-only security transformation

Independent SOURCE/SPEC Hypatia found an evidence-only gap: an actual empty
index can produce a valid receipt whose source_path contains credential-like
synthetic text. Generic adapter normalization redacts data only; with data=[]
the proof digest remains unchanged, but SQLite later redacts evidence, changing
the receipt after the Session seal. Zero-accepted partials have the same risk.
Parent traced adapter and SQLite redaction and selected a minimal proof-bearing
RAG check: compare the existing security transformation of complete data/evidence
against their original canonical digest, honoring spec sensitive_fields. Reject
changes as invalid_output without repairing receipt/hash or weakening redaction.
Generic receipt-free contracts remain unchanged. Add actual service-to-adapter-
Session/SQLite RED/GREEN tests for empty and zero-accepted partial outcomes, with
clean-name positive controls. Source findings are not executed test results yet.

Parent holds the local test slot after B1's final union78513 (2041 passed532.36s)
and subsequent expected RED96990 reached terminal. B1 is source-only until slot
return. PR83's first Linux CI has16 fault-injection failures; its test-only fix
and new CI are required before this dependent branch can publish. Later rebase
must carry that exact owned-generation test fixture too, not restore950880a's
older presumed FAISS class interception.

Actual new four-node service/Session test:2 failed/2 passed/3 SWIG warnings/2.36s,
exit1, chunk5597d5. The sensitive empty result remained success=true; sensitive
zero-accepted partial retained data=[] and its mutable evidence. Minimal adapter
check now rejects a changed canonical data/evidence digest under the unchanged
security transform and passes spec sensitive_fields at every proof boundary.
Focused GREEN:4 passed/3 SWIG warnings/1.79s, exit0, chunk04c545. Clean controls
verify complete receipt equality apart from the expected fresh invocation_id;
ledger, live seals, events and actual SQLite output agree. Full union and
corrected independent review remain pending. Slot returned to B1 afterwards.

Corrected SOURCE/SPEC confirms the original evidence-only P2 is fixed at source
level. Its P3 requested a successful actual strict-tool baseline before injecting
the new partial FAISS fault; parent added that baseline with valid_empty/valid_hits
checks. That final added assertion is not yet rerun. The existing Session test
and index-manifest strict-fault test also presume IndexFlatIP.search; scope the
same proven PR83 concrete-class repair and variant reproduction before the full
R3 union. No passing integration is claimed until these gates complete.

Hypatia's final SOURCE/SPEC approves the bounded security correction including
the pre-fault positive baseline (adapter blob69245d3, new-test blobc62a7e4), with
no remaining P2/P3 finding in that correction. This is source-only approval;
concrete-class follow-up, final delta review, ten-module union and fresh QUALITY
remain open. Reviewer ran no tests and did not count the prior four-node GREEN
as covering the subsequently added baseline.

### Concrete-class test follow-up

Use one opt-in, lazy-import fixture in tests/conftest.py for the two R3 test
modules. Its default native branch has no loader mutation. Explicit parametrized
tests also load an equivalent actual base IndexFlat with IP metric, checking
dimension, vector count and exact reconstructed vectors. This follows the
already reviewed PR83 reproduction, without editing PR83's owned-generation
tests or production code. The actual Session receipt/tamper test and strict
manifest fault test retain their original assertions; also repeat the four
security-transformation controls under both loader variants. First run the
expanded tests with the old IndexFlatIP interception to reproduce the blind
spot, then patch only the actual owned index class and rerun. No pytest is
started while the B1 worker holds the sole local test slot.

After B1 union79040 reached terminal (2072 passed590.44s) and its next two-node
RED completed, parent received the slot explicitly. Expanded R3 RED with old
class interception:8 failed/22 passed/3 SWIG warnings/6.26s, exit1, chunk636a31.
Six actual Session nonempty searches were not intercepted and two strict
manifest faults still returned success. Native controls and empty/security
controls passed. Changing only the two interceptions to the concrete owned
index class produced30 passed/3 warnings/5.91s, exit0, chunk068337. The final
security-test positive baseline is therefore now exercised, not merely reviewed.
Slot returned to B1 immediately; full union and independent QUALITY still pending.

The exact upstream43ea6fd owned-generation test patch has also been carried
forward (Git blobb83b25f1f5b0799e9285331cb967292abded6518, identical to PR83).
This is inherited prerequisite work, not a new R3 source repair. Publication
must compare against the actual updated PR83 squash, not obsolete950880a.

Focused command (RED and GREEN used the same targets):

```powershell
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -I -S -B scratch/ordinary_chat_offline_runner.py tests/agent/test_rag_receipt_consumption.py::test_real_dynamic_session_receipt_ledger_seal_and_persistence tests/test_rag_index_manifest.py::test_shared_search_skips_invalid_labels_and_empty_hits tests/agent/test_rag_receipt_consumption.py::test_evidence_only_redaction_rejected_before_session_seal
```

Final implementer ten-module union (exact command above) completed1138 passed,
zero failures/skips,7 warnings in61.92s, exit0, handle59479 terminal chunk4f5452.
Warnings are three SWIG and four FastAPI on_event deprecations. Parent compiled
all ten reviewed Python files in memory and checked diffs without application
imports or bytecode writes. Epicurus's independent source-quality inspection
found no actionable issues and verified all hashes, but its runtime approval
remains pending: sole local slot explicitly transferred to it for one repeat.

Independent QUALITY is now complete: Epicurus APPROVE with no actionable
findings, exact ten-module repeat1138 passed/0 failed/0 skipped/7 warnings/60.31s,
exit0, handle18301 terminal chunk5e7757. All ten source/test SHA256 values and
launcher hash matched before/after and final rechecka65066; scoped diff check
passed. Reviewer made no edits or commits. Slot returned to parent then B1.
The prior source-only statements above are historical, not outstanding gates.
Parent's scoped filename-only credential pattern scan found no matches; this
is not a whole-history security audit. Clean commit/transplant and new exact-head
CI remain required before publication can be merged.

## First published CI: failed, not eligible to merge

Workflow36240117332 at f050c5a is terminal. Agent job108398908120 reported
10 failed/9520 passed/1 skipped/23858 warnings in571.57s, exit1. Six other
independent gates passed; dependent offline-quality failed. Do not rerun the
same known-failing head or claim that the local1138 union covered the full suite.

Four failures are the two non-legacy entries and two prompt budgets in
test_chat_input_budget::test_shared_retrieval_and_ui_provenance_survive_prompt_budget:
they count the legacy get_embedding_sync hook which the approved strict receipt
producer deliberately does not call. Inspect and reproduce at the actual HTTP
boundary; preserve the original UI/provenance/prompt-budget assertions.
Six failures are native nonempty variants of the actual Session receipt/seal test:
HTTP count is1 but the search interceptor saw0. Concrete class interception passed
local and base-IP controls; the Linux native difference is not yet attributed.
Investigate before changing tests or scientific implementation. The source runtime
is pinned FAISS1.7.4 in CI; current local version differs. No waived counts,
timeout changes or weakened source proof are authorized by this diagnostic entry.

Local focused reproduction (same two families,24 nodes) first reproduced4 failed,
20 passed5.07s, chunk1b0b5e. HTTP-boundary instrumentation confirmed one actual
request with correct query/model/endpoint; this revealed the next four stale
assertions: receipt-bearing status metadata exceeds the existing2048-char reserve,
so ChatHandler correctly emits interpretation_budget_exceeded instead of sending
an incomplete proof to a summary model (4 failed20 passed4.97s,43f975).
Tests now preserve the full receipt/UI data/original summary and explicitly
assert no model call plus the budget error; both legacy prompt-budget tests keep
their original assertions. Current focus24 passed4.83s, chunk0e519c.

The six native Session failures do not reproduce locally. A bounded pass-through
diagnostic records class/bound-hook identity and exception type/source basename/
function/line, never exception text, records or full paths; all original search,
HTTP, status, receipt, ledger, seal and persistence assertions remain. Production
is unchanged. Pascal SOURCE/SPEC approves these two test hashes for diagnostic
publication, not merge: chat-budgetD52A657F9F19E9196813EB0F70F73EC5DCD0C64A2569FD210417DFF736C5CADF;
receipt-consumptionD433BF188B5DBCC3F64229AF19662B807F43FE0F14CF8880EFA4FD6FF4722951.

Expanded11-module union adds tests/agent/test_chat_input_budget.py before the
ten-module command above:1191 passed/4 FastAPI deprecation warnings/63.59s,
handle98579 terminal exit0 (summary8833d6, wrapper exitdf0843). Fresh independent
QUALITY repeats the same ordered command. Full Linux CI remains failed; the new
diagnostic follow-up must gather actual cause before the PR is eligible to merge.

Gibbs independent QUALITY approves diagnostic publication only: exact11-module
repeat1191 passed/4 FastAPI warnings/64.56s, handle40833 terminal, wrapper/process
exit0. Both test hashes and runner match before/after; no production edits.
The unresolved six Linux native failures remain a merge blocker. Parent will
publish only these two reviewed test changes and this evidence record to PR84,
then inspect the new exact-head CI diagnostics rather than waive the failures.

## Instrumentation independence control

Second workflow36241771811 at a4c1a61 completed all8 gates successfully. Agent
9530 passed/1 skipped/23858 warnings in589.78s, normal exit, job108403522987.
The six native failures did not recur, so no exception diagnostics were emitted.
This is an actual passing run, not proof of the earlier six failures' root cause.
Do not claim a production FAISS repair or erase the first failed run.

Before release, parameterize the actual Session receipt/ledger/seal/persistence
family with trace_search=False/True. False retains the unwrapped production
service method; True retains the bounded pass-through diagnostic. Both run the
same native/base-IP, hits/empty/partial and tamper controls with every existing
HTTP/search/status/proof/SQLite assertion. This controlled comparison tests whether
the diagnostic wrapper is necessary for success, rather than blindly retrying.
Parent focused36 passed/3 SWIG warnings/6.87s,exit0,terminala81ac8. Independent
review/repeat and new exact-head Linux CI are required. The original failure
remains unattributed; no stronger reliability claim is justified by local passes.

Tesla independent SOURCE/SPEC approves the control-only change. Parent full
11-module union1209 passed/4 FastAPI warnings66.88s,terminal97422/chunk56b1e1.
Hooke fresh QUALITY repeated exactly once:1209 passed/0 failed/0 skipped/4
FastAPI warnings66.68s,terminal35522/chunk19d9d2,exit0. Receipt test SHA256
C2F748F8D9D3D27E53F177284C6B4C25259A6DA246CAAD60A053EB1A01614C27 and runner
hash match before/after; no scoped actionable findings. Approval supports the
controlled new Linux CI publication, not a root-cause fix or merge claim.

## Third CI: unwrapped native counter mismatch remains unresolved

Workflow36242795132 at7f8e4cd is terminal with Agent1 failed/9547 passed/1
skipped/23858 warnings469.28s, normal exit1 (job108406355064). The sole failure
is [False-native-None-hits]: HTTP count1 passes, the returned tool observation
has success=True/error=None, but the FAISS class search probe counted0, not1.
This does not establish a scientific retrieval failure; it establishes an
unexplained missed probe. All35 other controlled variants passed. No merge,
waived assertion, production fix or blind same-head retry follows this result.

Next diagnostic is failure-only post-dispatch identity flags (owned index,
generation, class/bound hook, instance shadow and returned generation). The
assertion message expression runs only after a mismatch; the unwrapped success
path gets no extra wrapper or search/getattr before dispatch. Keep all existing
scientific/status/ledger/SQLite assertions. No claims yet about subclass caches,
thread interference or other possible causes; fresh local validation and source
review precede diagnostic publication.

Failure-only diagnostic SOURCE/SPEC (Dewey) and fresh QUALITY (Lagrange) approve
publication only. Parent exact36 actual Session variants passed6.50s, terminal
cb1e11; independent same36 passed6.54s, terminal72a7cc. Each has3 SWIG warnings,
zero skips, normal exit0. Test hashD1CAF5217F1D92460B9931286A736ADB02F469C293CE5633C573171BEC7A8AAC
and isolated launcher220D8B2D07BE845898ED495ACE8474D5B6F9390A93FA3530373263275C3EEDFA
were verified unchanged. In-memory compilation and diff checks passed. No full
suite rerun is claimed here; new exact-head Linux CI is still required. The
post-dispatch flags cannot by themselves prove dispatch-time state/root cause.
