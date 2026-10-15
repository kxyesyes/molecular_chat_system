# Full-corpus compact reverse-target implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close S08 and its identity-bound S09 follow-up with the complete existing corpus, unchanged scientific verification and truthful browser evidence.

**Architecture:** A compact private source preserves the existing logical-array receipt codec. An application-owned prewarm worker validates every row before atomic publication. Queries use bounded score/aggregation work; follow-up structure lookup consumes accepted current source identity, never model text.

**Tech Stack:** Python 3.10, NumPy, RDKit, FastAPI/Jinja/native JS, existing Agent/tool and task ownership infrastructure, pytest and Node.

---

## Execution contract

Approved spec: `docs/superpowers/specs/2026-10-15-reverse-target-compact-source-design.md`.
Baseline `a59492ab`; branch `codex/browser-agent-real-acceptance`; existing draft PR #209.
The user initially selected execution in this chat without merge. On 2026-10-16,
the maintainer additionally authorized finishing the remaining work, committing,
pushing and merging after all scientific, review and release gates pass.
Reuse this isolated task branch; do not disturb unrelated untracked user files or create another PR.

Use the MedChat environment, unique `--basetemp`, `-p no:cacheprovider --tb=short`.
No test credentials are required for offline stages. The final browser run may use the user's
already configured services, with secrets held only in runtime environment/key management.
Every code step starts with the relevant test RED; record actual assertion/missing-API failure,
then GREEN and the regression command. Existing correct donor behavior can remain GREEN;
do not manufacture a defect. Parent runs verification, then separate spec/quality reviews.

## File and interface map

- `src/reverse_target/compact_source.py`: `CompactSourcePolicy`, budget accounting,
  strict bounded TSV/NPY acquisition, `CompactTable`, `PackedFingerprints`, source construction,
  and a bounded scorer. No threads, Web, source-file writes or legacy singleton access.
- `src/reverse_target/predictor.py`: `initialize_compact_strict(...)`, atomic lifecycle integration,
  compact scoring dispatch through the same invocation/receipt owner.
- `src/reverse_target/source_runtime.py`: one prewarm owner, fixed safe status snapshot, cooperative
  stop and actual worker completion. It never fabricates settled state on a timeout.
- `src/agent/tools/reverse_target_tool.py`: app-only prewarm start, current ready producer binding,
  truthful loading/failure, close ownership; existing injected/legacy instances stay compatible.
- `src/reverse_target/config.py`, `src/web/app.py`: server-only settings and lifecycle wiring.
- `src/web/reverse_target_followup.py`, `src/web/chat_handler.py`, scientific reference persistence:
  same-owner ranked evidence and exact target/species resolution.
- New tests: `tests/test_reverse_target_compact_source.py`,
  `tests/test_reverse_target_source_runtime.py`, `tests/agent/test_reverse_target_rank_followup.py`.
- Existing receipt, diagnostics, target input, lifecycle and source-consumption tests are mandatory
  regression inputs. Update health/deployment instructions only for real implemented settings.

## R1 — Private compact acquisition and logical identity

**Files:** compact_source.py; test_reverse_target_compact_source.py. The predictor initialization
seam is introduced in R2; R1 tests can call the pure loader with explicit source paths/check/fingerprint.

- [x] Write a real fixture writer using CSV and `np.save`, with molecules `CCO`, `CCC`, `CCO`,
  distinct targets/species and optional identity columns. Compute fixture fingerprints using RDKit;
  no canned array may stand in for the fingerprint equality being tested.
- [x] Add first RED with a missing callable assertion before invoking the new loader:

```python
def test_compact_source_matches_logical_arrays(source_files):
    import importlib.util
    assert importlib.util.find_spec('src.reverse_target.compact_source') is not None
    from src.reverse_target.compact_source import CompactSourcePolicy, load_compact_source
    from src.reverse_target.owned_source import operation_check, _array_hash
    from src.reverse_target.predictor import ReverseTargetPredictor
    p = ReverseTargetPredictor(source_files)
    check = operation_check(30, None)
    owned = load_compact_source(p._resolved_source_paths(), CompactSourcePolicy(),
                               check, p.compute_query_fingerprints)
    assert owned.descriptor['row_alignment_verified'] is True
    for name in ('morgan', 'maccs'):
        bits = getattr(owned, name)
        original = np.load(p._resolved_source_paths()[name], allow_pickle=False)
        assert np.array_equal(bits.block(0, len(original)), original)
        assert owned.descriptor[name + '_sha256'] == _array_hash(original, check)
```

