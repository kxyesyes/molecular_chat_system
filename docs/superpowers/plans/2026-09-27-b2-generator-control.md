# B2 Generator Control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to execute a separately released task. Checkboxes are future work, not blanket permission. Current authority is A-CONTRACT IMPLEMENTATION SOURCE ONLY: this plan and `src/agent/contracts/decision_bindings.py`. ABAF test bytes remain frozen. No other production/test/runner writes, Python/import/collection/compile/test execution, asset/config/credential/network probes, staging, commit or push. Parent decides every control-seam release after independent written review. Sections 11–12 record successive narrow releases and supersede historical permission wording; neither releases A-real-path or G3/G4 runtime/client/backend work.

**Goal:** Preserve the actual five-round generator while making B2's one-root generation slot, per-dispatch request/token accounting, deadline/cancellation and eventual all-candidate evidence closure provable.

**Architecture:** Extend the approved B2 design inside existing binding contracts, request-local execution view, adapter ownership, Session metadata/checkpoints, continuation and evidence preparation. Reuse the actual strict Ollama methods and current candidate sanitizer/analysis/ranker/report implementations; no parallel framework, scheduler, database, model client, clock or chemistry/scoring algorithm.

**Tech Stack:** Python 3.10-compatible code, Pydantic 2 closed/frozen records, HTTPX MockTransport, pytest, actual RDKit for structural checks, existing SQLite Session persistence and Web lifecycle.

---

## 1. Authority, pins and source facts

Clean independent tree supplied by parent: branch `codex/b2-generator-control`,
HEAD **c612c9873a04823c936d710861bf890ad30b844a**. Status was clean before this
document. PR97 is the landed strict-generation prerequisite, not B2 completion.
Prior author/Bohr exact17 results belong to that previous slice; no result exists
for this plan's proposed control or closure tests.

Authoritative design read from the sibling `dynamic-bindings-b1` tree:

- `docs/superpowers/specs/2026-09-25-dynamic-tool-bindings-design.md`, §11.5
  token/client gate and §11.7 written approval. SHA256
  `0555E0F3A73C0C718C216B6F8AE4E079E004E15A6A4E7FEE7D277F6361F0D681`.
- `docs/superpowers/plans/2026-09-25-dynamic-tool-bindings.md`, §11.5 and
  §11.8 approval, with §§6–8 unchanged full-dataflow obligations. SHA256
  `D692112E83FFCC2001D5BD2558A4323EB7AABCFC9DB6DAE9AEC48D973E413410`.
- That tree's observed HEAD at this read was `a2b631061d2d333ed067a073bc5fca04cbd5f075`.
  It supplies approved documents, not unmerged source to copy into this tree.

Inclusive root16 and B-only revision8 are already approved design choices.
Numeric token bounds, trusted backend enforcement proof and production seam release
are **not** supplied by that approval. This plan refines execution order, not architecture.

| Pinned source | SHA256 |
|---|---|
| `src/agent/tools/llm_molecular_generator.py` | `5F59170E9BFE83D6DD10C86725FC775D2C69CC25E63B0DC7F72C4ED4533F143A` |
| `src/web/models/ollama_model.py` | `472EC2A73D638AAD8AB92CA2406C09456E9ABAC409FB1379A7F9D93FBF6DB0B1` |
| `src/agent/contracts/decision_bindings.py` | `4683C6CFA60156AD6D2576086F2E4ABAE1FC71862402B42A2DCB40E65D2A3A56` |
| `src/agent/contracts/binding_requirements.py` | `7C27697B3158461729774AB1E36A697D834505BA272A4BC0D74F2D75B2ECBC29` |
| `src/agent/harness/decision_execution.py` | `E1A648747E69EF58818CF23B5217F0D76E320B06D45FE10423440036A0E199AA` |
| `src/agent/runtime/run_session.py` | `7324C9BEB5240F0AB75A866705642F5671C18FA8E55B8B21907A804535DBA734` |
| `src/agent/harness/decision_bindings.py` | `D9490F70E8389163668852E2A65F59FE57A42872B84E94D88D96C8EF0A3C9A15` |
| `src/agent/harness/decision_binding_acceptance.py` | `E95A6B53AC99364F3EBC8B1C644AFA482A1B192F548AC91CD7E04D516AD09B84` |
| `src/agent/harness/decision_continuation.py` | `CA2506DFC83D8294A51255907C34A9D525B826B60A9D8AED50C3745171B1DC8C` |
| `src/agent/tooling/adapters.py` | `9486DC0E1D4EEAF05864460DD0F44A27D5C09D61A8588D6C234E3EAE25759E8E` |

Relevant existing interfaces, verified at that commit:

- Generator `execute` line139, `_generate_with_retry` line326, final-prompt helpers
  lines532/559, strict capture line608, three-path dispatch/bridge lines629/658.
  Defaults: five rounds, requested count1..10, per-call max_tokens1000; broad
  helper/retry catches currently know strict/unavailable failures, not B2 stop.
- Client `generate`/`generate_async` lines87/139 accept keyword-only
  strict_errors=False, issue real POSTs and return text. Strict response validator
  line36 rejects error/done/response violations but drops usage. HTTP client
  timeout150 and bridge wait30 are not the root deadline.
- `SingleAttemptTool.execute` keeps allow_retry=False and original adapter.
  `ToolAdapter._execute_once` retains the semaphore, reserve_worker and queued
  dispatch_guard; `LegacyPythonToolAdapter._invoke_guarded` line313 calls
  `self.tool.execute(payload)`. GenerationToolAdapter inherits through that path:
  its override must continue validating the untouched producer result.
- Session line492 journals step execution; line549 validates then aligns via
  candidate_source, line573 prepares proof, and line638 captures the seal.
  These are not a root-wide consumed generation slot.
- `SQLiteAgentStateStore.update_run_metadata` at
  `src/agent/persistence/sqlite_store.py:387` uses BEGIN IMMEDIATE for metadata
  read/merge/write. This alone does not make stale caller-computed nested budgets
  CAS-safe: prove single root writer/exclusive ownership and monotonic updates.
- ActiveSegment in `src/agent/contracts/ordinary_admission.py:254` is the existing
  time authority. WorkerOwner explicitly does not cover detached internal threads.
- BindingRequirements version2 currently permits B1 only; BindingProof permits B1
  roles/policy only. B1BindingResolver and evaluate_binding_acceptance already
  provide authenticated closure, but not generation/ranking semantics.
  decision_continuation currently rejects binding-profile snapshots/claims; do
  not simply delete those guards to claim B8.
- CandidateRanker excludes unrankable rows and slices top-N; it can succeed with
  a subset. Candidate alignment can discard rows and mark partial. B2 full-coverage
  obligations must reject insufficient closure without changing either formula.
  evidence_report.py:214 still marks dynamic generation `unsupported_binding`.

## 2. Immutable requirements and hard gates

Keep default ordinary chat, stream and close behavior, original parameter
positionals, gmm selection, prompts, temperature, requested count and completion1000.
No global generator/client/adapter request fields. No max_attempts knob in model
JSON, one-call workaround, weakened preflight or default success-to-unavailable swap.

**Gate G0 — independent written review:** this plan and proposed interface choices.
**Gate G1 — test-source release:** named tests/fixtures only; no execution implied.
**Gate G2 — reviewed runner and exclusive slot:** separate RED/GREEN transfers.
**Gate G3 — trusted backend/token grant:** still incomplete; blocks production
client/control implementation under the approved §11.5 gate. Pure contract tests
may use explicitly synthetic grants; these never certify a backend or enable B2.
The separately authorized backend-proof campaign must precede B production, not
wait until the later report/P8 slice. This document grants no such campaign.
**Gate G4 — production allowlist release:** parent names the exact slice after
reviewing RED and G3. Permission for contracts does not imply client/adapter code.
**Gate G5 — integration dependencies:** B2 bindings/B8/report and normal-entry
ownership require their own releases, source pins and reviews.
**Gate G6 — real backend/full P8:** separately authorized real proof; never inferred
from mocked HTTP, synthetic usage, compile, CI collection or offline passes.

### Token contract, without a fabricated deployment grant

Proposed names in existing `src/agent/contracts/decision_bindings.py`:
`GeneratorTokenGrant`, `GeneratorRequestReceipt`, `BRootBudget`;
parsers `parse_generator_token_grant` and `parse_b_root_budget`.
They are closed frozen native-value records, not trust issuers.

The grant has exactly the already approved fields:
`version='1'`, `generator_generation`, `backend_revision`,
`model_artifact_digest`, `tokenizer_digest`, `template_options_digest`,
`bound_policy_revision`, `total_reserved_tokens`, `prompt_token_ceiling`,
`context_token_limit`, `completion_token_limit=1000`. Maximum4KiB;
positive native integer token fields (bool/coercions forbidden), bounded safe
IDs/digests using existing contract conventions. The total is the generator's
reservation allowance, NOT actual tokens spent and NOT a new main-model allowance.

No production values are chosen here. Before G3 passes, parent must receive:

1. Exact backend revision and model/tokenizer/template/system/options identities
   captured by the trusted assembly owner, with an invalidation mechanism.
2. A reviewed bound function for the **complete final prompt plus all backend-added
   tokens**, not a character multiplier or a count derived from output text.
3. Verified context-fit and completion-limit enforcement without silent truncation
   of target/seed; selected positive total allowance and per-prompt ceiling.
4. Evidence binding that function and identities to the actual client dispatch.
   A mutable model_name, digest-shaped string or caller's verified flag is not proof.

Missing/stale/mismatched proof yields `generator_token_bound_unavailable` and zero
POSTs. For each round, reserve final_prompt_bound +1000; repeat the prompt charge
on every request. Ensure prompt_bound<=prompt_token_ceiling and bound+1000 fits
context and remaining generator allowance. Unknown usage keeps full reservation;
validated usage is separate, never a same-root refund. Over-bound usage records a
violation and stops further dispatch; no truncating counters to the permitted bound.
Only durably proven never-dispatched reservations may be released.

### Request/root contract and truthful receipt states

Preserve M=intent_requests+decision_requests and configured_main_limit<=16.
G includes reserved, dispatched and uncertain generator request debits, excluding
only durably proven never-dispatched releases. Enforce M+G<=16 and at most five
rounds. The logical invocation uses its existing outer tool slot; it is not an
extra HTTP debit. Outer tool ceiling12 remains distinct. A finishing decision
also consumes a main/root slot; consuming all16 cannot fabricate successful finish.

Budget projection `b_root_budget` carries version1, policy
`b-root-inclusive-16-v1`, main/root limits and counters, root/slot identity and
slot state, generator generation/grant digest and reserved-token total.
`generator_requests` contains at most five receipts. Each has reservation ID,
round1..5, root/slot identity, input/proof digest, generation/grant digest,
reserved prompt/completion tokens, nullable verified usage, fixed outcome and
phase reserved/not_dispatched/dispatched/settled/uncertain. Combined maximum16KiB.

Native types, sums, unique/ordered rounds and identity consistency are mandatory.
Persist no prompts, responses, credentials, live controls or absolute monotonic
clocks. Preserve bounded unknown dispatch facts: a durable pre-POST marker is NOT
proof that the backend received/executed a request. A crash at that gap keeps a
debit and unknown bounds; do not label reservation count as observed HTTP count.

One logical slot is claimed before entering the authorized producer. Once entered,
complete/partial/failed/cancelled/uncertain outcomes remain consumed across new
decision IDs, altered seed/target, replay and resume. Pre-admission rejection makes
zero producer entries; recovery never resumes inner rounds. A new sample requires
a newly admitted user request, not a modified operation key in the same root.

### 2.1 Review amendment R1 — exact wire contracts (PROPOSAL, not approved APIs)

This amendment answers Wegener's three P2s. The exact choices below supersede the
earlier shorthand, require independent re-review, and do not expand §3's allowlists.
No test preparation is currently authorized. Stage A-contract must be reviewed
and separately released before A-real-path test preparation; production control
remains blocked on G3/G4 even if pure syntax tests with fake grants pass.

All fields in the following three tables are **required, with no defaults**,
including literals and nullable fields. JSON null is accepted only where stated.
All records forbid extras; frozen Pydantic models use strict=True and
revalidate_instances='always'. Nested usage/grant/receipt values are frozen models,
not dictionaries; receipt sequences become tuples. Booleans are never integers.
Integers are native nonnegative/positive Python/JSON integers as specified, without
coercion or an invented business-token maximum. Preserve the existing independent
scalar guard: every persisted/parsed native int has bit_length()<=4096. This is
a representation/work bound, not a business-token allowance. Byte/depth/node bounds
also apply; fitting the byte budget never bypasses the scalar guard.

Reuse existing `GenerationId` (exact lower-case hex32), `Sha256` (lower-case
hex64), and `EvidenceId` from decision.py. Also apply the existing BindingRole
whole-string check: `re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}', value)`.
Apply whole-string hex checks too; a trailing newline is never an accepted ID.
These are syntax identifiers, not signatures, secrets, trusted revisions or authority.

**Table 1 — `GeneratorTokenGrant` (maximum 4096 UTF-8 bytes).**

