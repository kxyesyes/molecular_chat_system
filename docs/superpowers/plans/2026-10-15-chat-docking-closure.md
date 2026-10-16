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
The subsequent source-integration tests initially failed RED while that
baseline was being repaired: 24 missing-integration cases, one missing-page API case
and one missing-integration ownership precondition. An earlier fixture setup
collision and an ownership-test cleanup assertion masking the absent API were
test errors, corrected before recording these missing-feature results.

The source slice has now been implemented for independent review. Two actual
source-invalidation/draft-read races and two stale duplicate-preview cases were
reproduced RED, then corrected without a second source fetch or model dispatch.
An original-monotonic-deadline regression also caught a 30 ms reset before the
child; both children now use the minimum including the original root deadline.
The final parent source/initial-coordinator suite passed **135 tests in 70.05
seconds** (30 source, 105 coordinator), plus two-file in-memory compilation and
scoped diff checks. A preceding 488-test joint pass was before the last duplicate
fix and is not approval of the final diff. Independent SPEC and QUALITY remain
required. All source providers in these integration tests are synthetic, even
when the actual child/compiler/inspection and SQLite lifecycle execute; they
are not a current browser source selection or a real S10 result.

Independent source SPEC then reproduced four draft-revocation races during the
source read, despite 135 passing scoped tests. Four exact new regressions failed
RED. The repair uses one short retained, read-only `BEGIN IMMEDIATE` guard with
the agent database as main and TaskStore attached, excluding writers in both
databases during the existing resolver/draft/root reads. It writes no rows and
is not a cross-database publication transaction. Locks are released before any
child or human wait; a contended/uncertain read remains unavailable. Tests cover
both directions of invalidation, exclusion/release of both writers, and actual
child dispatch outside the guard. The repaired parent suite passed **140 tests
in 70.63 seconds** (35 source, 105 initial). Independent SPEC rereview PASS:
**140 tests in 66.87 seconds** and 13 additional race/ownership probes. Fresh
QUALITY APPROVED: **140 tests in 66.56 seconds** and 10 further lock, repeated
cancellation, paging, invalidation and physical-settlement probes. Both reviewers
confirmed unchanged frozen hashes and successful scoped compilation/diff checks.
Later C1 code must not nest this guard inside a reverse-order TaskStore write
transaction. Approval covers source inspection only, not choices, C1 or activation.

The following explicit-selection slice uses `select_source` on that same live
coordinator, covered by `tests/agent/test_web_docking_selection.py`. Before its
first await it captures a bounded native closed selection; all five selection
fields are mandatory and set-like ordering does not replace a fixed choice.
Only the retained canonical source feeds the fixed `projection` child. The
original explicit SMILES feeds one `ligand` child, with every physical owner
retained and live source/root/draft/generations rechecked after each return.
Duplicates reuse outputs; changed choices require a new root, not a second
projection or embedding. No model/caller source dictionary or derivative is used.

Private receptor/ligand descriptors cover original evidence, projection kind,
selection, derived bytes, pocket rule and ligand-modeling provenance. Their
content-addressed IDs fill only the corresponding pending draft slots via the
existing revision/version CAS; original explicit numeric values may also be
fixed, never overwritten by suggestions. An accepted refinement must have the
exact planned identity/slots, unchanged waiting expiry and unchanged spent budget;
an uncertain receipt cannot be recovered by reading the row. A bounded detached
preview shows hashes and visible parameter suggestions, but numeric suggestions
remain pending and no C1, nonce or execution is exposed by this slice.

