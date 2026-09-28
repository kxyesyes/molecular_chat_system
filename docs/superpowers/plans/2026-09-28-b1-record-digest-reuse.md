# B1 Record-Digest Reuse — Bounded Design and Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use executing-plans for separately released steps and review checkpoints. No new subagents, commits, or execution are authorized by this document.

**Goal:** Remove repeated pure whole-record digest work without reusing observation authentication, current-source validity, configuration, journal authority, or deadline conclusions.

**Architecture:** An implementation-private, per-resolver bounded memo associates an exact immutable raw JSON string with the successful `_record_digest` result. Every authentication still parses a fresh record and performs the original mutable checks in their original order. No decoded object is retained or shared.

**Tech Stack:** Existing Python B1 resolver, stdlib JSON/SHA256, existing EvidenceLedger codec, pytest under the separately approved isolated F2 runner.

## Status and boundaries

Written design ONLY. Parent authorized creation of this plan, not tests, business code, Python, imports, collection, compilation, benchmarks, or wholemodule retries. Worktree: `D:/MedChat/molecular_chat_system_worktrees/dynamic-bindings-b1`, branch `codex/dynamic-bindings-b1`, inspected HEAD `5b6ccf2d5e1814e10e70b3b981dab9c929a10be6`. Preserve all other dirty work and parent ledger. Future HEAD/runner/source pins require fresh parent preflight.

Future file boundary, requiring separate release after SOURCE:

- Modify only `src/agent/harness/decision_bindings.py`: resolver-private memo and `_authenticated_record` digest selection/admission; no other callers optimized initially.
- Create only `tests/agent/test_b1_record_digest_reuse.py`: focused behavioral/count controls and a temporary frozen reference used only by tests.
- Update this plan with actual receipts. No changes to normal-Web tests, scientific fixtures, `decision_inputs.py`, Ledger, schema generation, F2, gate/caller, deadlines, or the parent ledger.

Read-only baseline SHA256 pins:

| Source | SHA256 |
|---|---|
| `src/agent/harness/decision_bindings.py` | D9490F70E8389163668852E2A65F59FE57A42872B84E94D88D96C8EF0A3C9A15 |
| `src/agent/harness/decision_bounds.py` | 0D26E2F18606074598C9FC95186A979737ACDC3E186710FB3899C0AA0723CE27 |
| `src/agent/evidence/ledger.py` | 43EBACE472B2BDAEA00B721E922057770EA93CA1BD34450B9592640BD2C3C1C6 |
| `src/agent/harness/decision_inputs.py` | 8BEF78D5AF6AFBE9932241383B9C40E975CDF51ED4C8478122D20890BF82E56C |

## Evidence and limited benefit claim

Parent actual66459/cbfde1 ended with the unchanged wholemodule720s watchdog, settled ownership, slot released, may_continue=false, and unchanged676-input digest45D77. Native ordinal1 CALL failed in305.531s; its exact failure cause remains UNKNOWN. JSON ordinal2 CALL passed in221.703s. No wholemodule pytest summary/pass was obtained.

The two closed profile records are PARTIAL because retained-key limits overflowed. Owned records include3594 `_authenticated_record`,3600 integrity checks,144 closures and106725 `validate_json` calls. Inclusive times overlap; do not add them or attribute all native validation to record digests. Neither elapsed benefit nor the number of unique `(resolver, raw)` pairs was measured. This design does not cover `decision_execution.settle_action` descendant work.

For a successful six-observation full closure with an input journal, source paths imply48 authentications:6 initial science-scope,6 reachability,6 ordered reconstruction, and15+15 growing-prefix historical/current resolution checks. Only6 different raw records participate when unchanged. Digest computation can therefore be48->6 in that shape, while all48 integrity/authentication sequences remain. This is an operation-count prediction, not measured seconds or a claim that all3594 calls become6. In general the eligible computation count changes from A calls to U distinct admitted `(resolver, raw)` values while capacity permits.

The stopped Ledger.get optimization and rejected schema-class memo are NOT reopened. Whole47 coverage, original failures,720/+5 and internal scientific deadlines remain mandatory and unchanged.

## Source map and exact algorithm

Current `_authenticated_record` at779 runs the following sequence. Keep all steps, placement and exception handling except the pure computation selection at step4:

1. `verify_observation_integrity(source, session)` first, including its live seal/ledger/output comparisons and failure sanitation. No memo read is allowed before it.
2. Re-read `self._records.get(source.quality.get('step_id'))`; retain exact-native-str and character ceiling checks outside the existing try.
3. Inside the existing try, recheck UTF-8 bytes <= `_RECORD_BYTES`, run `json.loads(raw)` every time, and require exact dict. Do not cache encoding, parse exceptions, parsed dictionaries, or record shape decisions.
4. Look up the exact full raw string in the private memo. On hit use only its digest string. On miss invoke the unchanged `_record_digest(record)` at the original point. This still applies the512KiB native boundary, depth/node/type/nonfinite/cycle rules, Ledger's native JSON walk, exact canonical serialization and SHA256. The raw key is not a hash, step ID, object ID, or semantic JSON equality. Different whitespace or ordering is a different key and a miss.
5. Retain diagnostic classification, `_sanitized_preparation_failure`, `_initial_operation_key`, current binding-record digest comparison, `parse_binding_proof`, `parse_binding_arguments`, `_record_turn`, context reconstruction/owner, requirements/action/input commitments, provenance, and current adapter/version comparisons exactly as written. In particular do not optimize the extra digest inside `_initial_operation_key` in this first candidate.
6. Only after the complete original authentication succeeds, admit a miss's `(raw, digest)` if capacity allows, then return the original fresh `(record, diagnostic)`. A failure anywhere admits nothing. An earlier valid memo entry need not be erased by a later authentication failure: it contains no success authority and cannot bypass step1 or step5. A successfully authenticated retained failed-tool diagnostic may admit its pure digest; that does not grant scientific usability.

The existing caught exception set and conversion to `DecisionBoundaryError('invalid_dynamic_binding')` stay unchanged. Front-loaded integrity failures retain their different reason and cleanup. A bad seal must still fail before malformed raw parsing; digest failure before proof failure; proof failure before `_record_turn`; dynamic failures after a hit must still occur at their original step. No new broad catch, retry, fallback success or error-text logging.

`_record_turn` at285 remains entirely fresh: native turn check, current journal prefix digest and current journal context reconstruction. Do not memo its left-hand context wire yet. Likewise leave closure reachability/order, historical/current `_resolve`, source hooks, after-callback guard/configuration/TTL checks and observation side effects untouched. The memo is not an authentication result, checkpoint, reuse index, or new source of evidence.

## Lifetime, limits, and ownership