| Exact field | Type / native bound | Null / default |
|---|---|---|
| version | literal string "1" | no / none |
| generator_generation | GenerationId | no / none |
| backend_revision | EvidenceId, whole-string check | no / none |
| model_artifact_digest | Sha256 | no / none |
| tokenizer_digest | Sha256 | no / none |
| template_options_digest | Sha256 | no / none |
| bound_policy_revision | EvidenceId, whole-string check | no / none |
| total_reserved_tokens | native int >0; generator allowance, not usage | no / none |
| prompt_token_ceiling | native int >0 | no / none |
| context_token_limit | native int >0; prompt ceiling +1000 must fit | no / none |
| completion_token_limit | native int, exactly 1000 | no / none |

No multiplier, backend token count, production grant or main-model token allowance
is inferred. A total smaller than a legal reservation is valid syntax but cannot
admit that reservation. Grant digest is `EvidenceLedger.output_digest` of its
native JSON payload (sorted keys, ensure_ascii=False, allow_nan=False, existing
default JSON separators); not a digest of differently formatted JSON text.

Full valid **synthetic-only** grant payload:
```json
{
  "version": "1",
  "generator_generation": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "backend_revision": "offline-contract-v1",
  "model_artifact_digest": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "tokenizer_digest": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
  "template_options_digest": "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
  "bound_policy_revision": "offline-table-v1",
  "total_reserved_tokens": 6000,
  "prompt_token_ceiling": 200,
  "context_token_limit": 2048,
  "completion_token_limit": 1000
}
```
Its grant_sha256 is
`3217d3e88fd406c48510be4d73586df8e5be567095a9d2b97e532e65d93d74c4`.

**Table 2 — `GeneratorRequestReceipt` (within the combined 16384-byte budget).**

| Exact field | Type / native bound | Null / default |
|---|---|---|
| version | literal "1" | no / none |
| root_id | EvidenceId | no / none |
| logical_slot_id | GenerationId; one consumed invocation per root | no / none |
| reservation_id | GenerationId; stable journal operation identity | no / none |
| round_index | native int 1..5 | no / none |
| input_sha256 | Sha256 of admitted generator input | no / none |
| proof_sha256 | Sha256 of bound preparation proof | no / none |
| generator_generation | GenerationId, same as grant | no / none |
| grant_sha256 | Sha256, same as budget/grant | no / none |
| reserved_prompt_tokens | native int >0, bounded by grant prompt ceiling | no / none |
| reserved_completion_tokens | native int exactly 1000 | no / none |
| phase | reserved / dispatched / settled / uncertain / not_dispatched | no / none |
| dispatch_marker_id | GenerationId; durable intent-to-call marker only | yes / none |
| client_dispatch_id | GenerationId; client-observed entered POST attempt | yes / none |
| backend_request_id | EvidenceId; optional backend-provided correlation, not execution proof | yes / none |
| never_dispatched_evidence_id | EvidenceId of owner-held no-entry evidence | yes / none |
| drained | native bool; local request/worker cleanup settled | no / none |
| outcome | pending / success / backend_failed / stopped / uncertain / bound_violation | no / none |
| stop_code | null or one of §5.1's closed stop codes | yes / none |
| usage_status | not_observed / missing / valid / malformed / over_bound | no / none |
| usage | exact frozen GeneratorUsage record described below | yes / none |
| usage_issue | null / invalid_prompt / invalid_completion / invalid_both / usage_scalar_overflow / usage_wire_overflow / usage_scalar_and_wire_overflow | yes / none |

`GeneratorUsage` has exactly two required nullable native nonnegative-int fields:
`prompt_tokens`, `completion_tokens`. Wire usage comes from the same response's
`prompt_eval_count` / `eval_count`; do not estimate it from generated text.
Missing one/both fields produces missing with a usage record containing null(s);
explicit zeros produce valid integers, not missing. No response/usage observation
is not_observed with usage=null, issue=null. A wrong type (including bool), negative
or nonfinite component is represented by null for that component and a fixed
invalid_* issue, while any valid other component is retained. Complete native
counts within reservations are valid, issue=null. Any observed native component
above its corresponding reservation is over_bound, never clipped; preserve the
actual numbers only when BOTH bit_length()<=4096 and the complete budget fits
16KiB, even when the other component is missing/malformed. A positive native int
with bit_length()>4096 is a scalar overflow even when its decimal representation
fits 16KiB: 1<<4096 has 4097 bits but only 1234 decimal digits. Never relax the
existing scalar guard to preserve that raw integer in a receipt.

**R2 proposal — upstream bounded overflow facts, before parser admission.** The
trusted client/control usage-observation boundary first checks exact native type,
sign and bit_length, before decimal formatting, hashing, JSON serialization or
passing the raw observation into _bounded_model_payload. For a positive >4096-bit
component, record component=null with usage_issue=usage_scalar_overflow. Because
the corresponding admitted reservation itself satisfies <=4096 bits, that positive
component is already provably above its reservation: retain usage_status=over_bound,
outcome=bound_violation and stop_code=generation_token_bound_violated. Do not call
it missing, generic malformed, zero, a successful settlement or an unknown bound.
Negative/non-native input remains invalid_* rather than positive-over-bound proof.

For <=4096-bit components, retain the exact native count if it fits. If adding the
new usage observation would overflow the complete 16KiB budget, the upstream
boundary emits usage_wire_overflow, replaces only the new numeric components
needed to fit with null, and retains the established over_bound status/stop when
any observed count exceeded its reservation; otherwise status=malformed with
generation_usage_invalid. Deterministic fallback order is new completion count,
then new prompt count; recheck the full envelope after each omission. Never alter
previously journaled counts, identities or receipts to create room. If scalar and
byte overflow both occur, retain usage_scalar_and_wire_overflow (do not overwrite
one known fact with the other). Retain unaffected valid components where they fit.
If even the bounded fact cannot be persisted, quarantine the root as journal/
settlement uncertainty with all debits retained, not an unrecorded successful round.

These fixed issue/status combinations are the bounded facts, not clipped numeric
usage: omitted values are null, never 0, the reservation ceiling, a truncated
decimal string or a guessed replacement. No raw overflowing integer is formatted,
logged or persisted. Known over-bound observations always stop further dispatch,
retain the full request/token reservation and cannot authorize a refund or normal
release. The owner must retain the violation even if a later journal write fails.
The parser may validate the resulting bounded fact's syntax but does not attest
that a backend observed it. This adds no production grant or business-token cap.

dispatched means **durable marker written**, not proof of POST/backend execution.
A marker is allocated before POST; client_dispatch_id is recorded only when the
trusted client has actually entered the call boundary, never just for reservation
or intention. If journaling after entry fails, missing client_dispatch_id remains
unknown, not zero. MockTransport entries are independent observations of attempted
HTTP handling, not remote-model evidence. backend_request_id alone authorizes nothing.
No receipt stores prompts, text, URLs, exception messages, callback objects or clocks.

Full valid standalone receipt (synthetic successful first round):
```json
{
  "version": "1",
  "root_id": "root.offline.1",
  "logical_slot_id": "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
  "reservation_id": "11111111111111111111111111111111",
  "round_index": 1,
  "input_sha256": "2222222222222222222222222222222222222222222222222222222222222222",
  "proof_sha256": "3333333333333333333333333333333333333333333333333333333333333333",
  "generator_generation": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "grant_sha256": "3217d3e88fd406c48510be4d73586df8e5be567095a9d2b97e532e65d93d74c4",
  "reserved_prompt_tokens": 200,
  "reserved_completion_tokens": 1000,
  "phase": "settled",
  "dispatch_marker_id": "44444444444444444444444444444444",
  "client_dispatch_id": "55555555555555555555555555555555",
  "backend_request_id": null,
  "never_dispatched_evidence_id": null,
  "drained": true,
  "outcome": "success",
  "stop_code": null,
  "usage_status": "valid",
  "usage": {
    "prompt_tokens": 11,
    "completion_tokens": 7
  },
  "usage_issue": null
}
```

**Table 3 — `BRootBudget` (grant plus all receipts combined <=16384 UTF-8 bytes).**

| Exact field | Type / native bound | Null / default |
|---|---|---|
| version | literal "1" | no / none |
| policy | literal "b-root-inclusive-16-v1" | no / none |
| root_id | EvidenceId | no / none |
| input_sha256 | Sha256 | no / none |
| proof_sha256 | Sha256 | no / none |
| logical_slot_id | GenerationId; null only while unused | yes / none |
| slot_state | unused / claimed / running / complete / partial / failed / cancelled / uncertain | no / none |
| grant | exact GeneratorTokenGrant | no / none |
| grant_sha256 | Sha256, equals canonical digest of grant | no / none |
| main_limit | native int 1..16, configured main limit | no / none |
| root_limit | native int exactly 16 | no / none |
| intent_requests | native int 0..1 | no / none |
| decision_requests | native int 0..16 (includes repair and finish) | no / none |
| main_requests | native int 0..16, exactly intent+decision <=main_limit | no / none |
| generator_request_debits | native int 0..5, derived below | no / none |
| root_request_debits | native int 0..16, exactly main+generator | no / none |
| reserved_tokens | native int >=0, derived below, <=grant total | no / none |
| generator_requests | JSON array of exact receipts, length 0..5; frozen tuple output | no / none |

All receipts match root/slot/input/proof/generation/grant; reservations and markers/
client IDs are unique within their own identity domain, round_index is ordered
1..N with no gaps or replacements. Reserved prompt +1000 must fit context.
G counts every receipt except not_dispatched; reserved_tokens is the sum of
prompt+completion reservations for those same receipts. Neither actual usage nor
a settled failure reduces either debit. Round limit counts ALL receipts, including
released ones. Independent observed client-entry lower bound is count(non-null
client_dispatch_id); upper bound adds marked or uncertain receipts without that ID
unless proved not_dispatched. No durable marker and reserved alone do not prove
a historical zero after a persistence gap: recovery first records uncertain.
These are client-call bounds, not backend-compute counts.

Persist the flat model's fields except generator_requests under existing Session
metadata key `b_root_budget`; persist the ordered list under sibling key
`generator_requests` in the SAME serialized owner update/checkpoint.
On read, combine only these two explicit projections for parsing; missing either
is corruption, not empty/zero. Receipt identity is (root_id, logical_slot_id,
reservation_id); there is no new receipt store or global registry.
Example below is one settled request in a still-running invocation; it does not
assert whole invocation completion or authenticate the journal.

Full valid synthetic budget payload:
```json
{
  "version": "1",
  "policy": "b-root-inclusive-16-v1",
  "root_id": "root.offline.1",
  "input_sha256": "2222222222222222222222222222222222222222222222222222222222222222",
  "proof_sha256": "3333333333333333333333333333333333333333333333333333333333333333",
  "logical_slot_id": "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
  "slot_state": "running",
  "grant": {
    "version": "1",
    "generator_generation": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "backend_revision": "offline-contract-v1",
    "model_artifact_digest": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    "tokenizer_digest": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
    "template_options_digest": "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
    "bound_policy_revision": "offline-table-v1",
    "total_reserved_tokens": 6000,
    "prompt_token_ceiling": 200,
    "context_token_limit": 2048,
    "completion_token_limit": 1000
  },
  "grant_sha256": "3217d3e88fd406c48510be4d73586df8e5be567095a9d2b97e532e65d93d74c4",
  "main_limit": 16,
  "root_limit": 16,
  "intent_requests": 1,
  "decision_requests": 1,
  "main_requests": 2,
  "generator_request_debits": 1,
  "root_request_debits": 3,
  "reserved_tokens": 1200,
  "generator_requests": [
    {
      "version": "1",
      "root_id": "root.offline.1",
      "logical_slot_id": "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
      "reservation_id": "11111111111111111111111111111111",
      "round_index": 1,
      "input_sha256": "2222222222222222222222222222222222222222222222222222222222222222",
      "proof_sha256": "3333333333333333333333333333333333333333333333333333333333333333",
      "generator_generation": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "grant_sha256": "3217d3e88fd406c48510be4d73586df8e5be567095a9d2b97e532e65d93d74c4",
      "reserved_prompt_tokens": 200,
      "reserved_completion_tokens": 1000,
      "phase": "settled",
      "dispatch_marker_id": "44444444444444444444444444444444",
      "client_dispatch_id": "55555555555555555555555555555555",
      "backend_request_id": null,
      "never_dispatched_evidence_id": null,
      "drained": true,
      "outcome": "success",
      "stop_code": null,
      "usage_status": "valid",
      "usage": {
        "prompt_tokens": 11,
        "completion_tokens": 7
      },
      "usage_issue": null
    }
  ]
}
```

### 2.2 Proposed parser and pure transition APIs

All five signatures below reside in the existing decision_bindings module:
```python
parse_generator_token_grant(value: object) -> GeneratorTokenGrant
parse_generator_request_receipt(value: object) -> GeneratorRequestReceipt
parse_b_root_budget(value: object) -> BRootBudget
transition_generator_request(old: object | None, new: object) -> GeneratorRequestReceipt
transition_b_root_budget(old: object, new: object) -> BRootBudget
```
(The first three are parsers; the last two are pure transitions.) Accepted roots
are exact native dict or the EXACT named result model; no subclasses, raw JSON
str/bytes, Mapping, arbitrary dataclass, enum or coercion. Raw dictionaries contain
native JSON lists, not tuples. Exact model inputs may have tuple receipt fields,
projected explicitly to JSON arrays. Allowed nested model classes are only
GeneratorUsage, GeneratorTokenGrant, GeneratorRequestReceipt as appropriate;
BRootBudget is allowed only as the budget root. Extra/missing fields, cycles,
nonfinite values, native invalid types and constructed-object corruption fail.