Selection TDD in progress: **20 missing-method RED** cases became **20 passed in
75.10 seconds** with actual retained children, actual projection/RDKit, and real
SQLite on synthetic independently authored source files. Four additional actual
RED cases then reproduced invalidation during final preview encoding and altered
proposal budgets in a refinement receipt/same-version read. Their repair passed
**164 joint tests in 165.23 seconds** (24 selection, 35 source, 105 initial), plus
two-file in-memory compilation and scoped diff checks. Independent SPEC PASS:
**164 tests in 180.23 seconds** plus 16 receipt, invalidation, actual child
cancellation, order-invariance and descriptor-coverage probes. Frozen hashes
were unchanged. Fresh QUALITY reproduced one cancellation-propagation defect:
the changed-choice branch drained actual child cleanup but ignored cancellation
of its caller. An actual projection/held-close regression failed at the caller's
cancelled state, then passed after honoring the drain result and retaining
release in `finally`. QUALITY re-review APPROVED: two regression/control tests
passed, the independent three-way physical-close harness propagated cancellation
and retained resources correctly, and the prior 164-test/probe evidence stands.
Parent full joint rerun: **165 passed in 160.84 seconds**. Synthetic modeling is
not current S03 selection or a real scientific S10 result.

The separate waiting-draft cancellation seam is
`TaskStore.close_chat_docking_draft`, tested in
`tests/task_runtime/test_docking_draft_close.py`. It CAS-matches owner, task,
root, origin, revision and version, revokes only a settled AWAITING_INPUT chat
draft, and returns a receipt only after commit. It cannot cancel PREPARING,
READY or execution, alter expiry, or recover an uncertain receipt. Coordinator
integration must retain its actual SQL and may not infer success from a finished
wrapper. Worker evidence: one absent-method RED, **123 focused passed / 503
joint passed with 2 platform skips**. Independent SPEC PASS: **123 focused /
376 joint passed**, plus 18 real-lock, failed-COMMIT and boundary probes with
unchanged hashes. Fresh QUALITY APPROVED: **123 focused / 674 adjacent passed
with 3 platform skips**, plus 18 lock-race, failed-COMMIT, lost-acknowledgement,
identity and integer-boundary probes. Reviewed file hashes remained unchanged.
Parent joint verification with artifact readback: **194 passed / 4 skipped**.
This approval excludes coordinator integration and physical-settlement handling.

Parameter confirmation integration is now under QUALITY review:
`tests/agent/test_web_docking_parameters.py` expanded the original 14 cases to
**18 missing-method failures in 6.05 seconds**, then **18 passed in 81.04 seconds**.
`confirm_parameters` captures closed native numeric values before suspension,
binds current references/revision, checks verified pocket or original explicit
values, and fills only pending slots through the existing CAS. Duplicates reuse
the accepted state; changed fixed parameters require a new request. It rechecks
source authority after final preview encoding and never stages or issues a nonce.
Parent joint regression passed **183 tests in 267.46 seconds**. SPEC reproduced
two boundary defects: a stale changed confirmation could close the newer root,
and signed zero produced a different duplicate key. Added two actual RED cases
(after correcting an ambiguous test query), restricted the old-revision exception
to identical retries and normalized signed zero. The revised **20 tests passed in
127.22 seconds**. SPEC re-review PASS: four independent revision/all-fixed/duplicate
probes plus native-capture checks; prior eight receipt/invalidation probes stand.
QUALITY reproduced integer rounding above 2**53 in the float-based duplicate
normalizer. Two actual-source/coordinator regressions failed in 11.02 seconds;
normalization now preserves native integers and maps only exactly integral
floats to integers (including signed zero). The revised **22 tests passed in
114.93 seconds**. QUALITY re-review APPROVED: two independent SQLite numeric
regressions, 20 preservation probes and 400 canonical-key comparisons passed;
prior 11 cancellation/race/invalidation checks stand. Frozen hashes and two-file
compilation/diff checks passed. No preparation/HTTP/real-science claim is made.