- [x] Run `python -m pytest tests/test_reverse_target_compact_source.py -q` and record RED.
- [x] Implement the frozen policy and pure loader with these exact public-internal shapes:

```python
@dataclass(frozen=True)
class CompactSourcePolicy:
    max_snapshot_bytes: int = 768 * 1024**2
    max_acquisition_bytes: int = 1024**3
    max_query_bytes: int = 128 * 1024**2
    max_tsv_bytes: int = 256 * 1024**2
    max_rows: int = 1_000_000
    max_columns: int = 32
    chunk_rows: int = 4096

def load_compact_source(paths, policy, check, fingerprint_fn, *, progress=None):
    """Return a private owned source only after every validation has passed."""

class PackedFingerprints:
    # Private C-order uint8 storage; external mappings never escape.
    # shape is logical (rows, bits); block returns a private bounded unpacking.
    def block(self, start: int, stop: int) -> np.ndarray:
        return np.unpackbits(self._packed[start:stop], axis=1,
                             count=self.shape[1], bitorder='big')
```

Implementation sequence is fixed: validate policy/native types; open/pin regular source descriptors;
pass-one bounded UTF-8/csv validation with raw digest and row count; pass-two private dictionary
columns and float64 value with matching raw digest; bounded NPY header/shape/dtype preflight;
read/copy blocks; recompute every row and compare; validate original cached counts; pack private
bits; stream original logical-array hashes; close all mappings/fds; enforce ready/acquisition
allocation budgets; final check and return. The existing `_OwnedSource` receipt descriptor keys
and canonical hash prefix remain exact. Every error clears failed large locals/traceback frames.

- [x] Add rejection regressions (new behavior RED; correct donor guards already GREEN): swapped rows, non-binary bit, mismatched fingerprint, cached
  count negative/overflow/float/misalignment, NPY version/header/shape/object corruption, F-order
  compatibility, missing optional count computation, quoted TSV/BOM/CRLF/final-line parity,
  duplicate/empty headers, NUL, multiline/wide/short records, overlong cells and pass-two mutation.
  Run each RED selection before the implementing change.
- [x] Add bounded-allocation tests that instrument actual allocations/reads: reject before a large
  allocate, never read >1 MiB, never retain an mmap alias, max 4096-row unpack, reject rather than
  truncate on budget/row/column limits. Account dictionary/string capacity and conversion scratch,
  not only final array `nbytes`. Final full-corpus measurement must validate practical headroom.
- [x] GREEN: new module tests plus `tests/test_reverse_target_invocation_receipts.py` and
  `tests/test_reverse_target_popcounts.py`. Review and commit only these task files.

## R2 — Producer lifecycle and bounded exact scoring

**Files:** compact_source.py, predictor.py, test_reverse_target_compact_source.py.

- [x] RED: `assert callable(getattr(p, 'initialize_compact_strict', None))`, then initialize a compact
  and old-strict producer over identical fixtures. Compare `records`, `score_dtype`, all five logical
  source hashes and input/configuration semantics; generation IDs are deliberately different.
- [x] Introduce `initialize_compact_strict(*, policy=None, timeout_seconds=1800,
  cancelled=None, progress=None)`. Its acquisition clock permits 120–3600 seconds for application
  use; short deterministic test clocks are injected below the public policy boundary, not by
  changing the normal request's existing 300-second operation check. The old initialize method
  keeps original defaults and rejection behavior.
- [x] Reuse existing epoch/loading/active locks and capture/validate/final-check ownership. No load
  may replace a source during an active invocation; cancelled/non-owner/closed work cannot publish.
  No callbacks run under a lock order that can deadlock the tool lock.