Extend the existing _bounded_model_payload pattern, not model_dump on untrusted
objects: exact classes/fieldsets/extras are inspected, depth<=32 and nodes<=16384
checked before serialization, known tuple fields projected, and the unchanged
decision_bounds.validate_json scalar rule rejects bit_length()>4096 BEFORE any
JSON dump, independently of byte size. Then perform strict validation
from JSON and a second bounded projection. Revalidate even model_construct/model_copy
objects and already parsed instances. Return a new detached deeply immutable model;
mutating the original input or a later model_dump result cannot change the record.
No retained raw dict/list/usage references. Size measurement uses UTF-8 json.dumps
with ensure_ascii=False, allow_nan=False and default separators, including nested
grant/receipts. The grant independently satisfies 4096 bytes inside a budget.

The parsers and pure transitions NEVER normalize an overflowing raw integer into
an overflow fact. Raw dict, exact model, model_construct/model_copy and nested
usage paths containing >4096-bit ints all raise their fixed invalid_* error even
if their decimal JSON would fit. Grant/reservation overflow is rejected outright,
not made into a usage fact. Only the separate upstream observation step described
in R2 can create a bounded usage-overflow projection before these parsers. An
accepted hand-constructed fact in A-contract proves syntax only, not that this
normalization or an actual client dispatch was reached.

Each failure raises plain ValueError with only, respectively:
invalid_generator_token_grant / invalid_generator_request_receipt /
invalid_b_root_budget / invalid_generator_request_transition /
invalid_b_root_budget_transition; suppress exception chaining. No raw value,
Pydantic diagnostics or provider text in public error/log records. Transitions
parse both operands first, normalize canonical native JSON for equality, validate
the whole proposed new state, and return its detached parse. Invalid old input is
not repaired. None is accepted ONLY as receipt old for first reservation.

**Complete legal receipt transition table; every omitted edge is illegal.**

| Old | New | Additional required facts/constraints |
|---|---|---|
| None | reserved | next round reserved by budget owner; no marker/client/backend/evidence IDs, drained=false, pending, stop=null, usage not_observed |
| any phase | identical same phase | exact canonical equality is idempotent; no HTTP or counter increment |
| reserved | dispatched | install one stable marker; pending, drained=false, no client ID yet |
| reserved | not_dispatched | owner no-entry evidence ID, drained=true, stopped, non-null stop code; no dispatch/client/backend IDs or usage |
| reserved | uncertain | journal/ownership ambiguity; uncertain, drained=false, stop code generation_journal_unavailable or generation_dispatch_uncertain |
| dispatched | dispatched | marker unchanged; install client ID and optionally backend ID once; other fields unchanged |
| dispatched | settled | drained=true, client ID non-null; outcome success/backend_failed/bound_violation/stopped with usage rules below |
| dispatched | uncertain | preserve known IDs/facts; uncertain, drained=false, fixed stop code |
| dispatched | not_dispatched | marker preserved but client/backend IDs null, no usage, owner evidence, drained=true, stopped |
| uncertain | uncertain | only add previously unknown IDs/usage facts or false->true drained; never erase/change observations |
| uncertain | settled | authoritative late local settlement with client ID and drained=true; no new POST |
| uncertain | not_dispatched | owner proves no client entry despite marker/gap; no client/backend IDs or usage; evidence, drained=true |
| settled / not_dispatched | anything different | forbidden, including altered same-phase terminal values |

All identities through reserved_completion_tokens are immutable. Once non-null,
marker/client/backend IDs cannot be changed or cleared; no-entry evidence is only
set on not_dispatched. Outside uncertain's monotone facts, same-phase amendments
are forbidden except the dispatched row. Usage observation is installed once;
known non-null counts never change. A terminal settled record is emitted only after
all immediately available response/usage facts are captured (not success then fixup).
not_dispatched has not_observed usage and no client/backend ID; settled success
requires no stop code and valid/missing usage, backend_failed likewise may have
not_observed usage. malformed usage requires stopped+generation_usage_invalid;
over_bound requires bound_violation+generation_token_bound_violated; these forbid
further rounds. Settled stopped requires a non-null stop code. Other pending/
uncertain/not_dispatched states cannot contain a successful outcome. An uncertain
receipt may retain observed usage but never permits another dispatch.

The receipt parser enforces state shapes independently of transition history:
reserved has the empty pending shape in the first row; dispatched has a marker,
pending outcome, drained=false, no usage/evidence/stop; uncertain has a non-null
stop code, uncertain outcome and no no-entry evidence. settled/not_dispatched
must meet their terminal rules above. client ID requires marker; backend ID
requires client ID. not_observed requires usage=null/issue=null; all other usage
statuses require GeneratorUsage. missing means at least one null count and no
issue; valid means both counts present, in bounds, no issue. malformed requires
an issue; over_bound requires an actual over-bound component or an explicitly
recorded scalar/wire/combined overflow over-bound fact. The positive scalar-overflow
fact requires over_bound, never malformed, since valid reservations obey the scalar
guard; wire-only overflow may be malformed if no inequality was established.
Every overflow issue requires at least one omitted (null) usage component; an
all-numeric zero/clipped replacement is not a valid overflow-fact shape. Which
component was actually observed remains an upstream-owner fact, not something
the parser can reconstruct or authenticate from caller-supplied nulls.
Unknown/missing facts cannot be
turned into known zero by parsing or transitioning. A standalone receipt validates
its own reservations, not an absent grant's authority; the budget checks grant fit.

**Complete budget transition rules.** Identity/grant/digest/limits/input/proof are
immutable; logical_slot_id changes null->one ID only on unused->claimed. Same-state
exact equality is idempotent. main/intent/decision counters never decrease and grow
one charged main request at a time (intent only 0->1); a budget update may either
charge one main request OR append/transition one receipt OR change slot state, not
combine unrelated operations. All aggregate equations must hold before and after.
Main charges are allowed in any slot state if remaining main/root credit exists;
they never reopen generation. No generator receipt exists in unused/claimed.
running admits one next reserved receipt only after prior receipts are settled
(non-stopping success/backend_failed); five total, never a sixth. No appends in
terminal/uncertain states. A receipt update uses exactly the table above, preserves
list order, and never removes receipts. Refinement of already-pending facts during
drain/recovery is allowed without changing the consumed slot identity.

Complete legal slot-edge table (every omitted edge is forbidden):

| Old slot | New slot | Constraint |
|---|---|---|
| any | identical same state | exact idempotence, or one allowed main charge/receipt update; no replay |
| unused | claimed | set logical_slot_id once; no receipts yet |
| claimed | running | same claimed identity; before producer entry |
| claimed | failed / cancelled | consumed even without receipts; owner locally drained |
| claimed | uncertain | ownership/entry uncertainty; consumed |
| running | complete / partial | at least one settled success, all receipts locally drained, no control stop |
| running | failed / cancelled | all local requests/workers drained; no re-entry |
| running | uncertain | unresolved ownership/dispatch/cleanup; no more rounds |
| uncertain | failed / cancelled | authoritative recovery/drain only; never generation restart |

Same-state updates must be one allowed main charge or receipt change, not a new
invocation. complete/partial require every receipt settled or not_dispatched,
drained=true and no control-stop/bound violation; scientific success/count is
separately validated by the generator and later closure, not inferred by this
parser. failed/cancelled require local drain complete; otherwise use uncertain.
All non-unused states remain consumed even if every reservation was released.

Release arithmetic excludes not_dispatched receipts ONLY as a **claimed transition**.
A syntactically valid evidence ID is not proof. The runtime journal owner must
independently verify its exclusive unstarted/never-entered barrier, worker drain,
and durable checkpoint/event before accepting that transition and exposing credit.
On crash, stale writer, commit-then-error or caller-only claim it retains uncertain
debits until that proof exists. Pure parsing/transition functions do NO server
authorization, no I/O, no CAS, no signature verification and no persistence proof.
Never use parsed metadata alone to authorize dispatch or release; C must prove
authenticated journal reconstruction and single-writer monotonic persistence.

Contract RED additions (future A-contract file, not authored now):
test_exact_required_nullable_fields_and_closed_enums;
test_parser_revalidates_constructed_models_and_detaches_nested_values;
test_all_receipt_edges_idempotence_and_immutable_identities;
test_budget_edges_atomic_operations_and_release_arithmetic;
test_usage_missing_zero_malformed_and_over_bound_preserve_facts;
test_syntax_release_claim_is_not_runtime_authority.
Generate the negative edge matrix from the complete table; do not skip missing APIs.

R2 exact future test additions, within the existing allowlists/selections only:

- A-contract: test_usage_parser_4096_4097_bit_boundary. Use 1<<4095 and
  (1<<4096)-1 (both 4096 bits) as observed positive over-bound usage against the
  existing small synthetic reservation: when the full envelope fits, preserve
  the exact numbers and bound_violation. Use 1<<4096 (4097 bits, 1234 decimal
  digits) as the rejection control despite its fitting the byte budget. Cover
  raw dict, nested receipt/budget, constructed/copy model and pure transition
  entry points; fixed errors, no coercion/clipping and no source scalar-guard edit.
- A-contract: test_usage_parser_rejects_byte_overflow_independently_of_scalar_guard.
  All integers satisfy <=4096 bits; construct otherwise schema-valid envelopes
  exactly at 16384 bytes and at 16385 using permitted bounded field lengths,
  never extra padding fields. Assert acceptance/rejection at the byte boundary;
  grant still independently fits 4096 bytes. This is a representation test, not
  authenticated history or backend evidence. Size is the stated default-separator
  UTF-8 encoding; do not confuse bit length with decimal character count.
- A-contract: test_bounded_overflow_fact_is_syntax_not_upstream_normalization.
  Supply explicit scalar/wire/combined normalized facts with null omitted counts,
  unchanged reservations and valid retained other counts. Scalar fact must carry
  over_bound/bound_violation/generation_token_bound_violated; reject scalar fact
  mislabeled missing/malformed/success. Assert all debits retained, zero/refund
  substitutions rejected, no raw overflow accepted merely because an issue is set.
  These fixtures exercise parser/transition syntax ONLY; they do not prove that
  production normalized a response, and grant no client implementation permission.
- A-real-path, only after interface review and its separate preparation release:
  test_upstream_usage_scalar_and_wire_overflow_preserves_violation. Exercise all
  three real generator/client modes with lowest MockTransport responses for the
  same 4096/4097-bit boundary, byte-only overflow and combined overflow. The
  post-response boundary must normalize BEFORE receipt parsing; assert retained
  exact eligible counts or bounded issue/null facts, fixed stop code, no next
  POST, no refund, no seed/partial success and no raw number in errors/logs. For
  byte overflow, use a valid pre-response journal whose proposed new observation
  crosses 16KiB; never treat a previously corrupt journal as normalization evidence.
  Parser rejection is reported separately from reached upstream normalization;
  missing APIs remain API RED. No such tests are authored/executed in this turn.


## 3. Future file allowlists — independently released slices

This table is a proposal, not present write permission. Every slice also updates
only this plan's own evidence section after the parent unfreezes documentation.

| Slice | Future production writes | Future test writes |
|---|---|---|
| A: pure closed contracts + actual-dispatch RED | Only `src/agent/contracts/decision_bindings.py`, after contract RED; no client/control implementation | New `tests/agent/test_generator_control_contracts.py`, `tests/agent/test_generator_invocation_control.py`; reuse existing owned transport fixture context without global conftest |
| B: optional local control + narrow relay, gated G3/G4 | `src/agent/harness/decision_execution.py`, `src/agent/tooling/adapters.py`, `src/agent/tools/llm_molecular_generator.py`, `src/web/models/ollama_model.py` | Previous two tests; explicit signature-only migration in `tests/agent/test_generation_transport_characterization.py`; new `tests/agent/test_generator_control_lifetime.py` |
| C: durable root slot, inclusive budget and B8 non-replay | `src/agent/contracts/decision_bindings.py`, `src/agent/harness/decision_loop.py`, `src/agent/harness/decision_execution.py`, `src/agent/harness/decision_continuation.py`, `src/agent/runtime/run_session.py`; `decision_policy.py` only for named B2 policy/fingerprint validation | New `tests/agent/test_decision_generation_once.py`, `tests/agent/test_generator_control_persistence.py`; existing `tests/agent/test_ordinary_admission_budget.py`, `tests/agent/test_decision_continuation.py`, `tests/agent/test_decision_binding_inputs.py` |
| D: whole-candidate obligations and real joins | `src/agent/contracts/binding_requirements.py`, `src/agent/contracts/decision_bindings.py`, `src/agent/harness/decision_bindings.py`, `src/agent/harness/decision_binding_acceptance.py`, `src/agent/runtime/run_session.py`, `src/agent/evidence/ledger.py`; narrow new validator `src/agent/validators/decision_candidate_requirements.py` already contemplated by approved plan | New `tests/agent/test_decision_candidate_closure.py`; existing `tests/agent/test_decision_binding_arguments.py`, `test_decision_binding_requirements.py`, `test_decision_binding_session.py`, `test_decision_binding_acceptance.py` in that directory |
| E: report/Web dependency integration | Dependency-owner release only: `src/agent/presentation/evidence_report.py`, `src/web/decision_chat.py`; B2 admission/root accounting in `src/web/decision_request.py` and `src/web/decision_runtime.py` requires explicit re-pinned Web scope approval | New `tests/agent/test_dynamic_evidence_report.py`, `tests/agent/test_web_dynamic_bindings.py`; named existing report/reference nodes and `tests/home_evidence_report_test.js` |