- Hard maxima:128 admitted entries and524288 cumulative UTF-8 raw-key bytes per resolver. Count each retained distinct key once. Digest values have the existing64-character codec shape; dict/object overhead is not claimed to fit inside the raw-byte bound.
- No LRU, global cache, disk cache, shared registry, decoded-object pool, or new process/task framework. Cache full -> original computation, not rejection or eviction-driven authority changes. A key too large for the remaining memo budget can still pass the unchanged record boundary and authenticate normally.
- Lookup and capacity/accounting updates use a small resolver-private metadata lock if concurrent calls can reach the resolver. Never hold it over JSON parsing, `_record_digest`, original authentication, source callbacks, await, or output. Concurrent misses may compute twice; recheck membership/capacity at admission so only one entry/byte charge is retained. This is not a new scientific execution owner.
- A new resolver starts empty. `restore_records` does not transfer a trial resolver's memo; after successful record installation its receiving resolver memo is empty. Failed restore does not change scientific state or acquire memo authority. No memo is included in export/continuation/persistence.
- Keep the mapping and lock implementation-private and never hand a reference to tools/callbacks/returned values. Arbitrary privileged rewriting of implementation-private cache internals or Python code is outside the pure-code premise; ordinary mutable inputs, records, models and authority remain checked. If defending forged internal cache state is required, this design is insufficient and must not be released as that guarantee.
- Fresh parsed dictionaries retain the original noalias behavior. No decoded dict, Pydantic proof instance, Session, source result or journal object is stored in the memo.

## Frozen-code and secret-policy premise

The reusable function is `_record_digest` plus its module-level boundary constants, `validate_json` defaults and Ledger JSON codec in the pinned trusted code. It is not a runtime configuration fingerprint. The current function does not consult credentials, a secret scanner, config_generation, model settings, source state, time, or TTL. Thus a runtime config/secret-policy change cannot be treated as a memo hit for its own checks: all those checks outside this pure function remain fresh.

Do not claim that a digest validates absence of secrets. Full raw keys may contain the same sensitive user material already retained in resolver records. They stay local, bounded, unlogged and unpersisted; replacement can retain old keys until the resolver/memo is discarded, within the explicit512KiB budget. No records, keys, digest values, prompts, exception payloads or credentials enter measurement output.

If a future code change makes `_record_digest` depend on a mutable secret policy, dynamic callback or runtime validation setting, this proof no longer holds. It needs explicit invalidation/policy-key design or reuse disabled, not silent reliance on class identity/version/config_generation. Runtime code hot-patching is not silently promised equivalent. The frozen-code oracle and source review must identify such a dependency before adoption.

## Task1 — TEST-FIRST preparation, then separately granted RED

- [ ] Freeze the exact pre-change `_authenticated_record` body from the pinned source as a test-only reference literal; verify its verbatim correspondence before implementation. Compile it only inside a test fixture, with the same original module dependencies, and bind it to an independent resolver. Never derive the reference by removing cache lines from the new candidate. This temporary oracle is not a second production validator and needs later publication cleanup review.
- [ ] Add the following exact focused nodes in the proposed test file; cases use two independent, identical real Session/Ledger/seal setups from the existing dynamic-binding fixtures, not fabricated authenticated results:

| Node | Required assertions |
|---|---|
| `test_record_digest_reuse_matches_frozen_oracle` | Valid native records, retained sanitized failed diagnostics, whitespace/order-distinct JSON; compare return projections, reason/type, source fields, outputs, ledger and seals after each call, including existing failure sanitation |
| `test_record_digest_reuse_computes_once_not_authenticates_once` | Two valid calls on same resolver/raw: original digest count2, candidate1; original integrity/proof/turn/adapter check counts remain2; no source-hook or complete-closure checks suppressed; distinguish mechanism RED from already-green compatibility |
| `test_record_digest_reuse_preserves_failure_order` | Integrity+bad-raw, bad-digest+bad-proof, proof+bad-turn paired faults; exact old first reason and side effects; repeated malformed/oversize/nonfinite/deep/node-overflow records fail on each call with no admission |
| `test_record_digest_reuse_rechecks_mutable_authority` | Warm then mutate source/seal/ledger, raw, owner, journal prefix/context, requirements, adapter/version; include mutation by an actual current-source callback and unchanged before/after configuration guard; reject exactly as frozen oracle, no second tool execution |
| `test_record_digest_reuse_capacity_and_isolation` |128 then129 distinct admissible records; exact512KiB retained-key boundary and next key; full-budget fallback succeeds with original computation; duplicate key charged once; failed authentication not admitted; new resolver and successful restore start cold |
| `test_record_digest_reuse_has_no_decoded_alias` | Mutating one returned record never changes later returned records, source wire, ledger or seals; equality by full raw value rather than string identity; no returned/private memo alias |
| `test_record_digest_reuse_concurrent_admission_is_bounded` | Two finite synchronized misses delegate original digest once per contender if needed, preserve their outcomes and all live checks; retain one identical key/charge, cannot exceed either cap; no new worker owner or sleep-based guard |

- [ ] Have independent SOURCE review the oracle, mutation matrix and bounded fixtures before execution. No new runtime API is required to produce RED: count calls to unchanged `_record_digest` while delegating its real behavior.
- [ ] After parent grants a slot and verifies current approved F2/hash/HEAD/input pins, run only the mechanism node once:

```powershell
C:/Users/xkx52/.conda/envs/MedChat/python.exe -I -S -B scratch/ordinary_chat_offline_runner.py tests/agent/test_b1_record_digest_reuse.py::test_record_digest_reuse_computes_once_not_authenticates_once
```

Expected genuine mechanism RED: two actual digest computations instead of one, with both authentications completed. Missing fixture/API/import failure is not that RED. Record actual handle/phase/terminal/pins; no retries or implementation before reviewed RED/release.

## Task2 — Minimal implementation, SOURCE, focused GREEN

- [ ] Only after separate implementation release, implement the algorithm above in the one production file; no optimizations of adjacent callsites.
- [ ] Freeze source/test/plan and obtain independent implementation SOURCE. Check full-string key, original ordering, exact capacity fallback, original source callbacks, no cached failures and no decoded alias.
- [ ] After a separate slot grant, run the complete new module once with the same approved runner; preserve failures and inspect actual terminal before any next command.
- [ ] Run the separately approved compatibility selection with unchanged assertions: `test_decision_dynamic_bindings.py`, `test_decision_binding_inputs.py`, `test_decision_binding_session.py`, `test_current_source_tool_hooks.py`, plus the original source/configuration callback and bounds-before-digest nodes in continuation/migration tests. Exact module expansion and qualified ownership must be SOURCE-approved before launch; this document does not authorize unsafe wholemodule execution or replace Task8's full47 accounting.

## Task3 — Small measurement, not another timeout campaign

