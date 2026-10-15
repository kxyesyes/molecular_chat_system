# Full-corpus compact reverse-target implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close S08 and its identity-bound S09 follow-up with the complete existing corpus, unchanged scientific verification and truthful browser evidence.

**Architecture:** A compact private source preserves the existing logical-array receipt codec. An application-owned prewarm worker validates every row before atomic publication. Queries use bounded score/aggregation work; follow-up structure lookup consumes accepted current source identity, never model text.

**Tech Stack:** Python 3.10, NumPy, RDKit, FastAPI/Jinja/native JS, existing Agent/tool and task ownership infrastructure, pytest and Node.

---

## Execution contract

Approved spec: `docs/superpowers/specs/2026-10-15-reverse-target-compact-source-design.md`.
Baseline `a59492ab`; branch `codex/browser-agent-real-acceptance`; existing draft PR #209.
The user explicitly selected execution in this chat, including implementation, with no merge.
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

- [ ] Write a real fixture writer using CSV and `np.save`, with molecules `CCO`, `CCC`, `CCO`,
  distinct targets/species and optional identity columns. Compute fixture fingerprints using RDKit;
  no canned array may stand in for the fingerprint equality being tested.
- [ ] Add first RED with a missing callable assertion before invoking the new loader:

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

- [ ] Run `python -m pytest tests/test_reverse_target_compact_source.py -q` and record RED.
- [ ] Implement the frozen policy and pure loader with these exact public-internal shapes:

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

- [ ] Add REDs for each rejection: swapped rows, non-binary bit, mismatched fingerprint, cached
  count negative/overflow/float/misalignment, NPY version/header/shape/object corruption, F-order
  compatibility, missing optional count computation, quoted TSV/BOM/CRLF/final-line parity,
  duplicate/empty headers, NUL, multiline/wide/short records, overlong cells and pass-two mutation.
  Run each RED selection before the implementing change.
- [ ] Add bounded-allocation tests that instrument actual allocations/reads: reject before a large
  allocate, never read >1 MiB, never retain an mmap alias, max 4096-row unpack, reject rather than
  truncate on budget/row/column limits. Account dictionary/string capacity and conversion scratch,
  not only final array `nbytes`. Final full-corpus measurement must validate practical headroom.
- [ ] GREEN: new module tests plus `tests/test_reverse_target_invocation_receipts.py` and
  `tests/test_reverse_target_popcounts.py`. Review and commit only these task files.

## R2 — Producer lifecycle and bounded exact scoring

**Files:** compact_source.py, predictor.py, test_reverse_target_compact_source.py.

- [ ] RED: `assert callable(getattr(p, 'initialize_compact_strict', None))`, then initialize a compact
  and old-strict producer over identical fixtures. Compare `records`, `score_dtype`, all five logical
  source hashes and input/configuration semantics; generation IDs are deliberately different.
- [ ] Introduce `initialize_compact_strict(*, policy=None, timeout_seconds=1800,
  cancelled=None, progress=None)`. Its acquisition clock permits 120–3600 seconds for application
  use; short deterministic test clocks are injected below the public policy boundary, not by
  changing the normal request's existing 300-second operation check. The old initialize method
  keeps original defaults and rejection behavior.
- [ ] Reuse existing epoch/loading/active locks and capture/validate/final-check ownership. No load
  may replace a source during an active invocation; cancelled/non-owner/closed work cannot publish.
  No callbacks run under a lock order that can deadlock the tool lock.
- [ ] RED/GREEN bounded scorer through the real producer, not a fake result envelope:

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

- [ ] Expand parity: empty/zero-bit, duplicate target names across species, exact ties, top-k bounds,
  current weight default/clamp/native type, numeric-looking strings, optional assay identity fields,
  threshold-edge float32 behavior, organism aliases, no-hit and result count.
- [ ] Add lifecycle tests: path drift, close during loading/scoring, cancellation at all blocks,
  late callback mutation, concurrent invocations with independent receipts, retained exception
  frames, source disk mutation after publication, stale proof not authenticated by content alone.
- [ ] GREEN all reverse receipt/diagnostic/consumer focused suites; two-stage review then commit.

## R3 — Owned prewarm and Web lifecycle

**Files:** new source_runtime.py; reverse_target_tool.py, config.py, app.py;
new test_reverse_target_source_runtime.py; targeted Web lifecycle regressions.

- [ ] RED a real barrier-controlled loader: one start, immediate safe loading view, no result until
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

- [ ] Implement the runtime and sanitized progress snapshots, failure cleanup, load-once races,
  readiness binding and explicit shutdown. No secrets/paths/data in status. No automatic retries.
- [ ] RED/GREEN `ReverseTargetTool.start_preload` only for its application-owned uninjected source;
  `_get_predictor` returns current READY producer or safe unavailable promptly. Validate-current
  keeps producer identity; injected borrowed instances retain old ownership/close semantics.
- [ ] RED/GREEN app initialization starts the actual registered reverse tool's runtime once and
  removes the old unrelated daemon singleton preload. Shutdown closes the tool/runtime exactly
  once and retains unresolved owners. No model reader lease spans prewarm or human waiting.
- [ ] Add a session-authenticated read-only status route and fixed-label UI message only if needed
  to make the existing unavailable result actionable. It cannot launch/abort shared work, alter
  budget or replay requests. Validate strict configuration and default full-corpus profile.
- [ ] GREEN lifecycle, registry, source diagnostic tests; compileall and focused Node tests.
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
  explicitly stage task files, push the same branch; never merge.

## Progress and review ledger

- [x] User confirmed the two written designs and execution in this chat.
- [x] Plan self-check: all spec sections map to R1–R5; budgets/codec and identity are explicit.
- [ ] R1–R5 implementation/verification/reviews completed (record measured results here as executed).

No implementation or test success is claimed by this plan. Checkboxes change only after the
corresponding code, test command and review evidence exist.
