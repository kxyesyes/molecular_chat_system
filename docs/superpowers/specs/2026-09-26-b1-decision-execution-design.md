# B1 actual decision execution and source closure

Baseline `66039eb`, 2026-09-26. User delegates recommended choices and requests
completion through package8, not deployment. This is the functional B1 core
increment in the previously approved dynamic bindings design, not a substitute
for its normal Web entry or B2/C/P8 gates.

## Choice

Extend the existing ModelDecisionLoop and WorkflowRunSession using an explicit
server-only `binding_profile=B1_PROFILE_REVISION` constructor argument. Default
None retains existing v1/original-four behavior byte-for-byte where tested.
B2/unknown profiles reject until implemented, never downgrade. No browser field,
parallel loop, static Planner dispatch or automatic ordered tool sequence.
Alternatives rejected: globally extending INITIAL_TOOLS breaks old authority;
building another B execution engine duplicates recovery and validation.

This increment exercises the real loop, real registry/adapters/Session/ledger,
actual RDKit and temporary RAG/reverse sources. Web assembly/admission remains
closed for B until the immediately following normal-entry integration. Do not
claim normal /ws B behavior or change existing application startup here.

## Boundaries

Production scope:

- contracts/decision_bindings.py: immutable resolved-input and proof views, beside
  existing closed syntax; no I/O or chemical parsing at import.
- new contracts/binding_requirements.py: closed v2 obligations and explicit parser.
- new harness/decision_bindings.py: actual input/role closure, record handles,
  action identity, current-source checks and acceptance; retain legacy helpers.
- harness/decision_loop.py, decision_policy.py, decision_continuation.py,
  decision_execution.py, decision_inputs.py: explicit B dispatch and replay only.
- runtime/run_session.py, evidence/ledger.py: optional pre-ledger preparation hook,
  propagation and integrity of the new binding proof. Existing defaults unchanged.
- tools/reverse_target_tool.py and tools/rag_search_tool.py: load-free current
  observation verification against their already attached server sources.

All paths above are under src/agent. No predictor/RAG-service/scoring/model-client,
static Planner, Web app, DB schema or registry/factory change. If evidence requires
another file, parent reviews the specific dependency rather than implicit expansion.
Tests: new tests/agent/test_decision_dynamic_bindings.py,
test_decision_binding_requirements.py, test_decision_binding_continuation.py;
only additive boundary cases in existing consumer/Session tests when necessary.

## Explicit v2 requirements, never inferred from model actions

### Task4B consumed-reference dispatch seam (source-grounded clarification)

The four Task4A-input incidental-selection characterization tests preserve an
actual integration gap: `WorkflowRunSession._reject_unavailable_reference`
unconditionally revalidates `context.resolved_molecule`, both for checkpoint
reuse and immediately before dispatch. The legacy loop also checks that raw
selection before catalog construction and around model calls. These checks must
not reject B-only RAG or user-target lookup solely because an unused selection
expired. Do not clear or replace the admitted context to obtain a passing test.

Use a server-only optional dynamic Session reference guard, not a browser flag,
tool-name bypass list, or globally weakened reference validation. Default None
keeps the existing behavior. A supplied guard must be callable and requires a
dynamic Session; it receives the step and resolved input, returns exactly None
on success, and raises on invalid authority. Unexpected return values reject.
The existing two Session reference-check locations invoke this guard instead
of the raw-selection check. Exceptions fail the action without dispatch; they
must not be converted into successful checkpoint reuse or silently retried as
tool execution. Before propagating a guard rejection, journal a denial, replace
any provisional checkpoint result and clear checkpoint_reused: settlement may
re-enter execute_step, whose existing-result shortcut would otherwise retain
the provisional success. Preserve the first boundary-error reason in the guard
latch through normal and exceptional settlement, then at the loop boundary.