`tests/agent/test_web_docking_close.py` expanded the original four failures to
**7 failures in 18.22 seconds**, then **7 passed in 9.56 seconds** after adding
the retained close finalizer. It joins actual SQL, shares concurrent closes,
retains uncertain initial/close receipts, and refuses durable owner release
before physical source cleanup. Two further RED cases found pending claim SQL
was not reported and normal model cancellation lost the already-acknowledged
budget version. The coordinator now records only the actual budget CAS receipt
(exact next version and budget increment) before model dispatch and reports
pending root work even before draft reservation. Ordinary reads cannot adopt
that receipt. All nine close cases passed in the next joint run; 26 original
tests rejected the new optional `cleanup_pending` field in their strict view
helper, now explicitly allowed only as native `True`. The five-file regression
passed **196 tests in 294.52 seconds**. Independent SPEC PASS reran 23 targeted
tests in 48.89 seconds and 13 isolated budget, receipt and concurrent-close
probes, with unchanged three-file hashes. Fresh QUALITY APPROVED found no
reproducible ownership/receipt/cancellation defect, and 13 independent probes
passed. Its five-file run had **195 passed / 1 failed in 875.05 seconds**: one
reservation-identity test exited before attempting reservation. A separate
coordinator rerun passed all 105 tests, and that case passed 20/20 additional
runs. The initial failure's cause remains unresolved; this is not a claim of
an entirely green independent full run. All three frozen hashes were unchanged.
Close must never free owner capacity while a physical preparation child remains
unsettled; lack of a confirmed close receipt is not settled cleanup.

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

For the later projection readback gate, add the narrow
`DockingInputStager.read_verified_input_bytes(task_id, manifest_locator)` method
in `src/task_runtime/staging.py`, tested in
`tests/task_runtime/test_staging_readback.py`. It returns the current verified
manifest and immutable receptor/ligand bytes under the existing stager lock,
using the existing descriptor-safe reads and rechecking their size/hash against
that manifest. It creates no execution snapshot, lease, grant, path-based
capability or scientific authority. No caller callback or arbitrary locator is
accepted. C1 must subsequently run explicit projection verification on these
actual readback bytes before READY while retaining all preparation ownership;
the byte-read helper alone does not implement that gate.

Readback helper implementation is frozen for review: 82 missing-method RED
failures (four platform skips) became **78 passed / 8 skipped**. Independent
SPEC repeated that baseline RED and current GREEN, passed **390 tests / 21
platform skips** across readback and related staging/execution regressions, and
118 additional tamper/boundary probes. Symlink privilege skips are not claimed
passed. Existing resolver equality semantics were preserved; this helper does
not add a strict-native-locator guarantee. Fresh independent QUALITY APPROVED:
baseline **82 failed / 4 skipped** for the absent method, current readback/staging
**253 passed / 16 skipped**, plus **42 independent probes**. Both frozen hashes
remained unchanged. Platform skips are not passes. This remains only the byte
readback helper; it is not the subsequent C1 projection-verification gate.

The source integration gate began with **one failure in 4.67 seconds**:
self-consistent fixed chat metadata without a live verified source owner could
reach C1 READY. The new concrete server-only input context now binds the actual
coordinator/root, accepted confirmation, private producer outputs and runtime.
It is consumed once before asynchronous work; missing contexts deny chat drafts.
Actual staged receptor/ligand bytes are read twice around the retained
`projection_verify` child, and PREPARING snapshots are compared under the existing
TaskStore write lock. Source validation holds Agent DB before TaskStore, without
a model reader/database lock across child work. Unsettled child cleanup retains
the stage and capacity; a later close may settle only after the physical owner.
The application feature remains disabled: this is an internal preparation seam,
not completed Web READY/close/approval lifecycle integration or execution authority.

