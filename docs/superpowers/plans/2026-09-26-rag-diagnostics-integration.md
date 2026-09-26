# RAG retrieval diagnostics: focused integration

## Scope and provenance

This is the first clean-main publication slice of the approved P7 dependency
chain, not a replacement Agent implementation or completion of packages 7/8.
The selected implementation already passed TDD and independent reviews on the
development branch. Integrate it without changing its behavior:

- Local base: `a1258d978450a70a55e71156060b4d8d88a8db29` (PR #81 squash).
- Source: `25c6a51999017487ae1f41120ebfda2c77a468c5`.
- Branch: `codex/rag-retrieval-diagnostics-integration`.
- Production file: `src/rag/retrieval.py`.
- New test file: `tests/test_rag_retrieval_outcome.py`.
- This integration record is the only additional file.

The source commit's two historical umbrella documents are deliberately not
copied: they include other pending work. The reviewed design is retained in the
source branch's `docs/superpowers/specs/2026-09-25-dynamic-tool-bindings-design.md`
section 14, and original test chronology in the corresponding plan sections
16-17. Local base inspection is not a fresh remote/CI check.

## Behavior to preserve

The legacy `search_molecular_index` API keeps its keyword-only signature, list
return values, filtering/truncation, object-cell aliasing, error propagation and
source-row mapping. No existing test or scientific assertion is weakened.

The new `search_molecular_index_outcome` takes the same parameters and returns
exactly `records` and `diagnostics`. Both use shared manifest/source/k/vector
validation and at most one FAISS search per invocation. No rebuild or second
retrieval is introduced.

- `valid_empty` only describes a validated zero-vector index: zero counts and
  `index_search_executed=false`. Query validation still runs first.
- Strict nonempty output requires two NumPy arrays of shape `(1, effective_k)`;
  malformed/truncated output is `invalid_discard`, not an empty search success.
- Invalid labels, invalid finite-real scores and duplicate accepted labels or
  source rows are counted once per pair, with ordered unique reason codes.
- Mixed valid/invalid output retains accepted records for diagnosis but cannot
  report `valid_hits`; all-invalid output cannot report `valid_empty`.
- Accepted strict records are deeply detached. Original mapping/provenance and
  backend/projection exceptions are preserved.

Diagnostics are NOT a stable loaded-source receipt, model provenance, or proof
that the Agent consumed a verified result. Service ownership, receipt producers
and consumers, reverse-target evidence and decision-loop integration remain
separate dependent slices. No service, tool, Web entry or production mode changes
belong in this PR.

## Integration checks

Before extraction, local diff confirmed that the source commit's parent and
the base have identical `src/rag/retrieval.py`, `src/rag/index.py` and
`tests/conftest.py`. Extraction uses the exact reviewed production/test files.
Their Git blob IDs must remain:

| File | Git blob |
|---|---|
| src/rag/retrieval.py | `4f87536d749f3d1e832e38cfed633f4af11702d5` |
| tests/test_rag_retrieval_outcome.py | `322d8108302ab5b34e7294cee71da242e67b8750` |

Historical source evidence: new module RED 61 passed/140 failed, GREEN 201
passed; four-module regression 573 passed with seven warnings, independently
repeated 573 passed in 18.62 seconds. These are historical, NOT tests run on this
new integration branch.

Required fresh checks before publication:

1. Independent specification review of this three-file slice.
2. Run the new module and unchanged RAG integration regressions through the
   reviewed isolated launcher, repinned only to this worktree:

   ```powershell
   & C:/Users/xkx52/.conda/envs/MedChat/python.exe -I -S -B scratch/ordinary_chat_offline_runner.py tests/test_rag_retrieval_outcome.py tests/test_rag_index_manifest.py tests/test_rag_service_boundary.py tests/agent/test_rag_tool_contract.py
   ```

3. Fresh independent quality review, exact file identity, in-memory compilation
   and `git diff --check`. One heavy test process at a time across both worktrees.
4. Recheck remote base, publish a focused draft PR and require all current CI
   gates/no unresolved findings before squash merge under the delegated goal.

The ignored launcher removes inherited configuration, isolates temporary assets
and blocks parent-test network sockets; it does not claim to sandbox arbitrary
descendant processes. Use only synthetic temporary CSV/index fixtures and the
three existing tracked acceptance fixture files. Do not read credentials, start
services, call real models, alter production assets or deploy for this slice.

## Current checkpoint

Clean-base extraction and both Git blob equality checks passed. In-memory
compilation of both Python files and `git diff --check` passed. A fresh
`git fetch origin main` still resolves to the base above. The GitHub open-PR
read returned no entries; this is not a completed publication gate.

Independent Heisenberg SOURCE/SPEC and Pauli initial source-only QUALITY reviews
found no actionable issues in the three-file scope. Neither treated historical
573-pass runs as new-branch evidence. After B1 explicitly handed over the single
heavy-test slot, Pauli ran exactly the four-module command above once on this
branch: **573 passed, seven warnings, 15.19 seconds, exit 0**. Handle `47238`
was polled to terminal completion; no restart or retry. Warnings are three SWIG
type deprecations and four FastAPI `on_event` deprecations, not suppressed.
Pauli's final independent QUALITY verdict is APPROVE. Both Git blobs and the
launcher hash are unchanged before/after; `git diff --check` is clean. The test
slot was returned to B1 immediately after terminal completion.

The ignored launcher is repinned only to this worktree, preserving the approved
isolation behavior. Its SHA256 is
`0BB0850BBA58E7CCF2193C96A5057817B82B67138205AED1DD385279BFB94FC7`.
This is fresh focused offline integration evidence, not a full Agent or real
model run. Remote CI, commit and publication are still pending at this record.
The later anonymous GitHub recheck returned HTTP 403; it does not invalidate the
local tests and must not be represented as a passed remote gate. No push or
merge has occurred. The original 13 dirty paths and concurrent B1 source/tests
were not changed. Keep the full P7/P8 objective open.