The B guard belongs to the request-local harness, not the shared adapter. It
must authenticate the registered server-issued action for that exact step,
verify current closure, and re-resolve its original tool/arguments against the
unchanged admitted journal head. Compare the complete bounded action/input and
step binding metadata, not only tool name, equivalent SMILES or a supplied hash.
Validate the actual Session-owned step copy (append_step deep-copies its input),
not merely the harness's pre-append object.
It therefore validates selected molecules and upstream sources when consumed,
while independent lookups have no fabricated molecular dependency. Check the
root deadline before and after this owned validation. Also use the existing
request-local `SingleAttemptTool.dispatch_guard` at adapter dispatch, since
callbacks, persistence and concurrency admission can invalidate earlier checks.
The request-local guard must latch its first boundary failure: an exception
inside the adapter's submitted worker can otherwise be normalized into a generic
tool error. Recheck that latch after settlement and before any subsequent model,
tool or answer; retain its safe reason code, never provider text. A later
successful revalidation cannot undo the failure. No extra predictor/search
invocation or source repair is allowed.

For B only, replace the loop's unconditional raw-selection gates with owned
resolver closure checks at the already specified pre/post-callback boundaries.
Keep the full admitted context and journal identity; do not inherit legacy
`effective_molecule` mutation as authority. Legacy callers retain their current
selection checks. Source/clock failures still use the Task6 publication barrier;
this seam does not by itself authorize an answer or a continuation nonce.

Task4B tests must replace, not delete, all four named gap characterizations with
same-context successful actual loop/Session dispatch. Add controls for consumed
expired/foreign selections, molecular ancestors behind target lookups, tampered
step/input/action metadata, expiry during callbacks/persistence/adapter admission,
non-callable/static guards, unexpected returns, unchanged legacy rejection and
zero physical tool invocations on denial. Pre-Session-dispatch denial has zero
Session attempts; an adapter-stage denial may already consume one conservative
Session/budget attempt and must not refund it or replay the tool. Both native
and JSON decision modes remain required.
This paragraph is a reviewed implementation choice to be checked before coding,
not evidence that the integration gap has already been repaired.

Independent source review by Sartre approved this choice on2026-09-26 without
tests or production edits. Its checkpoint-reentry and Session-owned-copy checks
are made explicit above. Actual Task4B implementation still requires RED/GREEN
and independent specification/quality review.

`BindingRequirements` is frozen strict extra-forbid, version literal '2', profile
literal B1 revision, forbidden_tools unique tuple max32, with these optional
obligation groups:

- molecular_results: existing MolecularRequirement values max2, unchanged metrics.
- analysis_results: max2 unique tools admet_predictor/activity_predictor; each
  has exact_molecule_count 1..100 or None, expected_smiles tuple max100, and
  target str (1..128) or None for activity only. ADMET scope literal
  `available_methods`; it never promises every endpoint or an experiment.
- reverse_result: expected_smiles exactly one complete valid structure.
- retrieval_result: query exact nonblank str <=16384 bytes, k literal3;
  `require_hits` strict bool defaultFalse. Empty verified retrieval satisfies
  execution/no-hit, never a requested hit. Query must equal original admitted query.
- target_result: input literal 'user' or 'reverse', query exact nonblank str
  <=8192 or None (required for user, forbidden for reverse), require_resolved strict
  bool defaultFalse. User query must come from original admitted query or its
  server-derived structured target, not model arguments. Reverse mode selects
  exactly one issued supported record handle, not an arbitrary target.

Parser bounds input/default-expanded serialization to32KiB, validates exact native
JSON before serialization, revalidates constructed instances, rejects bool counts,
duplicate tools/subjects and forbidden-required intersections. Chemical subject
validation/canonical equivalence uses existing whole parser/RDKit during preparation,
not imports; count and subject constraints must agree. v1 parse_task_requirements
is not widened. B mode requires explicit v2; v1 caller cannot select B by payload.

Required tools equal explicit required_tools union all v2 groups, intersect allowed
and permissions; inconsistent required permissions reject before any model/tool.
Missing input produces clarification/rejection, never implicit fragment selection.
Clarification cannot overwrite original obligations, target, or requested counts.

## Authorization and data flow

B1 contains exactly seven tools from B1_TOOLS. `scientific_tools` and `rag` remain
independent strict bool capabilities. Only explicitly allowed/obligated retrieval
can dispatch with scientific tools false; chat always has zero scientific/RAG tools.
Existing owner checks/idempotence/side-effect restrictions remain. Readiness unknown
is not success. No B2 generation/ranking or docking through an alias.