TDD: **19 failed in 3.79 seconds**, then 19 passed in 70.40 seconds. Eleven old
metadata-only staging fixtures failed the intentional new gate; all eleven were
adapted to actual confirmed synthetic source contexts, retaining TTL, parameter,
clock-expiry, cancellation and seal-drift coverage. Metadata-CAS tests now supply
their actually accepted local receipt, not a recovered source authority. Further
RED probes found four changed-producer captures, two root-close/SQL windows,
three expiry reason-code mismatches, one cancellation while revocation was held,
and one native identifier hook. After minimal fixes, the four-file gate/staging/
draft regression passed **130 tests in 173.24 seconds**. One additional close
race during parameter validation failed in 6.08 seconds (staging still began
after close); the existing runtime checkpoints now also check the concrete
context's local liveness. The rerun passed **131 tests in 173.78 seconds**;
the entire legacy C1/C2/C4a file passed **165 tests in 256.37 seconds**.
Eight-file memory compilation and diff checks passed. Independent SPEC PASS
reran 131 tests in 181.81 seconds plus 15 independent native-type, fake-context,
snapshot, lost-acknowledgement, second-readback, digest, actual-lock-order and
single-cancel probes in 62.88 seconds. All eight frozen hashes stayed unchanged.
Fresh QUALITY reproduced one cancellation window after the final source SQL
check completed but before its awaited return resumed. Parent reproduced it:
**1 failed in 6.27 seconds**, including an actual projection process starting
after cancellation. A synchronous local liveness check immediately before child
dispatch passed that case and both readback-cancel controls: **3 passed in 13.42
seconds**. The full parent rerun passed **132 tests in 194.53 seconds**.
QUALITY re-review APPROVED the internal C1 gate: six tests in 28.01 seconds,
including five independent adjacent return-window probes, unchanged eight-file
hashes and a clean diff check. Cancellation/root close prevented further child
dispatch and seal; physical cleanup settled. This is not approval of future D3.
The five-file coordinator regression passed **196
tests in 236.03 seconds**, verifying the narrow shared-source-check extraction.
The separate next lifecycle tests in
`tests/agent/test_web_docking_staging.py` are intentionally **3 failed in 2.27
seconds** for missing Web prepare, one-time nonce disclosure/READY TTL and close
coordination; no claim is made that this future integration has been implemented.
None of these synthetic fixtures counts as real S10 evidence.

Integration constraints for that remaining gate: use a concrete server-only,
one-use context bound to the exact live root, locally accepted confirmation and
private producer outputs, not a client dictionary/hash or a callback returning
`True`. Validate actual staged receptor/ligand bytes; run the existing retained
`projection_verify` child on the staged receptor and compare the exact modeled
ligand bytes. Attach its physical owner before dispatch and keep it through
uncertain/late cleanup. All work fits the smaller of C1's 30-second deadline and
the root's remaining original budget. Final live-source/seal coordination must
use Agent DB before TaskStore lock ordering, never call the two-database source
read guard from inside a TaskStore write transaction, and never hold a database
or model-reader lease across child work or human waiting. READY starts its own
existing consent TTL; upstream work expiry must not shorten that approval period.

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

The internal same-root preparation lifecycle (no HTTP/approval activation) now
has **22 passing synthetic tests in 95.38 seconds**. The original three missing
method failures became three passes in 15.16 seconds. Two additional RED cases
prevented premature READY publication during final SQL validation and invocation
of a non-concrete runtime; the 12-case run passed in 53.16 seconds. Three more
RED cases detected closed/closing/replaced-policy runtime refresh, followed by
15 passes in 65.57 seconds. Additional checks cover fresh context capture,
nonce-free duplicate/status, independent READY expiry, wrong owner/revision/refs,
actual staging/final-SQL cancellation and lost revocation acknowledgement.
The latter keeps its root/stage and does not retry or recover authority from a
REVOKED readback. One test initially called a nonexistent stager path helper;
that test-only error was corrected to inspect its actual retained manifest.
The adjacent regression passed **380 tests in 392.49 seconds**. Independent
SPEC then reproduced a concurrent refresh window: while guarded status SQL was
waiting, another status/prepare could expose cached READY after source/model
invalidation. Two parent regressions with real SQLite write locks failed in
10.96 seconds. READY projection now exposes a non-ready BUSY view while its
retained validation task is pending. The full lifecycle rerun passed **24 tests
in 114.67 seconds**; SPEC re-review PASS with unchanged frozen hashes. The
original INITIAL coordinator plus these cases passed **129 tests in 127.49
seconds**. QUALITY independently ran 24 tests and nine additional probes, then
reproduced a first-caller handoff window: a pending concurrent READY refresh
produced a BUSY view, but the initial prepare caller reattached its old nonce.
Two parent real-SQLite reproductions failed in 11.07 seconds. Nonce disclosure
now also requires unchanged task ownership and an already validated READY
response. The 26-case rerun passed **26 tests in 116.92 seconds**; bounded
QUALITY re-review PASS independently verified six focused cases and unchanged
three-file hashes. The concurrent-refresh nonce finding is closed. This approval
is limited to the internal Web/C1 lifecycle, not HTTP or C2 execution.