- [ ] After focused behavior/operation-count GREEN and a separate release, add/run one temporary `test_record_digest_reuse_bounded_measurement` in the same test module. Use six real server-issued, sealed B1 records with bounded offline data from existing fixtures; no service/model call in timing, no host assets, no new profiler.
- [ ] Compare frozen full `_authenticated_record` versus candidate on independent equivalent resolvers. Fixed2 warmup pairs then8 measured pairs, alternating AB/BA. Each arm authenticates the same six records twice in fixed order. Verify projected outcomes and mutable state after each pair outside the timed interval. A separate deterministic pass counts digests and live checks; no counting spies in timed intervals.
- [ ] Report only fixed arm label, record count, sample count, aggregate p50/p95 ns and operation counts. Nearest-rank p95 on8 samples is the maximum; identify it accurately. Do not print raw data or count unrelated parsing as eliminated. No machine-dependent pass threshold; no extrapolated wholemodule savings. Include cold first-admission count separately from warmed steady-state count.
- [ ] If the complete boundary's measured benefit is negligible or defensive correctness becomes complex, stop this candidate. No larger substituted payload or repeated wholemodule watchdog used to manufacture justification. Any later unchanged native/json/full47 gate requires separate parent approval and physical ownership qualification.

No implementation, test result, speedup, full Web completion or timeout repair is claimed by this plan. Independent written SOURCE precedes every further release.

## TEST-FIRST preparation receipt — awaiting independent TEST SOURCE

Parent released only the new test module and this plan after Kant design SOURCE
READY on `5FE78542BE831078BFC68BA9E15C65CCC53DFAC5FBD4361D8655CC316BF377E9`.
This section supersedes the historical design-only authorization status, not the
algorithm or the separate execution/implementation gates above. No Python,
imports, compilation, collection, tests, benchmark, production edit, runner edit,
commit or network request was performed. Existing dirty B1 work is preserved.
Read-only HEAD now reports `570f9b8633aa517436b74641b867282642e9cf9b` rather than
the earlier design HEAD; parent must use fresh HEAD/input pins for any execution.

Prepared `tests/agent/test_b1_record_digest_reuse.py`, 601 lines, SHA256
`33A998476C1EDF4C18C9D5B8A5F93625303E39CC2A911B334F553D9FA37F0CF6`.
Static enumeration only: eight functions / 40 parameter cases (not collected).

| Exact function | Cases | Scope |
|---|---:|---|
| `test_record_digest_reuse_matches_frozen_oracle` | 4 | canonical, whitespace, ordering, real Session preparation-failure diagnostic |
| `test_record_digest_reuse_computes_once_not_authenticates_once` | 1 | first mechanism RED; two completed real authentications before final digest-count assertion; fresh parse/integrity/proof/turn/adapter counts |
| `test_record_digest_reuse_preserves_failure_order` | 13 | paired faults; malformed/character+UTF8 size/nonfinite/depth/nodes/int-bit/surrogate/shape/native-string guards; repeated failure counts and cleanup |
| `test_record_digest_reuse_rechecks_mutable_authority` | 9 | actual warmed closure; source/seal/ledger/raw/owner/journal prefix+context/requirements/adapter-version mutations |
| `test_record_digest_reuse_current_callback_guards` | 4 | candidate/frozen x actual retired source closure / actual loop configuration mutation inside current-source callback |
| `test_record_digest_reuse_capacity_and_isolation` | 5 | 128/129 entries, exact524288 raw UTF8 bytes, failed-auth nonadmission, cold new resolver, cold restored receiver |
| `test_record_digest_reuse_has_no_decoded_alias` | 1 | nested returned-record edits, equal-but-distinct raw string, full source/ledger/seal/output equality |
| `test_record_digest_reuse_concurrent_admission_is_bounded` | 3 | simultaneous same-key/entry-cap/byte-cap misses; observed later hit/fallback counts and duplicate byte-charge control |

The frozen oracle literal is exactly the existing `_authenticated_record` method
in module D949, including indentation/comments/statements, with CRLF normalized
to LF for comparison. A PowerShell source extraction compared exact strings:
3080 UTF8 bytes, SHA256
`03C7F8AD439B625418CF75E05C8DBB232A2D51FDF8741763116643415D7D8A60`, equal=true.
It is compiled only inside the future executed fixture, with a function rebound
to the original module globals so both arms see the same delegated dependencies.
It is not reconstructed from the future candidate and adds no production API.

Fixture/source review points:

- Reuse existing `build`/`execute`/`attach` and trusted Session restoration pattern.
  Independent Sessions, detached restored results, Ledgers, seal mappings and
  journals originate in the same actual RDKit execution; no generated proof or
  successful tool result is fabricated. This trusted-caller setup is not a claim
  of exercising the continuation CAS. The diagnostic is an actual Session
  rollback after real preparation raises, not a substituted ToolResult.
- Noncanonical whitespace/order and large whitespace-padded capacity keys are
  direct-authentication-only cases. They do NOT claim canonical closure success.
  Mutable authority uses actual closure; callback/configuration cases use an
  actual temporary-source hook and ModelDecisionLoop with scripted decisions.
- The paired proof/turn fault delegates the real proof parser then injects an
  explicit test dependency failure while the journal is invalid. It does not
  invent a sealed malformed proof that could bypass the earlier digest check.
- Capacity assertions observe actual digest recomputation/hits/fallbacks, not a
  guessed private cache API. Unicode context metadata distinguishes UTF8 bytes
  from characters. No cache structure access or wall-clock performance gate.
- Concurrent cases route immutable raw encodings per actual calling thread via
  a test-only `_records.get` mapping, avoiding racing writes to the same record.
  Both delegate the real digest after a finite barrier. Every path releases the
  event, aborts the barrier, joins all started threads and asserts none remain.
  The barrier is not held across a source callback and is not a new worker owner.
  Subsequent unblocked probes check single admission and capacity accounting.

Read-only hash checks still match D949/0D26/43EB/8BEF above; approved F2 remains
`485B0557F2EE5479A0E2EA5ADF576070339776D4A677BA391A7F31506C2E728B`.
PowerShell source comparison/hash inspection and `git diff --check` were the only
checks; neither can establish Python collection or behavioral success. No RED or
GREEN has occurred for this candidate.

First parent command after TEST SOURCE and explicit exclusive-slot release:

```powershell
C:/Users/xkx52/.conda/envs/MedChat/python.exe -I -S -B scratch/ordinary_chat_offline_runner.py tests/agent/test_b1_record_digest_reuse.py::test_record_digest_reuse_computes_once_not_authenticates_once
```

Expected source-grounded RED remains **two actual digest calculations instead of
one**, after both real authentication outputs and all five live check counts
match the frozen oracle. Missing fixture/API/import, timeout or collection errors
must be reported as such and are not this mechanism RED. Full 40-case execution,
implementation and the deferred small measurement require separate release.

## Actual mechanism RED and minimal implementation freeze

Parent authoritative receipts (not author reruns):