Use existing parse_binding_arguments with profile bound by server. User molecular
inputs use complete current user input or authorized selected molecule. Molecular
evidence consumers accept whole sealed source batches, never model-authored SMILES;
preserve activity target from user obligations. Reverse enforces exactly one
subject before its first-of-batch producer. ADMET joins the molecular input tools;
RAG accepts only input_ref=user, exact original retrieval query and fixed k3.

Target user input is original admitted query/declared exact target query validated
as user-authored. Reverse target input selects one row using deterministic handle
`record-` plus SHA256 of evidence ID, row index and canonical original row digest.
Expose descriptors beside the actual observation, not in scientific data. Derive
handles anew from sealed rows; no serialized model-supplied handle authority.
Select only source-backed accepted fields from TargetSearchInput: gene_symbol,
target_gene, uniprot_id, target_name, protein_name. No conversion of CHEMBL IDs or
name hashes to fake gene/UniProt IDs. A sole nonempty target_name is a protein-name
search hypothesis, not confirmation. Empty/ambiguous identifiers block dispatch;
do not infer an organism filter unsupported by the target tool. No top-score
selection language is assumed in this core; such constraints need explicit
admission/obligation representation before normal Web support.

Every upstream role checks source producer, status SUCCEEDED, no errors/demo/
fallback, whole observation seal, same owner/trace, original obligation digest,
input binding, prior-only ancestry and current source eligibility. Reconstruct
ancestor closure in observation order; cycles/missing/forward parents reject.
Equal SMILES or query alone never validate a different source role.

## Proof and action identity

Closed binding_proof fields: version '1', profile, requirements_sha256,
action_sha256, input_sha256, roles (ordered entries role/evidence_id/output_sha256),
own_source (None or a closed tool-specific generation/config identity),
selection_sha256 (None or chosen reverse-row descriptor digest), policy 'b1-v1'.
Keep <=5 roles, <=8KiB proof, same64KiB observation and512KiB continuation ceilings.
Oversize observations fail honestly; do not truncate evidence or enlarge limits.
Only server resolution/preparation creates proof; strip any raw tool-supplied
reserved binding fields before attaching the authoritative values.

Nested proof protocol is closed, not left to implementation inference:

- own_source for RAG is exactly `{kind: 'rag', generation_id, epoch,
  configuration_sha256, source_identity_sha256}`. Generation is lowercase32hex,
  epoch exact native integer >=0, both digests lowercase64hex. The last digest
  is the existing service's receipt-source projection digest, not the entire
  receipt/result digest. First publication captures the attached service and
  validates the original receipt against that snapshot; subsequent consumption
  requires equality with the stored projection and fresh service validation.
- own_source for reverse is exactly `{kind: 'reverse', generation_id,
  source_sha256, configuration_sha256}` with the same hex bounds. The three
  values must equal the original prediction receipt and current producer
  snapshot. They cannot be recovered from normalized target rows.
- own_source is null for the other five tools and for failed/unavailable source
  observations. Null never makes a failed RAG/reverse observation usable.
- Each role is exactly `{role, evidence_id, output_sha256}`. In B1 the role
  vocabulary is `molecules` (one whole upstream property/likeness/activity/ADMET
  batch) or `reverse_record` (one strict reverse observation). User input has
  no roles; upstream input has exactly one. Reject duplicates, extra roles and
  invalid producer/consumer pairings. The five-role ceiling reserves capacity
  only; B1 does not admit five roles or B2 vocabulary. Role order is lexical by
  role and then evidence_id; existing EvidenceId syntax and lowercase64hex apply.
- selection_sha256 is non-null only for reverse_record and hashes the exact
  selected original raw row plus its zero-based position. The public record
  handle hashes `{evidence_id, row_index, row_sha256}` using that original row.
- Binding/action/selection/operation hashes use EvidenceLedger.output_digest
  (sorted keys, UTF-8, ensure_ascii=False, allow_nan=False, default JSON
  separators). Producer receipt hashes keep their existing compact codecs;
  never recalculate those with the ledger codec. Every hashed object is native,
  bounded JSON before hashing. Digests detect consistency, not authentication.

