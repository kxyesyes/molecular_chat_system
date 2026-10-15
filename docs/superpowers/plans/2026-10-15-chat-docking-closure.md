# Chat docking consent and real Vina closure implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close S10 from the existing normal chat through genuine input preparation, user-clicked consent, one real Vina execution and authorized verified results.

**Architecture:** A shared C request coordinator serves the existing legacy and decision entry points without migrating profiles. Bounded upstream producers create owned artifacts and a verifiable pocket proposal; existing C1/C2/C4a retain execution authority. C4b feedback and C5 artifact/alternate-path closure must pass before activation.

**Tech Stack:** Python/FastAPI, existing SQLite TaskRuntime and isolated-process ownership, RDKit/PDB, Vina/ADFR/Meeko, Jinja/native JS, pytest/Node and real browser UI.

---

## Authority and delivery

Approved spec: `docs/superpowers/specs/2026-10-15-chat-docking-closure-design.md` plus the
unchanged security/lifecycle provisions of `2026-09-27-docking-consent-integration-design.md`.
Baseline `a59492ab`; same branch/PR #209 as reverse-target work. The initial
no-merge instruction was superseded on 2026-10-16 by maintainer authorization to
finish, commit, push and merge after all completion/review gates pass.
The user confirmed this written design and current-chat TDD implementation. No further design
approval is required within these boundaries. A new scientific dependency or scope change still
requires explicit direction. The **runtime Vina card must be clicked by the user**, not the agent.

Use isolated fixtures for tests, unique pytest temp roots, no real credentials in test sources,
commands or logs. Run each new regression RED before production changes. Keep synthetic tests
separate from real scientific acceptance. Independent spec review precedes quality review.

## Interfaces and dependency order

1. D1 pocket evidence and deterministic input conformer producers, independent of Web/runtime.
2. D2 chat draft/owned artifact binding and C proposal/admission.
3. D3 same-origin/session HTTP transport and one-use runtime bridging.
4. D4 retained physical feedback and authoritative result/artifact publication.
5. D5 close generic/legacy alternate paths and retention before activation.
6. D6 homepage inline UI and both existing profile hooks.
7. D7 actual S03/S10 browser run, user approval, real output cross-check and regression delivery.

The feature flag stays false until D1–D6 gates pass. No independently working lower API is
treated as permission to expose execution. Avoid a second scheduler, queue or WebSocket receiver.

## D1 — Real input producers and verifiable pocket proposal

**Files:** new `src/docking/pocket_evidence.py`, `src/docking/chat_preparation.py`;
new `tests/test_docking_pocket_evidence.py`, `tests/test_chat_docking_preparation.py`.
Read unchanged source tools and process isolation; do not edit legacy preparation algorithms.

- [x] Write RED tests against a real small PDB byte fixture with fixed-width coordinates, distinct
  protein/reference/glycan records and deposition metadata supplied as a validated owned source.
  Assert the new module/callable exists at call phase, not a broken collection import.
- [x] Use these pure input/output interfaces, closed native dictionaries with bounded fields:

```python
def inspect_pocket_candidates(pdb_bytes: bytes, *, source: dict) -> dict:
    """Return source-bound residue identities and diagnostics, not execution permission."""

def propose_pocket(pdb_bytes: bytes, *, source: dict, residue: dict) -> dict:
    """Validate selected deposited ligand and return heavy-atom AABB+4Å proposal."""

def prepare_receptor_bytes(pdb_bytes: bytes, *, source: dict,
                           protein_selection: dict, reference_residue: dict,
                           retained_hetero_residues: list[dict]) -> dict:
    """Return exact derived PDB bytes/hash and explicit retained/removed lineage."""

def build_ligand_sdf(smiles: str) -> dict:
    """Pure isolated-worker entry: modeled SDF bytes plus RDKit method/hash facts."""
```

Source is a trusted-server validated descriptor, not an HTTP body: original bytes hash, PDB ID,
target/species/entity/chain facts, source/version/proof identity, deposited ligand roles and atom
identity/completeness evidence. The pure functions recheck shape/hash/type and coordinates; the
Web producer must separately establish authenticity/ownership before calling them. A forged
self-consistent dictionary is never claimed to be source authentication.

