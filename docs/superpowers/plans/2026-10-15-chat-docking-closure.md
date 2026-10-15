# Chat docking consent and real Vina closure implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close S10 from the existing normal chat through genuine input preparation, user-clicked consent, one real Vina execution and authorized verified results.

**Architecture:** A shared C request coordinator serves the existing legacy and decision entry points without migrating profiles. Bounded upstream producers create owned artifacts and a verifiable pocket proposal; existing C1/C2/C4a retain execution authority. C4b feedback and C5 artifact/alternate-path closure must pass before activation.

**Tech Stack:** Python/FastAPI, existing SQLite TaskRuntime and isolated-process ownership, RDKit/PDB, Vina/ADFR/Meeko, Jinja/native JS, pytest/Node and real browser UI.

---

## Authority and delivery

Approved spec: `docs/superpowers/specs/2026-10-15-chat-docking-closure-design.md` plus the
unchanged security/lifecycle provisions of `2026-09-27-docking-consent-integration-design.md`.
Baseline `a59492ab`; same branch/PR #209 as reverse-target work; no merge.
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

## D2 — Same-root chat draft and owned source binding

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
That new scope is pending approval; S10 remains BLOCKED.

**Files:** new `src/web/docking_request.py`; narrow changes to scientific references,
TaskStore draft operations, runtime generation binding; new tests in
`tests/agent/test_web_docking_consent.py` and existing consent store tests.

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
  commit/review scope and unresolved gates; stage explicit files, push same branch; keep Draft/no merge.

## Progress and review ledger

- [x] User approved the written design, current-session implementation and manual Vina approval.
- [x] Plan self-check: D1–D7 cover both profile entry, upstream inputs, C3/C4b/C5/C6/C8 and no bypass.
- [ ] D1–D7 implemented/tested/reviewed; record actual RED/GREEN and browser evidence as executed.

The plan itself is not evidence of scientific execution or new passing tests.