First-use reverse acquisition stays inside its existing owned tool action, not
outside the adapter slot or in a reuse/resume hook. Its tool already captures the
strict snapshot after lazy initialization and before prediction, validates the
same-call envelope and postflight. RAG retains its equivalent producer capture.
After actual execution, server preparation validates against current attached
source and stores the original receipt identity; never backfill missing receipts.

Separate logical action identity from the final operation key:

- action_sha256 binds profile/policy, tool and adapter versions, canonical resolved
  input, role evidence/output digests, obligations and record selection.
- operation_key hashes action_sha256 plus the actual own_source identity. Source
  identity can first become known during lazy initial execution, so finalization
  happens before ledger/seal. Failed source execution has explicit own_source=None
  and cannot satisfy scientific acceptance.
- existing run-local observed index uses action_sha256 to locate earlier attempts;
  before reuse, validate the complete proof/source closure, then require unchanged
  final operation key. Stale source rejects; never auto-rerun to relabel old scores.

Add optional trusted `observation_prepare(result, step)` Session callback after
domain validation/alignment and before final provenance/output digest/ledger.
It attaches proof/final key to actual copied step metadata and quality. It runs
in the existing owned action, not event loop. No extra scientific execution.
Preparation has a separate journal boundary before registration: cache the
accepted result, detached validated proof and completion flag. A registration
retry cannot repeat validation/preparation against mutated state or rerun the
tool. The normalized/captured flags protect the subsequent persistence stages.
Failure yields
safe rejected/invalid output with no certifiable scientific data; no unsafe raw
payload written first. Preserve existing observation_capture write-once seal.
Extend ledger input_binding and verify_observation_integrity together only under
explicit server proof authority, not the presence of a raw quality key. The
ledger's optional binding_proof argument defaults to unchanged legacy behavior;
when supplied, both closed native projections must validate and agree. The
verifier follows the authoritative ledger binding. Revision8 decode and Session
restoration each explicitly propagate proof; revision6/7 calls stay unchanged.
Callback failure/proof mismatch caches a fresh sanitized invalid result, without
provisional payload or proof/source metadata. See the independently approved
Task2 clarification at e10df12 for retry and restoration boundary tests.

## Live source checks, lifecycle and budgets

Tool hooks validate current observations without initialization, scoring, embedding,
search, cache writes or receipt repair. Reverse rejects closed/loading/uninitialized
state, captures attached predictor under short state lock, calls producer only
outside that lock and rechecks tool state/identity. Validate raw records from
evidence, not normalized rows; preserve consumer digest binding. Borrowed sources
are not closed. RAG uses attached service capture/validation and original receipt.
Retain full CSV-byte freshness: this may hash a file and is not a pure/no-I/O
accessor. Do not introduce a new no-rehash policy or source manager.

Both actual tools expose `validate_current_observation(data, evidence, *,
input_data, expected_source=None) -> dict`. This validates only detached scientific
data/receipt binding and attached source eligibility, not ToolResult success,
trace authority or seals (the loop owns those checks). Inputs must be native
JSON bounded to64KiB before copying or receipt selection. RAG uses exact input
query and k3; reverse takes exactly the already-resolved single SMILES, no whole
prompt parsing or first-of-batch fallback in this hook. Missing or multiple
matching receipts reject. The returned detached dictionary is the exact
own_source projection above. With expected_source supplied, exact closed fields
and native scalar types are validated before provider calls, then compared to
the fresh projection. With None, first publication still validates the original
receipt against current source; it does not fill a missing receipt. These hooks
never mutate the observation or close borrowed sources. Failures use one sanitized
ValueError('current_source_unavailable'); cancellation BaseExceptions propagate.
The resolver calls them through tracked owned work, never directly on the event
loop. RAG requires valid_hits/valid_empty; invalid_discard is diagnostic partial,
not a reusable successful source. Reverse reuses validate_prediction_observation
to bind normalized records to the unchanged raw receipt before current validation.