Read-only dependencies unless a new scope decision: generation_ranking_contract.py,
CandidateSet schema, CandidateRanker/scientific producers/formulas, workflow templates,
shared registry retry/idempotence policy, ordinary_admission.py, worker_ownership.py,
SQLite schema/store, configuration/assembly and model assets. If an ownership or
atomicity proof needs one of these files changed, stop with the exact missing seam;
do not silently include it in the slice or detach cleanup to make a test pass.

## 4. Task A — closed contract RED and real transport RED first

**Two-stage release, not one blanket A grant:** execute planning order
A1/A2 -> separately approved A-contract RED -> A6 contract implementation/GREEN
only if released -> interface re-review -> separate A3/A4 real-path test-source
release -> A5 actual-path RED. No tests are prepared in the present document-only
turn. Contract GREEN cannot substitute for real-path preparation or G3 evidence.

- [ ] A1: Write the contract module with imports inside test bodies, so missing
  proposed APIs produce clearly identified call-phase API RED, not collection
  failure. Start with the executable fixture/example below; all numeric values
  are synthetic contract data, never a deployment grant:

```python
import pytest

def synthetic_grant_payload():
    return dict(version="1", generator_generation="a" * 32,
                backend_revision="offline-contract-v1",
                model_artifact_digest="b" * 64, tokenizer_digest="c" * 64,
                template_options_digest="d" * 64,
                bound_policy_revision="offline-table-v1",
                total_reserved_tokens=6000, prompt_token_ceiling=200,
                context_token_limit=2048, completion_token_limit=1000)

def test_grant_round_trip_is_syntax_not_backend_authority():
    from src.agent.contracts.decision_bindings import parse_generator_token_grant
    payload = synthetic_grant_payload()
    grant = parse_generator_token_grant(payload)
    assert grant.model_dump(mode="json") == payload
    assert "verified" not in grant.model_fields
    with pytest.raises((ValueError, TypeError)):
        grant.completion_token_limit = 1

@pytest.mark.parametrize("field,value", [
    ("total_reserved_tokens", True), ("prompt_token_ceiling", "200"),
    ("context_token_limit", 0), ("completion_token_limit", 999),
    ("model_artifact_digest", "not-a-digest"), ("verified", True),
])
def test_grant_rejects_untrusted_shape(field, value):
    from src.agent.contracts.decision_bindings import parse_generator_token_grant
    payload = synthetic_grant_payload()
    payload[field] = value
    with pytest.raises((ValueError, TypeError)):
        parse_generator_token_grant(payload)
```

- [ ] A2: Add exact contract families
  `test_grant_rejects_oversize_nested_and_non_native_values`,
  `test_receipt_transition_and_identity_validation`,
  `test_budget_rejects_inconsistent_sums_or_duplicate_rounds`,
  `test_unknown_dispatch_retains_debit_and_bound`,
  `test_root_budget_counts_intent_repair_and_generator_separately`.
  Include M11/G5: no finish credit; M14/G1: only one remaining request; M16/G0:
  zero generator allowance; G5: no sixth; configured main limit below16; missing
  versus zero usage; native bool/NaN/negative counts; forged root/grant/source IDs.
- [ ] A3: Only after the contract stage, interface re-review and a distinct G1
  real-path test-source release, prepare RED in
  `tests/agent/test_generator_invocation_control.py`. Reuse
  isolated_production_context/mocked_client/ScriptedHTTP from
  `tests/agent/test_generation_transport_characterization.py`; construct the
  actual Ollama object without constructor discovery. Only replace lowest HTTP
  transport. Do not replace execute/generate helpers/_call_llm_sync/client methods.
  Test controls may use a finite exact-prompt-to-bound table; a missing key fails,
  no character multiplier or unrestricted fake "verified" method.
- [ ] A4: Define these tests with three modes native-sync/coroutine-no-loop/
  coroutine-running-loop, and separate missing-API failures from reached behavior:
  `test_missing_or_stale_grant_blocks_before_transport`;
  `test_final_prompt_and_completion_are_reserved_before_each_post`;
  `test_real_five_round_transport_counts_match_receipts`;
  `test_usage_missing_malformed_or_over_bound_never_refunds`;
  `test_control_stop_crosses_all_helper_catches_without_next_round`;
  `test_captured_method_and_control_cross_thread_bridge_once`.
  Use real HTTP200/error/malformed/doneFalse/nonstring/disconnect replies.
  Count logical entry, helper round, client entry and transport entry separately;
  "five rounds" must not be guessed from final candidate count.
- [ ] A5: Obtain runner review and a distinct exact RED slot for the A dispatch
  selection below once; the A contract selection had its own earlier slot.
  Record API/call/collection/setup/teardown phases accurately, all output/handles,
  warnings/skips and hashes. No collection error substitutes for dispatch evidence.
- [ ] A6: Only if parent releases the pure-contract source, add the strict frozen
  records/parsers in the existing contract module, with bounded pre-serialization
  checks and cross-field validation stated in §2. Run a separately granted A
  contract GREEN; actual-control behavior remains unproved until G3/G4 and the
  separately authorized real-path execution. Do not claim all A
  GREEN merely because syntax tests pass. Present reviewed G3 proof requirements
  and missing backend evidence to parent before any client/control source edit.

## 5. Task B — optional immutable control and minimal adapter relay

**Recommended proposed API; not an existing callable interface:**

```python
# Existing public positionals/defaults stay unchanged.
LLMMolecularGenerator.execute(query, temperature=0.7, mol_count=None,
                              *, invocation_control=None)
OllamaModel.generate(prompt, temperature=0.7, max_tokens=1500,
                     *, strict_errors=False, invocation_control=None)
OllamaModel.generate_async(prompt, temperature=0.7, max_tokens=1500,
                           *, strict_errors=False, invocation_control=None)
```

In decision_execution.py, a frozen `GeneratorInvocationControl` captures root/
logical-slot identity, existing ActiveSegment, trusted grant and backend/bound
implementation identity, cancellation check and idempotent journal operations.
Mutable journal state lives in the root's existing Session owner, not this record.
A frozen per-round view records round1..5/reservation ID; it carries the captured
method/control through the bridge, never re-reads shared binding to redispatch.
Callbacks are process-local authority only when injected by the trusted owner;
never deserialize them from metadata. Exact construction, methods, callbacks and
acknowledgements are specified in §5.1, superseding the earlier shorthand names.
Each checks captured identities and returns bounded detached values or a fixed,
sanitized `GeneratorControlStop`. No provider text/request objects in stop errors.

- [ ] B1: Before production edits, require G3 and G4. Compare two relay options:
  recommend an opt-in adapter parameter captured in the EXISTING worker closure,
  with ContextVar set/reset INSIDE that worker around invoke; LegacyPythonToolAdapter
  reads it only for the real authorized generator and passes the explicit keyword.
  Keep GenerationToolAdapter's schema/normalization override untouched.
  An explicit-parameter-only relay is the alternative only if parent approves the
  additional override changes it requires. Never copy the adapter/semaphore/pool.
- [ ] B2: Extend RED in `test_generator_invocation_control.py`:
  `test_control_relay_shares_adapter_slot_and_worker_owner`,
  `test_concurrent_roots_cannot_exchange_controls`,
  `test_queued_stop_rolls_back_without_producer_entry`,
  `test_relay_reset_after_every_failure`,
  `test_default_adapter_passes_no_new_keyword`.
  Assert the exact original semaphore/adapter identity, reservation rollback and
  cleanup. A ContextVar set only in the calling thread is insufficient: the real
  executor boundary must install it and finally reset it.
  Author the six lifetime families listed in B6 in the same RED preparation,
  before B3–B5 production changes. Execute the B selection once only after its
  separate SOURCE/runner/sole-slot approval; missing-API cases do not establish
  that a blocked request or cleanup branch was actually reached.
- [ ] B3: After accepted RED, add only the released relay and control opt-in.
  No-control paths keep old calls without added kwargs. Controlled generator
  preflight requires the actual bound client methods and explicit accepted
  signature, never TypeError redispatch or silent fallback. Client control requires
  strict_errors=True; absent control preserves PR97 default/strict behavior.
- [ ] B4: Pass control explicitly through final-prompt helpers and captured bridge.
  Add GeneratorControlStop to explicit propagation in execute/helpers/retry before
  broad catches. Durable settlement failure and budget/cancel/deadline/identity
  failure abort scheduling; ordinary settled backend failure may consume one round
  and allow the next within the same bounded invocation. Keep unresolved backend
  execution/cleanup uncertain and non-retryable; a local timeout alone proves no drain.
- [ ] B5: At actual client boundary: check identity/active state, validate the final
  bound/context, durably reserve, recheck after journal/queue delay, durably mark
  dispatch intent, then enter the captured POST once. Parse strict text and nullable
  usage from that same response; settle fixed status even on errors. Persistence
  after a POST cannot cause another POST. If settlement cannot be established,
  retain debit/uncertainty and prohibit more rounds. Never label a pre-call journal
  marker as observed remote execution.
- [ ] B6: Implement against the already-authored, accepted lifetime RED in
  `tests/agent/test_generator_control_lifetime.py`:
  `test_stop_between_rounds_prevents_next_post`,
  `test_deadline_does_not_reset_across_rounds_or_cleanup`,
  `test_blocked_sync_post_retains_owner_until_transport_returns`,
  `test_async_cancel_drains_request_and_loop_before_lease_exit`,
  `test_repeated_cancel_cannot_release_while_cleanup_is_blocked`,
  `test_cleanup_failure_keeps_owner_unsettled`.
  Use deterministic events/barriers and synthetic clocks; release every barrier and
  join every thread/task in finally. Verify no next round, physical settlement
  before lease exit/model switch/shutdown, and honest stopped versus uncertain.
  Reuse existing WorkerOwner; unprovable tool-internal ownership is a scope blocker.
- [ ] B7: Run separate B GREEN only after reviewed source freeze/slot. Preserve
  first-round10, multi-round10, five-round partial/failure, unchanged seed, legal I,
  count/temp/target and all legacy chat/stream/close tests. One explicit PR97 test
  migration is needed: its exact client-signature assertion at line453 gains only
  invocation_control as keyword-only defaultNone, retaining every old positional,
  default and strict assertion. Its uncontrolled repeat-entry characterization
  stays valid; root non-replay is tested on the controlled path, not imposed globally.

### 5.1 Review amendment R1 — exact local-control proposal

These are **proposed** APIs in decision_execution.py, not existing interfaces and
not a second framework. No constructor performs discovery or makes a deployment
grant. The production assembly/injection owner remains a G3 scope gate.

Construction is keyword-only; every argument is required, with no defaults:
```python
GeneratorInvocationControl(
    *, root_id: EvidenceId, logical_slot_id: GenerationId,
    input_sha256: Sha256, proof_sha256: Sha256,
    grant: GeneratorTokenGrant, segment: ActiveSegment,
    clock: Callable[[], float], cancelled: Callable[[], bool],
    attest_backend: Callable[[GeneratorTokenGrant], None],
    bound_prompt: Callable[[GeneratorTokenGrant, str], int],
    reserve_receipt: Callable[[GeneratorRequestReceipt], GeneratorJournalAck],
    commit_receipt: Callable[[GeneratorRequestReceipt, GeneratorRequestReceipt],
                             GeneratorJournalAck],
)

```

Use a frozen dataclass with exact-class/field validation (subclasses rejected).
Reparse grant, validate ID full matches, copy/revalidate existing ActiveSegment,
and require each callback callable. Those checks do NOT authenticate a callback.
Store no mutable list/dict or current round/result on this object. clock returns
finite native float/int >=0; cancelled returns exact bool; wrong results stop
generation_control_invalid. Deadline remains the same captured ActiveSegment,
never reconstructed per round or from a browser clock. Capture references once.

attest_backend must return exactly None only after checking the *current trusted*
backend/model/tokenizer/template/options identity against grant; it is called
before bounding and again immediately before dispatch after journal/queue delays.
Stale/unprovable identity raises GeneratorControlStop(generator_token_bound_unavailable).
bound_prompt(grant, final_prompt) returns a native positive int that upper-bounds
the complete effective backend prompt; it is not an estimator. Unknown prompt,
unproved added tokens or callback failure is token_bound_unavailable, never a
fallback multiplier. Synthetic exact-prompt tables are test fixtures only.

GeneratorJournalAck is an exact frozen dataclass with two required fields:
receipt: GeneratorRequestReceipt (reparsed/detached), applied: bool (native).
The owner returns applied=True only for its first durable application of that
operation; an exact repeat returns the same receipt with applied=False. Different
payload for an existing operation is rejected, never overwritten. reserve_receipt
accepts only a new reserved receipt. commit_receipt accepts expected-old/new,
enforces the transition, full budget arithmetic and authoritative owner state
atomically, and returns an ack of the EXACT proposed new receipt. A differing ack,
non-ack object or callback failure aborts with generation_journal_unavailable.
Neither callback accepts authority from the record alone. Exception details are
suppressed at the control boundary, including arbitrary injected callback errors.

