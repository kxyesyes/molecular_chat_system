# Reverse-target owned source: focused integration

## Scope

Base: actual PR85 squash d05dec2433d28f6df7fbc3be0a085662c2e1216f.
Branch: codex/reverse-owned-source-integration. Preserve the landed popcount
arithmetic repair. Its predictor blob exactly matches historical9a3ccd3, the
reviewed producer implementation's baseline; no whole accumulated branch merge.

Extract from a8d4e3d only src/reverse_target/owned_source.py, predictor.py,
tests/test_reverse_target_invocation_receipts.py and the historical producer
design/plan. Do not copy the historical global handoff ledger. Shared scoring
remains one implementation, and the legacy API remains compatibility-only.
Strict acquisition owns bounded RAM data, validates actual TSV/fingerprint
correspondence, produces detached same-call proof and rejects source drift.
No host assets, model activation, real credentials, network or deployment.

## Gates

1. Exact extraction through apply_patch; confirm source/test blobs and preserve
   historical RED/GREEN records. Read inherited instructions and actual diff.
2. Fresh independent SOURCE/SPEC against the complete producer design.
3. Parent exact six-module union from the historical plan using only the
   approved isolated runner repinned to this worktree. One local process at a
   time; await an explicit slot release before tests.
4. Fresh independent QUALITY and repeat, hash checks, in-memory compile,
   diff check and exact scoped staging. Do not label synthetic data as real
   scientific acceptance. Original mixed checkout is untouched.
5. One attached draft PR to main after complete local gates; all exact-head CI
   and unresolved-review gates precede delegated squash merge. Consumer/session
   integration, normal B1/B2/C and P8 remain separate required work.

Preparation only: no fresh tests or release approval yet.

## P2 TSV structural correction evidence (2026-09-26)

This worker changed only `src/reverse_target/owned_source.py`,
`tests/test_reverse_target_invocation_receipts.py`, and this appended evidence.
The separately authorized ignored `scratch/ordinary_chat_offline_runner.py` was
created with apply_patch. No predictor changes, staging, commit, push, network,
host assets/config/secrets, or original-checkout writes. HEAD remains
`d05dec2433d28f6df7fbc3be0a085662c2e1216f` on
`codex/reverse-owned-source-integration`.

Root cause reproduced with real temporary TSVs and actual writer RDKit/NumPy
arrays: pandas inferred an implicit index from an extra leading field on every
row, and skipped an inserted blank record. Both invalid sources were published.
The new preflight uses strict, quote-aware CSV parsing of the already bounded
TSV bytes, retaining only the header/current logical record rather than a second
table. It rejects duplicate/empty/whitespace headers, wrong field counts, blank
records and malformed quoted fields before pandas. Post-normalization headers,
record count and positional index must match preflight before any array read.
All original scientific, byte-budget, fingerprint correspondence and popcount
checks remain in place. No TSV reread, source repair or other source format added.

Cooperative checks surround each physical-line read (including inside multiline
quoted fields) and logical-record validation. The existing TSV byte cap bounds
input/line size; this is not a hard RSS or preemption guarantee. The standard
csv parser's field-size guard is retained without changing process-global limits.
Tests retain exceptions while checking parser streams are collected and unwound
parser frames cleared. Acquisition owner cleanup also clears returned headers.

Approved R3 runner source SHA256:
`220D8B2D07BE845898ED495ACE8474D5B6F9390A93FA3530373263275C3EEDFA`.
Read from the sibling `rag-receipt-consumption-integration` scratch runner;
exact comparison and `git diff --no-index` confirmed that ONLY the REPO literal
changed. Repinned runner SHA256:
`8C6B3C3A785CAC8A40F40FA6CA8CF7FAE70EE7928E1D70E89F6646F710AD6E6F`.
`git check-ignore` confirmed it remains ignored.

Every test command used this exact launcher prefix, with only explicit test
nodes/files appended (no raw pytest/import probes):

```powershell
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -I -S -B scratch/ordinary_chat_offline_runner.py
```

Sequential fresh runs, all observed to terminal before the next launch:

1. RED, receipt-file nodes
   `::test_strict_tsv_rejects_silently_normalized_records` and
   `::test_strict_tsv_preserves_quoted_fields_and_empty_organism`:
   **2 failed, 4 passed in 1.08s**, exit 1. Both corruptions failed with
   `DID NOT RAISE ValueError`; quoted tab/newline/escaped-quote/CRLF-with-blank-line
   fields and empty organism were already valid. Captured RDKit deprecation
   messages accompanied failures, not a dependency/import error.