The original strict multipart HTTP prepare contract asks for two uploaded files,
while the integrated chat producer now owns and freezes those bytes server-side.
The user explicitly approved on 2026-10-16 a closed root/revision/receptor_ref/
ligand_ref JSON prepare request instead. This narrow amendment replaces file
uploads only for this chat preparation route; browser bytes/paths/URLs cannot
replace the already authenticated producer outputs. Runtime Vina approval still
requires the user's explicit click on the current card. No executable HTTP
route is activated yet.

The prepare-only HTTP module has **10 tests passing in 40.68 seconds**, using
actual FastAPI/session middleware, SQLite, the confirmed coordinator and C1.
Four initial missing-module failures took 2.39 seconds. Two framing RED cases
showed mismatched Content-Length and Content-Encoding reaching staging; both
are now rejected. A query-field RED case (1 failed / 2 passed in 15.31 seconds)
is also fixed. Cases loop through unknown/duplicate/non-native fields, wrong or
absent/duplicate Origin, foreign/unknown roots, stale references, unavailable
session storage, anonymous new sessions, disabled construction, and bounded
chunked body consumption. Two test-fixture mistakes were corrected without
changing production semantics: existing middleware emits `private, no-store`,
and HTTPX seed cookies need the same host domain as the middleware's cookie.
The joint session regression passed **189 tests / 2 skips in 45.42 seconds**;
independent SPEC PASS reran **10 tests in 40.56 seconds** and 27 additional
parser-boundary probes with unchanged two-file hashes. Fresh QUALITY APPROVED:
10 tests in 40.86 seconds, 58 parser/framing/stream checks and 25 additional
authorization/dependency/status mapping checks (stub responses for mapping),
with unchanged two-file hashes. No app
wiring, approval/cancel/status/artifact HTTP or UI activation is claimed here.

The internal live-source C2 bridge is now frozen for independent SPEC review.
Its first isolated run observed four failures (nonce-only chat execution was
accepted and the original-context seam was absent). The bridge now requires the
exact C1 context/runtime and acknowledged READY record; claim and each execution
guard own a short model reader and acquire Agent DB before TaskStore. A first
integration failure exposed an unguarded model capture, followed by a conflicting
SQL keyword; both were diagnosed and corrected before a 14-test passing run.
The reader is released before the synthetic raw boundary. A separate real RED
showed consumed-consent expiry incorrectly stopping a valid execution; the root
now uses fixed execution budgets only after dispatch. Two real SQLite receipt
tampering cases then failed and passed after exact version/cleanup checks.
Initial focused run: **18 passed in 81.88 seconds**, including actual writer-lock
cancellation and retention of an acknowledged-but-invalidated UNKNOWN claim.
Independent SPEC repeated 18 passes but found three gaps: the final result SQL
guard could be detached on backend-owner cancellation, an earlier CLAIMED
snapshot could be restored at dispatch, and reduced receipt comparison omitted
persisted policy/generation fields. Parent regressions reproduced all six cases
as failures in 30.00 seconds. The final guard is now explicitly retained,
shielded and drained by cancellation/settlement/shutdown; live rows use complete
frozen facts plus phase-specific state/dispatch/capacity checks. The first broad
adjacent run was **311 passed / 63 failed in 496.76 seconds**: the failures hit
the old exact signature prerequisite, which did not include the new optional
server-only context. The signature test now explicitly includes that keyword
with default None; every original required keyword/default check remains.
Focused review-fix/legacy controls passed **9 tests in 30.25 seconds**. The full
source module passed **24 tests in 112.54 seconds**; existing C2/C4a passed
**61 tests / 104 deselected in 31.38 seconds**, and private output passed all
**46 tests in 11.89 seconds**. No C4a skips were reported.
SPEC re-review PASS: nine focused/legacy controls in 32.55 seconds and six
independent probes in 26.56 seconds; repeated cancellation/shutdown retained
the blocked physical guard, capacity and model reader until release. All five
hashes stayed unchanged. Fresh QUALITY repeated 24 source tests and 61 C2/C4a
tests, then reproduced a P1: post-claim database readback could replace the
original 420-second execution deadlines. Its synthetic probe started raw work
at 700 seconds and returned success at 950 seconds after extending both columns.
Six parent SQLite tampering regressions (each deadline at readback, dispatch,
and result) failed in 30.42 seconds. An earlier fixture run used an unsupported
repository-local staging directory and is not behavioral RED evidence. The
context now retains the exact clock samples used by the actual claim UPDATE,
derives fixed limits only after commit acknowledgement, and rejects later
deadline differences before submission and at live execution guards. Lost
acknowledgement does not recover authority through readback. Six regressions
plus two valid controls passed in 37.83 seconds. The full source suite, including
a late-ack clock control, passed **31 tests in 143.22 seconds**; legacy C2/C4a
passed **61 tests / 104 deselected in 27.75 seconds**. QUALITY re-review APPROVED:
five independent probes passed in 22.36 seconds. Both original extended-budget
attacks now prevent submission/raw execution and retain acquired UNKNOWN
ownership; lost ACK cannot mint budgets/replay and delayed ACK retains original
deadlines. All five frozen file hashes were unchanged.
No new execution transport or application activation is part of this slice.