False reserve/dispatch-marker ack **never** authorizes another POST. Raise
generation_slot_consumed for a re-entered reservation/marker; an idempotent
settlement ack is allowed to finish cleanup only. Commit-then-error is reconciled
by retrying/querying the SAME owner operation, not by repeating HTTP. A callback
cannot expose applied=True twice across restart; C must prove this property.
Before that proof the memory example below is only a local contract test double.

Exact round construction and callable surface:
```python
GeneratorInvocationControl.check_active() -> None
GeneratorInvocationControl.for_round(index: int) -> GeneratorRoundView
GeneratorRoundView.check_active() -> None
GeneratorRoundView.reserve(final_prompt: str, completion_limit: int) -> GeneratorRequestReceipt
GeneratorRoundView.mark_dispatch(old: GeneratorRequestReceipt) -> GeneratorRequestReceipt
GeneratorRoundView.settle(
    old: GeneratorRequestReceipt, *, entered: bool, backend_failed: bool,
    response_usage: object | None, backend_request_id: str | None,
    drained: bool,
) -> GeneratorRequestReceipt
GeneratorRoundView.mark_uncertain(
    old: GeneratorRequestReceipt, *, code: str,
) -> GeneratorRequestReceipt

```

GeneratorRoundView is an exact frozen dataclass with init=False; only for_round
constructs it. Required fields: invocation (exact control), round_index (native
1..5), reservation_id, dispatch_marker_id, client_dispatch_id (GenerationId).
IDs are stable: first32 hex of EvidenceLedger.output_digest of the native dict
{kind: "reservation"/"dispatch-marker"/"client-entry", root_id, logical_slot_id,
round_index}. They are domain-separated operation identifiers, not observations.
A round view may have all allocated IDs while its receipt still has null marker/
client IDs; only the corresponding real event populates receipt fields. Repeated
for_round constructs an equal view but gives no repeat-execution authority.

reserve requires literal native completion_limit=1000, checks activity/backend,
bounds the exact prompt, checks ceiling/context, then calls owner reserve once.
mark_dispatch rechecks activity/backend, writes intent marker, checks activity
again and only a first-applied ack admits the captured single POST call.
If a check stops after reservation/marker, retain that debit and journal uncertain
before unwinding unless the exclusive owner can durably prove never-entered using
the no-entry path below. If even that write fails, preserve the last durable record
and quarantine the root; never clear an in-memory counter and call it rollback.
At the client, POST is entered once in the same guarded try/finally; after it
returns/raises, settle records client-entry identity (dispatched self-edge) and
then terminal facts. If the task/bridge cannot determine entry or physical drain,
mark_uncertain instead; no optimistic settled=true merely because await timed out.
entered/drained are exact bools supplied by this trusted client boundary, never
copied from a response, caller model JSON, parser or callback claim. entered=false
cannot settle; it becomes uncertain unless the owner separately proves no-entry.
A proven-no-entry release is a journal-owner recovery operation using the pure
transition API, NOT a public round.release(proof_string) authority shortcut.

settle receives either None (no response observed) or the exact native response
projection containing only whichever prompt_eval_count/eval_count keys were
present ({} means response observed with both missing). It installs usage according
to §2.1/R2: classify scalar overflow before raw receipt parsing/serialization,
then enforce the complete wire bound without losing any known bound violation.
It preserves no raw response text. backend_failed=True covers existing
strict protocol/HTTP errors with physically drained local requests. Valid/missing
usage does not override strict failure. It returns the final receipt, except that
malformed/over-bound usage is journaled as stopped/bound_violation then raises the
corresponding fixed control stop. Missing usage retains the full reservation but
does not alone forbid another round. Uncertain remote compute remains distinct
from drained local transport: a disconnect may close the local request while
leaving backend execution unknown; the trusted backend cancellation/drain contract
must classify it. Without that proof use uncertain and stop, not retry-as-settled.
backend_request_id is null unless the G3-reviewed backend protocol provides a
bounded correlation value; Ollama's present body contract provides no such ID.

The generator accepts only GeneratorInvocationControl or None; the client keyword
invocation_control accepts only GeneratorRoundView or None, and non-null requires
strict_errors=True. Capture the real bound generate/generate_async method using
PR97's exact __self__/__func__/signature checks; bind the additional named keyword
before any dispatch. Pass the captured method and round view through all three
modes, including the executor closure. Never wrap generate to smuggle control,
mutate model control fields, re-read an injected method in the bridge, or catch
RuntimeError from future.result as if get_running_loop failed.

GeneratorControlStop is a proposed Exception subclass, constructor
GeneratorControlStop(code: str), with read-only code from the closed set below,
args=("Generation control stopped",). Unknown input maps to
generation_control_invalid; no custom message/cause/provider repr is accepted.
Public logging is the fixed message plus validated code, without exc_info/raw
traceback text. Explicit propagation precedes broad catches in client, helpers,
round loop and execute. The adapter/Session boundary converts it to a failed
fixed-status tool result; direct controlled execute raises it. Thus a stop after
some candidates does not return them as successful partial generation or add seed;
the bounded receipts persist. Ordinary exhausted five-round partial results remain
unchanged when there was no control stop.

Closed stop codes (spelling is part of this PROPOSAL):
- generation_control_invalid
- generator_token_bound_unavailable
- generation_cancelled
- generation_deadline_exceeded
- generation_request_budget_exhausted
- generation_token_budget_exhausted
- generation_context_exceeded
- generation_journal_unavailable
- generation_dispatch_uncertain
- generation_usage_invalid
- generation_token_bound_violated
- generation_cleanup_unsettled
- generation_slot_consumed

check_active checks cancellation first, then remaining_credit(segment, now=clock());
zero remaining raises deadline_exceeded. Request/token/context failures have their
matching codes; prompt bound above granted ceiling is token_bound_unavailable.
Owner/lease release is blocked until local drain is proven; a fixed stop is not
itself a release acknowledgement. GeneratorControlStop is deliberately not a
RuntimeError subclass, but the existing narrow get_running_loop catch must remain
narrow regardless.

### 5.2 Complete future three-mode installation example (DOCUMENT ONLY)

This single proposed test belongs in A-real-path's named module only AFTER the
interfaces above receive review and a separate test-source grant. It is not
executed or installed now. It uses the existing finite ScriptedHTTP/MockTransport
context, real bound client, real generator/helpers and real RDKit. It does not
monkeypatch generate, _call_llm_sync, _generate_with_llm or _generate_with_retry.
The fixture's numbers are synthetic contract data; no backend model is consulted.

```python
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest


@pytest.mark.parametrize("mode", [
    "native-sync", "coroutine-no-loop", "coroutine-running-loop",
])
def test_actual_two_round_control_installation(tmp_path, mode):
    # Imports inside the test body: absent proposed symbols = call-phase API RED.
    from tests.agent.test_generation_transport_characterization import (
        isolated_production_context, mocked_client, ok,
    )
    from src.agent.contracts.decision_bindings import (
        parse_generator_token_grant, parse_b_root_budget,
        transition_b_root_budget, transition_generator_request,
    )
    from src.agent.contracts.ordinary_admission import begin_segment
    from src.agent.evidence.ledger import EvidenceLedger
    from src.agent.harness.decision_execution import (
        GeneratorInvocationControl, GeneratorJournalAck, GeneratorControlStop,
    )

    def probe():
        payload = dict(
            version="1", generator_generation="a" * 32,
            backend_revision="offline-contract-v1",
            model_artifact_digest="b" * 64, tokenizer_digest="c" * 64,
            template_options_digest="d" * 64,
            bound_policy_revision="offline-table-v1",
            total_reserved_tokens=6000, prompt_token_ceiling=200,
            context_token_limit=2048, completion_token_limit=1000,
        )
        grant = parse_generator_token_grant(payload)
        grant_sha = EvidenceLedger.output_digest(payload)
        identity = dict(root_id="root.offline.1", logical_slot_id="e" * 32,
                        input_sha256="2" * 64, proof_sha256="3" * 64)
        # This fixture starts after synthetic root admission. It is not evidence
        # that Session claimed a durable slot or that these digests are authentic.
        initial = dict(
            version="1", policy="b-root-inclusive-16-v1", **identity,
            slot_state="running", grant=payload, grant_sha256=grant_sha,
            main_limit=16, root_limit=16, intent_requests=1,
            decision_requests=1, main_requests=2, generator_request_debits=0,
            root_request_debits=2, reserved_tokens=0, generator_requests=[],
        )

        class MemoryJournal:
            def __init__(self):
                self.lock = threading.Lock()
                self.view = parse_b_root_budget(initial)

            def install(self, receipts):
                candidate = self.view.model_dump(mode="json")
                candidate["generator_requests"] = receipts
                debited = [r for r in receipts if r["phase"] != "not_dispatched"]
                candidate["generator_request_debits"] = len(debited)
                candidate["root_request_debits"] = 2 + len(debited)
                candidate["reserved_tokens"] = sum(
                    r["reserved_prompt_tokens"] + r["reserved_completion_tokens"]
                    for r in debited
                )
                self.view = transition_b_root_budget(self.view, candidate)

            def reserve(self, new):
                with self.lock:
                    rows = list(self.view.generator_requests)
                    for row in rows:
                        if row.reservation_id == new.reservation_id:
                            if row != new:
                                raise GeneratorControlStop("generation_slot_consumed")
                            return GeneratorJournalAck(receipt=row, applied=False)
                    fresh = transition_generator_request(None, new)
                    # Pure budget transition checks full identities, next round,
                    # prior settlement, root16, five rounds and token arithmetic.
                    self.install([r.model_dump(mode="json") for r in rows]
                                 + [fresh.model_dump(mode="json")])
                    return GeneratorJournalAck(receipt=fresh, applied=True)

            def commit(self, old, new):
                with self.lock:
                    rows = list(self.view.generator_requests)
                    indexes = [i for i, r in enumerate(rows)
                               if r.reservation_id == old.reservation_id]
                    if len(indexes) != 1:
                        raise GeneratorControlStop("generation_journal_unavailable")
                    i = indexes[0]
                    if rows[i] == new:
                        return GeneratorJournalAck(receipt=rows[i], applied=False)
                    if rows[i] != old:
                        raise GeneratorControlStop("generation_journal_unavailable")
                    # Never accept a caller's no-entry claim in this example.
                    if new.phase == "not_dispatched":
                        raise GeneratorControlStop("generation_journal_unavailable")
                    rows[i] = transition_generator_request(old, new)
                    self.install([r.model_dump(mode="json") for r in rows])
                    return GeneratorJournalAck(receipt=rows[i], applied=True)

        journal = MemoryJournal()
        now = [10.0]                    # synthetic monotonic clock, no wall sleep
        cancel = threading.Event()
        checks = {"clock": 0, "cancel": 0, "attest": 0}
        bounded_prompts = []
        query = "generate 10 molecules"
        ten = tuple("C" * n for n in range(3, 13))

        with isolated_production_context(tmp_path) as production:
            with mocked_client(production, [ok(*ten[:5]), ok(*ten[5:])]) as pair:
                model, transport = pair
                if mode != "native-sync":
                    # Actual class-defined bound method; not a wrapper.
                    model.generate = model.generate_async
                expected_method = (production.model.generate if mode == "native-sync"
                                   else production.model.generate_async)
                assert model.generate.__self__ is model
                assert model.generate.__func__ is expected_method
                generator = production.generator(llm_model=model)
                final_prompt = generator.generation_prompt_template.format(
                    requirements=query, count=10, target_evidence="[]")
                # Existing retry leaves intent count=10 on both rounds.
                prompt_table = {final_prompt: 200}  # finite fixture, NOT a tokenizer

                def clock():
                    checks["clock"] += 1
                    return now[0]

                def cancelled():
                    checks["cancel"] += 1
                    return cancel.is_set()

                def attest(candidate):
                    checks["attest"] += 1
                    if candidate.model_dump(mode="json") != payload:
                        raise GeneratorControlStop("generator_token_bound_unavailable")
                    # Only synthetically binds this explicit HTTP fixture.
                    if model.model_name != "gmm-llama:latest":
                        raise GeneratorControlStop("generator_token_bound_unavailable")
                    return None

                def bound(candidate, prompt):
                    if candidate != grant or prompt not in prompt_table:
                        raise GeneratorControlStop("generator_token_bound_unavailable")
                    bounded_prompts.append(prompt)
                    return prompt_table[prompt]

                control = GeneratorInvocationControl(
                    **identity, grant=grant,
                    segment=begin_segment(now=10.0, allowance=30.0),
                    clock=clock, cancelled=cancelled, attest_backend=attest,
                    bound_prompt=bound, reserve_receipt=journal.reserve,
                    commit_receipt=journal.commit,
                )

                def observe(event):
                    body = event["payload"]
                    assert body == dict(
                        model="gmm-llama:latest", prompt=final_prompt, stream=False,
                        options=dict(temperature=0.23, num_predict=1000))
                    assert event["url"].endswith("/api/generate")
                    assert event["method"] == "POST"
                    last = journal.view.generator_requests[-1]
                    assert last.phase == "dispatched"
                    assert last.dispatch_marker_id is not None
                    # Client observation is recorded after entered POST returns.
                    assert last.client_dispatch_id is None
                    assert journal.view.reserved_tokens == 1200 * len(transport.calls)

                def advance(_event):
                    now[0] += 0.25

                transport.on_request = observe
                transport.on_response = advance

                def invoke():
                    return generator.execute(query, temperature=0.23, mol_count=10,
                                             invocation_control=control)

                if mode == "coroutine-running-loop":
                    async def in_running_loop():
                        return invoke()  # actual generator executor bridge
                    result = asyncio.run(in_running_loop())
                else:
                    result = invoke()

                assert result["success"] is True
                assert [row["smiles"] for row in result["data"]] == list(ten)
                assert result["quality"]["requested_count"] == 10
                assert result["quality"]["actual_count"] == 10
                assert result["quality"]["partial_generation"] is False
                assert len(transport.calls) == 2
                assert [c["channel"] for c in transport.calls] == (
                    ["sync", "sync"] if mode == "native-sync" else ["async", "async"])
                if mode == "coroutine-running-loop":
                    assert all(c["thread"] != threading.get_ident()
                               for c in transport.calls)
                receipts = journal.view.generator_requests
                assert [r.round_index for r in receipts] == [1, 2]
                assert all(r.phase == "settled" and r.outcome == "success"
                           and r.drained and r.client_dispatch_id for r in receipts)
                assert all(r.usage_status == "valid" and r.usage.prompt_tokens == 11
                           and r.usage.completion_tokens == 7 for r in receipts)
                assert journal.view.generator_request_debits == 2
                assert journal.view.root_request_debits == 4
                assert journal.view.reserved_tokens == 2400  # no usage refund
                assert bounded_prompts == [final_prompt, final_prompt]
                assert min(checks.values()) >= 2
                assert control.segment.deadline == 40.0
                cancel.set()
                with pytest.raises(GeneratorControlStop) as stopped:
                    control.check_active()
                assert stopped.value.code == "generation_cancelled"
                cancel.clear()
                now[0] = 40.0
                with pytest.raises(GeneratorControlStop) as stopped:
                    control.check_active()
                assert stopped.value.code == "generation_deadline_exceeded"
                assert len(transport.calls) == 2
            # mocked_client finally calls real model.close outside running loop;
            # it asserts both clients and every finite response/stream are closed.
        # isolated_production_context restores cwd/logger and closes added handlers.

    # Isolate helper-created loop state from pytest; join even when assertions fail.
    # This is the existing probe pattern, not a replacement of a tested method.
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(probe).result()

```