2. Extended RED, receipt-file node
   `::test_strict_tsv_structure_rejected_before_pandas`:
   **9 failed in 1.31s**, exit 1. Eight malformed forms were accepted; the
   unterminated quote was rejected only inside pandas, failing the preflight
   ordering assertion. Production code was still unchanged for both RED runs.
3. GREEN, all three nodes above: **15 passed in 0.93s**, exit 0.
4. Added 14 supplemental controls for parser cancellation/deadline/bad callback,
   retained-error cleanup, normalized count/header/index mismatches, BOM, CR/LF
   and no final record terminator. Full
   `tests/test_reverse_target_invocation_receipts.py`:
   **219 passed in 8.27s**, exit 0.
5. Required full six-file union:

   ```powershell
   & C:/Users/xkx52/.conda/envs/MedChat/python.exe -I -S -B scratch/ordinary_chat_offline_runner.py tests/test_reverse_target_invocation_receipts.py tests/test_reverse_target_popcounts.py tests/test_reverse_target_health.py tests/test_reverse_target_pharmacophore.py tests/agent/test_reverse_target_complete_input.py tests/agent/test_target_tool_contract.py
   ```

   **622 passed, 2 subtests passed in 21.16s**, no skips, exit 0.
   Execution session `81351` was polled unchanged through terminal
   `ORDINARY_PYTEST_EXIT=0`; no restart on observation timeout. The sole local
   test slot was released after that terminal result. No test process remains.

These are fresh synthetic/offline regression results, not scientific production
acceptance. Historical 593-pass evidence is not represented as a fresh run.
No separate compileall/health-check launcher was used under the explicit runner
restriction; the changed modules were imported by the authorized tests.
`git diff --check` and no-index whitespace checks for both untracked source/test
files passed. Parent independent source/spec and quality review remain pending;
this worker does not claim release approval.

Frozen SHA256 values after the union:

- `src/reverse_target/owned_source.py`:
  `D07E51CE68DFB901A309B520462EF1B1133B13A76D9965712E42C24866DF9D28`.
- `tests/test_reverse_target_invocation_receipts.py`:
  `35ACB159F39A28BB0E88D64836F5DE757C4C885CAB317CDBACB84C6C15D5A933`.
- Unchanged parent `src/reverse_target/predictor.py`:
  `45C1A434B5B29C12EE1F9B3912C5E233327CB3BFDE1A0A9028D175242E395A8E`.
- Unchanged historical producer plan:
  `AD034D72D79DFAC2E46E6CDDA03AAFA6B5B078B806A3A042D29E06A72FC5DB7E`.
- Unchanged producer design:
  `FCDF892D5190D1238E965C42852D148117502212DC7C2F91AC70F4602DCD90E3`.

### Parent NUL-truncation hypothesis: regression preparation only

Parent requested a further tiny TDD probe before final freeze: pandas' C parser
may truncate NUL-containing cells even when preflight width/count checks pass.
Prepared `test_strict_tsv_rejects_nul_cells_before_truncation` in the existing
receipt test file, with six cases: `canonical_smiles=CCO\0junk`,
`target_name=Synthetic A\0forged`, and `standard_value=1\0junk`, each quoted and
unquoted. Each uses a real temporary TSV containing one actual NUL byte and
leaves the fixture's real RDKit-generated arrays unchanged. Acquisition must
raise the fixed source error and leave no eligible published source.

Preparation only: no test, collection, import probe or production edit was run
for this follow-up. The hypothesis has NOT yet been reproduced; no RED/GREEN
result is claimed for these six cases. Earlier 622-pass union evidence applies
to the earlier test-file hash above, not this newly prepared revision.
The sole test slot is reserved for Lagrange36; this worker awaits an explicit
parent grant before launching the single regression node through the unchanged
approved isolated runner. Only after actual RED may a minimal pre-pandas NUL
rejection be implemented, preserving quoted fields/CRLF and all scientific checks.

### NUL probe result after explicit slot grant: not reproduced

Parent explicitly granted this worker the sole test slot after Lagrange terminal
`72a7cc`, with Ampere waiting. Ran exactly:

```powershell
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -I -S -B scratch/ordinary_chat_offline_runner.py tests/test_reverse_target_invocation_receipts.py::test_strict_tsv_rejects_nul_cells_before_truncation
```

Result: **6 passed in 0.81s**, `ORDINARY_PYTEST_EXIT=0`, execution chunk `3fe29b`
returned terminal exit 0 directly (no running session or restart). All three NUL
cell variants, quoted and unquoted, already raised the fixed source error and
left acquisition unavailable against the unchanged production source. Therefore
the requested probe did NOT produce actual RED and the truncation/acceptance
hypothesis is not reproduced in this approved runtime. No inference is made
about the exact internal rejection stage or other runtimes from this result.