The next Web/C2 lifecycle tests separately observed **2 failures in 7.31 seconds**:
the coordinator approval seam is absent, and closing a CLAIMED root does not yet
signal its actual C2 owner. That next slice is intentionally still RED; the
existing C1 close path must not be used as C2 execution cancellation. This is a
release gate, not a completed cancellation claim.

The internal Web/C2 lifecycle subsequently observed **8 failures in 32.76
seconds**, then **8 passes in 32.57 seconds** after owned explicit-proof
approval, duplicate coalescing, safe execution progress and exact C2 close were
added. Close signals the retained execution synchronously before draining its
real owners; acquired/uncertain unresolved executions retain the heavy root and
do not fall through to C1 revocation. A further source-refresh regression was
**1 failed / 2 passed in 16.02 seconds**; active status now checks live source
under its short model/SQL guard. Finished execution is only RESULT_PENDING,
not scientific publication. Joint execution/staging verification passed
**37 tests in 152.06 seconds**. One additional blocked physical status read test
passed in 12.13 seconds: the five-second feedback observation returned while
the read remained owned, and close could not release the root until that read
actually completed. SPEC PASS independently verified pre-binding cancellation
retains/drains its physical read, invalid proofs during pending approval cannot
coalesce into authority, and completed progress does not renew execution budgets
or claim new source/publication authority. Three probes passed (two within a
14.40-second run and one corrected-harness rerun in 5.13 seconds); harness-only
collection/spawn/monkeypatch failures are not behavioral evidence. Frozen hashes
were unchanged. Fresh QUALITY APPROVED independently passed all 12 lifecycle
tests in 56.53 seconds, verified unchanged two-file hashes, and found no
actionable in-scope issues. Parent adjacent C2 live-context plus preparation
HTTP regression passed **41 tests in 179.51 seconds**. HTTP controls, global C4b transport
deadline, D4 publication and activation are
still separate unfinished gates.

**Files:** new `src/web/routes/docking_consent_routes.py`, `src/web/docking_consent.py`;
TaskRuntime/Store narrow seams; tests/agent/test_web_docking_consent.py.

- [ ] RED using the actual FastAPI app/session middleware and real temporary runtime/store: no
  anonymous, foreign, absent/multiple/wrong Origin request can stage, refresh, approve or cancel.