- Preflight `b13265` expected657 tracked Python files using an incorrectly
  narrowed `src + tests/agent` selection, observed493, and stopped **before
  Python**. This was neither a test failure nor mechanism RED. Parent corrected
  enumeration to `git ls-files '*.py'` (657) and froze664 focused inputs including
  the fixed/tracked/fixture selection.
- Actual `59555/5d4bf9`: terminal exit1, **1 failed, 3 SWIG warnings, 21.76s**,
  all664 focused pins unchanged. The test reached line233 with two real digest
  computations versus expected one, AFTER both successful authentication
  outputs/state and fresh parse/integrity/proof/turn/adapter counts passed.
  This establishes the intended mechanism RED, not an import/API/scaffolding
  failure and not a new scientific failure or timing measurement.
- Executed selector was exactly
  `tests/agent/test_b1_record_digest_reuse.py::test_record_digest_reuse_computes_once_not_authenticates_once`
  through the approved MedChat `-I -S -B scratch/ordinary_chat_offline_runner.py`
  command already written above. No retry was performed by this author.

Parent then released only `src/agent/harness/decision_bindings.py` and this plan
for implementation. The test remains byte-for-byte frozen at33A998. Author used
`apply_patch` and read-only PowerShell/git source inspection; no Python, imports,
compilation, collection, tests, benchmark, runner change or commit.

Implementation is +29/-3 lines in that one production module:

1. Two fixed limits (128 entries,524288 retained raw UTF8 bytes), per-resolver
   exact-string-to-digest dict, native byte counter and metadata-only Lock.
2. Existing integrity verification, exact raw type/character check, UTF8 size
   check and fresh JSON parse remain before lookup. The existing encoding length
   is retained in a local for accounting; no second encoding or parsed-object
   cache. A miss calls the unchanged `_record_digest` at its original position.
3. Existing diagnostic/proof/arguments/journal/context/owner/requirements/input/
   provenance/adapter checks and original exception conversion remain in order.
   The extra `_initial_operation_key` digest is unchanged. No source, closure,
   loop guard, scientific fixture or cleanup code changed.
4. Admission is after the entire successful authentication and outside its
   unchanged error handler. Under the metadata lock it rechecks key absence and
   both caps before inserting; concurrent misses may compute twice but charge
   once. Full capacity simply skips admission. No failure result is cached.
5. Successful `restore_records` installs the original trial record map then
   clears the receiver's memo/counter under its metadata lock. Trial memo is
   never transferred. Failed restore follows the original path unchanged.
   Lookup/admission/install locks do not span parsing, digest work, original
   authentication, callbacks, awaits or output. Concurrent mutation of Session
   lifecycle is not newly made safe by this metadata lock.

Freeze read-only HEAD: `85ebdcc389b5d9a8bfeb7540cd867e7741def49e`.

| File | SHA256 |
|---|---|
| `src/agent/harness/decision_bindings.py` | E63B15C16B9CBB95BFCF582CB1AFC44C9B2EA5D2EA3F45E6DE3F09AA3463BA95 |
| `tests/agent/test_b1_record_digest_reuse.py` (unchanged) | 33A998476C1EDF4C18C9D5B8A5F93625303E39CC2A911B334F553D9FA37F0CF6 |
| `scratch/ordinary_chat_offline_runner.py` (unchanged) | 485B0557F2EE5479A0E2EA5ADF576070339776D4A677BA391A7F31506C2E728B |

`git diff --check` passed for the production edit; this is not executable
verification. Independent Kant implementation SOURCE is requested before any
GREEN. Focused full-module command remains pending a separate parent release:

```powershell
C:/Users/xkx52/.conda/envs/MedChat/python.exe -I -S -B scratch/ordinary_chat_offline_runner.py tests/agent/test_b1_record_digest_reuse.py
```

No40-case GREEN, compatibility GREEN, latency benefit, whole-Web completion or
timeout repair is claimed. No wholemodule watchdog experiment is authorized.

## Full40 GREEN receipt and Task3 measurement TEST preparation

Parent authoritative `33640/6b7590`: **40 passed, 3 SWIG warnings, 68.74s,
exit0**,664 focused inputs unchanged. This is the complete new behavior/count
module at test33A998, productionE63B, plan5E44; not a performance measurement or
full compatibility/Web result. No author rerun occurred.

Kant compatibility SOURCE confirms the four planned full modules already have
fixed qualified-owner selectors. The additional exact tail remains pending:
`test_decision_binding_continuation.py::test_current_source_change_never_releases_old_science`
(4), `::test_restore_rechecks_configuration_changed_inside_callback` (3), and
`test_decision_migration_boundaries.py::test_integrity_checks_bounds_before_digest`
(8). Those15 static cases lack the fixed gate mapping. No owner/table/runner
change or compatibility execution was made here. Parent explicitly released
the already-planned small measurement preparation first, to assess usefulness
before investing in that mapping. All full-scope compatibility/Web gates remain
outstanding; negligible measured benefit means stop this candidate.

Only one test function appended at line604:
`test_record_digest_reuse_bounded_measurement`. The original601-line prefix has
been checked byte-for-byte by PowerShell SHA256 and still equals33A998. No
existing oracle, control, fixture or production statement changed. Test module
now725 lines (one124-line append including separators), SHA256
`828F660EE1E5AD9175DDBB3A7D75F606411188C74BB4A8D87FC1A8A65A65E113`.

Exact bounded design implemented by that test, awaiting SOURCE/execution:

- Create six **actual property-calculator executions** through the existing
  `issued`/`admit`/`execute` helpers, on six explicitly admitted journal turns
  with the same scientific input. Every record/step/evidence ID/seal is genuinely
  server-issued; all six successful observations pass current integrity and an
  actual full closure before measurement. Assert six distinct raw records and
  total retained raw UTF8 size within512KiB. This is a repeated-property fixture,
  not a fabricated six-tool scientific chain or a claim of representing every
  profiled record distribution. No padded payload, model or host asset is added.
- Fork independent real Session/Ledger/seal/journal arms from those observations
  using the already-reviewed restoration helper. Compare all six observations,
  full WorkflowState/outputs, ledger, seals, issued raw records and journal; not
  merely the first-result convenience projection. Authentication never reexecutes
  a tool, and tool-call lists must remain equal to their pre-measurement snapshot.
- First a separate untimed deterministic pass: frozen reference and candidate,
  each cold then warm, each pass authenticating the six records twice in fixed
  order (12 authentications). Expected digest counts reference12/12 and
  candidate6/0; fresh raw parse/integrity/proof/turn/adapter counts are12 for every
  pass. Values and full state must match. Count spies delegate original behavior
  and are scoped by `monkeypatch.context`; original module function identities
  and JSON parser are checked restored before timing.