All current checks run inside existing WorkerOwner-tracked work, with remaining
root deadline checked before and after; no blocking checks on event loop. Reuse,
finish and resume verification use a narrow reusable owned-call settlement helper
extracted from existing settle_action, preserving cancellation drain semantics.
B direct callers require a WorkerOwner; normal Web integration will supply A2's
existing owner. No new executor/scheduler/lease. Keep main-model<=16, tools<=12,
root<=300s. B2 inner generation accounting remains its required later increment.

For B only, defer the current loop's terminal worker_owner.settle() until after
terminal persistence, finish_dynamic and continuation-publication callbacks and
their final owned eligibility check. Completed per-action roots still settle
before every next action. The outer run finally closes/drains the owner exactly
once and permits no further action; never reopen a settled owner. Legacy/A2
ordering remains unchanged. On final-check failure, do not release the stale
answer or continuation; persist an explicit failed correction through the
existing terminal metadata/result boundary and retain cleanup ownership. Test
the returned result AND stored terminal status, not just an exception.

B terminal publication is a separate explicit contract, never a change to legacy
terminal events. The B-only DecisionEvents view projects terminal attempts into
detached allowlisted summaries: terminal attempt ID, observed outcome,
publication_stage='pending_source_check', answer_released=false. Do not put
scientific answer/data/evidence/artifacts/claims or continuation ID into this
provisional event. Task-completed here names the observed execution outcome,
not answer authorization. Before enabling B on normal Web, its consumer must
honor this flag and not display overall completion before the verified result;
the unchanged legacy frontend must not receive B provisionals as final success.

Use three fixed barriers: terminal metadata write then owned verification;
finish_dynamic/event/status callbacks then owned verification; waiting snapshot
publication (or waiting-status fallback) then owned verification. Only after the
last successful check release the result/checkpoint. No additional positive
publication callback follows that check. Eligibility is established at this
boundary, not atomic with later external source changes.

If a callback raises after committing, post-callback validation still runs; an
exception does not prove no state changed. Detected invalidation irreversibly
latches failure for this segment. Add an opt-in Session method
invalidate_dynamic_publication with a separate immutable correction request and
per-write journal, allowed after finish began/ended but never reopening actions.
Build a new sanitized FAILED result, not a mutation of old event/result objects.
The new result contains no scientific answer, tool results, source evidence,
artifacts, claims, ledger/outputs, satisfied acceptance or continuation IDs.
Keep only bounded diagnostic counters, invalidated evidence IDs and reason.
Persist an invalidation marker, failed status, then one distinct stable TASK_FAILED
correction identifying the superseded attempt and failing boundary. Preserve
historical observation/terminal-attempt records; do not rewrite their payloads or
reset the ordinary finish journal. Once invalidated, ordinary finish retries
cannot resume successful writes. Retry each idempotent frozen write at most once;
a failed write does not skip the other failure-only writes. Correction callbacks
need no source recheck: failure cannot be reversed even if they restore the source.
Persistent storage failures suppress success and report correction durability as
unconfirmed rather than claiming the database was corrected.

If waiting publication committed before invalidation, retain the historical
snapshot, return no nonce and leave admission_exchange.checkpoint unset. Revision8
claims reject the invalidation marker as well as non-waiting status. Pre-CAS
rejection still writes nothing; post-CAS or uncertain-claim failure never dispatches.
This is not a new crash-recovery protocol. Source checks of historical successful
tool events do not retrospectively change what was valid when they were observed.

Establish a monotonic B segment start and deadline before any continuation
read/decode/source verification. After validating the saved numeric credit,
cap that deadline at segment_start + saved_remaining_seconds (and any existing
admission deadline), not current_time + saved_remaining_seconds. Thus the read,
decode and full-file verification time all consume the saved credit. Check the
same deadline before/after owned verification and immediately before CAS;
exhaustion before CAS leaves waiting unchanged. Restore state with this exact
deadline, never a newly started allowance. Test a controlled clock advanced by
source validation and by storage callbacks, including post-CAS exhaustion.