- [x] RED/GREEN bounded scorer through the real producer, not a fake result envelope:

```python
@pytest.mark.parametrize('combine', [False, True])
@pytest.mark.parametrize('threshold', [0.0, 0.6, 1.0])
def test_compact_ranking_parity(pair, combine, threshold):
    old, compact = pair
    kwargs = dict(threshold=threshold, top_k=10,
                  combine_by_target=combine, organism_filter='')
    a = old.predict_with_receipt('CCO', **kwargs)
    b = compact.predict_with_receipt('CCO', **kwargs)
    assert b['records'] == a['records']
    assert b['receipt']['score_dtype'] == a['receipt']['score_dtype']
```

Compute query fingerprints once; for each bounded row block use the same uint intersection and
float32 similarity assignment/weight expression. Threshold before species filtering as existing.
Iterate qualified indices in original order. Store only best row and hit count per exact
target/species key (earliest row wins equal score); non-combined mode stores bounded top-k.
Construct result dicts only for selected final rows via the existing record builder. Charge every
aggregation entry and temporary array to the query budget, reject on overflow. Keep two maximum
compact active queries without changing unrestricted legacy producer behavior.

- [x] Expand parity: empty/zero-bit, duplicate target names across species, exact ties, top-k bounds,
  current weight default/clamp/native type, numeric-looking strings, optional assay identity fields,
  threshold-edge float32 behavior, organism aliases, no-hit and result count.
- [x] Add lifecycle tests: path drift, close during loading/scoring, cancellation at all blocks,
  late callback mutation, concurrent invocations with independent receipts, retained exception
  frames, source disk mutation after publication, stale proof not authenticated by content alone.
- [x] GREEN all reverse receipt/diagnostic/consumer focused suites; two-stage review then commit.

## R3 — Owned prewarm and Web lifecycle

**Files:** new source_runtime.py; reverse_target_tool.py, config.py, app.py;
new test_reverse_target_source_runtime.py; targeted Web lifecycle regressions.

- [x] RED a real barrier-controlled loader: one start, immediate safe loading view, no result until
  the loader returns; cancellation/close revokes publication while the native worker stays owned.

```python
runtime.start()
assert runtime.snapshot()['state'] == 'loading'
with pytest.raises(ReverseSourceUnavailable):
    runtime.ready_predictor()
runtime.request_close()
assert runtime.snapshot()['cleanup_pending'] is True
release.set()
runtime.close()
assert runtime.snapshot()['state'] == 'closed'
```

`SourceRuntime` owns the producer and one non-daemon worker; `start`, `snapshot`,
`ready_predictor`, `request_close`, `close` are server-side methods. A safe snapshot contains
state, phase, verified/total rows, elapsed seconds, fixed reason and cleanup_pending only.
`close` does not declare completion before actual thread exit; a bounded observation method can
report unresolved while retaining ownership. A closed worker never restarts itself.

- [x] Implement the runtime and sanitized progress snapshots, failure cleanup, load-once races,
  readiness binding and explicit shutdown. No secrets/paths/data in status. No automatic retries.
- [x] RED/GREEN `ReverseTargetTool.start_preload` only for its application-owned uninjected source;
  `_get_predictor` returns current READY producer or safe unavailable promptly. Validate-current
  keeps producer identity; injected borrowed instances retain old ownership/close semantics.
- [x] RED/GREEN app initialization starts the actual registered reverse tool's runtime once and
  removes the old unrelated daemon singleton preload. Shutdown closes the tool/runtime exactly
  once and retains unresolved owners. No model reader lease spans prewarm or human waiting.
- [x] Add a session-authenticated read-only status route and fixed-label UI message only if needed
  to make the existing unavailable result actionable. It cannot launch/abort shared work, alter
  budget or replay requests. Validate strict configuration and default full-corpus profile.
- [x] GREEN lifecycle, registry, source diagnostic tests; compileall and focused Node tests.
  Commit after independent spec and quality review.

## R4 — Current ranked evidence to exact structure identity