- Use new independent receivers for timing. Exactly two warmup pairs followed
  by eight measured pairs, alternating AB/BA throughout; each sample contains
  the full12 authentications including fresh parse and all mutable checks. Only
  `perf_counter_ns` reads and the identical authentication/list-return loop are
  in the measured interval. Compare outputs/state outside both intervals after
  every pair. No SQL/tool/model setup, restoration, counting spy, assertion or
  deep-copy projection is in either timed interval. Require no existing current
  or threading profile; never alter a foreign hook or install a profiler.
- Emit one fixed `B1_RECORD_DIGEST_MEASUREMENT` JSON line with two fixed arm
  labels, counts, p50/p95 ns and separate cold/warm operation counts. p50 is the
  integer midpoint of sorted observations4/5; nearest-rank p95 on eight samples
  is sample8 (the maximum). `capsys.disabled()` makes only this aggregate report
  visible under the approved runner; no raw sample, record, digest, path, user
  payload, secret or exception body is printed. No machine-dependent performance
  assertion, minimum speedup, source optimization, or extrapolated Web claim.

Production still
`E63B15C16B9CBB95BFCF582CB1AFC44C9B2EA5D2EA3F45E6DE3F09AA3463BA95`;
F2 still `485B0557F2EE5479A0E2EA5ADF576070339776D4A677BA391A7F31506C2E728B`.
No Python/import/compile/collection/measurement/commit was performed during
preparation. Next command is only a candidate for parent release after Kant
TEST SOURCE and fresh exclusive-slot/input preflight:

```powershell
C:/Users/xkx52/.conda/envs/MedChat/python.exe -I -S -B scratch/ordinary_chat_offline_runner.py tests/agent/test_b1_record_digest_reuse.py::test_record_digest_reuse_bounded_measurement
```

No measurement value or performance conclusion is available yet. Do not treat
the previous40-case GREEN as evidence of elapsed-time improvement.

## Actual bounded measurement receipt — modest local benefit only

Parent authoritative `57237/d5a0c0`: terminal **exit0, 1 passed, 3 SWIG warnings,
29.96s**, all664 focused inputs unchanged. Exact selector was the single
`test_record_digest_reuse_bounded_measurement` above, with test828F, productionE63B
and approved F2485B. No author execution or retry occurred.

| Arm | Warm p50 ns | Warm nearest-rank p95 ns | Cold-pass digest count | Warm-pass digest count |
|---|---:|---:|---:|---:|
| Frozen reference | 105073900 | 106007900 | 12 | 12 |
| Candidate | 97431700 | 100676400 | 6 | 0 |

Both arms used six distinct genuine server-issued property records, each
authenticated twice per sample: **12 authentications per sample, not12×6**.
There were two discarded warmup pairs and eight measured AB/BA pairs. Each
separate cold/warm count pass likewise contained12 authentications; raw parse,
integrity, proof, turn and adapter counts were12 in every pass. All complete
outcome/state comparisons passed. Sample statistics contain no setup/restore/
tool work or counting spies.

Observed reduction is approximately **7.27% at p50 and5.03% at p95**, equivalent
to7.6422ms /5.3315ms respectively for a12-authentication sample. This is a modest
warm full-auth benefit on these six repeated-property records. No cold elapsed
time was measured. Eight samples are not a stability study; no extrapolation to
the full scientific chain, closure totals,720s watchdog or Web completion is
justified. Historical watchdogs and native CALL failure remain failures.

Recommendation, not new implementation authority: retain the29-line memo
provisionally for compatibility review. The local benefit is nonzero and the
change is narrow, with full40 behavior/count GREEN; it is not a sufficient
reason to widen the optimization or claim a root fix. Costs remain a per-resolver
Lock/dict and up to128 raw keys/512KiB UTF8 plus Python object/digest overhead,
including retention of replaced raw keys within that bound. Fresh guards and
failure handling remain mandatory. Prefer the four already-qualified fixed
compatibility selectors next, without extending any ownership framework; the
15-case fixed-tail mapping and full Web/47-scope remain pending. Publication
would still need their actual receipts and temporary test/oracle cleanup review.

Source-only revisit of the historic native failure: saved profile artifact
records native CALL enter21.297s, exit326.828s, failed report328.781s and passed
teardown report, while JSON CALL passed. It does not contain the native exception
type, traceback or failed assertion line. Current original test has distinct
waiting assertions104–113 and resume/counter/provenance assertions118–129,
plus actual scripted-decision assertions. The305.531s whole native CALL includes
fixture-body setup and potentially both invocations; it is not one measured
300s root segment. Default300s and post-callback deadline checks make profiler
budget pressure a plausible hypothesis, not a demonstrated cause. Saved artifacts
cannot distinguish waiting failure, resume failure or an exception in the body.
F2's immediate failure record carries phase+node hash only; the wholemodule
watchdog prevented pytest's final short traceback/summary. Root cause stays
UNKNOWN. No production/test/runner edits or new diagnostic run were made here.

## Actual four-module compatibility and fixed-follow-up TEST preparation

Parent authoritative `87361`: terminal **exit0**, all666 pre/post inputs
unchanged (including then-plan343400). Every original `_run_one` row returned
`outcome=passed`, `ownership=settled`, `slot_released=true`, `may_continue=true`.

| Full existing module | Passed | Warnings | pytest duration |
|---|---:|---:|---:|
| `test_decision_binding_inputs.py` | 162 | 3 | 173.11s |
| `test_decision_dynamic_bindings.py` | 145 | 3 | 123.97s |
| `test_decision_binding_session.py` | 73 | 0 | 29.17s |
| `test_current_source_tool_hooks.py` | 218 | 3 | 82.40s |

Total **598 passed,9 warnings**. This closes these four full-module selections,
not the15-case tail, original native failure, or full47/57 campaign. No author
Python invocation, repeat or new performance measurement occurred.

Kant provided written design SOURCE GO for two fixed follow-up entries. Parent
then released TEST preparation only in the existing supervisor control test and
this plan. C4 parent run77947 still pins gateAC259 and Windows89E91: neither file,
the caller, F2, production nor scientific tests may change in this release.
Parent ledger-only HEAD advance to `a9eca7c` supersedes the older85ebdcc for later
fresh preflight; it is not a source change by this author.

### Closed mapping design for separately released implementation

Add an independent `_FOLLOWUP_TARGETS` literal with exactly these two keys;
leave `_TASK8_ORDER`, `_TASK8_GROUPS`, `_TASK8_TARGETS` and `main()` unchanged:

1. `tests/agent/test_decision_binding_continuation.py::test_sqlite_revision8_new_loop_reuses_original_scientific_chain[native]`
   maps to a singleton tuple containing that exact selector.
2. `b1-record-digest:compat-tail` maps, in order, to:
   - `tests/agent/test_decision_binding_continuation.py::test_current_source_change_never_releases_old_science`
   - `tests/agent/test_decision_binding_continuation.py::test_restore_rechecks_configuration_changed_inside_callback`
   - `tests/agent/test_decision_migration_boundaries.py::test_integrity_checks_bounds_before_digest`

