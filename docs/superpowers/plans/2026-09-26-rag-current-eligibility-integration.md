# Focused RAG current-source eligibility integration

Date:2026-09-26. This is focused P7 integration of previously reviewed R4 code,
not a claim that B/C/P8 or real-model acceptance is complete. User delegated
recommended choices and subsequent qualified PR merges; independent review and
complete exact-head CI remain mandatory.

## Base and requirements

Preparation base is PR84 headf050c5ae975069f22840b78071c557964243c507, still open.
Do not publish before PR84 lands. After its merge, verify source-tree equality,
then cleanly transplant only this branch's scoped commits onto the actual squash.
Preserve PR83 source/frame coherence and dtype-copy fixes, PR84 proof-bearing
security-transform validation, Session/SQLite controls and concrete FAISS tests.
Do not copy entire old service or receipt-test files over these later repairs.

Historical source is1e9bb3456f7324c72a5ec639d78b4d4be9864369 against2d9fbdc.
The accompanying design and original implementation plan are historical reviewed
requirements/evidence. Their old local-only release language is not permission
to skip this fresh integration's review or runtime checks.

Exact production scope: src/rag/service.py and src/agent/tools/rag_search_tool.py.
Add tests/agent/test_rag_current_eligibility.py; apply only the synthetic source
contract migrations to test_rag_receipt_consumption.py and test_rag_tool_contract.py.
No changes to receipt validation, generic adapter, Session, model transport,
scientific algorithms, runtime settings or Web entry.

## Implementation and evidence steps

1. Copy the new current-eligibility test from the historical reviewed commit.
   Apply the four existing-file diffs as narrow patches, retaining all later R2
   and R3 repairs. Compare exact source delta and assert the service's original
   guarded dtype/coherence path and adapter remain unchanged.
2. Repin only the approved isolated launcher's REPO path; never run raw pytest,
   discover host credentials/configuration or use actual scientific assets/models.
   One local heavy-test process at a time; obtain explicit slot handover first.
3. Historical tests still intercept faiss.IndexFlatIP rather than the loaded
   concrete type. Before adjusting, use existing opt-in native/base-IP fixture
   to reproduce missed counted/fault searches. For no-search guards, add an
   explicit guard-reachability control after the genuine no-search assertions.
   Keep strict initialized baselines, real temporary FAISS/CSV/SQLite, existing
   counts and all scientific assertions. Change only test interception, not
   production loading or index algorithms, and retain RED/GREEN evidence.
4. Independent SOURCE/SPEC checks the approved requirements and final delta;
   any new real defect gets a behavioral RED before a minimal fix. Run focused
   actual-tool positives/negatives and the eleven-module union below, recording
   every terminal failure/pass and warnings. Historical1163-pass results are not
   fresh results for this integrated tree.
5. Fresh independent QUALITY repeats the union with frozen hashes. In-memory
   compile, diff checks, explicit staging, scoped filename-only secret scan,
   original dirty-checkout preservation and exact clean-transplant tree comparison.
6. One draft PR to main after prerequisite landing, attach it, require complete
   exact-head CI and no unresolved review findings before delegated squash merge.
   No deployment or real-model enablement.

## Exact regression

From this focused worktree, after sole-slot release:

```powershell
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -I -S -B scratch/ordinary_chat_offline_runner.py tests/agent/test_rag_current_eligibility.py tests/agent/test_rag_receipt_consumption.py tests/test_rag_owned_generation.py tests/test_rag_retrieval_outcome.py tests/test_rag_index_manifest.py tests/test_rag_service_boundary.py tests/agent/test_rag_tool_contract.py tests/agent/test_registration_consistency.py tests/agent/test_dynamic_run_session.py tests/agent/test_decision_inputs.py tests/agent/test_decision_spec_findings.py
```

Capture and validation must perform no embedding, search or rebuild. Stable
partial remains diagnostic partial; malformed proof is invalid_output, changed
or unavailable source is tool_unavailable. Stale snapshots cannot revoke healthy
new generations; formatting must not bypass the final source check. A boundary
freshness check is not filesystem atomicity or remote-weight verification.

## Initial extraction checkpoint (superseded by RED/GREEN below)

Historical five-file implementation is now extracted through narrow apply_patch
diffs. Tool and old contract-test blobs equal1e9bb34 exactly; service differs only
by the preserved PR83 dtype/coherence guards. R3 receipt validator/adapter and
its conftest/owned-generation tests remain byte-identical to preparation base.
New eligibility-test original blob58a0337b0bfec8c90cbde6f6ca05b1618443ff19 was
verified before adding native/base-IP variants and guard-reachability controls.
These controls are prepared for RED: the old five constructor-class interception
sites are intentionally still unchanged. No R4 test has run on this branch yet.

Repinned isolated launcher SHA256
8C044090FB83203EC756C1C99A975BA9505E6E3B3422D930E9484A1EFA2C3703.
Task5 worker currently owns the sole local test slot; do not start a competing run.
Initial SOURCE/SPEC reviewed production and the bounded fixture plan;
final test freeze, runtime results and QUALITY are separate pending gates.