- [ ] Register strict prepare/approve/revoke/status/cancel routes under
  `/api/agent/docking`; disabled constructor feature registers no executable pathway.
  The 2026-10-16 approved chat prepare amendment accepts only bounded closed JSON
  `{root, revision, receptor_ref, ligand_ref}` for the existing owned confirmed draft.
  Duplicate/unknown fields, bytes/files, parameters, paths, URLs and owner/backend
  overrides reject before staging. The server supplies its frozen exact files/parameters.
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
The original A2 timeout-then-drain behavior was restored after three mixed-path
RED failures and three unchanged baseline passes, without adding a second C
feedback budget. Independent SPEC rereview passed **412 tests** (38 C/interop
and 374 A2/runtime) plus its independent timeout/cancel/late-close controls.
Fresh QUALITY APPROVED passed **327 tests**, including all 38 C tests, and a
12/12 concurrent cancellation/timeout/multiple-waiter/shutdown matrix. Each
regression suite reported only seven existing dependency deprecation warnings;
frozen hashes were unchanged throughout review. This approves the transport
slice only. No C call site is activated; publication, grants, wire/UI integration
and the remaining D4 work are still outstanding.
Parent pre-commit C/interop rerun: **38 passed in 10.54 seconds**, with those
same seven dependency warnings and unchanged reviewed hashes.

The D4 per-read byte foundation adds only
`DockingCompletionStore.read_verified_artifact` in `src/task_runtime/completion.py`,
covered by `tests/task_runtime/test_completion_artifact_readback.py`. Its fixed
artifact IDs (`pose`, `extra-00` through `extra-07`) index the verified completion,
never a caller path. Before and after the actual secure byte read it compares
input/config identities and the expected canonical completion digest; final
invalidation also rejects. The returned immutable bytes are themselves size/hash
verified. The digest is SHA-256 of sorted, compact, ASCII-escaped canonical JSON
of the parsed completion's `to_dict()`, with nonfinite JSON disallowed. The helper
adds no grant, TTL, reader ownership or HTTP authority; those D4 integrations
remain required. Worker TDD: **71 missing-method failures / 4 platform skips**,
then **71 passed / 4 skipped**, and **191 joint passed / 6 skipped**. Symlink/
reparse privilege skips are not passes; hardlink negatives passed. Independent
SPEC PASS reran **191 passed / 6 skipped** plus 22 identity, order, size,
late-invalidation and native-boundary probes. Fresh QUALITY APPROVED reran
**191 passed / 6 skipped** plus 12 short-read, EOF, I/O error, inode-replacement,
other-artifact corruption and removed-manifest probes. Both reviews verified
unchanged hashes; existing producer/parser/schema behavior is unchanged.
Parent verification with the separate draft-close tests: **194 passed / 4 skipped
in 22.98 seconds**. This approval covers byte readback only, not D4 authorization.

- [x] RED physical send catches CancelledError and remains behind a barrier: the single 5-second
  logical gate becomes undeliverable without waiting for it; owner/lock remains registered and no
  successful terminal/history/grant is published later. Release every test barrier in finally.
- [x] Implement a C-only send owner using the same socket lock/close-once owner, token, send
  generation and monotonic deadline. Do not call A2's draining helper or append a close budget.
  Queued expiry performs no physical send. A late completion records cleanup only.
- [x] RED/GREEN separate blocked close begins only after physical send ends; close error or
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

The generic-task sidecar is frozen for independent SPEC then QUALITY review:
`src/task_runtime/store.py` adds durable C membership and optional pre-pagination
filtering; `src/task_runtime/routes.py` denies C list/get/events/cancel through
either known database before generic control. No task-ID prefix, owner, state or
task-row presence substitutes for consent membership. Missing/broken configured
storage is unavailable, not legacy fallback. New actual-session/SQLite tests in
`tests/task_runtime/test_docking_consent_generic_routes.py` observed **55 failed /
3 passed** before implementation; the expanded suite plus ownership/store
regressions passed **408 tests with 4 skips**. Worker compileall and diff checks
passed. This does not close legacy docking/history/artifact or retention paths.
Parent ownership regression initially passed **103 tests / 3 skips**. SPEC then
reproduced a real cross-database race: a normal consent-only reservation committed
during the owned read bypassed the initial membership check. Twelve actual-route
regressions failed with HTTP 200 instead of 404, then a post-read/collision
membership recheck passed **115 tests / 3 skips in 73.38 seconds**. Four more
actual RED cases reproduced consent reservation during the opposite-store list
collision lookup. A post-lookup membership recheck retained bounded refill;
the final parent run passed **119 tests / 3 skips in 61.96 seconds**. SPEC
re-review PASS: 18 focused races/refill cases and four independent list probes.
Fresh QUALITY APPROVED: **30 passed / 2 skipped / 90 deselected in 12.42
seconds**, plus 12 independent fail-closed probes for nonboolean classifiers,
damaged SQLite schema and classifier failure after ownership reads. The skips
are no-runtime cases; all three frozen hashes remained unchanged. This approval
is limited to generic-task isolation, not the remaining D5 paths.