Only gate selector validation/fixed tuple selection and caller `_run_one`
whitelist membership will need amendment, AFTER actual RED and separate grant.
Require exact native str and membership before Owner/Popen. No arbitrary argv,
environment, deadline, runtime mapping injection, extra scientific parameter,
new owner, or process manager. Values are nonempty tuples and keys disjoint from
original Task8 and join selectors. Tail is one720/+5 owner for all three nodes,
not a refreshed deadline per node. Keep `_launch`, constructor observation,
physical health, capture/report validation, raw receipts and all cleanup intact.

Run native first only after separate qualification/release. The unchanged F2
exact-wholemodule guard is false for this singleton and for the tail tuple;
neither enables cProfile/cost hooks. Native is therefore a **current E63
unprofiled isolated control**, not an identical replay of historical D949
profiled failure. Natural failure may supply pytest's short traceback; a pass
does not identify the old failure's cause. Watchdog/missing report stays UNKNOWN.
Tail is a later separate grant, never automatically appended to native or57 rows.

### Prepared tests, no implementation or execution

Appended174 lines to `tests/agent/test_b1_web_root_supervisor.py`; original2564
lines independently rehashed by PowerShell to exact
`587451AEB7832AE638D62169385BA3D1CA9122D79315032035DB9F7283ECB1D1`.
Current2738-line control file SHA256:
`624296E53CC399B492503BE13B7AAB28F7AE11F75C62512314953FEA16964164`.
Static enumeration is seven new functions /28 cases, not a collection result:

| Exact new function | Cases | Contract |
|---|---:|---|
| `test_b1_followup_fixed_argv_control` | 2 | actual supervise under no-real-Popen rig; exact native/tail argv, original hidden/suspended launch, environment,720/+5, settlement |
| `test_b1_followup_closed_mapping_preserves_campaign_control` | 1 | exact two-entry map/disjoint keys; original Task8 tables equal item-for-item; original main source hash unchanged |
| `test_b1_followup_exact_source_cases_and_unprofiled_guard_control` | 1 | actual source AST confirms native mode and4+3+8 tail values; F2 exact-whole guard false for both new tuples |
| `test_b1_followup_tail_has_one_original_deadline_control` | 1 | blocked launch spends original720 once; late return never resumes; no deadline multiplication |
| `test_b1_followup_rejects_unlisted_input_before_owner_control` | 9 | nonstrings/json sibling/bare function/whitespace/extra argv syntax/direct unlisted tail node rejected by both interfaces before Owner/Popen |
| `test_b1_followup_no_public_execution_overrides_control` | 2 | no kwargs for timeout/argv/env/runner on either entry |
| `test_b1_followup_actual_owner_preserves_raw_receipt_control` | 12 | two selectors x clean pass/clean failure/late capture/query/watchdog/uncertain; actual captured owner and physical settlement, unchanged receipt identity |

The actual-row tests reuse `_task8_start_actual_row` unchanged: temporarily route
its **test-local** `NODES[0]` to the chosen exact follow-up with monkeypatch. No
production mapping or receipt is inserted; real `supervise` and `_run_one`
still validate the input and produce the receipt under existing fake OS
primitives/real reader threads. Rig teardown releases every barrier and joins
all owned threads on assertion paths. This is finite control evidence only,
never actual science or real Windows qualification.

A clean rc1 preserves `test_failed`; existing `may_continue=true` means physical
and report health, not scientific success. The parent follow-up driver must
stop on either nonpassed outcome or `may_continue != true`. Late faults and
observed uncertainty prohibit continuation even when the first raw outcome was
already `test_failed`; tests do not rewrite that first outcome. No automatic
next follow-up is introduced.

First proposed RED command after independent TEST SOURCE and explicit parent
slot grant (not executed):

```powershell
C:/Users/xkx52/.conda/envs/MedChat/python.exe -I -S -B scratch/ordinary_chat_offline_runner.py tests/agent/test_b1_web_root_supervisor.py::test_b1_followup_fixed_argv_control[native]
```

This body calls `rig.start(selector)` before any new private-map assertion. On
unchanged AC259 it must reach actual `supervise` selector rejection without
creating a real process; `rig.finish()` exposes that failure. Missing import,
collection or unrelated fixture failure is not the intended selector RED.
Full28 controls, implementation, full old-owner regression, any necessary
existing-fixture Windows qualification and each scientific follow-up all await
their own releases. No modification/compile/import/Python execution of gate,
caller, F2, Windows89E or production occurred during this preparation.

## Actual fixed-follow-up selector RED — implementation HOLD

Parent authoritative `2377/6fee4d`: terminal **exit1,1 failed,4.66s**, all667
focused pins unchanged. Exact selector:
`tests/agent/test_b1_web_root_supervisor.py::test_b1_followup_fixed_argv_control[native]`.
The call reached `rig.finish()` (test2588/helper271) with actual
`ValueError('unsupported B1 selector')` from unchanged gate `supervise`, as
expected. No scientific child was launched. This is the intended fixed-selector
API RED, not a new reproduction or repair of the original native science failure.

Kant TEST SOURCE had approved the first-node execution safety and full static28
controls at624296/plan70AD105. Parent now permits this receipt-only append but
explicitly HOLDS gate/caller implementation until the new C4 GREEN run has
physically completed; that run continues to pin gateAC259. No gate, caller,
Windows89E, F2, production or test edit is included in this receipt. No author
Python invocation, repeat or scientific follow-up occurred. The already-reviewed
two-entry mapping/whitelist-only design remains pending a separate release.

## Fixed-follow-up implementation freeze — SOURCE pending

Parent confirmed C4 independent fresh `73768/9a9dcc` physically terminal:
159 passed,432.85s,340 pins unchanged, slot released. Parent then explicitly
released this minimal implementation; this supersedes the implementation HOLD
above, not any test or scientific-execution restriction.

Only the ignored gate and caller were changed. Gate adds the separate closed
`_FOLLOWUP_TARGETS` two-entry map described above (exact native singleton and
fixed three-function/15-case tail tuple), admits those exact strings before
Owner/Popen, and expands their fixed argv. Caller `_run_one` admits the same
closed map. Original Task8 tables/order, all57 rows, `main()`, Owner mechanics,
720/+5, raw receipts and actual-owner health/continuation checks are unchanged.
Neither follow-up is automatically appended to the campaign. No F2, Windows,
production, scientific fixture or control assertion was edited.

Source freeze SHA256:

