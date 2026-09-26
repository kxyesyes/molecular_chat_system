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
Existing journal normalized flag protects completed preparation; retrying an
uncommitted normalization may revalidate but cannot rerun the tool. Failure yields
safe rejected/invalid output with no certifiable scientific data; no unsafe raw
payload written first. Preserve existing observation_capture write-once seal.
Extend ledger input_binding and verify_observation_integrity together, only when
the new proof is present, so legacy evidence IDs/serialization stay unchanged.

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

Establish a monotonic B segment start and deadline before any continuation
read/decode/source verification. After validating the saved numeric credit,
cap that deadline at segment_start + saved_remaining_seconds (and any existing
admission deadline), not current_time + saved_remaining_seconds. Thus the read,
decode and full-file verification time all consume the saved credit. Check the
same deadline before/after owned verification and immediately before CAS;
exhaustion before CAS leaves waiting unchanged. Restore state with this exact
deadline, never a newly started allowance. Test a controlled clock advanced by
source validation and by storage callbacks, including post-CAS exhaustion.

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