- [x] RED/GREEN strict parser rejects malformed/truncated/nonfinite atom fields, mixed models,
  unselected altloc, partial/mismatched atoms, wrong source digest, wrong chain/entity, no eligible
  residue and unproven ligand role. Glycans, metals, solvent and buffer do not become pocket proof.
  Selection must identify the actual model/chain/resname/resseq/icode and cannot bypass checks.
- [x] For selected heavy atoms compute exactly:

```python
center = [(low + high) / 2.0 for low, high in zip(minimum, maximum)]
size = [max(10.0, high - low + 8.0) for low, high in zip(minimum, maximum)]
```

Return source hash, atom IDs/count, residue identity, rule version, margin=4.0 and Å units;
reject runtime dimension overflow, never clamp. Keep the original receptor intact; derive a
separate protein input that excludes the reference residue and records every retained/removed
hetero/water selection. Missing residue repair, blind docking and implicit chain choice are absent.

- [x] RED/GREEN ligand uses strict full SMILES, one connected component and <=512 atoms; preserve
  canonical topology/formal charges/defined stereochemistry before and after SDF serialization.
  `AddHs`, ETKDGv3 seed 20261015 once, complete MMFF parameters, maxIters=200 and return code 0.
  Failure returns fixed safe error; no ETKDGv2/UFF or silent protonation/fragment change.
- [x] Test real aspirin SDF output with RDKit read-back and finite nondegenerate 3D coordinates;
  missing MMFF, failed embed/nonconvergence and altered structure reject. Instrument only the
  low-level scientific failure seams, not the validating wrapper.
- [x] Build async owned wrapper using existing `IsolatedProcess` / `start_isolated_process`:
  total child work <=60 seconds, cleanup on cancel/timeout, no child result before actual stop.
  Integration upstream root budget is 180 seconds. Tests prove no ADFR/Meeko/Vina call here.
- [x] GREEN producer and existing docking preparation/command cancellation regressions; then
  spec and quality review; commit only D1 files.

### D1 execution evidence and remaining source dependency

D1 review evidence (2026-10-16): initial missing-producer RED, then 95 focused tests passed.
Independent spec review reproduced two lifecycle defects: untracked physical helper cleanup and
success published after cleanup crossed the original deadline. Five new regressions failed RED,
then passed after the owner tracked actual helper calls and the original deadline covered
settlement/publication. Independent SPEC PASS (100 tests plus six actual process probes), then
QUALITY APPROVED (100 tests plus 12 additional checks). Parent joint reverse/D1 regression:
930 passed, 2 subtests passed, three third-party SWIG warnings. Worker docking/process/protocol
regression: 705 passed, 6 POSIX-only skipped. No Vina, ADFR or Meeko executed in D1.

Known real-input limit, not browser acceptance: a read-only RCSB coordinate check found 1P0I
has 66 altloc atoms and 222 non-unit occupancies; SHA-256
`851e194bf21de0ff56be206c239237e2d042c7b9498ca30faacc98749bcc5470`.
The initial strict supported PDB subset rejects this unchanged input. The first ten RCSB
BuChE candidates also do not establish an eligible pocket under that subset: 2PM8 has no
altloc/non-unit occupancy but only GOL/water/NAG/SO4 hetero compounds; the other nine have
altloc and/or non-unit occupancy. No coordinates were edited, no ligand role was inferred from
an ID alone, and no scientific failure was reclassified as PASS. D2/D7 must find genuinely
eligible, explicitly selected source evidence or retain BLOCKED and request direction.