Outgoing revision8 checkpoints conservatively debit finalization work; merely
checking the root deadline after publishing a snapshot is insufficient. At
snapshot start t, let R=deadline-t. Require R>0, reserve F=min(30.0,R/4), and save
remaining_seconds=R-F, pre_finalization_remaining_seconds=R,
finalization_reserve_seconds=F. These are finite native numbers, not bools;
revision8 replay verifies this exact policy/relation within the original root
limit. Define the tail deadline min(root_deadline,t+F). All subsequent finish,
publication, owned checks and final owner drain must complete within this tail
credit. The reserved amount is spent even if actual work is faster; never refund
it, recompute a fresh R at resume or rewrite the snapshot to manufacture credit.
Exhaustion suppresses checkpoint release and latches the failure correction.
This fixed reservation avoids an unbounded publish/recalculate/revalidate loop.

For B, actual outward result/checkpoint release belongs to outer run after owner
drain and a final clock check, not to _run's candidate return. Pass a private
request-local finalization record between those two existing methods; it is not
a new wire/store DTO. Only then set admission_exchange.checkpoint and return.
If drain overruns the reservation, correction is failure-only, requires no new
source work/owner action, and releases no nonce. Tests advance clock by30s after
a snapshot with R=100/F=25: it must fail, not grant100s or75s to a resumed task.
A successful20s tail retains at most75s on the next segment. Human think time
between finished segments is not charged by this policy. The new maximum30s
reservation applies only to B continuation finalization, not legacy/A2 budgets.

Before any downstream action, duplicate reuse, successful finish and continuation
claim, validate supporting source closure. Validate again after store callbacks
before another model/tool request or answer release. Checks are not an atomic
transaction with external source replacement: a known stale pre-CAS snapshot must
leave waiting state untouched; source mutation inside/after CAS consumes the nonce
but must reject subsequent work rather than pretending the CAS never happened.

## Acceptance and continuation

B acceptance checks actual property/likeness metrics; ADMET method/endpoint data
and exact batch subjects without unknown-as-low-risk; activity real provenance
and existing validator (family disagreements remain partial/review, not success);
strict reverse proof/single subject; strict RAG valid_hits/valid_empty only; actual
target lookup status and source evidence. Every required group must be fulfilled
and no forbidden tools executed. LLM self-evaluation never decides acceptance.
Scientific final prose remains deterministic observed results; failed/partial
steps and method limitations remain visible. Do not leak provisional scientific
data on source/proof failure or silently complete a known subset.

Successful scientific finish additionally requires citation closure. Validate
explicit cited IDs, then deterministically expand their validated role ancestors
in original observation order. For each immutable obligation and explicit
required tool, select the earliest qualifying observation inside this expanded
closure. If a group is fulfilled elsewhere but not inside the cited closure,
report missing_required_citation and reject completion; unrelated-only citations
must not pass just because all tools happened to run. Publish cited_closure_ids
and required_evidence_ids (selected observations plus ancestors) in acceptance.
Explicitly listing every ancestor is unnecessary; the server expands only sealed
roles, never equal inputs or model-authored IDs. The final successful scientific
prose renders only this verified cited closure. Uncited failures can still have
safe diagnostic messages/warnings, not uncited numeric claims. Partial/error
reporting keeps existing scientific-state distinctions and cannot use the citation
gate to promote failed/fallback/family-review data to successful evidence.

Use B continuation protocol revision8, preserve legacy6 and ordinary7 defaults.
Fingerprint binds profile/v2 obligations/current server configuration. Persist
proofs and original inputs, never producer objects/callbacks. Reconstruct handles,
inputs, roles, action/final keys and current closure before CAS using actual
historical decisions and sealed data. No scientific reexecution during replay.
Unknown/incomplete source snapshots reject; failed observations remain diagnostics.
Resume checks original obligations; changed target/count/subject is a new task.

## Release evidence