| File | SHA256 |
|---|---|
| `scratch/b1_web_root_gate_supervisor.py` | `362FBDD6A36C33D6872AE2226DB932BB35BDA47958E57F1E4DC8717D0044DEC1` |
| `scratch/b1_task8_campaign.py` | `2D0553BC18EE33A9EA2D774752E0ECD55165365DC4125AC77941699688B63E53` |
| `tests/agent/test_b1_web_root_supervisor.py` (unchanged) | `624296E53CC399B492503BE13B7AAB28F7AE11F75C62512314953FEA16964164` |
| `tests/agent/test_b1_web_root_supervisor_windows.py` (unchanged) | `89E91E073B7C2BED6CF58C009C3794B4FA2E04BB9D4470E53241B87869C30EB1` |
| `scratch/ordinary_chat_offline_runner.py` (unchanged) | `485B0557F2EE5479A0E2EA5ADF576070339776D4A677BA391A7F31506C2E728B` |

Read-only PowerShell in-memory inverse verification removed exactly the new
map/comment and restored the two original gate expressions and original caller
whitelist expression. Whole-file UTF-8 hashes then exactly recovered gate
`AC259293AC752A0F305CFDBAB492BC7932EECF47C7D54CD023B747E0090E5E09`
and caller
`67E35397BC33B47F570561B7A70A85391ED134B27B713F8DB15525B39A289845`.
No reversed file was written. This is source-preservation evidence, not GREEN.

No Python, import, collection, compile, test, native science or tail invocation
was performed. The actual selector RED remains `2377/6fee4d`; control GREEN and
any further qualification require independent implementation SOURCE and parent
execution release. Original scientific failures remain unresolved by this API
change; even a future isolated unprofiled native pass cannot explain the earlier
profiled failure. Implementation is frozen for Kant SOURCE review.

## Actual follow-up qualification and separately owned native/tail receipts

Kant independent implementation SOURCE READY confirmed the final gate362F /
caller2D055 / plan291281 freeze and independently reproduced both exact
AC259/67E inverse hashes. The following are parent-authoritative terminal
receipts, not author reruns. Every run preserved all668 pre/post inputs,
including plan
`29128156F5EC5ECF75ADDC3AA89A6F67742E8DCB977AEC695496A3318A350EA2`;
reported execution HEAD was `1172859431a6ac184acb03977ed306b6947eb0f4`.

| Selection | Actual handle / terminal | Passed | Warnings | Duration | Exit |
|---|---|---:|---|---:|---:|
| Seven follow-up control functions | `e2cfce` | 28 | Not separately supplied in receipt | 2.17s | 0 |
| Full owner control module | `13277/c8f22c` | 286 | Not separately supplied in receipt | 49.21s | 0 |
| Original Windows multi-target qualification | `e6770e` | 3 | Not separately supplied in receipt | 1.09s | 0 |
| Current unprofiled native singleton | `71605/351d55` | 1 | 3 | 120.67s | 0 |
| Separately owned fixed compatibility tail | `97226/88324b` | 15 | 3 SWIG | 22.70s | 0 |

Native and tail each returned the original raw `outcome=passed`,
`ownership=settled`, `slot_released=true`, with actual-owner health result
`may_continue=true`. They were separately authorized, sequential existing-owner
invocations, not direct unowned F2 science launches and not automatic additions
to the57-row campaign. Original720/+5 and scientific assertions were preserved.
Tail covered all4+3+8 parameters of the three fixed targets; no case was removed.
The Windows three cases exercised real clean exit, pipe-held descendant and late
Popen qualification using the existing harmless payloads, not scientific output.

The native pass is a current E63 **unprofiled isolated** result. The historical
profiled native CALL failure and wholemodule720s watchdog remain recorded above;
the historical assertion/root cause is still **UNKNOWN**. These receipts do not
prove a timeout fix, attribute performance improvement to the memo, establish
live-model/service acceptance, or make the incomplete original47-module/57-row
campaign GREEN. No production, test, runner, gate, caller or parent-ledger edit
was made for this receipt, and no author Python was run. Only this plan changed
after parent explicitly released its receipt-only freeze.

### Recorded execution commands

All commands used working directory
`D:/MedChat/molecular_chat_system_worktrees/dynamic-bindings-b1` and fixed
`C:/Users/xkx52/.conda/envs/MedChat/python.exe`. Parent surrounded each with saved
`b1followupgreenpre`668-input pre/post hash checks and HEAD1172859 checks,
preserving the actual exit. Commands below document those runs; they were not
executed during this append.

Focused seven functions /28 parameters:

```powershell
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -I -S -B scratch/ordinary_chat_offline_runner.py tests/agent/test_b1_web_root_supervisor.py::test_b1_followup_fixed_argv_control tests/agent/test_b1_web_root_supervisor.py::test_b1_followup_closed_mapping_preserves_campaign_control tests/agent/test_b1_web_root_supervisor.py::test_b1_followup_exact_source_cases_and_unprofiled_guard_control tests/agent/test_b1_web_root_supervisor.py::test_b1_followup_tail_has_one_original_deadline_control tests/agent/test_b1_web_root_supervisor.py::test_b1_followup_rejects_unlisted_input_before_owner_control tests/agent/test_b1_web_root_supervisor.py::test_b1_followup_no_public_execution_overrides_control tests/agent/test_b1_web_root_supervisor.py::test_b1_followup_actual_owner_preserves_raw_receipt_control
```

Full single owner module /286 parameters:

```powershell
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -I -S -B scratch/ordinary_chat_offline_runner.py tests/agent/test_b1_web_root_supervisor.py
```

Original Windows multi-target function /3 parameters:

```powershell
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -I -S -B scratch/ordinary_chat_offline_runner.py tests/agent/test_b1_web_root_supervisor_windows.py::test_windows_task8_multitarget_retains_existing_physical_contract
```

Native actual qualified outer invocation, as supplied by parent:

```powershell
$driver = @'
import os,runpy,json; p='scratch/b1_task8_campaign.py'; m=runpy.run_path(p); safe={k:os.environ[k] for k in m['_gate']._OS_FIELDS if k in os.environ}; os.environ.clear(); os.environ.update(safe); selector='tests/agent/test_decision_binding_continuation.py::test_sqlite_revision8_new_loop_reuses_original_scientific_chain[native]'; row=m['_run_one'](selector); print('B1_FOLLOWUP_ROW='+json.dumps({'selector':selector,**row},sort_keys=True),flush=True); raise SystemExit(0 if row['receipt']['outcome']=='passed' and row['may_continue'] is True else 1)
'@
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -I -S -B -c $driver
```

Tail actual qualified outer invocation was the exact same driver with only the
selector literal replaced, separately launched after native terminal:

```powershell
$driver = @'
import os,runpy,json; p='scratch/b1_task8_campaign.py'; m=runpy.run_path(p); safe={k:os.environ[k] for k in m['_gate']._OS_FIELDS if k in os.environ}; os.environ.clear(); os.environ.update(safe); selector='b1-record-digest:compat-tail'; row=m['_run_one'](selector); print('B1_FOLLOWUP_ROW='+json.dumps({'selector':selector,**row},sort_keys=True),flush=True); raise SystemExit(0 if row['receipt']['outcome']=='passed' and row['may_continue'] is True else 1)
'@
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -I -S -B -c $driver
```