Primary entry context: [RCSB 1P0I](https://www.rcsb.org/structure/1P0I).

Expanded read-only check (2026-10-16, user-authorized): all 113 current P06276
RCSB entries were identity-checked. Of their PDB coordinate reads, 91 failed the
existing supported subset, 21 returned HTTP 404, and only the already-ineligible
2PM8 passed coordinate parsing. No structure was modified or chosen on the user's
behalf. PDB 404 is not a claim that mmCIF or the whole entry is absent.
Further inspection of 1P0I found zero-occupancy protein atoms, missing-atom/residue
annotations and a BUA–SER198 LINK; clearing altloc alone is not a safe closure.
The user requested a preprocessing design for review before any implementation:
`docs/superpowers/specs/2026-10-16-receptor-conformer-projection-design.md`.
The maintainer subsequently confirmed option A. Its bounded implementation plan is
`docs/superpowers/plans/2026-10-16-receptor-conformer-projection.md`; P1–P5 produce
the independently tested input family, and P6 adds explicit requirements to the
D2–D6 authority/preview/consent gates below. No new browser evidence exists yet;
S10 remains BLOCKED. Later authorization permits submission/push/merge only after
the complete implementation and verification gates, not before them.

## D2 — Same-root chat draft and owned source binding

**Files:** new `src/web/docking_request.py`; narrow changes to scientific references,
TaskStore draft operations, runtime generation binding; new tests in
`tests/agent/test_web_docking_consent.py` and existing consent store tests.
The bounded model-call owner is separated into `src/web/docking_proposal.py`
with `tests/agent/test_web_docking_proposal.py`: it reuses the existing C proposal
transport/journal and model reader gate, and carries the same durable draft
budget. It does not decide whole-request admission, authenticate scientific
sources or expose another execution/HTTP pathway.

The live request coordinator is implemented in `src/web/docking_coordinator.py`
with `tests/agent/test_web_docking_coordinator.py`. It owns the one live context,
original snapshot, root/task/preparation identity, original 180-second deadline,
one `TextSegmentIssuer`, retained asynchronous operations and publication gates.
Root acceptance first makes a one-shot claim in the existing owner-bound agent
run metadata; only a returned committed receipt permits constructing its issuer.
An uncertain receipt is not recovered by reading metadata. A new coordinator
cannot restore a claimed root, even within the same runtime generation. Local
duplicate attempts do not reconstruct snapshots, issuers or proposal calls.
The initial integration slice wires actual draft reservation and the existing
proposal owner/whole-text checker; pending scientific slots remain pending and
no route is enabled until source binding and the subsequent D3–D6 gates pass.

The next source-integration slice stays in that same coordinator, covered by
`tests/agent/test_web_docking_sources.py`. Its optional server-only S03 context
is captured once before the first await, retained privately and digest-bound in
the committed root claim. It is not added to the model's authority or accepted
from an HTTP body. Only the exact PDB ID in a consistent whole-text request may
resolve against that current owner-bound observation. Resolve the accepted
target's organism through the existing supported-taxonomy mapping, then call
the fixed canonical source worker; callers provide no URLs, bytes or callbacks.
Recheck the live run, draft, model/policy/runtime generations and accepted source
before dispatch, after physical settlement, and on every published refresh.
The original root deadline covers both canonical acquisition and the separate
retained `projection_inspect` child. An unresolved missing-input proposal may
prepare source facts, but never makes inputs READY or approves execution.

Retain original bytes, independent source evidence and inspection in the live
root, never in model output or logs. Only a detached bounded source/choice
preview is public; no default protein, reference, hetero, conformer or limitation
choice is filled. The control preview contains source identity and complete
option counts, not the complete atom inventory. The same coordinator exposes
owner-bound read-only pages of the immutable inspection (fixed section names,
native offset/limit, at most 32 records and 48 KiB per page, explicit next offset).
Every page revalidates the live bindings; pagination never selects or hides a
default. The later D3/D6 UI must render all requested pages before claiming that
the user reviewed a complete choice set. Duplicate begin/status must not fetch again. Cancellation,
stale evidence or an expired budget removes publication eligibility permanently;
all actual `PreparationOwner` instances remain attached until their snapshots
prove settlement. A finished async wrapper alone must not release root context
or allow app shutdown to claim cleanup success. This slice supplies no new
execution endpoint; selection/materialization/C1 readback verification and D3–D6
remain mandatory before activation.

Initial-coordinator review ledger (in progress): the original 66-test slice
passed SPEC, then QUALITY reproduced receipt-identity and stale-draft refresh
defects. Its 24 targeted RED cases became GREEN; the expanded coordinator suite
passed 99 tests and the joint run passed 454. QUALITY rereview confirmed those
fixes but found six publication/refresh races: root authority could change while
the later draft read was pending. Six actual RED regressions were repaired by
rechecking the root after the draft read in the same retained SQL helper.
The final worker suite passed **105 focused / 513 joint tests**. Independent
QUALITY rereview APPROVED the frozen INITIAL slice: **105 passed in 25.04
seconds**, all six exact races and 22 previous P2 reproductions rejected, and
four additional lifetime/reader/writer/deadline probes passed. This approval
does not complete D2, revoke waiting drafts on close or enable any route.
The subsequent source-integration tests are also intentionally RED while that
baseline is repaired: 24 missing-integration cases, one missing-page API case
and one missing-integration ownership precondition. An earlier fixture setup
collision and an ownership-test cleanup assertion masking the absent API were
test errors, corrected before recording these missing-feature results.

The D2-to-C1 bridge uses narrow changes in `src/task_runtime/store.py` and
`runtime.py`, covered by
`tests/task_runtime/test_chat_docking_staging.py`. Before file staging the
acquired writer must compare all four numeric parameters with the fixed chat
draft. Sealing rechecks these parameters. The original waiting deadline must
still be valid at sealing; the separate 15-minute execution-consent TTL starts
at READY, as specified in section 3 of the approved September C design. Neither
waiting refinement nor status refresh renews the waiting period. Both chat and
legacy C1 retain this two-phase behavior. This bridge is not
source authentication and cannot replace the coordinator's original 180-second
upstream-work deadline, scientific selection or live-generation checks.

The request module also owns a bounded, conservative whole-text assessment
(`assess_docking_request_text`). It consumes one explicit docking imperative,
the six closed numeric/reference fields, or a single explicit SMILES/PDB pair
with the declared co-crystal pocket instruction. Unknown/negative/question/mixed
prose and conflicting or duplicate fields require clarification; no recognized
subset is extracted. This is private lexical evidence, not general NLP support,
source authentication, validated chemistry, a proposal substitute or READY.
Original text/digest and exact field spans remain available for subsequent C
proposal/ownership checks. Generic ligand labels are syntax only; scientific
input identity must still be independently verified and displayed by the
coordinator. No old route is enabled by this helper.
`validate_docking_text_proposal` recomputes that whole-query assessment and
checks the private C DTO's exact values and original query spans. Initial-text
mode requires one complete request span and one exact value span per explicit
field, with no omitted fields or unused/extra-obligation spans. Non-query spans
and refinement/observation modes require their separate owned-source handling;
this helper does not silently borrow assistant prose or authenticate references.

- [ ] RED against real temporary SQLite: original request reserves one future task ID; refinement
  retains it, owner/query digest/admission/source identity, fixed expiry and spent model budget.
  Pending source slots are not eligible artifacts. Ready/claimed input changes cannot refine.
- [ ] Add a closed request context/coordinator with original source snapshots, immutable identities,
  generation and lifecycle. Only current owner and matching revision can fill missing fields via
  store CAS; fixed changes revoke old consent and require a new explicit root.
- [ ] RED/GREEN exact S03 structure-reference resolution: current accepted evidence + actual PDB
  ID + target/species/entity/chain, no arbitrary filenames/URLs or assistant-body facts. Resolve
  verified cached or trusted downloaded bytes through existing downloader; use stable snapshots.
  Source descriptor IDs are content-addressed and owner-bound, not bearer authority.
- [ ] Attach D1 producers. Emit required missing fields/choices; do not fabricate missing cofactor,
  chain, ligand role or pocket data. Create SDF once per draft; all CPU/I/O under retained owners.
- [ ] RED closed C proposal protocol through existing selected native/JSON transport and journal:
  same whole request/source spans, exact inputs, no hidden omitted obligations, no provider retry.
  Proposal `not_c`, observation, mixed/uncertain or disabled tools cannot stage an executable request.
- [ ] Drain real model workers/reader lease before making a card actionable. Human waiting never
  holds that lease; time spent and lost-response dispatch count remain spent across refinements.
- [ ] GREEN negative docking, capability questions, false history, cross-session, expiry/revision,
  malformed proposal, duplicate fields, stale model generation and concurrency tests; review/commit.

### D2 owned-reference foundation (partial, 2026-10-16)

`OwnedDockingStructureEvidence` captures only the exact accepted live S03 result
matched to the current owner-bound SQLite checkpoint and recomputed evidence
ledger. Resolution requires one explicitly named, actually offered experimental
PDB ID with matching target/species and fresh evidence, rechecked at publication.
It returns identities only, never stored paths or download instructions, and
explicitly leaves `input_ready=False` and source authentication outstanding.

Measured TDD: initial missing-module RED; later publication-expiry RED (1 failed,
52 passed), then 53 focused tests passed. Parent combined target/owned-reference
regressions: 277 passed. Independent SPEC passed 53 tests plus 27 adverse probes;
fresh QUALITY approved with 53 tests, 55 real-SQLite probes and two compilation
checks. This is only the evidence-reference foundation: full D2 source
authentication, draft coordination, producers and model admission remain open.

### D2 durable draft/store foundation (partial, 2026-10-16)

The additive `draft_json` contract preserves one owner/root/task, original query
and source-snapshot digests, waiting expiry and spent proposal budget. The six
explicit pending/fixed slots are updated only by current revision/version CAS;
fixed-field changes revoke the old request. Pending inputs cannot enter C1
staging. A descriptor hash is still not source authentication or bearer authority.

Measured TDD batches were 62 missing-feature failures, then 4 privacy failures,
then 2 closed-keyword failures; the final draft suite passed 88 tests. Independent
SPEC passed 88 new tests, 48 selected C1/C2 tests and 41 real-SQLite probe
assertions. Fresh QUALITY APPROVED passed 111 combined selected tests (142
deselected), 305 existing TaskStore tests (1 skipped), and 84 additional
real-SQLite assertions. Parent combined draft/proposal/protocol/owned-reference
regression passed 641 tests in 29.47 seconds. The proposal files in that combined
run are separately under quality review, not approved by the D2a reviews.

The full legacy C1 suite is not claimed passed: earlier temporary-path failures
and a stalled run were not passing evidence. This is a store foundation, not
complete D2 admission, authenticated inputs, preparation or a browser result.

Follow-up parent verification at `2db29091`: the entire existing
`tests/task_runtime/test_docking_consent.py` module completed with **165 passed
in 263.74 seconds**, using `-v -x -p no:cacheprovider --tb=short
-o faulthandler_timeout=60` and a fresh short TEMP basetemp. This includes the
C1/C2/C4a controlled-execution tests; no real Vina was invoked. The earlier
interrupted/temporary-path runs remain failures to obtain evidence, not passing
runs, but this later full-module result supersedes the unresolved coverage gap.
It is still not the final full-repository test run or browser scientific gate.

### D2-to-C1 fixed draft staging bridge (2026-10-16)

Four actual failing regressions showed numeric staging parameters could diverge
from already-fixed chat slots. The acquired writer now checks them before any
file staging and again under the seal write lock. Cancellation of the new SQL
read retains the actual thread/worker until settlement; no late card appears.

An initial fifth asserted failure incorrectly conflated the waiting and READY
TTLs. The parent caught this against the explicit two-phase approved design
during integration and paused QUALITY. The erroneous expiry override was
removed, without changing `docking_consent.py` or the legacy TTL contract.
Two corrected contract tests failed before that removal, then the final 11-test
bridge plus 88 draft tests passed **99 tests in 8.38 seconds**. Three initial
test-code row-index errors were also corrected before assessing product behavior.

Earlier parent 261-test and independent 266-test passing runs covered the
superseded expiry implementation; they are retained as historical diagnostics,
not approval of the corrected final diff. Corrected-contract independent SPEC
then passed 99 bridge/draft tests, 21 selected legacy TTL/cancel/ownership tests
and 12 additional probes, using the approved two-phase design as primary
authority. Fresh QUALITY APPROVED passed 99 bridge/draft tests, 50 selected
legacy regressions and 27 additional probes, with unchanged reviewed hashes,
successful compilation and scoped diff checks. The parent latest fourteen-module
source/preparation/draft/staging/proposal/reference run passed **1,731 tests in
119.80 seconds**; it excludes the changing initial coordinator and C feedback
slices. No synthetic bytes were treated as scientific inputs or real Vina results.

### D2 whole-text assessment evidence (partial, 2026-10-16)

Initial missing-helper RED had 52 failures. Independent SPEC then reproduced
deep numeric JSON recursion and overlapping-whitespace backtracking: seven
new regressions failed before bounded scalar/triple decoding and non-overlapping
token patterns were applied. A second two-case RED isolated field-assignment
backtracking; linear assignment splitting retained original-codepoint spans.
SPEC rereview passed 114 tests plus 1,144 original probes, 217 exact-span cases
and 60 assignment negatives. Fresh QUALITY then found recognizable paths/URLs
accepted in the SMILES operand position: ten new negatives failed while four
chemical controls passed. Lexical chemical-token/explicit-path checks repaired
that gap without interpreting any operand as file access.

Final parent module: **128 passed in 5.37 seconds**. QUALITY APPROVED reproduced
128 tests and 6,683 additional checks, including 210 path/URL negatives, 66
chemical-token controls and 1,200 exact-span cases. The slowest independent
four-call performance batch was 9.686 ms; this is a regression measurement,
not an application latency guarantee. `CCC/CCC` remains literal chemical text,
not a mounted file. Existing owned-evidence behavior is unchanged. This helper
still does not validate C proposal spans, chemistry, source authenticity or
complete admission, and enables no HTTP/chat execution path.

### D2 initial-text proposal consistency (partial, 2026-10-16)

The private initial-text checker now compares the closed C proposal with the
complete original request, all six explicitly supplied fields and exact original
codepoint spans. Missing/extra/borrowed spans and invented or omitted fields
clarify; model completeness never fills server-side missing inputs. Both
`input_ready` and `execution_authorized` remain false.

Measured missing-helper RED: 22 failures. Parent consent/protocol regression
passed 601 tests in 13.65 seconds. Independent SPEC passed the same 601 tests
and 164 additional probes; fresh QUALITY APPROVED passed 157 focused tests,
340 boundary probes with zero custom-hook executions, and 3,000 validations
in 0.330 seconds. The later nine-module joint run had 1,722 passes and one
failure in the preparation-owner thread-entry synchronization test; that run
is not claimed green and its failure remains tracked in P5. This slice does
not authenticate sources or complete D2 admission, coordinator or routes.

### D2 single-use proposal owner (partial, 2026-10-16)

`docking_proposal.py` now owns one bounded native/JSON model call under the
existing model reader and transport journal. A committed permanent text-segment
claim plus initial journal and a separately committed draft-budget receipt are
both required before HTTP dispatch. Ambiguous/failed commits do not recover
authority through readback, recreate the journal or refund the spent budget.
Four accepted text opportunities are independent of structured draft revisions;
revision 4 or 5 does not itself consume a text opportunity.

The trusted root context owns one bounded `TextSegmentIssuer`. Exact recapture
returns the same capability; its invocation is consumed before the first await,
including cancellation and SQL failure. It binds root, owner, preparation,
original turn/snapshot and runtime generation. An uncommitted SQL failure burns
this local invocation, **not a durable cross-process claim**. There is no global
unbounded failed-root registry. Closing the issuer invalidates its capabilities
but never detaches already-running model/SQL work; physical work and reader
settlement remain required before return/publication.

Measured evidence: the final issuer/revision repair added 19 tests to the prior
91, after reproducing the retry/revision failures. Worker: 110 passed in 37.30s;
fresh independent SPEC: 110 passed in 24.51s; QUALITY APPROVED: 110 passed in
29.00s and eight additional probes. Parent proposal/draft/whole-text/protocol
joint run: **799 passed in 38.86s**. Compilation and diff checks passed.

Required integration gate: the actual coordinator must construct exactly one
issuer per live root, never reconstruct/retry an accepted turn, and invalidate
root contexts across runtime generation changes/restarts. Test these at the
coordinator boundary before activating either C chat route. The module does not
establish text admission, source authenticity, prepared inputs or full D2.

## D3 — HTTP and C runtime bridge

**Files:** new `src/web/routes/docking_consent_routes.py`, `src/web/docking_consent.py`;
TaskRuntime/Store narrow seams; tests/agent/test_web_docking_consent.py.

- [ ] RED using the actual FastAPI app/session middleware and real temporary runtime/store: no
  anonymous, foreign, absent/multiple/wrong Origin request can stage, refresh, approve or cancel.
- [ ] Register strict prepare/approve/revoke/status/cancel routes under
  `/api/agent/docking`; disabled constructor feature registers no executable pathway.
  Multipart is bounded before full body allocation; only existing owner draft + revision and exact
  files/parameters are accepted. Unknown/duplicate parameters, paths, URLs, owner/backend fields reject.
- [ ] Bridge prepare-only to existing C1, with source descriptors verified before staging and hash
  comparison after staging. No scientific child/backend submit from prepare; nonce only in the
  owner response with no-store. Never recover it through GET/history.
- [ ] Approval accepts exactly:

```json
{"approval_nonce":"<64 lowercase hex>","binding_digest":"<64 lowercase hex>","confirm":true}
```

Invoke existing C2 claim and local execution; fixed files/config/source/policy/generation/expiry
must match. Never allow body-supplied task/owner/runtime_config or switch execution backend.
Tests race two SQLite connections and double approval; the submit count is one and raw call at
most one, including commit-then-raise, lost-response and cancel-before-submit uncertainty.

- [ ] GET returns safe owner projection, not raw rows/manifests/tokens; unknown/foreign are
  indistinguishable. Cancel/revoke use existing immutable terminal arbitration and retain real
  physical owners; no text/model approval path exists.
- [ ] GREEN existing C1/C2/C4a, session, route framing and legacy caller regressions; review/commit.

## D4 — Finite feedback and verified success publication

**Files:** docking_consent.py, decision_runtime.py narrow shared sender interface,
runtime/store projection and completion access; new test_docking_consent_artifacts.py.

Transport review ledger (in progress): the C-only sender/physical-operation slice
had 22 initial RED cases and one subsequent mixed-close RED, then 32 passing C
tests. Independent SPEC passed those 32 and 374 existing A2/runtime tests, but
its additional mixed-path control reproduced a P1: after using C on a socket,
the A2 close entry no longer requested cancellation at its original timeout.
The original A2 timeout-then-drain behavior must be restored without adding a
second C feedback budget. This slice is not approved or activated; publication,
grants, wire/UI integration and the remaining D4 work are still outstanding.

- [ ] RED physical send catches CancelledError and remains behind a barrier: the single 5-second
  logical gate becomes undeliverable without waiting for it; owner/lock remains registered and no
  successful terminal/history/grant is published later. Release every test barrier in finally.
- [ ] Implement a C-only send owner using the same socket lock/close-once owner, token, send
  generation and monotonic deadline. Do not call A2's draining helper or append a close budget.
  Queued expiry performs no physical send. A late completion records cleanup only.
- [ ] RED/GREEN separate blocked close begins only after physical send ends; close error or
  unresolved shutdown cannot remove its owner. A2 behavior remains drain-before-return.
- [ ] RED cancellation versus confirmed completion/SQL commit races: confirmed existing success
  stays success; earlier cancellation/timeout/unknown stays non-success even on late completion.
  Publication CAS must be confirmed before terminal success or artifact grant.
- [ ] Implement deterministic completion projection from the verified real output, with original
  finite energy/units/pose_count/tool/input/config/source digests. Failure payload has no score/link.
  No model rewrite of scientific values and no raw path/stderr leakage.
- [ ] Implement bounded artifact grants (owner/task/completion/artifact/runtime, 24h non-sliding)
  and per-read verified snapshots. Verify bytes actually served, not a prechecked path reopened by
  FileResponse. Readers remain owned through disconnect and retention coordination.
- [ ] GREEN tampered/foreign/expired/replaced files, symlink/reparse/hardlink, forged completion,
  bool/NaN energy, reader disconnect, late send/grant and repeat terminal cases; review/commit.

## D5 — Alternate path and cleanup closure

**Files:** src/task_runtime/routes.py; docking_routes.py; docking_report_routes.py;
existing retention/ownership queries. Test in test_docking_consent_artifacts.py.

- [ ] RED C task IDs on generic list/get/events/cancel, legacy result/pose conversion/report/history
  delete and global history operations. Check ownership before refresh/read/delete/cancel side effects.
- [ ] Add durable C ownership classification (not ID prefixes) and safe filtering before pagination;
  foreign C records are absent, authorized cancellation uses the C coordinator. Unavailable store
  is unavailable, never legacy fallback. Preserve non-C behavior and cross-database collision checks.
- [ ] Enforce one sealed private C output root outside static/legacy roots across service,
  DockingExecution and artifact routes; never change process cwd or trust client runtime_config.
- [ ] Protect waiting consent, staging writer, dispatch/persistence owner, process lease and reader
  from cleanup. Retain execution tombstones after artifact removal; no nonce/task replay.
- [ ] GREEN existing durable task/artifact, docking history/report, staging/retention regressions;
  no unresolved authority issue may be deferred while enabling C. Review/commit.

## D6 — Existing homepage entry and inline controls

**Files:** app.py, chat_handler.py, decision_runtime.py, legacy_websocket_protocol.py,
ordinary_capabilities.py; new home/docking_consent.js; main.js and index.html narrow hooks;
new tests/home_docking_consent_test.js plus Python actual route tests.

- [ ] RED actual legacy and decision normal chat frames enter the same C coordinator only when
  enabled and eligible, preserving the original message/root/task. Non-C behavior is unchanged.
  Add strict C refine/watch frames to the existing receiver, never another receiver or `/api/chat`.
- [ ] Inject flag/policy/private root through trusted app assembly; default disabled. Capability
  facts distinguish preparation from ready/executed science; flag alone is not proof of readiness.
- [ ] RED DOM consumes captured actual `DockingView@1` route payload, mounts original-message
  missing input/preview/consent/progress/result card and validates identity/revision/generation/sequence.
- [ ] Implement fixed-label/textContent display: source/pocket/3D-input assumptions, exact files/
  hashes/grid/options/expiry, explicit confirm and revoke/cancel. Confirm sends one approved-bound
  payload only from the actual click handler; disable duplicate clicks. No nonce persistence.
- [ ] Release composer only on drained segment_complete; waiting approval is not success.
  Sticky local cancel/timeout/disconnect rejects late success; reconnect is read-only and cannot
  resubmit. Expired/lost nonce requires explicit new preparation; no silent autoapproval.
- [ ] GREEN DOM XSS/invalid event/cross-task/stale/race tests, all changed JS `node --check`, and
  existing homepage/task/completion Node and actual route Python suites; review/commit.

## D7 — Real browser and final gates

- [ ] On the agent-owned local service only, enable C after D1–D6. Preserve other running services,
  user input and unrelated assets. Confirm actual Vina and prep runtime policy/executable identity.
- [ ] Use the normal homepage and original S03 in a new controlled conversation, then choose an
  actual returned structure and send S10 in that same conversation:

```text
查找人源丁酰胆碱酯酶 BuChE（UniProt P06276），列出可用于对接的实验结构，说明物种、结构编号和共晶配体信息。
用阿司匹林 CC(=O)Oc1ccccc1C(=O)O 与上述 [实际返回并选择的结构编号] 做一次对接。使用共晶配体定义口袋；缺少该信息先说明，不要猜坐标。
```

- [ ] If role/chain/cofactor choice is scientifically ambiguous, ask the precise missing choice.
  If no verified usable input exists, retain BLOCKED and report why, never pick a fabricated pocket.
- [ ] At READY tell the user which current card/task to inspect and ask them to click the explicit
  Vina approval themselves. Do not click it with browser automation or invoke the approve API.
  While awaiting that action, finish safe independent regression/report work.
- [ ] Observe the same real task through completion; independently check actual pose output,
  energy/count/hash against UI and download. Do not run separate terminal Vina as a substitute.
- [ ] Test no-approval/revoke/duplicate/expiry UI behavior without unintended real jobs; keep
  destructive/tamper/race checks isolated in tests. Record limitations, failures and unavailable work.
- [ ] Run complete offline pytest, compileall, JS syntax/Node tests, strict health check; collect
  actual current-code results. Independent final review is required; prior baseline tests are not
  current implementation evidence. Mark each S08/S09/S10 individually from actual browser results.
- [ ] Update existing validation report and PR #209 with exact source/test files, commands, results,
  commit/review scope and unresolved gates; stage explicit files and push the same
  branch. Keep Draft while any gate is incomplete; the later authorized merge
  requires all completion gates and remote checks/reviews to pass.

## Progress and review ledger

- [x] User approved the written design, current-session implementation and manual Vina approval.
- [x] Plan self-check: D1–D7 cover both profile entry, upstream inputs, C3/C4b/C5/C6/C8 and no bypass.
- [ ] D1–D7 implemented/tested/reviewed; record actual RED/GREEN and browser evidence as executed.

The plan itself is not evidence of scientific execution or new passing tests.