The separate legacy-route slice is not yet approved. Worker TDD first observed
64 failures / 4 passes, then 199 passes / 2 platform skips. Five old durable
artifact fixtures lacked a real consent classifier; their owned-result stub now
inherits an isolated TaskStore, preserving the synthetic artifact assertions;
the six compatibility tests passed in 4.26 seconds. Independent SPEC reproduced
non-C cross-database collisions bypassing the legacy-hit branch and a same-total
history mutation yielding a mixed page view. The follow-up worker observed
9 failures / 5 passes, plus 4 failures / 1 pass for strict snapshot checks, then
**241 passed / 2 skipped in 80.96 seconds**. Legacy membership/collision checks
now cover both databases before reads/deletes, and a narrow history helper reads
one owner-only metadata snapshot under the existing thread/process locks. Its
detached view is capped at 10,000 owned rows. SPEC re-review PASS independently
verified the two fixes, with **46 passed / 100 deselected**, six-file compilation
and unchanged hashes. Whole-JSON loading remains an inherited resource
limitation of the existing index; there is no new byte/raw-row bound, and this
is not claimed otherwise. Fresh QUALITY found two further reproducible P1
issues: a runtime binding swap after an owned read caused reclassification to
miss the actual captured authority, and cross-owner duplicate history IDs could
pass the owner-filtered snapshot and let clear delete foreign metadata. A focused
worker reproduced authority failures (12 failed / 12 passed, plus two binding
failures) and history failures (14 failed / 5 passed), then fixed classification
of the captured runtime and identity of the current binding. History duplicate
checks now count only the bounded owned IDs across the same locked raw snapshot,
before any delete. All 45 added cases passed; the joint regression passed
**286 tests / 2 platform skips in 112.17 seconds**. Six-file compilation and
diff checks passed. QUALITY re-review APPROVED: 10 independent probes and
**76 focused tests in 19.04 seconds**, six-file memory compilation/diff checks,
and unchanged frozen hashes. Both P1 findings are closed; no blocker remains
within this legacy-route isolation slice.
This is not completed
private-output-root or retention integration.

The private-output-root slice is separately frozen for review. It binds a
constructor-only private local executor, leaves the legacy dependency unchanged,
and rejects overlap with project/cwd legacy output, static and staging trees.
It does not exclude the entire project tree. Initial worker TDD observed 28
failures / 1 pass, then 36 passes. The existing C4a fixture had replaced its
executor after construction: one actual failure preceded the fixture-only
construction change, then all 14 C4a tests passed without skips or weakened
assertions; the C2/D5 selection passed 83 tests. Independent SPEC ran 201 tests
in 266.22 seconds and found stager/completer object identities were not sealed:
same-root replacement reached synthetic success. Worker regression reproduced
8 failures / 2 passes, then all 46 focused tests passed after retaining and
checking both original objects. Bounded SPEC re-review PASS: 10 tests, six
replacement probes, two adversarial identity probes and a positive synthetic C2
control. Fresh QUALITY APPROVED: 46 tests in 10.95 seconds, all 14 C4a tests in
12.13 seconds without skips, and 11 independent private-root/dispatch/result
probes; all four hashes stayed unchanged. These are synthetic boundary tests,
not real Vina execution, retention,
artifact-grant or UI acceptance evidence.

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