**Files:** new reverse_target_followup.py, chat_handler.py, scientific_references.py and existing
persistence seam as necessary; new test_reverse_target_rank_followup.py.

- [ ] RED accepts only a validated accepted reverse ToolResult from this owner/conversation and
  current producer projection. A raw message saying a target is first is not accepted evidence.
- [ ] Represent follow-up context by detached evidence digest, source generation/configuration,
  trace, result order and exact target/species. Never rewrite the prior receipt or rank.
- [ ] RED unique identity mapping: a `name-sha256` key cannot be used as accession. Resolve via
  actual target provider/database aliases plus species; zero/multiple/inconsistent identities
  require clarification. Caller-selected identity must exist in the returned candidates.
- [ ] Implement and bind the resolved request to existing target search; validate returned target,
  species and structures. The phrase asking whether docking is possible must not launch docking.
- [ ] Display an explicit five-row prefix for S08 only if supported by the request; keep the full
  ten-row proof and actual count. Test escaping, small result sets and unchanged order/precision.
- [ ] GREEN cross-owner, stale generation, corrupt proof, false success, missing rank, same-name
  species, model-generated identity and ambiguous follow-up regressions; review and commit.

## R5 — Complete-corpus and real browser gate

- [ ] Start only the agent-owned local browser-safe service; preserve the user's other service.
  Record actual profile/settings and actual source row count, digest, elapsed validation and
  logical/RSS observations without committing datasets or local run artifacts.
- [ ] If full validation fails, diagnose the precise stage and failing invariant. Never crop,
  regenerate, patch or accept bad rows to pass. If fixing actual data is necessary, seek the
  required asset/provenance scope and retain BLOCKED rather than forging publication.
- [ ] Once READY, open the normal homepage and send original S08, then S09 in the same chat:

```text
对咖啡因 CN1C(=O)N(C)c2ncn(C)c2C1=O 做反向寻靶，给出前 5 个候选靶点、依据和局限。
查找排名第一的靶点结构，并说明能否继续对接。
```

- [ ] Verify visible candidates/source/limitations against accepted raw evidence and original row
  identity. Confirm S09 exact target/species mapping and no docking execution. Record real trace
  IDs and truthful PASS/BLOCKED individually in the existing validation report.
- [ ] Run `python scripts/health_check.py --strict`, compileall, focused suites, Node tests and the
  final complete offline suite after the docking increment integrates. Keep every failure/skip.
- [ ] Update PR #209 with exact files, commands, results, review scope, commit and remaining gates;
  explicitly stage task files, push the same branch; merge only after the later
  authorized completion and remote review/check gates are satisfied.

## Progress and review ledger

- [x] User confirmed the two written designs and execution in this chat.
- [x] Plan self-check: all spec sections map to R1–R5; budgets/codec and identity are explicit.
- [ ] R1–R5 implementation/verification/reviews completed (record measured results here as executed).

### R1 execution evidence (2026-10-16)

Initial missing-module RED: 7 failed; first implementation: 7 passed. Expanded tests exposed
missing allocation metrics and incomplete fingerprint pairs. Independent spec review then
identified uncounted retained headers and source mutation during final count hashing; both were
reproduced as RED and fixed. Quality review identified a swallowed test assertion; the corrected
test now fails a deliberate mutation disabling `_Budget.reserve`.

Current gate: `python -m pytest tests/test_reverse_target_compact_source.py
tests/test_reverse_target_invocation_receipts.py tests/test_reverse_target_popcounts.py -q
-p no:cacheprovider --tb=short --basetemp=<fresh-temp-root>`: **411 passed in 14.40s**.
Final focused test-only adjustment: **60 passed in 2.09s**. Independent spec re-review PASS;
independent quality re-review APPROVED (60 passed independently; budget mutation correctly fails).
R1 adds only the compact-source module and its tests; no legacy strict-loader behavior changed.
The ongoing full-corpus measurement and R2–R5 are not completion evidence yet. S08/S09 remain BLOCKED.

### Preliminary full-corpus acquisition measurement (2026-10-16)