The example deliberately does not prove persistence, cryptographic/backend
authority, transport disconnect drain or between-round cancellation. Those remain
the separately named A/B/C cases; do not promote its two successful mocked replies
to deployment evidence. An unavailable import/signature is **missing-API RED**.
An assertion after actual transport/helper entry is **reached behavior RED**, with
entry counts recorded. A collection/setup/cleanup/import dependency failure is
reported in its actual phase, never counted as a demonstrated budget/seed/drain bug.
No skip/xfail fallback for unavailable APIs and no fixture-produced success result.

Written-review checklist before A-real-path source release: confirm all symbols,
constructor arguments and ack semantics above; confirm the shared context remains
at the pinned source; review the finite prompt table against the actual template;
then re-pin the future test source and reviewed runner. A reviewer must not run this
Markdown example as an alternate launcher or treat it as an already-created test.

Required installation-case refinements in the SAME future invocation module:
test_reentered_reservation_or_dispatch_ack_never_reposts;
test_journal_ack_mismatch_and_false_first_dispatch_stop;
test_control_constructor_revalidates_and_captures_callbacks;
test_round_view_stable_ids_do_not_claim_observed_dispatch;
test_complete_three_mode_fixture_has_real_bound_client_identity.
For cancellation inside rounds use B's deterministic lifetime barriers; do not
pretend the post-success check_active assertions above reach those branches.


## 6. Task C — durable root slot, budgets and non-replay

- [ ] C1: Author `tests/agent/test_generator_control_persistence.py` against actual
  temporary SQLite Session metadata/checkpoints, never host DBs:
  `test_reserve_failure_prevents_post`,
  `test_commit_then_error_retries_only_same_journal_write`,
  `test_dispatch_gap_recovery_retains_unknown_debit`,
  `test_settlement_failure_never_refunds_or_reexecutes`,
  `test_stale_writer_cannot_reduce_budget_or_replace_slot`,
  `test_corrupt_missing_receipts_fail_closed`.
  Reuse retry_persistence only for idempotent writes. Demonstrate exclusive
  root writer across ownership/restart, not merely atomic JSON merging. If the
  present metadata/store API cannot prove this, stop and request an exact storage
  scope amendment; no optimistic stale nested-view overwrite is acceptable.
- [ ] C2: Author `tests/agent/test_decision_generation_once.py` using the actual
  DecisionLoop/Session/adapter/real producer:
  `test_one_root_slot_survives_changed_decision_seed_and_target`,
  `test_consumed_partial_failed_cancelled_uncertain_slots_never_reenter`,
  `test_inner_rounds_share_main_root_allowance`,
  `test_finish_requires_remaining_main_and_root_credit`,
  `test_root_deadline_includes_queue_validation_and_cleanup`,
  `test_resume_validates_slot_receipts_and_generation_before_cas`,
  `test_resume_never_continues_generator_rounds`.
  Keep multiple successful internal rounds toward ten as positive, not replay.
- [ ] C3: Obtain exact C RED slot. Then, only under C release, persist slot,
  b_root_budget and generator_requests alongside Session metadata before producer/
  POST boundaries; persist monotonic uncertain/settled facts on every exit.
  Extend both decide/repair checks and generator checks with inclusive16 while
  preserving existing M semantics. Outer retry/idempotence policy stays shared/
  unchanged; consumed-slot denial belongs to the request-local view.
- [ ] C4: Add B-only8 fingerprint/strict snapshot and history validation in existing
  decision_continuation paths; preserve ordinary6/7 byte/semantic regressions.
  Bind original requirements/source closure, generator generation and grant digest,
  full main journal, logical slot and ordered receipt arithmetic. No upgrading old
  snapshots, reset-to-zero corruption or replaying any tool. Keep512KiB total
  snapshot bound,16KiB budget subview, set-once waiting credit/downward clamp,
  existing TTL/CAS and the same ActiveSegment. Existing B continuation guards stay
  until the complete validating path is released; do not enable incomplete resume.
- [ ] C5: After independent source review and C GREEN, retain a production gate:
  root Web intent/carry admission must charge the same inclusive allowance before
  enabling B2 normal entry. Parent coordinates E's Web-owned release; no alternate
  raw API that sidesteps root accounting is introduced as the positive endpoint.

## 7. Task D — true all-candidate consumer and ranker closure

- [ ] D1: Extend existing binding requirements/proof contracts with a closed B2
  branch; keep B1 exact behavior, forbidden fields and single-role constraints.
  Use existing GenerationBindingArguments/RankingBindingArguments syntax.
  Count/target/seed/core/top-N and analysis-vs-score obligations come from immutable
  admitted requirements, never model-supplied overrides. Add generation/ranking
  roles only to B2 proofs; do not widen every B1 proof to five arbitrary roles.
- [ ] D2: Create `tests/agent/test_decision_candidate_closure.py` with
  `test_actual_multiround_generator_feeds_every_candidate_to_all_consumers`,
  `test_cross_set_same_smiles_cannot_substitute_source_identity`,
  `test_missing_duplicate_foreign_or_conflicting_rows_cannot_complete`,
  `test_core_chirality_is_checked_for_all_candidates`,
  `test_actual_ranker_preserves_scores_weights_nulls_and_prefix`,
  `test_required_score_component_and_top_n_shortfall_block_finish`,
  `test_finish_citations_cover_required_candidate_source_closure`.
  Use actual generator/HTTP mock and RDKit sanitizer, actual property calculator,
  real ADMET/activity tool/domain paths with only lowest backend fixture seams,
  actual CandidateRanker, Session alignment/proof/ledger/seal and model decisions.
  No aggregate fake tool-success dictionaries standing in for the chain.
- [ ] D3: Exact obligations: canonical-unique requested count; original target/seed;
  source evidence+candidate ID+manifest digest (not candidate ID alone); exact
  all-candidate consumer coverage; no foreign/duplicate/conflicting original IDs
  hidden by alignment repair; discarded/missing rows remain partial/unmet.
  Reuse candidate_source metadata and normalization→alignment→proof→ledger→seal.
  Capture raw conflict checks before alignment; accepted result cannot resurrect
  rejected raw data. Explicit core uses actual RDKit chirality-aware substructure
  matching for every candidate; invalid/unavailable core validation blocks success.
- [ ] D4: Build the existing ranker envelope with source molecules, full properties,
  explicitly absent or proven ADMET/activity roles and admitted docking_top_n.
  Run actual CandidateRanker; compare returned real ranking_evidence, weights_used,
  ranked list/top prefix and source IDs, not a second implementation of its formula.
  Mandatory analysis is not mandatory scoring: missing risk_count/total_endpoints
  or normalized_activity/probability cannot be invented from endpoint flags/pIC50.
  A separately reviewed compatible scoring contract is a blocker for those
  required-score positives, not permission to make the obligations optional.
- [ ] D5: Under D release implement the narrow obligation checks, then run D
  RED/GREEN separately. Extend existing evaluate_binding_acceptance and
  verify_binding_closure to one coherent candidate closure; do not fall back to
  completed tool-name membership. Known-good subsets/optional failures cannot
  produce full completion. Novelty/improvement remain unsupported without their
  own reviewed predicates; unchanged valid seed is not an improvement claim.

## 8. Task E — report/ordinary-route dependencies, not a shortcut

- [ ] E1: Parent re-pins/releases G1/A2-owned interfaces and B2 ordinary admission.
  Add `tests/agent/test_dynamic_evidence_report.py` tests
  `test_dynamic_report_reconstructs_sealed_candidate_roles_without_reexecution`
  and `test_dynamic_report_rejects_swapped_partial_or_tampered_sources`.
  Replace unsupported_binding only with validated dynamic reconstruction, preserving
  static report guards, numeric source checks, exact rank weights and output bounds.
- [ ] E2: Add `tests/agent/test_web_dynamic_bindings.py` tests
  `test_same_frames_mount_ack_and_followup_preserve_full_candidate_closure`
  and `test_cancel_and_resume_cannot_release_or_replay_generation`.
  Real ordinary /ws frames must feed existing home normalizer/DOM/controller;
  exact mounted ACK returns through protected confirmation and selected follow-up.
  Foreign cookie, stale revision, out-of-order/unmounted key and forged proof fail;
  candidate ACK conveys selection authority, not calculation success.
- [ ] E3: Under separately reviewed Web/JS runner selections, prove root budget at
  initial intent, decide/repair, resumed admission and every generator POST.
  Keep ordinary chat and B1 regressions, worker drain before lease exit and shutdown.
  No whole Web suite, browser, model or service run is authorized by this document.
- [ ] E4: Parent alone schedules P8 ordinary-route repeats under explicit
  environment/asset authority; verify that G3's earlier backend enforcement/token
  evidence remains valid for the final pins (reprove if identity changed).
  Full targeted/seeded10,
  all-candidate properties/ADMET/activity, actual ranking and same-frame report/
  follow-up positives remain mandatory. Preserve B016/B017 and REAL010/DIVERSE015/
  multi-turn expectations. An unavailable gate or offline synthetic pass closes
  none of these positives.

## 9. Reviewed-runner request and exact future selections

Request parent to create/review an ignored
`scratch/ordinary_chat_offline_runner.py` in THIS tree by changing only REPO in
the approved prior runner (old-tree SHA256
`9F6F5A2F68272442316DD614F6BEBD62C49371669C8E5E90DC4BC50BC5BED778`).
Require full bidirectional byte comparison after REPO normalization and record the
NEW hash; the old hash cannot identify the new-tree runner. No launcher is created
or executed now. Preserve -I -S -B, sanitized environment/temp stores, denied
network before imports, no host .env/config/key/model discovery, controlled
MockTransport and the narrow stdlib loop socketpair exception. No raw pytest,
compile/import smoke test or collect-only execution outside the sole slot.

Approved future command PREFIX, only after runner review and explicit transfer:

```text
C:/Users/xkx52/.conda/envs/MedChat/python.exe -I -S -B scratch/ordinary_chat_offline_runner.py
```

Append exactly the following ordered whole-module selections for that slice;
each gets its own SOURCE freeze and RED/GREEN grant. No predicted collected count,
no -k/-m/xfail/skip conversion, no automatic retry.

**A contract selection:**
```text
tests/agent/test_generator_control_contracts.py
tests/agent/test_decision_binding_arguments.py
```

**A dispatch RED selection:**
```text
tests/agent/test_generator_invocation_control.py
tests/agent/test_generation_transport_characterization.py
```

**B control RED/GREEN selection:**
```text
tests/agent/test_generator_invocation_control.py
tests/agent/test_generator_control_lifetime.py
tests/agent/test_generation_transport_characterization.py
tests/agent/test_generation_temperature_transport.py
tests/agent/test_generator_optimization_input.py
tests/agent/test_worker_ownership.py
```
Lifetime cases must be authored/reviewed before this selection is granted. API
RED and reached lifecycle/behavior RED are reported separately; initial absent
APIs do not count as observed cancellation/drain defects.

**C root/journal selection:**
```text
tests/agent/test_generator_control_persistence.py
tests/agent/test_decision_generation_once.py
tests/agent/test_ordinary_admission_budget.py
tests/agent/test_decision_continuation.py
tests/agent/test_decision_binding_inputs.py
tests/agent/test_dynamic_run_session.py
```