## Additional integration finding: formatter exception safety

Kepler SOURCE/SPEC found format_rag_context outside the ordinary-exception guard.
An exception can escape direct execute and be stringified by the generic adapter,
leaking provider-like details. This was initially source-derived; the runtime
failure and subsequent correction are recorded below.
Selected minimal repair: keep a working actual-tool baseline, inject synthetic
RuntimeError/ValueError/compatibility formatter failures through direct and real
adapter paths, and retain BaseException cancellation propagation. After observed
RED, contain ordinary formatting failures locally with the existing generic
unavailable result (empty data/evidence). Successful formatting must still reach
the final freshness validator; malformed proof classification stays invalid_output.
Do not change generic adapter error handling or conceal malformed proof by
rewriting it. No real secret is used in these diagnostic fixtures.

## Integration RED/GREEN checkpoint

The seven focused families actually ran under the isolated launcher:17 failed,
35 passed/3 SWIG warnings in5.65s (terminal chunk aed7c1). Eleven failures exposed
the missed base-IP interceptions; six exposed ordinary formatter exceptions via
direct and adapter execution. The adapter assertion failed on its error code
before checking raw text; do not claim a runtime leak assertion was reached there.
After concrete-owned-class interception and the local formatting Exception guard,
all52 passed/3 SWIG warnings in4.75s (terminal338368). BaseException controls and
successful-format final freshness validation remain. The original RED is retained.

Current five-file hashes are frozen for corrected SOURCE/SPEC; full eleven-module
union and fresh QUALITY remain outstanding. Sole local test slot is now Jason's
Task5 independent union22518; no competing pytest. PR84's first CI subsequently
failed10 tests (9520 passed/1 skipped), so it remains unmerged and R4 publication
is blocked on correcting that prerequisite, not waived by the52 local passes.

Kepler corrected SOURCE/SPEC now APPROVES all five hashes with no actionable
findings; the formatter P2 is closed and protected R2/R3/Session/security paths
are unchanged. This reviewer performed source-only verification, not a runtime
repeat. Final tool SHA256 C1957C09712FB879231E9E385B87F6DB75A9D3B702A9A1EE9DE94223F032CAF9;
service7168B1FF0FD37FDE1E1C42A476DC2E7CEC2D8B175CB7D2E3DA1CE5ED32327809;
receipt-testC7D7975E4494EDCB95C8717E7DBF3E6527B835F35AB71D59E6C95C36295FD395;
tool-contract6E6573520080CBC34DA4B4F49C3C9F105A40B7F2AC260C313981C523C54474C5;
new-test2D917EC4AE739EB6AAD96EB26FD0233F039A4181CE83826E8DA2EFA02DAC4ADA.

Parent exact eleven-module union58842 is terminal:1252 passed/7 warnings/70.28s,
exit0, chunk1a1ef6. Warnings are three SWIG and four FastAPI deprecations.
Fresh Meitner QUALITY source inspection has no actionable findings and confirms
the five hashes; explicit sole test slot transferred for its independent repeat.
R4's base is stillf050c5a. Prerequisite PR84 now has diagnostic follow-upa4c1a61
with new live CI36241771811; not merged. Native Linux search failures remain
unresolved, and any resulting prerequisite changes must be carried/re-reviewed
before this dependent branch can be published. No positive CI claim from local
1252 passes and no production-entry/model switch.

Independent Meitner exact eleven-module repeat is terminal55939/chunk12fbfc:
1252 passed,0 failed,0 skipped,7 warnings in69.12s,exit0. Three SWIG and four
FastAPI deprecations; no edits and all five code/test hashes plus runner match
before/after. QUALITY approves this frozen offline snapshot and released the
sole local test slot. This snapshot can be committed locally; publication still
requires PR84 correction/landing and fresh verification of inherited changes.

## Reviewed prerequisite test follow-ups integrated

After local R4 commita35c9c4, cleanly cherry-picked PR84 test-only follow-ups
a4c1a61 and7f8e4cd as271c363 and41391c6. No production source changed; the receipt
test retains all R4 current-source fixtures and adds wrapped/unwrapped actual
Session controls. Receipt-test SHA256 now
B7D9F417D926680099818E2E2C48A5CCACFAD7E8940F723ED834E1F24454C75E.

Expanded regression is the exact eleven-module command above with
tests/agent/test_chat_input_budget.py inserted first. Parent terminal97574,
chunk907834:1323 passed/0 failed/0 skipped/4 FastAPI warnings71.63s,exit0.
Meitner is re-reviewing inherited changes; independent repeat remains pending.
PR84 latest controlled CI36242795132 is still live. Its prior8/8 passing run does
not explain the first six native failures; no false root-cause fix claim.
R4 remains unpublished until actual prerequisite landing, clean transplant and
final affected-change verification.