Actual-loop TDD must cover all seven tools, both native/JSON decisions, a next
model action determined by actual prior result, reverse→target consumption,
property→ADMET/activity whole-subject binding, pure retrieval with tools disabled,
and no tool in chat. Synthetic source data and transport are explicitly fixtures.
Exercise no-hit vs unavailable/discard, stale config/generation at dispatch/reuse/
finish, source swap during store callbacks, missing/forged/foreign/cyclic roles,
sealed nested tamper, malformed arguments, failed optional stage and partial final,
clarify→restore/reuse with zero repeat scientific calls, rejected pre-CAS unchanged
waiting record, root timeout/cancellation with actual worker settlement.
Retain all v1/current Web regression tests unchanged. Approved isolated runner only;
no external API or host assets. Independent SPEC then QUALITY and actual-loop
regression precede commit. Next normal-entry slice must use real /ws, not a lab
endpoint. No final P7/P8 claim from this core release alone.

## Task4 source-grounded input-admission clarification

Task3 at33f2847 authenticates original action records and can preserve a prior
admitted subject/target after an actual sealed action. It deliberately cannot
authorize pure clarification text just because it appears in session.input_queries.
The real loop already has server-owned original input, explicit clarified_query,
owner-bound continuation claims and proposal input_turn. Use these existing
boundaries to support original request -> SMILES reply -> target reply even when
no tool has yet executed. This is required normal clarification behavior, not a
new agent, conversation store or scientific evidence source.

Recommended approach: a small bounded BindingInputJournal in new
src/agent/harness/decision_binding_inputs.py. Alternatives considered were making
the resolver read mutable session.input_queries (authority is implicit) or
duplicating the entire dialogue/persistence system (unnecessary). The journal
contains only explicit server-admitted context projections; it never mines model
messages or infers user inputs from a model's tool arguments. User delegated the
recommended bounded choice; independent written review remains required.

The constructor freezes the original owner/context and seeds turn0. The explicit
admit_context(context, input_turn=...) operation admits only the next native
integer turn; retry of the same turn is idempotent only for exactly equal native
input. Reject bools, gaps, rewrites and foreign trace/session/user. Original
metadata/capabilities/obligations remain fixed; only query and the server-resolved
molecule selection may vary in a reply. Store detached native projections of
query and selected-reference identity, not live objects. At most16 turns and
512KiB total journal; each query <=16384 UTF-8 content bytes and each reconstructed
context <=64KiB. The eventual entire revision8 snapshot remains <=512KiB,
including records/messages/results; the journal does not grant an extra budget.

The journal is a server-input boundary, not scientific authority. Admission does
not certify SMILES, target support, a selected reference, tool success or facts.
Those checks stay in the owned resolver/validators/current-reference checks.
Original obligations and the first admitted valid molecular subject/explicit
target cannot silently be replaced by a later reply; malformed explicit input
never falls back to an earlier valid subject. Property parsing remains separate
from activity-family validation. Original RAG/target user queries retain their
existing exact-input policy; clarification does not silently change the retrieval
question. A selected reference's identity must be retained/revalidated, not
replaced merely because a new reference has equal canonical SMILES.

Pass the journal explicitly to the B resolver; no implicit import of Session
history. Default absent journal preserves the reviewed Task3 direct API. Normal
B-loop execution must always supply it. Original action records additionally
commit the zero-based admitted input turn and a digest of that journal prefix.
This affects the sealed full-record identity, not logical action identity, so a
target-only clarification still permits independent property reuse. Historical
resolution uses only the prefix that existed at that action; current reuse also
checks current admitted constraints. Unexecuted human replies can supply input
but never satisfy a scientific requirement or become an evidence ID/role.

The loop alone appends a new admitted reply after its owner-bound continuation
claim is validated. Task5 revision8 persists the bounded journal with original
action records and reconstructs it before CAS: compare original context, exact
input query order, each proposal's existing input_turn, each action's committed
prefix, selection identity and original obligations. Replay only semantically
validates saved server inputs; no model/tool calls. A native dictionary or its
checksum alone is not authorization. Expose explicit restoration only for the
caller-validated revision8 path; never read arbitrary transcript/query lists in
the resolver. Legacy revision6/7 readers and fingerprints remain unchanged.