**D full-candidate selection:**
```text
tests/agent/test_decision_candidate_closure.py
tests/agent/test_decision_binding_arguments.py
tests/agent/test_decision_binding_requirements.py
tests/agent/test_decision_binding_session.py
tests/agent/test_decision_binding_acceptance.py
tests/agent/test_generation_ranking_contract.py
tests/agent/test_generation_ranking_contract_integration.py
tests/agent/test_candidate_alignment.py
tests/agent/test_candidate_ranker.py
tests/agent/test_generated_candidate_validation.py
```

**E proposed dependency selection, requiring a fresh Web safety review:**
```text
tests/agent/test_dynamic_evidence_report.py
tests/agent/test_web_dynamic_bindings.py
tests/agent/test_evidence_report_contract.py
tests/agent/test_evidence_report_snapshot.py
tests/agent/test_evidence_report_frames.py
tests/agent/test_scientific_reference_execution.py
tests/agent/test_scientific_reference_web.py
tests/test_model_request_lifecycle.py::test_actual_http_stream_cleanup_precedes_request_release
tests/test_model_request_lifecycle.py::test_actual_ollama_close_attempts_both_clients
tests/test_model_request_lifecycle.py::test_actual_ollama_close_failure_is_redacted_by_owner_only
```
All node parameters included. The distinct Node command
`node tests/home_evidence_report_test.js` also requires review/authorization;
do not run a browser lab or live model as a substitute.

For every granted run: capture HEAD, plan/runner/all touched production/test and
all selected-module SHA256 before/after; keep complete failure output and phase,
actual initial/terminal handle, count/subtests/warnings/skips/pytest duration and
process/runner exit. Poll the actual session to terminal, never restart. Release
the sole slot at terminal, then perform read-only hash comparison. Any failure
returns to parent review without editing assertions or automatic retries.
Freeze after each slice for independent SOURCE and fresh QUALITY. Parent owns
exact staging/commit/PR; this plan grants no commit command.

## 10. Open choices for independent written review

1. **Next release:** recommend A-contract only after this amendment's independent
   review, with its own test-source/RED/contract implementation/GREEN releases.
   Then re-review the fixed interface before a separate A-real-path preparation
   and RED grant. Alternative is to wait for the real G3 evidence first.
   Reject coding a deployable client control with a synthetic grant.
2. **Relay:** recommend the bounded in-worker ContextVar relay in §5 because the
   actual GenerationToolAdapter override retains its raw-validator chain; explicit
   parameter plumbing is acceptable only with its additional named-file review.
   Neither option changes shared instance state or creates another executor.
3. **Trusted grant issuer/proof:** not present in inspected source. Recommend the
   existing server assembly owner supply a reviewed immutable attestation/bound
   implementation and invalidation contract through explicit injection. No new
   asset discovery in generator/client. If enforcement/identity cannot be proved,
   retain zero-dispatch unavailable and request exact owner scope; do not choose
   a permissive fallback or synthetic deployment value.
4. **Receipt atomicity/ownership:** recommend existing exclusive Session root owner,
   serialized monotonic metadata writes and stable IDs, proven by C's races and
   restart cases. If this cannot prevent stale root updates with current store
   APIs, parent must approve the smallest persistence amendment before release.
5. **Closure release order:** recommend B/C control evidence before D, then E.
   D test design can proceed independently, but no B2 route is enabled without
   both root control and complete obligations. Required ADMET/activity-score
   incompatibility and missing dynamic report proof remain explicit blockers.
6. **R1 wire/API choices for re-review:** recommend the exact three closed records,
   native required-nullable semantics, owner-separated release proof, pure transition
   APIs and two-part Session projection in §§2.1–2.2. Recommend explicit first-write
   JournalAck and captured immutable round views in §5.1. Stable allocated IDs
   must not be mistaken for observed dispatch; parsed release claims never refund
   authenticated runtime credit by themselves. These are proposals, not inherited
   approvals from the older design or permission to invent a production token grant.

Self-review: no new architecture or production token numbers; synthetic grant is
test syntax only; source/test/run permissions are separate; all three invocation
modes, five-round/count positives, legacy chat, uncertain persistence, B8 no replay,
root deadline/drain and full candidate/report/P8 gates are mapped above. This
document is the only authored artifact. Independent written review and parent
scope decisions come next, not implementation or execution.

## 11. A-contract test-source preparation checkpoint — 2026-09-27

Parent reports Socrates's independent A-contract written re-review **READY, no
blockers**, with full verification of the pre-preparation plan SHA256
`F75B3527969F996D15C4449BBCB9D353B6098C06D2E09713DAD6B72C7669601A`.
This is approval of that written contract, not SOURCE approval of the newly
authored test below. Parent released only this test source and ownplan recording.
No production contract/parser/transition, runtime control or runner was implemented.

Source pin remains HEAD `c612c9873a04823c936d710861bf890ad30b844a`, branch
`codex/b2-generator-control`. The ten production file pins in §1 remain unchanged.

**New artifact:** `tests/agent/test_generator_control_contracts.py`.
SHA256: `ABAF6100011DB84D6065F44E3013BB74FF325AE023A4CEA224034F1749B8C808`.
1130 source lines; 54 `def test_` declarations counted by read-only text inspection.
These are NOT collected/executed case counts; parametrized totals await the
reviewed runner and explicit sole-slot transfer. No RED or GREEN result exists.

| Source case family | Authored matrix / exact assertions |
|---|---|
| Closed records/API | Three exact parser models plus nested GeneratorUsage; exact fields, every field required, explicit nullable semantics, defaults forbidden, strict/frozen/extra-forbid/revalidate config, parser and pure-transition signatures, detached round trips |
| Hostile ingress / constructed values | Raw dict or exact model only; str/bytes/Mapping/proxy/subclasses/list/null rejected; missing/extra/internal extras/model_copy corruption; nested constructed corruption and cycles; no untrusted serializer hooks; deep immutability and detached dumps |
| Native syntax / limits | GenerationId/Sha256 full-string hex; EvidenceId length/grammar/newline; native int/bool/nonfinite distinctions; completion1000; context fit; grant digest; insufficient allowance remains syntax, not dispatch authority |
| Receipt states and usage | Five phase shapes, closed outcomes/status/issues/13 fixed stop codes; missing versus actual zero; malformed/over-bound exact facts; sanitized fixed errors; marker/client/backend identity distinction |
| Pure receipt transitions | Cartesian `(None + five phases) x five phases` edge matrix, exact idempotence, legal first-write/install/refinement, immutable identities/reservations, observed-ID monotonicity, terminal no-amendment, invalid old/new rejection |
| Root budget / slot | Eight-by-eight slot-edge matrix; exact aggregate equations and ordered unique rounds; root16/main-limit/round5/finish-credit distinction; main charges monotonic and distinct from reserve/settle/slot transitions; consumed states never reopen |
| Release / debit facts | Only not_dispatched syntax claims remove a debit; caller proof ID is explicitly not owner evidence; no usage/timeout/uncertainty refund; settled success/backend failure may allow a next round, unresolved/control-stopped facts may not |
| Scalar / byte / overflow facts | 4096-bit low/high retain exact usage; 4097-bit raw/constructed/copy/nested/transition rejection even below16KiB; grant4096/4097-byte and budget16384/16385-byte fixtures with <=4096-bit integers; scalar/wire/combined bounded facts preserve known violation, fixed stop and all reservations, never normalize in parser |

Byte-boundary fixture builders use only permitted fields and native numeric decimal
lengths, with explicit fixture-size assertions. They are finite synthetic test-data
construction, not a grant issuer, tokenizer, copied production parser, transition
engine, journal or upstream normalizer. Integer-heavy fixtures are contract wire
limits, not business-token choices. Missing proposed APIs are detected by `api()`
called from each test body, so absence is a clearly labeled call-phase API RED,
not an import failure during collection or a setup/skip/xfail substitute.

For 4097-bit tests the JSON-dump guard is scoped to each invalid parser input;
pure transitions are checked outside that monkeypatch, because they may legitimately
serialize the valid old operand before rejecting the new one. The tests do not
invent a stronger all-operands prewalk order than the reviewed design.

**Not installed:** `test_generator_invocation_control.py`, memory journal, client/
generator three-mode probe, callbacks or runtime overflow normalization. Accepting
a manually supplied bounded overflow fact here proves only syntax. Actual upstream
normalization, authoritative no-entry release, durable settlement, cancellation/
drain and real backend token enforcement stay behind their distinct G3/G4/later
slice grants. No G3 proof or B2/scientific completion is inferred from this source.

**Exact pending A-contract selection, unchanged from §9:**

```text
tests/agent/test_generator_control_contracts.py
tests/agent/test_decision_binding_arguments.py
```

Whole modules, all parameters; no filters. Next: independent SOURCE of these test
bytes and plan checkpoint, then parent-approved REPO-only runner preparation/hash
review and a separately scheduled API RED slot. No runner was created or modified
here. On execution later, record real collection/call/setup/teardown phases and
actual counts/handles/terminal status; do not label API absence as reached budget,
normalization, transport, cancellation or persistence behavior. No commit/push.

## 12. Accepted API RED and A-contract source freeze — 2026-09-27

### Actual executed evidence, before implementation

Lovelace independently reported SOURCE READY for ABAF test / 5A8 plan and ten
unchanged source pins. Parent supplied and reviewed ignored runner SHA256
`45567D6B7E7CFD7BF6F910104664148A72FC116DDA678B89FB8F7B26BF6F2FD6`.
After an explicit sole-slot transfer, the exact ordered two whole modules in §11
were executed **once**, with the approved MedChat Python `-I -S -B` launcher.
No selection filtering, runner edit, raw pytest or automatic retry occurred.

- Session **84529**, initial chunk **581d3f**, terminal chunk **54bb08**.
- Actual pytest terminal: **415 failed, 64 passed in 34.27s**.
- All **415** failure markers are **phase=call**. The full log contains exactly
  415 matching `A-contract API RED: missing ...` assertions and no other error
  messages. Missing symbols: GeneratorTokenGrant, GeneratorRequestReceipt,
  BRootBudget, GeneratorUsage, parse_generator_token_grant,
  parse_generator_request_receipt, parse_b_root_budget,
  transition_generator_request, transition_b_root_budget.
- No collection/setup/teardown failures; no warnings or skips reported.
  `ORDINARY_PYTEST_EXIT=1`; process exit1. The existing binding-arguments module
  supplies the64 passes. New contract assertions beyond `api()` were NOT reached.
- The14 before/after files (plan, runner, both selected modules, ten production
  pins) were byte-hash identical; HEAD/status unchanged and tracked diff empty.
  Python slot explicitly **RELEASED** at terminal. No second execution followed.
- Full ignored log: `scratch/b2-a-contract-api-red.log`,427548 bytes, SHA256
  `89D25BD43596CE5C48BE9EFE19047051D21B8D5C477CDCD158220C5BAA024176`.

This RED establishes absent APIs only. It does **not** reproduce schema/transition
behavior, overflow handling, budgets, dispatch, persistence or scientific results.

### Separately authorized implementation, not yet executed

Parent accepted that API RED and released ONLY this plan and the existing
`src/agent/contracts/decision_bindings.py` for minimal A-contract implementation.
The local Python slot is not held here; parent assigned it to Wegener.

Production diff: **480 insertions, no deletions**, appended after the original
260 lines. Original B1 argument/proof definitions, parsers and shared projection
helper remain unchanged. No new production file or package re-export was added.

Current source SHA256:
`D4A56EDF37EF8FE5C315B5B0260538CCA9ECB79E73CA46A331F0E2AB8F45F609`.
The §1 value `4683C6CF...` remains the historical source-before pin; this section
is the new freeze for that one changed file. The other nine §1 pins are unchanged.

Implemented within that single module:

- GeneratorTokenGrant / GeneratorUsage / GeneratorRequestReceipt / BRootBudget
  inherit the existing _ProofView; existing identity aliases and whole-string
  checks are reused. Required-nullable fields and native integer/literal checks
  retain the4096-bit representation guard, completion1000, root16 and round5.
- Three fixed-error parsers reuse _bounded_model_payload and strict JSON
  revalidation, returning detached frozen records. Grant4KiB is checked separately
  inside the complete16KiB budget. The budget uses the existing pure
  EvidenceLedger.output_digest; no ledger instance, lookup or evidence creation.
- Closed usage/state rules preserve exact eligible counts or explicitly supplied
  scalar/wire overflow facts. The parser rejects raw overflowing counts; no
  upstream response normalizer is implemented. An uncertain receipt may retain
  observed usage without becoming a success or dispatch/release authority.
- Pure receipt transitions enforce legal edges, stable identities, idempotence,
  monotonic known observations and terminal no-amendment. Pure budget transitions
  permit one operation at a time, preserving main counters and exact receipt
  arithmetic. Claims of never-dispatched release remain syntax, not owner proof.
- Main/slot changes cannot be combined with receipt writes, old receipts cannot
  be removed, terminal/uncertain slots cannot append rounds, and a non-running
  slot cannot create a reserved->dispatched intent. No HTTP or persistence occurs.

Frozen test SHA256 remains
`ABAF6100011DB84D6065F44E3013BB74FF325AE023A4CEA224034F1749B8C808`;
neither tests nor runner were changed to accommodate implementation. Source self-
review and `git diff --check` are complete; **no Python/import/compile/collection/
GREEN run** has been performed on this implementation. Behavior is still unverified.