Following the explicit non-reproduction instruction, no NUL guard or other
production change was added. No further test or full-union run was launched;
the earlier 622-pass union remains evidence for the earlier test-file revision,
not a new result for the six additional cases. The sole local test slot was
returned after the terminal probe; this worker has no active test process.

Frozen SHA256 values at this follow-up handoff:

- Unchanged `src/reverse_target/owned_source.py`:
  `D07E51CE68DFB901A309B520462EF1B1133B13A76D9965712E42C24866DF9D28`.
- Updated `tests/test_reverse_target_invocation_receipts.py`:
  `6F6E5C9D550BD433EBB215B97D4EE082F5F7DE0F3E01E12FB9B8BEAE472E8FB0`.
- Unchanged `src/reverse_target/predictor.py`:
  `45C1A434B5B29C12EE1F9B3912C5E233327CB3BFDE1A0A9028D175242E395A8E`.
- Unchanged ignored approved runner:
  `8C6B3C3A785CAC8A40F40FA6CA8CF7FAE70EE7928E1D70E89F6646F710AD6E6F`.

Independent parent/source review remains pending. No commits, push, network or
host/original-checkout changes were made.

## Final revision parent verification

Corrected SOURCE/SPEC (Dewey) approves the final D07E source / 6F6E test /
45C1 predictor freeze, including the additive non-reproducing NUL controls;
no outstanding actionable finding. Parent independently inspected the patch,
verified hashes, compiled all three modules in memory and checked whitespace.
The exact six-module command above now completed on this final revision:
628 passed,2 subtests passed,0 skipped,19.36s, normal exit0 (33248 terminal
3f8030). No warnings were reported by this run. Fresh independent QUALITY and
its repeat are still required before commit/publication; slot granted to Anscombe.
These remain temporary-data offline checks, not production scientific acceptance.

Fresh independent QUALITY (Anscombe) now approves the complete scoped producer
integration, no actionable findings. Exact same six-module final-revision union
628 passed,2 subtests passed19.52s, normal exit0, session70241 terminal7b39f2;
all source/test/runner hashes match before/after. No skips/warnings reported.
The review made no edits or publication changes. Local test slot released to
the separate normal-Web Task2 worker. Parent will publish only these six files,
not accumulated B code; new exact-head Linux CI remains a required merge gate.

## First Linux CI and reproduced import-isolation fault

PR86 head1d4dd928 workflow36244742726 failed root job108411744213:
4 failed/2862 passed/75 skipped/5 warnings233.82s. The failures were the three
loading variants of final_callback_mutations_cannot_publish (close/path/cancel)
and failed_acquisition_exception_releases_owned_arrays[late-candidate]. No merge.

Source tracing found FingerprintConversionCompatibilityTest restores sys.modules
after a fake-RDKit predictor reimport, but not the parent package's predictor
attribute. Collected receipt tests keep the original predictor class, whereas
their later package imports patch the replacement module. The earlier local
union placed receipts before health and therefore did not expose that ordering.

Raman reproduced the exact existing four failures by running health compatibility
first:4 failed/6 passed1.22s, terminal28b4d7. Four new actual-TestCase lifecycle
regressions then failed0.66s, terminal48437a, specifically on stale package
identity. The minimal test-fixture cleanup restores the exact prior parent
attribute or removes it when absent. No producer code or fault/scientific
assertion changed. Present/absent and success/failure controls passed14/1.01s,
terminal50399c. Health-first six-module union passed632/2 subtests20.24s,
session10412 terminal448db0 exit0. New health test hash:
704BF1733E42DF0724B414A075C97AB870386D1DDD7D5C1E1206AA82490001EF.
The approved runner and producer hashes remain unchanged. Independent SPEC/
QUALITY and new Linux CI are still required; this bounded attribution does not
claim a full-root pass or resolve PR84's separate FAISS probe mismatch.

Independent SOURCE/SPEC Lorentz approves the fixture-only correction; fresh
QUALITY Sagan inspected it and repeated the exact health-first six-module union:
632 passed/2 subtests19.04s, session37257 terminalf87c4d, process and wrapper exit0.
No warnings/skips were reported. Health/source/predictor/runner hashes matched
before/after; no actionable findings. Parent diff checks and in-memory compilation
passed. This follow-up is eligible for publication and fresh exact-head Linux CI,
not merge before the eight gates pass. First CI failure remains recorded above.
