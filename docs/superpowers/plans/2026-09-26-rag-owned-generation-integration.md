# Focused RAG owned-generation integration

## Approved scope

Integrate the reviewed R2 source component after PR #82, without importing the
accumulated B1 branch or changing its behavior. This is a publication slice of
the existing P7 plan, not a new retrieval engine or a completed P7/P8 gate.

- Clean base: `959860eb014c371ca9974a460d0902e0840c02f2` (PR #82 squash).
- Source implementation: `f5544c3ad3e916778a95c62457c3b41ab4bf22d3`.
- Branch: `codex/rag-owned-generation-integration`.
- Production: `src/rag/service.py` only.
- Tests: `tests/test_rag_owned_generation.py` only; old tests unchanged.
- Documents: this integration record and the exact source commit's
  [owned-generation specification](../specs/2026-09-26-rag-owned-generation-design.md).

The specification's implementation-release wording records the original design
checkpoint. The component has since been implemented/reviewed in the source
commit; the new branch must still receive fresh integration validation. The
source commit's umbrella handoff/implementation plan are not copied wholesale.

## Behavioral contract

Preserve the linked specification, especially:

- Parse and hash the same private CSV bytes; do not label old loaded rows with a
  replacement file's digest. Only actual initialize publishes a strict generation.
- Reuse the existing FAISS algorithm, row mapping, manifest schema and atomic
  pair writes. Reject stale initializers/builds before persistence/publication.
- Capture one operation's model, endpoint, client and source configuration;
  check epoch/configuration/source at the specified boundaries. Full-source hash
  reads must not grow with embedding row count.
- Separate public compatibility aliases from private frame/index/manifest data.
  Public readiness assignment and direct legacy helpers are not certification.
- The strict sync producer returns same-invocation records/receipt with exact
  input/result/mapping digests and R1 diagnostics; it never backfills missing
  remote weights or historical index endpoint identity.
- Owned clients close once, borrowed clients remain open, cancellation waits for
  owned cleanup, and close/reload prevents stale publication. No retry/rebuild
  is added to strict queries. Legacy behavior remains covered.

Tool receipt consumption, current-eligibility checks, Session sealing and B
dispatch remain later slices. This does not activate a new tool path, connect to
a real embedding provider, change scientific scoring, or deploy a server.

## Source identity and prior evidence

Pre-extraction diff between main and the source parent was empty for the RAG
directory, legacy RAG import, related existing tests and conftest. Exact Git
blobs after extraction:

| File | Blob |
|---|---|
| src/rag/service.py | `1e42405cdf151a33fb2906f342d7af74a4217d47` |
| tests/test_rag_owned_generation.py | `912a621f305f2674cf9c5d0cd3f3b0fafcffe5bc` |
| owned-generation-design.md | `0c33a3f0a234ed523ab10a402eb348bc2b5276e2` |

Historical source chronology, not fresh new-branch test evidence:

- Initial two RED nodes reproduced loaded-old-rows/new-file-digest acceptance
  and the missing strict API (two failures).
- Subsequent RED runs covered client leaks, initialization/query races,
  cancellation, drift and malformed configuration; they are retained in the
  source commit's original R2 implementation plan.
- Independent review found legacy per-row transport drift and full-source
  hashing proportional to row count. Both were reproduced (11 failed/1 passed)
  before correction; previous disk pairs and valid compatibility overrides are
  now preserved, and counted full-source checks are row-independent.
- Final source six-module run: 746 passed/0 skipped/seven warnings; independent
  repeat: 746 passed in19.82s. SPEC Goodall and QUALITY Wegener approved that
  frozen source, not this newly based integration branch.

## Fresh integration gates

1. Independent SOURCE/SPEC review of these four files and their current base.
2. Fresh independent QUALITY review and the exact six-module regression below
   using the existing isolated launcher repinned only to this worktree:

   ```powershell
   & C:/Users/xkx52/.conda/envs/MedChat/python.exe -I -S -B scratch/ordinary_chat_offline_runner.py tests/test_rag_owned_generation.py tests/test_rag_retrieval_outcome.py tests/test_rag_index_manifest.py tests/test_rag_service_boundary.py tests/agent/test_rag_tool_contract.py tests/agent/test_registration_consistency.py
   ```

3. Match source/test/spec blobs, compile the two Python files in memory, run
   diff checks and scan only staged non-document files for credential patterns.
4. Refresh main and check no competing PR for this branch; publish a focused
   draft PR. Require all current CI gates and no unresolved findings before
   delegated squash merge. Verify reviewed and merged trees match.

Use one local heavy test at a time; the B1 continuation worker holds the slot
until explicit handover. No competing pytest, real configuration/assets,
credentials, network, services or models in local tests. Temporary CSV/indexes
and labeled HTTPX transport fixtures are not evidence of real-model quality.
The ignored launcher isolates environment/cwd and blocks parent-test sockets;
it is not a universal descendant-process sandbox.

## Initial extraction checkpoint

Extraction and exact three-blob identity checks passed. Fresh source review,
runtime regression, quality review and remote CI are pending. No push, merge,
deployment or production activation has occurred for this slice. B1 and the
original dirty checkout are untouched. Overall packages7/8 remain incomplete.

## Integration SPEC finding and bounded TDD repair

Rawls source-only review requested changes: `_legacy_candidate` independently
captures the public frame and hashes the current path, so a same-row-count CSV
replacement before legacy build can tag old embeddings with the new digest.
This is not covered by the historical tests that mutate the CSV after capture.
The linked specification now selects byte/parsed-frame coherence and detached
capture; no production repair has been written at this checkpoint.

Rawls approved the bounded written design (source-only): compare detached
positional frames without coercion, preserve explicit incompatibility status,
check both legacy helpers and injected callbacks, and retain boundary freshness
checks. The recovery test must verify actual B embedding prompts and persisted
vectors/mapping, not just B-labelled output. Equal-looking different-dtype tables
are intentionally rejected; historical disk pairs are not retroactively proven.

- [ ] Add `test_legacy_stale_frame_cannot_poison_later_owned_load`: actual
  initialize A, replace the source with B, call default-transport legacy build,
  retain the persisted-pair outcome, then actual initialize/strict query B.
  Require no legacy embedding request, unchanged previous pair and correct B
  embeddings/rows after normal rebuild. Run this exact node before repair;
  preserve the failure and diagnostic evidence of erroneous later acceptance.
- [ ] Add a coherent legacy frame with non-default row labels as a positive;
  row mapping remains positional. Add public in-place mutation during the first
  embedding callback and require the captured original row sequence throughout.
- [ ] After RED, deep-copy the legacy frame at capture. Read/hash/parse one CSV
  byte snapshot, compare positional frames, invalidate/reject mismatch before
  load/build without changing the prior disk pair. Keep error/freshness and
  owned/borrowed cleanup paths; no silent data replacement or manifest change.
- [ ] Re-run the new exact nodes and unchanged six-module regression using
  launcher SHA256
  `45A04659D7CC97AA94564E273C7D3924CE81BBE5076D17DDF1FD8EB9DA3E00C7`.
  It differs from the approved B1 launcher only in the worktree pin.
- [ ] SPEC re-review and fresh independent QUALITY must approve the corrected
  snapshot before publication. The original source blobs remain extraction
  provenance, not the expected final corrected hashes. Preserve both histories.

All local runtime tests remain subject to the current B1 worker's explicit
heavy-slot handover. Parent in-memory compilation and diff checks already pass;
they do not reproduce the finding or certify the repair.

## Actual integration repair evidence

The approved isolated runner was repinned only to this worktree. Parent obtained
the sole local test slot after B1 handle72888 terminated; no parallel pytest.
All following runs used the command template above, never raw pytest.

| Stage | Targets/result | Seconds | Exit |
|---|---|---:|---:|
| Original finding RED | Three new nodes/six cases:5 failed,1 passed,3 warnings |1.23|1|
| Minimal coherence/detachment GREEN | Same six cases:6 passed,3 warnings |0.95|0|
| First six-module regression |752 passed,7 warnings; handle45458 terminal |19.64|0|
| Expanded no-coercion/capture coverage |758 passed,7 warnings; handle75134 terminal |18.75|0|
| Object-dtype review RED |`test_legacy_frame_coherence_does_not_sort_or_coerce[object_dtype]`:1 failed,3 warnings |0.83|1|
| Numeric-copy RED |`test_frame_detachment_preserves_object_numbers_and_missing_values`:1 failed,3 warnings |0.81|1|
| Corrected six-module regression |760 passed,7 warnings; handle69476 terminal |18.67|0|

First RED cases used both legacy helpers and default/injected transports. They
actually persisted old `CCO`/`CCC` vectors under the replacement CSV digest;
subsequent initialize reported `loaded` and the strict CCN-query returned top
`CCCC`. The in-flight public-frame mutation separately changed the second
embedding prompt to CCN. These are synthetic transport results that reproduce
the source-identity bug, not real model or chemistry accuracy claims.

The minimal guard reads/hash/parses the same CSV bytes, compares a detached
positional frame before transport/load/build, and invalidates with the explicit
incompatibility status before the generic legacy catch can replace it. Existing
freshness checks remain; the prior disk pair survives rejection. Successful
recovery now checks actual B embedding prompts and persisted vectors/mapping.

Rawls re-review confirmed that repair but found object-column copying could
infer/coerce dtypes through `Series.map`. Both new RED tests reproduced this:
an object-typed numeric column incorrectly passed coherence, and a large integer
plus None became float64. `_copy_frame` now constructs a Series from individually
deep-copied values with the original explicit dtype and index. No infer-then-cast
path, sorting or value coercion is used. Nested-object isolation remains tested.

Current frozen SHA256:

- service: `6B7EE819B3327FD9338C159D242C9A11B3A73416FD70D1C842D4EB8DF4348F9B`.
- tests: `218794656C292DEE655C50A16C158CCF0DC410549ED0B8A76281A89C89B9957A`.

Warnings remain3 SWIG plus4 FastAPI deprecations. No skips or weakened existing
tests. All runs are terminal; the test slot was explicitly returned to B1 after
handle69476. Corrected SPEC re-review and independent QUALITY/runtime repeat
were pending at that test checkpoint. Rawls subsequently approved the corrected
SOURCE/SPEC snapshot (service blob27a318810292d010e7e50a1f6e72b1aaa38323ba,
test blob26e90c68c8fd2c7378979ed08be02cf60a0b7d30). Independent QUALITY/runtime
repeat remains pending; no implementation commit, PR or merge yet. Later dependent slices
must preserve this repair as a delta rather than overwrite service.py with an
older whole-file snapshot. The B1 development tree has not been changed here.

### Final independent integration approval

The pending quality gate above is now satisfied for the frozen four-file scope.
Halley independently reviewed the implementation and ran exactly the authorized
six-module command once: **760 passed,0 skipped,7 warnings,18.59s,exit0**.
Handle40915 was polled to terminal completion without retries; source/test and
launcher SHA256 matched before/after. Final QUALITY: APPROVE, no actionable
findings. The test slot was returned to B1 immediately; no local RAG test remains.

Parent in-memory compilation of both Python files and diff checks also passed.
No existing test, deadline or scientific assertion was relaxed. Fresh remote
base/PR checks, publication and all CI gates remain required before merge; this
approval is not retrospective index authentication or complete Agent acceptance.