Split Task4 implementation into reviewable increments without narrowing its end
state: (4A) this input boundary/resolver integration and deterministic B acceptance;
(4B) actual seven-tool loop/catalog/owned boundaries and native/JSON execution.
Put acceptance in new harness/decision_binding_acceptance.py rather than adding
another responsibility to the now large resolver. Reuse existing metric/domain
validators and the same Session/ledger/source closure, not another interpreter.
These two focused helper files are explicit additions to the core allowlist.
Task4 tests may use new test_decision_binding_inputs.py and
test_decision_binding_acceptance.py; actual loop cases remain a separate new
test_decision_binding_loop.py to preserve Task3's focused resolver tests.

Acceptance of this clarification requires no-action multi-reply positive,
foreign/mutable/gapped/reordered history negatives, historical-prefix tampering,
same-structure/different-reference rejection, immutable target/count/subject
constraints, property reuse after target-only clarification, and no authority
from model history. The full actual SQLite continuation case remains mandatory
in Task5; helper-only success cannot count as completed loop/resume behavior.

### Input clarification review resolution

The existing proposal convention stays one-based. A sealed action uses
`record.input_turn = proposal.input_turn - 1`; its digest covers journal entries
0 through that record turn inclusive. Original -> SMILES -> target has journal
turns0/1/2 and proposal turns1/2/3. Reuse does not rewrite an earlier sealed record
or prefix: validate the new proposal against the current admitted constraints,
then reuse only the unchanged eligible earlier observation.

With an injected journal it is the exclusive input authority. Before resolution,
preparation, reuse and closure, the full admitted projection of session.context
must match its current head, or reject. Historical reconstruction uses the exact
record prefix with that prefix's reconstructed context, not the current head.
restore_records must explicitly propagate that journal to its trial resolver;
an omitted argument must not silently take the no-journal compatibility branch.

Freeze all AgentContext dataclass fields, not the Task3 reduced projection:
trace_id, user_id, session_id, active_skill, workflow_name, model_name, stream,
temperature, mol_count, memory and metadata remain exactly the detached original
values. Only query and resolved_molecule may vary at explicit admission. Rebuild
every context from this full frozen base, never dataclass defaults. Raw admitted
selection and its effective scientific identity are distinct: omission of a
selection retains the preceding effective reference; a replacement reference is
not interchangeable merely because its canonical SMILES agrees. Explicit SMILES
takes precedence over selection as in effective_molecule, while preserving the
already fixed molecular subject. Once explicit SMILES clears a selection, later
omission cannot resurrect it. A new selection after clearing is a new identity
and cannot silently replace the fixed subject's authority.

Reduce each prefix in order through the existing scientific input checks. Do
not search backwards for any valid molecule. A malformed explicit molecular
input or subject/count/target conflict anywhere in a prefix remains an error
for that task; a later omission, target-only reply or "continue" cannot erase
it. Thus CCO -> malformed explicit SMILES -> PDE5A must reject, not recover CCO.
Admission itself may preserve that input for diagnostics; it does not certify
its scientific validity. Target-family rejection is applied only when resolving
activity; it must not prevent properties for an otherwise valid subject.

The narrow bounds dependency is explicitly allowed in decision_bounds.py:
extend context_value with keyword-only query_content_bytes=False, requiring an
exact bool. Default False retains the existing JSON-encoded16KiB query check;
True enforces <=16384 UTF-8 content bytes instead, retaining native/surrogate,
field and whole64KiB JSON context checks unchanged. Explicit B journal, initial
loop admission, fingerprinting and revision8 replay use True consistently.
Legacy6/7 paths retain False. This switch does not grant B execution or change
the aggregate512KiB snapshot ceiling. Boundary tests cover escaped quotes,
backslashes and non-ASCII content, full-context overflow and malformed values.

Task5 restores from the existing trusted server store plus authenticated owner,
configuration and waiting nonce. A digest detects corruption; even internally
consistent query/proposal/digest edits do not create authority outside that
boundary. Build and validate an isolated candidate journal before CAS; install
it only after a successful claim, then admit the newly supplied server reply
exactly once. Failed or uncertain claims expose no dispatch authority. No new
signature system or parallel transcript store is introduced. The helper restore
API is only for that trusted caller and cannot itself prove storage provenance.