Next gate: independent SOURCE on these exact source/test/plan/runner bytes, then
parent alone may grant the unchanged exact2 selection for GREEN. Preserve any
failure and its actual phase; do not relax frozen tests or restart automatically.
Runtime control/client/backend grant, authenticated journal/refund/drain and all
candidate/report/P8 work remain separately gated. No commit/push is authorized.

## 13. A-contract GREEN, independent QUALITY and PR99 alignment — 2026-09-27

This checkpoint supersedes the historical permission/status text above. Parent
released one ff-only alignment and one post-alignment exact2 execution, followed
by this documentation-only append. Those execution permissions are now exhausted;
the sole Python slot is **RELEASED**. No source/test/runner edit, retry, staging,
commit, push or local backend/asset/environment probe is authorized by this entry.

### Independent evidence and exact execution selection

Lovelace reported independent SOURCE **READY, no P1/P2** on D4A source / 8878 plan /
ABAF test. The following results are distinct executions, not one result reused:

| Execution | Actual handle / terminal | Result and scope |
|---|---|---|
| Author GREEN, before alignment | Direct terminal chunk `4611bf`; no live session ID | 479 passed in 4.87s; runner/process exit0; no failures, errors, warnings or skips; 14 before/after hashes identical |
| Socrates fresh QUALITY, before alignment | Parent-reported session `2312`, terminal `41bc3b` | Parent reports 479 passed in 7s, all14 pins unchanged, no blockers and slot released; independent result, not re-executed here |
| Author post-alignment regression | Direct terminal chunk `99ccfa`; no live session ID | 479 passed in 4.91s; runner/process exit0; no collection/setup/call/teardown failures, warnings or skips; new14 before/after hashes identical |

The fresh QUALITY duration is recorded exactly as supplied by parent, without
inventing a more precise pytest duration or an unprovided warning/exit breakdown.
Author GREEN full ignored log was `scratch/b2-a-contract-green-once.log`,
SHA256 `3EA044DE65F34E3AC04C63CFFEA73F23752FB96039D235C2ABD1EE950E1F4B97`.
Post-alignment full output is preserved in tool terminal `99ccfa` (seven progress
lines, pytest summary and `ORDINARY_PYTEST_EXIT=0`); command wall time7.0042796s
is not the pytest4.91s duration. No extra log file was authored in this append.

All three selections are the same ordered whole modules, without filters:
```text
tests/agent/test_generator_control_contracts.py
tests/agent/test_decision_binding_arguments.py
```
The actual post-alignment launcher was the approved MedChat Conda Python with
`-I -S -B scratch/ordinary_chat_offline_runner.py`, followed by those two paths.
No raw pytest, collection-only pre-run, automatic retry, compile or extra module
execution was performed. The one permitted run directly returned terminal;
there was no session to poll or restart. Slot release was announced immediately.

### ff-only alignment and honest dependency pins

Read-only preflight found HEAD `c612c9873a04823c936d710861bf890ad30b844a`,
origin/main exactly `108df5d8acbdc1881a8f09c31b8b450acdfb247b`, and only the
expected dirty own3paths. No incoming path overlapped those three. Rechecked
origin/main immediately before `git merge --ff-only origin/main`; it fast-forwarded
successfully to that exact108df5d commit. No fetch, conflict resolution or manual
merge edit was performed. Branch remains `codex/b2-generator-control`.

The incoming PR99 paths are the publication-integration plan,
`src/agent/harness/decision_execution.py`, `src/agent/harness/decision_loop.py`,
`src/agent/runtime/event_bus.py`, `src/agent/runtime/run_session.py`, and
`tests/agent/test_decision_binding_publication.py`. They are landed PR99 changes,
not this contract implementation. Within the14-item manifest, execution changed
from E1A64874... to D8C66637... and Session from 7324C9BE... to 5F606B9F....
Therefore **not all old pins survived alignment**. Own sourceD4A, testABAF,
plan8878, arguments07FD and runner45567, plus the other seven manifest entries,
did survive byte-identically. Historical §1 line references describe the old pin.

New14-item manifest, identical immediately before and after the post-alignment
run (the plan hash is the pre-append8878 version, not a self-referential final hash):

| Path | SHA256 before = after execution |
|---|---|
| `src/agent/contracts/decision_bindings.py` | `D4A56EDF37EF8FE5C315B5B0260538CCA9ECB79E73CA46A331F0E2AB8F45F609` |
| `tests/agent/test_generator_control_contracts.py` | `ABAF6100011DB84D6065F44E3013BB74FF325AE023A4CEA224034F1749B8C808` |
| `docs/superpowers/plans/2026-09-27-b2-generator-control.md` | `8878D4459364549421E0161936B6D52B4EFCBDA42308C44626FE76F4308E2AB2` |
| `scratch/ordinary_chat_offline_runner.py` | `45567D6B7E7CFD7BF6F910104664148A72FC116DDA678B89FB8F7B26BF6F2FD6` |
| `tests/agent/test_decision_binding_arguments.py` | `07FD2E0129C465065D83E8F6D9D2C89AD8A88082B0FB7B8FFCC88FF87DE74B40` |
| `src/agent/tools/llm_molecular_generator.py` | `5F59170E9BFE83D6DD10C86725FC775D2C69CC25E63B0DC7F72C4ED4533F143A` |
| `src/web/models/ollama_model.py` | `472EC2A73D638AAD8AB92CA2406C09456E9ABAC409FB1379A7F9D93FBF6DB0B1` |
| `src/agent/contracts/binding_requirements.py` | `7C27697B3158461729774AB1E36A697D834505BA272A4BC0D74F2D75B2ECBC29` |
| `src/agent/harness/decision_execution.py` | `D8C66637F9A6BBC744F69791F8E96CE0382719D8026AE8A61B757B1196C815A8` |
| `src/agent/runtime/run_session.py` | `5F606B9F97A95A01BEDC667951A6C343F1C7B62770E12138C06FCE7AEF76105B` |
| `src/agent/harness/decision_bindings.py` | `D9490F70E8389163668852E2A65F59FE57A42872B84E94D88D96C8EF0A3C9A15` |
| `src/agent/harness/decision_binding_acceptance.py` | `E95A6B53AC99364F3EBC8B1C644AFA482A1B192F548AC91CD7E04D516AD09B84` |
| `src/agent/harness/decision_continuation.py` | `CA2506DFC83D8294A51255907C34A9D525B826B60A9D8AED50C3745171B1DC8C` |
| `src/agent/tooling/adapters.py` | `9486DC0E1D4EEAF05864460DD0F44A27D5C09D61A8588D6C234E3EAE25759E8E` |

HEAD, branch and status remained unchanged through the execution; tracked diff
remained the480-line A-contract source addition. After these checks, ONLY this
plan receives §§13–14. Final plan SHA256 is returned separately after writing.
The other13 manifest entries must still match. `git diff --check` is required
after the append, but no Python rerun is authorized or needed for prose.

This evidence supports the pure A-contract release only: parsed immutable records
and pure transitions, with synthetic grants. It is not authenticated issuer proof,
durable budget ownership, actual token normalization, dispatch/cancel/drain,
scientific backend success or full B2 completion. Parent may independently publish
the pure-contract batch now; it does not wait for full G3, and publication does not
release G3/G4 implementation. Parent owns exact staging/commit/PR.

## 14. Next G3 evidence campaign proposal — not executed or authorized

This proposal retains approved design §11.5/§11.7 and G3/G4; it adds no production
grant, code or execution permission. Only public official sources were consulted:
no local version/config/environment/credential/model/weights probe or Ollama call.

### Evidence already available, and limits

[Generate API](https://docs.ollama.com/api/generate) identifies prompt_eval_count
and eval_count as response fields: post-call usage alone cannot prove the complete
prompt's pre-dispatch bound. [Modelfile](https://docs.ollama.com/modelfile) describes
TEMPLATE/SYSTEM additions, num_predict and num_ctx; a configured value or model
name is not actual enforcement evidence. Both must be checked against the installed
server/runner revision, not treated as installation proof from current documentation.

A supported count-only interface for the installed /api/generate path is **not
yet verified**, not declared absent. Public
[llm/server.go](https://github.com/ollama/ollama/blob/main/llm/server.go) has an
internal Tokenize interface; this does not establish a supported public endpoint
or exact full-template counting. The /api/show documentation access failure is
not evidence that the local interface is missing. No private-port probe is implied.

### Proposed releases, each independently reviewed

1. **Minimal metadata/source package first.** Parent supplies or explicitly
   authorizes an owner to capture only: responding server version/build and active
   runner revision matched to source; selected molecular model manifest/component
   digests; tokenizer algorithm/metadata/special-token identity; original template,
   system/message additions and resolved options digests; requested versus effective
   context and the matching completion/stop/truncation implementation. Bind these
   to the actually loaded model, including invalidation on reload or changed tags/
   options. CLI version or a digest-shaped caller value alone is insufficient.
   Prefer whitelisted summaries; any tokenizer metadata extraction, artifact hashing
   or model-detail API access needs an exact allowlist. No general inventory,
   tensor traversal, config/secret dump, pull/install/restart or cloud fallback.

2. **Offline implementation proof and count-seam choice.** Trace that exact version's
   generate rendering, backend-added tokens, tokenizer, effective context and
   num_predict=1000 enforcement. Prefer an equivalent supported count-only interface;
   otherwise review use of the same pinned renderer/tokenizer implementation.
   If neither is supportable, request the smallest owner/backend seam with named
   files/tests before coding, not a parallel client or guessed tokenizer.
   The reviewed bound must cover the entire effective prompt on every round and
   establish prompt_bound<=prompt_token_ceiling, prompt_bound+1000<=effective_context
   and sufficient remaining allowance. Parent selects positive token allowances
   from evidence, never synthetic fixture values or character multipliers.
   Pure tests can prove contracts, arithmetic, identity rejection and controlled
   transport behavior; they cannot prove the loaded backend or live enforcement.

3. **Finite actual-local campaign, before P8.** Separately review exact benign cases,
   endpoint/model identity, maximum requests, deadline, cleanup/drain, redacted
   observations and launcher; grant the sole slot explicitly. The offline runner
   remains unchanged. Distinguish metadata/count-only/inference requests; hidden
   warm-ups or count-via-generate calls cannot escape accounting. Verify original
   prompt/seed/target retention near context boundaries, actual completion1000
   enforcement, installed usage semantics and cancel-to-backend termination/drain.
   EOS-short output is not a cap-boundary proof; HTTP disconnect/future cancellation
   is not drain. Cache/special/hidden-token semantics must match the claimed budget.
   Missing or over-bound usage retains reservations and violation facts, without
   clipping, zero substitution, refund or replay. Untouched boundary cases and
   unavailable observations remain explicit gaps, not inferred passes or retries.
   Bounded-live prerequisites must be established before such execution.

Actual generator/client evidence must retain all three call modes, strict capture,
five rounds, requested10, completion1000, model and parameters. Deterministic offline
cases retain first-round10, multi-round fill10, five-round partial/invalid/duplicate
and exact failure/cancel attempt counts; they do not substitute for actual-local
results. No raw=true template bypass, one-call substitute, reduced positives,
arbitrary multiplier or unlimited retries. A real partial result stays partial.

**Recommended next release:** metadata/source evidence scope only. Any missing
count/enforcement/drain capability needs a named owner and smallest reviewed next
action; it cannot permanently close G3 as “unavailable” or defer real generator
proof until P8. Trusted issuer/identity-to-dispatch binding and G4 remain gates.
Full candidate -> all required analyses -> ranker -> report closure and P8 positives
remain mandatory. Pure A-contract publication proceeds independently through parent;
479 offline passes and strict transport are neither G3 proof nor B2 completion.

## 15. Pure-contract publication alignment

Parent aligned this isolated branch from108df5d to actual main
4280b7a7a41506698a35f31e1d882f8724d58c4e after PR100's9/9 CI and guarded merge.
Only its test_service.py cleanup and ownplan arrived; neither overlaps the14
contract regression inputs. The three owned source/test/plan files retained
D4A56E/ABAF610/99C611 across alignment. This evidence append changes only the
plan after those checks; code and tests retain independent SOURCE/QUALITY scope.
Earlier actual479 results are not newly rerun results on4280b7a. Latest-head
full CI/review/merge guards remain required. Publication is exactly the contract
source, new contract test and this plan; no generator/client/runtime integration,
environment probe, model activation, real inference or deployment is included.

## 16. Landed ASCII-boundary alignment

PR98 landed as c463dd2824265fbe1015ea5cf0564c804a4e3cf6 after exact-head
8ce19a5 CI36286487041 passed all nine checks and review guards found no unresolved
threads. Reviewed and landed trees both791e4617. Its actual collection was11519
=11295core+224web; core11294passed1skipped, Web224passed. Historical Web3s and
sandbox initial0.5s causes are not declared fixed by that success.

Parent merged this landed main into the contract branch as7a1a70d. The incoming
three ASCII paths do not overlap this PR's three owned paths, whose SHA256s were
unchanged before/after alignment. Shared decision_bounds is intentionally now the
landed implementation; do not claim all earlier regression inputs are unchanged.
The previous479 local results remain previous-revision evidence. This aligned
head requires fresh complete CI; no additional local run or runtime proof is
claimed. At the last observation the prior-head CI36286571814 had not yet yielded
a final aggregate; its outcome remains separate from this new head.