A read-only local diagnostic, started before the two final R1 review fixes, completed all
**831,120 rows in 1,340.14 seconds**. Every Morgan/MACCS row and original popcount cache was
validated; no source row, fingerprint or metadata was modified. Raw TSV SHA-256:
`f62c6f3fdf8cad701ffd77ac020d0b413db4e43be6632430cee26aa90544f6bb`.

That diagnostic version reserved 538,069,928 snapshot bytes and peaked at 788,076,712 logical
acquisition bytes. Sampled process RSS reached 2,328,936,448 bytes, including resident file
mappings; this is not an OS/RSS-limit guarantee. The small header accounting correction is not
included in those preliminary counters. These measurements establish full-corpus feasibility,
not current application readiness or browser acceptance. R3/R5 must revalidate current code
through the actual application-owned producer before S08/S09 can pass.

### R2 execution evidence (2026-10-16)

Missing-initializer RED: 6 failed; first parity implementation: 66 passed. Expanded real-RDKit
parity tests preserve stable ties, organism/identity distinctions, zero-bit score dtype and weight
resolution. The two-active-query regression failed before admission was limited. Lifecycle tests
cover closing a still-running loader, path/generation drift and retained exception ownership.

Spec review found that a fixed per-record reservation missed escaped JSON conversion copies.
Both long-field probes were reproduced as RED and fixed with content-based reservations before
the first result hash. Independent re-review measured rejection peaks of 30,177 and 217,381 bytes
for 10-row/5 MiB and 100-row/128 MiB cases; the 10-row default-budget case still succeeded.

Independent SPEC PASS and QUALITY APPROVED. Core command:
`python -m pytest tests/test_reverse_target_compact_source.py
tests/test_reverse_target_invocation_receipts.py tests/test_reverse_target_popcounts.py -q
-p no:cacheprovider --tb=short --basetemp=<fresh-temp-root>`: **475 passed**, independently in
36.00s and 28.57s. Parent combined reverse diagnostics/current-source/D1 regression:
**930 passed, 2 subtests passed, 3 third-party SWIG deprecation warnings in 72.16s**.
This does not establish application prewarm, S08/S09 browser success or D1 review completion.

### R3 implementation and independent review (2026-10-16)

The application-owned prewarm/runtime and lifecycle increment completed both
independent review stages after the corrections recorded below.
Spec review identified interrupted Thread.start bootstrap ownership before ident;
real-thread RED tests reproduced early closure and the local fix passed re-review.
Quality review identified overlapping process-global RDKit BlockLogs exposure and
optional adapter close compatibility. The local thread-filter replacement and
adapter fixes have additional real diagnostic/shutdown regression coverage.

Parent combined source/receipt/current-source/diagnostic/lifecycle/registry/adapter/
allocation regression: **1120 passed, 7 existing warnings in 229.40s**. This run
preceded the last no-prewarm standalone-adapter correction; its focused follow-up
was **61 passed, 7 warnings in 4.17s**. Compilation, four Node scripts and strict
health **23/23** also passed. These results do not supersede the failed review below.

Latest independent quality re-review: **126 passed, 1 failed** in its combined
gate, plus **763 passed** in source/diagnostic/input regressions. The remaining P2
was first-time `LogToPythonLogger()` initialization resetting native enable flags
while another thread is inside BlockLogs. Its raw synthetic SMILES diagnostic
became visible; a warmed logger does not reproduce it. The standalone flags test
also failed. These failures were retained and fixed, not waived as limitations.

The next local correction moved routing to explicit single-threaded bootstrap,
restored native flags only there, and rejected late first-time installation.
Cold-process/native-block interleaving tests passed; parent combined regression
was **1139 passed, 7 warnings in 243.01s**. Independent SPEC review ran **179
passed** but found two additional reproducible gaps: Uvicorn's multi-worker child
starts its heartbeat before app import, causing bootstrap refusal; and cold
RDKit import overwrites an already configured Python logger. These findings were
not covered by the passing combined suite and required new RED regressions.