No direct unowned science command is substituted for these outer invocations.
Neither driver calls campaign `main()` or mutates the closed selector tables.

## Independent56 receipts and authorized measurement archival

Kant independently executed the following separately authorized selections;
parent accepted all three physical-terminal receipts before releasing cleanup:

| Selection | Actual handle / terminal | Passed | Warnings | Duration | Exit |
|---|---|---:|---:|---:|---:|
| Eight behavior functions, all parameters | `63688/65ce66` | 40 | 3 SWIG | 28.77s | 0 |
| Current unprofiled native singleton | `76161/86e684` | 1 | 3 SWIG | 87.34s | 0 |
| Separate fixed compatibility tail | `73027/85d4fa` | 15 | 3 SWIG | 24.69s | 0 |

No skips were reported. Each run retained all668 inputs, aggregate digest
`685CAAD30116C725E760A53A6112750C700CD190D02CB236ACDD8D3EB57DC6DF`,
HEAD `1172859431a6ac184acb03977ed306b6947eb0f4`, and then-plan
`FF4D8432E3B745A649A454AC21864AEA6C01606E092E9CC23C769C4D5A2F5802`.
Native and tail used the exact existing `_run_one` selectors and outer commands
recorded above, each with its own original720/+5 owner; their raw receipts were
`passed`/returncode0/`settled`/slot_released=true, may_continue=true.
Each actual handle was polled to terminal without retry, and the slot was
explicitly released. Independent40 excluded the temporary measurement:

```powershell
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -I -S -B scratch/ordinary_chat_offline_runner.py tests/agent/test_b1_record_digest_reuse.py::test_record_digest_reuse_matches_frozen_oracle tests/agent/test_b1_record_digest_reuse.py::test_record_digest_reuse_computes_once_not_authenticates_once tests/agent/test_b1_record_digest_reuse.py::test_record_digest_reuse_preserves_failure_order tests/agent/test_b1_record_digest_reuse.py::test_record_digest_reuse_rechecks_mutable_authority tests/agent/test_b1_record_digest_reuse.py::test_record_digest_reuse_current_callback_guards tests/agent/test_b1_record_digest_reuse.py::test_record_digest_reuse_capacity_and_isolation tests/agent/test_b1_record_digest_reuse.py::test_record_digest_reuse_has_no_decoded_alias tests/agent/test_b1_record_digest_reuse.py::test_record_digest_reuse_concurrent_admission_is_bounded
```

These56 are independent focused QUALITY evidence for the digest-only candidate,
not completion of the original fullWeb/47-module/57-row campaign. The historical
profiled native failure remains UNKNOWN. The earlier bounded measurement and
its modest benefit remain recorded, without attributing a wholeWeb timeout fix.

After all56 completed, parent authorized mechanical cleanup only. Using
`apply_patch`, archived the full original725-line file to the new ignored path
`scratch/b1_record_digest_reuse_828f_archive.py`, exact SHA256
`828F660EE1E5AD9175DDBB3A7D75F606411188C74BB4A8D87FC1A8A65A65E113`.
The archive is a frozen historical source artifact, not an extra maintained
validator, test-discovery entry, runner or automatically executable benchmark.
`git check-ignore` confirmed the archive is ignored.

Removed only the124-line appended suffix (two blank lines and the temporary
measurement function) from `tests/agent/test_b1_record_digest_reuse.py`. The
remaining601-line file has exact original SHA256
`33A998476C1EDF4C18C9D5B8A5F93625303E39CC2A911B334F553D9FA37F0CF6`.
The frozen oracle, fixtures, all eight functions/40 behavioral parameters and
their strict assertions are unchanged. No oracle rewrite, fixture refactor,
performance threshold or replacement benchmark was introduced. Source-only
byte comparison verifies the retained file is exactly the archive's first601
LF-terminated lines and suffix reattachment recovers the full828F source.

Production remains E63B (29 additions/3 deletions); gate362F, caller2D055,
Windows89E and F2485B are untouched. No Python, test, import, compilation,
production edit, staging or commit occurred in this cleanup. The independent
runs above preceded this suffix-only cleanup; this is not a new execution
receipt for the cleaned file. Freeze the archive, restored test and this plan
for Kant independent inverse/archive/suffix-only SOURCE review.

## Final publishable40 actual whole-module receipt

Kant independently approved the mechanical archive/prefix cleanup, including
byte-for-byte inverse verification. Parent then separately executed the cleaned
whole module from
`D:/MedChat/molecular_chat_system_worktrees/dynamic-bindings-b1`:

```powershell
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -I -S -B scratch/ordinary_chat_offline_runner.py tests/agent/test_b1_record_digest_reuse.py
```

Parent authoritative handle/terminal `75579/28aaba`: **exit0,40 passed,3 SWIG
warnings,35.04s,no skips**. This is the actual post-cleanup whole-module run,
not a restatement of the earlier eight-node selection; the measurement function
is absent from this published test module and was not run.

All669 pre/post pinned inputs were unchanged, at HEAD
`1172859431a6ac184acb03977ed306b6947eb0f4`. Relevant execution pins:

| Input | SHA256 at execution |
|---|---|
| Production `src/agent/harness/decision_bindings.py` | `E63B15C16B9CBB95BFCF582CB1AFC44C9B2EA5D2EA3F45E6DE3F09AA3463BA95` |
| Published601-line test | `33A998476C1EDF4C18C9D5B8A5F93625303E39CC2A911B334F553D9FA37F0CF6` |
| This plan before this receipt append | `B641B274F816CE7926A0468E8DE3D375A0CFB39BF10CAE6BA900AD82FBC68494` |
| Ignored725-line archive | `828F660EE1E5AD9175DDBB3A7D75F606411188C74BB4A8D87FC1A8A65A65E113` |
| Approved F2 runner | `485B0557F2EE5479A0E2EA5ADF576070339776D4A677BA391A7F31506C2E728B` |

The669-input freeze updates the prior668 test/plan pins and adds the archive;
the earlier668 aggregate digest is not asserted for this new membership.
No new669 aggregate digest was supplied in this receipt, so none is invented.
This append occurs only after terminal and parent receipt-only release; its new
plan hash is not claimed to have been the execution-time B641 pin.

The scoped candidate now has a passing post-cleanup40-case module in addition
to the recorded independent56 and parent compatibility evidence. This does not
complete fullWeb/47-module/57-row coverage, explain the historical profiled
native failure (still UNKNOWN), or establish a wholeWeb timeout repair or live
service result. The modest measured benefit remains bounded to its experiment.

Only this receipt was appended. No production/test/runner/archive/gate/caller
edit, Python execution, staging or commit was performed by this author. Parent
owns the proposed exact three-file local checkpoint: production bindings,
published regression test and this plan. The ignored archive is not part of
that checkpoint. Freeze for Kant receipt audit before parent checkpoint work.