Cold-import and actual Windows spawn/Uvicorn regressions: **3 failed / 3 passed**
before the next correction, then **6 passed**. Each `main.py` spawn child now
initializes during `__mp_main__` replay, before Uvicorn's heartbeat. Cold RDKit
import preserves existing logger configuration without removing unconfigured
RDKit defaults. Direct single-worker ASGI remains supported; direct Uvicorn
multi-worker startup is explicitly unsupported/fail-closed and must use the
documented `main.py` multi-worker path. Linux ordering was inspected, not executed.

Final independent SPEC PASS: **187 passed, 7 existing deprecation warnings** for
privacy/startup/compact/runtime. Fresh independent QUALITY APPROVED: **63 passed**
(one harness import-rewrite warning), plus 16 overlapping privacy guards, 75
start/two-close schedules and eight simultaneous closers retaining a live worker.
Parent final startup/privacy/runtime/Web lifecycle/registry/adapters/deployment
gate: **95 passed, 7 warnings in 21.38s**. `compileall -q src scripts main.py`,
strict health (without loading an env file) **23/23**, four relevant Node scripts
and `git diff --check` passed. Health retained two pre-existing registry warnings.

No new status endpoint was necessary: the existing unavailable result carries a
fixed loading message; internal snapshots do not grant control or auto-replay.
R3 is ready for its scoped commit, but the agent-owned application has not yet
revalidated the full corpus on this code. R4/R5 and S08/S09 remain incomplete;
these offline tests are not browser or full-current-repository acceptance.

### R4a bounded exact-name provider helper (2026-10-16)

Added `src/target_search/exact_name.py` and `tests/test_target_exact_name.py` as a
separate prerequisite, not the completed R4 chat integration. The resolver only
matches literal provider protein names with the requested supported species;
missing/ambiguous/inconsistent identities or real provider versions fail closed.
Its bounded response digest is provenance data, never execution authority.

Missing-module RED: **110 failed**; first GREEN: **110 passed**. Independent spec
review found that Unicode case expansion could turn an invalid accession into
an apparently valid ASCII identity. The additional invalid cases were reproduced
as **12 failed / 3 valid controls passed**, then fixed by validating the original
ASCII alphabet before normalization. Final exact-name suite: **125 passed**.

Independent SPEC PASS and QUALITY APPROVED. Quality independently ran the 125
tests, 80 selected UniProt/transport tests, 543 malformed-response/bounds/query
probes and 10 ownership/concurrency/independence/syntax checks. Parent command
`python -m pytest tests/test_target_exact_name.py tests/test_target_search_fallback.py
-q -p no:cacheprovider --tb=short --basetemp=<fresh-temp-root>`:
**376 passed, 2 skipped in 30.20s**. No live provider or browser claim is made.

At the R4a checkpoint the owner/session/trace/current-receipt binding, ranked chat
context, five-row display and real S09 structure follow-up remained unimplemented.
The following increment records subsequent progress; S08/S09 are still BLOCKED.

Checkboxes change only after corresponding code, test and review evidence exists.

### R4b owned ranked-evidence helper (2026-10-16)

Added `src/web/reverse_target_followup.py` and its focused test module. The helper
checks the authenticated session, bounded consistent SQLite checkpoint snapshot,
provenance and recomputed evidence ledger, complete raw/normalized receipt, and
current attached producer. Its detached pointer binds source version, original
trace/step/observation, evidence and result digest plus current source generation.
It does not recover authority from prose or rerun scientific computation.

Initial missing-module RED: **22 failed**. After the core and mutation tests,
**57 passed**. Retirement during rank resolution/rendering reproduced **2 failed**;
the final read revalidation brought the focused suite to **59 passed**.
Independent SPEC PASS: **59 tests + 32 supplementary assertions**. Independent
QUALITY APPROVED: **59 tests + 37 supplementary probes**. Both reviews are for
the helper only, not the still-in-progress chat/provider integration.

Five-row rendering leaves all ten receipt rows, original rank order and exact
floating-point display values intact. Source/version retirement, wrong ownership,
changed proof or pointer, and ambiguous checkpoints fail closed. `name-sha256`
remains a local identity key. No runtime provider, full-corpus or browser claim
is established by these synthetic tests. R4c/d and R5 remain required.
