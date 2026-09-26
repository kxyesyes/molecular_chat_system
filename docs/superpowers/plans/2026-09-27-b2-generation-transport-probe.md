# B2 generation transport characterization and strict-error RED design

> **For agentic workers:** Use executing-plans for later authorized execution;
> this preparation does not grant a test slot or any production implementation.

**Goal:** Count the current real generator/helper/Ollama function chain with only
the lowest HTTP transport replaced; record existing behavior before designing a
generation invocation control or claiming a token bound.

**Architecture:** Real `LLMMolecularGenerator.execute`, its default five-round
helper and real Ollama methods; HTTPX MockTransport supplies synthetic responses.
Real RDKit validates candidates. No adapter, production, runner or budget changes.
**Tech stack:** Python 3.10-compatible pytest, HTTPX, asyncio and RDKit.

**Current revision:** Parent accepted the full-module RED at session84090/
terminal587bf9 and authorized the minimal two-production-file implementation plus
this plan record. Implementation is prepared for SOURCE review; **GREEN remains
HOLD** while the parent schedules Wegener17 controls GREEN. This worker released
the slot at RED terminal and has no active run. Test SHA `B5DEA14E...9187AE`, runner
and all nine old fixture files remain frozen. No Python/import/compile/collection/
test execution accompanies implementation. Earlier preparation restrictions and
approvals below are historical; the final section records the current authority.

## Authority and current source pin

Initial preparation-only authorization: exactly this plan and
`tests/agent/test_generation_transport_characterization.py`. SOURCE review must
precede execution under an explicit scientific-slot owner. At preparation the
slot remained with Wegener; the later parent-owned run is recorded below.
No Python/import/collection/compile/test execution, network, host model/config/key
inspection, staging, commit or publication during preparation.

Clean initial tree: `b2-generation-transport-probe`, branch
`codex/b2-generation-transport-probe`, actual supplied main/HEAD
`5d36eee2977152fe5047dc21e260500d6c965c4b` (PR93). This is a local Git source pin,
not a fresh remote verification. Do not transplant the dynamic worktree's code.

Design authority: `dynamic-bindings-b1` at
`d76229700595dc77a5f6d54a4c23b1f76e4dd38d`,
`docs/superpowers/specs/2026-09-25-dynamic-tool-bindings-design.md` §§11.5/11.7
(Git blob `41aa5492a86dc6c909b092ef9ae47065fac17baa`), and companion plan §11.5
(blob `a7b71143a85b22db7413215a834947844206117a`). These approve bounded design
scope, NOT a proven numeric token grant or automatic production release.
Parent selects the real-chain offline probe FIRST, not grant/control authoring.

| Read-only dependency at current HEAD | Git blob |
|---|---|
| `src/agent/tools/llm_molecular_generator.py` | `ee96bf586fe5c9d5bc768ca7f0d2f173b5bcd553` |
| `src/web/models/ollama_model.py` | `d46a87577a929bf01bd97a1388661844d918d8c5` |
| `src/agent/tools/base_tool.py` | `7da2cb4a9b54e7dc74cae573115ec272c2d97c27` |
| `src/agent/contracts/generation_request.py` | `51c376e704b471ca4a3d872017f70b48e9f28f7f` |
| `src/agent/tooling/generation_ranking_contract.py` | `d31dc36983783ad6d71194c3e34567f3af47a200` |
| `src/agent/tooling/adapters.py` | `cff17c3a3e8985ad4b87a7207670c21d959d1152` |

## Existing interfaces and test boundary

- `execute(query, temperature=0.7, mol_count=None)` consumes structured
  `metadata.requested_count`/temperature. `_generate_with_retry(..., max_attempts=5)`
  accumulates valid canonical-unique structures. Each round currently asks for the
  original count, not the remaining count; retain requested10 in all requests.
- Native Ollama exposes sync `generate` and separate `generate_async`. The helper
  inspects **generate**, so merely having generate_async does not select it.
  Probe native sync plus the supported injected-coroutine interface using the
  unchanged bound `model.generate_async` as an explicit binding's `generate`.
  Cover both no-running-loop and running-loop/new-thread helper branches. This
  binding is a test input, not a claim about normal production async assembly.
- No method monkeypatch/wrapper replaces execute, helper, _call_llm_sync,
  _run_async_in_new_loop, generate or generate_async. A worker-local profiling
  callback counts real synchronous generator frames; transport records separately
  count actual MockTransport entries and their response/error outcome.
- Construct Ollama's four required fields explicitly with mocked sync/async HTTPX
  clients (`trust_env=False`). Constructor/client setup is outside the probe;
  current source __init__ constructs clients, not a health request. Do not invoke
  default assembly, app startup, model discovery or unmocked HTTP.
- Synthetic `.invalid` endpoint, prompts and responses only. Close real clients
  using `OllamaModel.close`; join the test worker and the helper's context-managed
  child executor; restore profiling/event-loop state. Isolate logger import in
  temporary cwd and close newly added handlers. No sleeps or timing-based passes.

## Historical exact characterization matrix (original 46-pass revision)

Each scenario below exercises all three bindings/loop contexts:

| Scenario | Expected transport entries | Current raw producer behavior to assert |
|---|---:|---|
| First response supplies ten unique valid structures | 1 | success, actual10, not partial |
| Three responses supply 4+3+3 | 3 | accumulated exact ten |
| Five responses supply 2 each | 5 | fifth-round exact ten, no sixth call |
| Five duplicate/canonical-alias/invalid responses | 5 | one CCO, success with partial_generation/warning |
| Five invalid or five empty responses | 5 | success false, data None, no quality receipt |
| Five HTTP503 or five ReadError disconnects | 5 | apology token `I` survives as iodine; raw partial success, no transport error/receipt |
| HTTP503 or ReadError followed by ten | 2 | iodine + first nine candidates; raw actual10 despite contamination |
| Valid3, HTTP503, disconnect, duplicate/invalid, empty | 5 | three + iodine, partial, failures not separately receipted |
| Optimization with five HTTP errors/disconnects | 5 | unchanged seed + apology iodine; partial raw success, NOT backend success |
| Two identical explicit producer entries | 2 | both invoke backend; demonstrates absence of producer logical-slot protection only |
| Invalid requested_count11 | 0 | invalid_input before helper/backend |

Check every actual POST's endpoint, model, complete formatted prompt, temperature,
`num_predict=1000`, stream=false, channel and thread/loop context. Check exact
canonical structure order/count with real RDKit; no scientific plausibility claim.
Raw output currently has success/quality rather than typed status, no backend
receipt and no retained usage. HTTP fixtures' usage numbers are synthetic only.
Signature check records the absence of a control parameter; never pass an invented
keyword and silently adapt the fixture. No xfail/importorskip or fake chemistry.

Initial source-derived hypothesis, subsequently confirmed by the parent-owned
offline run recorded below: the client's fixed
apology begins with standalone `I`; `_clean_generated_smiles_line` retains the one
SMILES-like token, and real RDKit accepts iodine. Optimization additionally
prepends the seed. The original assertions intentionally preserved bad behavior;
neither chemical validity nor an actual_count10 flag authenticates model output.

That revision is existing-behavior characterization, not B2 RED/GREEN. Preserve
surprising success/partial/error behavior as evidence; do not repair production
or rewrite observations to make it appear safe. MockTransport entry is not proof
of real network delivery, backend settlement, or live transport retry behavior.

## Preparation and later execution gates

- [x] Read AGENTS, relevant standards/source/tests and pin clean HEAD anew.
- [x] Prepare this short spec/plan and the full test module; no production edits.
- [x] Independent SOURCE review of complete test and source coupling: Sartre
  approved the two frozen files, no P1/P2 (parent-reported review).
- [x] Parent provided the reviewed isolated launcher for this new tree, then
  retained the sole slot and ran the module itself. No execution grant or slot
  transfer to this worker occurred; this worker stayed on HOLD.
- [x] Parent ran exactly the new module once with before/after file-hash control
  and an empty production Git diff, as reported in the execution record below.
  The test target is `tests/agent/test_generation_transport_characterization.py`;
  raw pytest and application import probes are not authorized alternatives.
- [x] Parent's execution returned directly terminal, without restart; terminal
  result and supplied hash evidence are recorded below. Parent released its slot.
  No additional poll, run or independent QUALITY repeat occurred in this worker.
- [ ] Parent reviews observed transport counts/status before choosing any next
  grant/control/client seam. Fresh QUALITY/relevant regressions require their own
  release; commit/PR remain parent-owned, not authorized by this preparation.

## Non-claims and retained gates

No durable root slot, reserve/dispatch/settle journal, token-bound enforcement,
cancel/drain guarantee, candidate closure or Web activation is implemented here.
Finite completed mock calls/closed clients do NOT establish cancellation drainage
or remote backend quiescence. Real pinned backend/tokenizer/template/context-limit
proof and a trusted numeric grant remain prerequisites to strict B2 dispatch.
Unknown usage is not zero, and 1000 requested output tokens is not a total bound.
Do not replace the five-round/requested10 positives with one-call success.
All-candidate analysis, real Ranker, target/core/count closure, full report/normal
Web/mount/ACK/follow-up and original P8 positive expectations remain mandatory.

Historical preparation evidence: this worker did not import, collect, compile or
run the tests. The subsequent parent-owned result below supersedes only the
earlier no-execution status, not the production/B2/live-science gates.

## Sartre SOURCE and parent-owned execution record

Parent reports Sartre independently approved the complete two-file preparation
at the frozen hashes below, with no P1/P2. Parent created the ignored launcher
from the approved admission runner (`6D49050C00D66642F2045828AAAE927C7254B8C282ACCFC0EC1E24A7AF5623AD`),
changing only literal REPO, with full-byte bidirectional verification. This worker
independently rechecked the supplied new runner/file hashes while remaining HOLD;
it did not write or execute the runner.

After Mencius's four-case GREEN terminal/release and the parent's terminal
one-pass lookup microprobe, parent owned the sole local slot and ran this exact
approved module once from the probe tree using the isolated launcher:

```text
C:/Users/xkx52/.conda/envs/MedChat/python.exe -I -S -B scratch/ordinary_chat_offline_runner.py tests/agent/test_generation_transport_characterization.py
```

Parent-supplied terminal execution identifier: `366731` (returned directly
terminal). Actual result: **46 passed in 3.14s; 0 failures, 0 warnings, 0 skips;
process exit 0; ORDINARY_PYTEST_EXIT=0**. No persistent session ID, PID, separate
wall-clock duration or UTC timestamps were supplied; none are inferred here.
This is a recorded parent execution, not an independent run by this worker.
Parent explicitly released the slot afterward; this worker neither owns nor
starts another scientific run. Pending other work is not this module's evidence.

| File | Parent before = after SHA-256 |
|---|---|
| `tests/agent/test_generation_transport_characterization.py` | `2D29D517E1270DC948138BB793D2D49E075B717B5E006818010D87AC9BA334EF` |
| This plan, at execution time | `2959BDEAEB3BFCA84A25CFB86500C9AA9544B2D967C2DA83C5063AE6661C81C0` |
| `scratch/ordinary_chat_offline_runner.py` | `9F6F5A2F68272442316DD614F6BEBD62C49371669C8E5E90DC4BC50BC5BED778` |

Parent reports the production Git diff remained empty. Before appending this
record, this worker read-only rechecked all three supplied hashes and the empty
production diff at unchanged HEAD `5d36eee2977152fe5047dc21e260500d6c965c4b`.
At that recording step only this owned plan changed; its execution-time hash
above is historical and is not asserted to equal the updated plan's hash.

The real generator/helper/client functions with MockTransport and real RDKit
confirmed the stated characterizations, including apology-`I` contamination,
optimization seed retention on transport failure, count/round behavior and the
absence of producer-level replay protection. These are offline behavior findings:
they do not authenticate generated scientific results, demonstrate live backend
success, fix error handling, establish token/control/drain guarantees, or complete
B2/P8. No new tests, source/runner edits, commit, push or network activity
accompanied that evidence-recording step. The following separately authorized
design/test revision is unexecuted and has no production implementation.

## Bounded strict generation-only design (pending independent review)

### Scope and chosen compatibility boundary

Future implementation, only after a separate grant, is limited to
`src/web/models/ollama_model.py` and
`src/agent/tools/llm_molecular_generator.py`. Neither is edited now. Do not change
model selection, default assembly, adapters, chat routing, streaming, requests'
model/prompt/options, candidate count limits, or the five-round policy. Do not
introduce a client-control/grant/receipt abstraction ahead of evidence.

Use a named opt-in client keyword, not response-text heuristics. Rejected
alternatives: filtering legal iodine `I`; English apology regexes; replacing
requested10/replenishment with one call; relying solely on exception inheritance
to mask the running-loop bug; or passing a keyword speculatively and retrying
without it on TypeError. A separate strict client class would duplicate the two
existing HTTP paths and is unnecessary for this bounded change.

Compatibility is deliberately narrower for **molecular generation injection**:
only the verified real Ollama bound methods described below are eligible in this
slice. An arbitrary injected model/fake may previously have worked; that is not
proof of strict failure semantics. It must now fail closed, not silently retain
legacy generation. Supporting other concrete clients needs a separately reviewed
strict implementation plus evidence. Ordinary chat's omitted/default-false
client calls continue their existing return behavior; stream_generate is untouched.

### Client contract, before any chemistry

Both existing methods gain the same keyword-only `strict_errors=False`, retaining
their prompt/temperature/max_tokens positions and default max_tokens1500. Require
`type(strict_errors) is bool` before dispatch; invalid values raise only
`ValueError("invalid_strict_errors")`, not fallback text. With False, keep current
legacy behavior, including its HTTP/error apologies. Do not apply strict response
validation to default chat. With True, issue exactly one existing HTTP request:

| Strict outcome | Result |
|---|---|
| HTTP status other than 200 | typed error, reason `http_error` |
| HTTPX transport failure, including timeout/disconnect | typed error, `transport_error` |
| Bad JSON or invalid successful envelope | typed error, `invalid_response` |
| Other ordinary client exception (including TypeError/RuntimeError) | typed error, `client_error` |
| Valid envelope | return the unchanged native string, no chemistry/text filtering |

A valid envelope is a native JSON object/dict, with **no `error` key** (even
null), `done is True`, and a present native-string `response`. Missing/null/list/
number/bool response, nonobject JSON and absent/false/nonboolean done are invalid.
Extra usage keys are allowed but ignored: these do not establish a token bound.
Empty/whitespace strings are valid protocol content but cannot create candidates.
Literal `I` remains valid content and a valid RDKit candidate.

Define `OllamaGenerationError(Exception)`, deliberately not a RuntimeError
subclass, with only fixed message `Ollama generation failed` and the four-value
reason above. No raw provider text, URL, request/response object or original
exception as attributes. Translate exceptions by retaining only the closed reason
and raising a fresh error **outside the original except block** so both cause and
context are None (merely `from None` would hide, not remove, raw context). Never
interpolate the caught exception or log traceback/local arguments. Preserve an
already-typed strict error unchanged through upper helpers. Do not catch
BaseException/cancellation and label it successful or fabricate candidates.

### Binding preflight and the three existing call modes

After existing input validation (including optimization parsing/RDKit availability)
but before `_generate_with_retry`/any generation helper,
`execute` verifies capability without invoking the client. Capture one bound
`generate` method locally; require a real bound method whose `__self__` is an
actual OllamaModel instance of the reviewed class and whose `__func__` is exactly
the reviewed `OllamaModel.generate` or `generate_async`. The supported injected
`SimpleNamespace(generate=model.generate_async)` retains that real identity.
No class-name/model-name, advertised flag, arbitrary wrapper, subclass override
or `**kwargs` alone establishes trust. This is application compatibility checking,
not a sandbox against malicious Python code in the process.

Inspect the bound signature: explicit keyword-only `strict_errors` with native
False default, and successful `Signature.bind` of prompt, temperature,
max_tokens1000, strict_errorsTrue. Missing/positional/required-only keyword,
uninspectable signature, incompatible required parameters and unknown identity
are unavailable **before dispatch**. No TypeError-driven second invocation.
Revalidate and capture once at each `_call_llm_sync`; carry that same captured
method into the child helper rather than checking then rereading a mutable binding.
Do not cache a prior capability/result on the generator instance. If capability
is lost between rounds, abort as unavailable and discard the partial batch;
never enter an unverified client. A fixed private unavailable exception passes
through private helpers to execute, rather than being treated as empty output.

Preserve all three dispatch choices:

1. Native sync bound generate: direct single call with strict_errorsTrue.
2. Coroutine generate, no running loop: existing new-loop/wait_for path, single
   strict call, finally close loop and clear the owned current-loop reference.
3. Coroutine generate, genuinely running loop: existing context-managed executor
   and real `_run_async_in_new_loop`, single strict call, join/close owned resources.

**Necessary loop-boundary correction:** the try/except RuntimeError contains
ONLY `asyncio.get_running_loop()`. Executor construction, submit and future.result
are outside it. A failure there becomes a sanitized client_error; it must not
create a fallback loop or recreate/invoke the coroutine. Keep the existing
timeouts and ownership model. Not using RuntimeError for the strict error is
defense in depth, NOT a substitute for shrinking this catch. The source bug is
statically identified; this new boundary regression has not yet been executed.

### Propagation, round state and candidate/seed rules

`_call_llm_sync`, `_generate_with_llm`, `_optimize_with_llm` and the child async
bridge must not convert strict errors to `""`/`[]`. Catch and reraise strict/
unavailable errors before broad exception handlers; translate other ordinary
helper failures to sanitized client_error instead of logging raw exceptions.
`_generate_with_retry` alone handles backend round errors: mark a local failed-
round flag, add no candidate or seed, and continue within the same five attempts.
One helper invocation has at most one client invocation, even on TypeError.
The next explicit outer round is allowed; it is not a hidden helper retry.

Keep the helper's list return for compatibility. Pass an optional keyword-only
private invocation-local round-state object from execute to `_generate_with_retry`
(default None for direct existing callers); it records only whether a round
failed. Never store cross-call state on self or reuse intent fields as receipts.
Unavailable capability propagates past the retry handler, without five futile
calls. Unexpected outer errors must also become fixed failure results, never
provider text in message/formatted/reasoning/error/logs.

Every round still asks for the ORIGINAL requested_count10, keeps temperature and
num_predict1000, canonical deduplication and count cap; terminate on ten or after
five rounds. A failed response never reaches line cleaning or seed insertion.
For optimization, preserve successful seed inclusion **only after at least one
actual response candidate passes existing cleaning and real RDKit validation**.
Protocol-valid empty/whitespace or all-invalid content gives no seed. This is the
only extra seed-eligibility guard; do not filter legitimate I or reject successful
optimization wholesale. Seed inclusion on a successful round remains legacy
behavior, not proof that the backend itself generated the seed.

| Completed raw producer outcome | Required result |
|---|---|
| Capability unavailable | successFalse, dataNone, no quality; fixed tool_unavailable error |
| No valid candidate, at least one failed backend round | successFalse, dataNone, no quality; fixed provider_error |
| Only valid-protocol empty/invalid content, no backend error | existing generic no-candidate failure, no invented provider error |
| Valid partial/full accumulated batch | successTrue, canonical data; retain existing four quality fields and partial warning |
| Successful batch also had failed rounds | same success, plus fixed round warning; no error key |

Exact unavailable message: `Strict molecular generation unavailable`; error is
`{code: tool_unavailable, message: <same>, details: {reason: generation_strict_unavailable}}`.
Exact provider failure message: `Molecular generation request failed`; error is
`{code: provider_error, message: <same>, details: {reason: generation_backend_failed}}`.
Fixed failed-round warning: `Some molecular generation rounds failed.`
Retain `model/requested_count/actual_count/partial_generation` quality keys; do not
invent status, backend receipt, per-call usage, settled state or scientific success.

### Logging boundary

Generation-path logs and strict client logs contain fixed messages only, with no
query, prompt, seed, intent, model configuration, provider text, args, traceback
or stack_info. Allowed event texts for this slice:
`Molecular generation started`, `Molecular generation round started`,
`Molecular generation round failed`, `Molecular generation completed`,
`Molecular generation unavailable`, `Ollama strict generation failed`.
Strict client failures emit that final client message, not a legacy error log.
Do not modify other model routes' logging in this patch. Existing user-facing
successful result/query rendering is not a log and is outside this redaction
change; raw exceptions must never be inserted into it.

## Desired-behavior RED preparation and review gates

The same module replaces the original bad-behavior assertions with target
behavior; it does not duplicate the historical suite or use xfail. The parent
46-pass result and its hashes above are retained as evidence, not reused as
proof for these edited tests. Current expected tests are **authored, unexecuted**;
neither RED nor GREEN nor an updated collected count is claimed.

Coverage retains the real helper/generator/client and RDKit, including:

- All three call modes: first-round10, three/five-round10, canonical duplicate/
  invalid/empty shortfall, mixed real successes and failures, exact full prompts/
  options and actual MockTransport attempt counts. True I success remains positive.
- HTTP errors, disconnects, TypeError/RuntimeError and invalid HTTP200 envelopes:
  direct strict client one-call errors; five generation/optimization rounds with
  no apology/seed contamination; helpers must propagate typed failure, not empty.
- Default chat with omitted and False strict keyword, sync and async, preserves
  legacy HTTP/error text and genuine success. Invalid strict-flag values dispatch0.
- Unknown/flag-advertising injected bindings dispatch0; known bound methods with
  faulted signature inspection are rejected before the helper. Signature/API
  assertions happen inside tests, not missing-symbol imports at collection time.
- True running-loop submit-before failure dispatches0; real child HTTP completed
  followed by result failure dispatches1. Inject both RuntimeError and TypeError
  at the executor boundary, never replace the real helper/client. Observe and
  delegate the real loop factory: no new loop on the already-running worker.
  This catches a broken fallback even if a second coroutine never reaches HTTP.
  Normal running-loop success is already covered by the first-round10 control.
- Optimization positive controls retain successful seed, real I partial output
  and fifth-round10; failed/empty/all-invalid responses cannot manufacture seed.
- Strict errors have fixed args/reason and no cause/context/request/response;
  captured generator/client logs are fixed and contain no synthetic private
  prompt/seed/provider marker. Resource/context cleanup remains mandatory.

Fault injection is limited to HTTP, signature-inspection and executor/loop
boundary observations; no replacement of tested execute/helper/generate methods.
Both HTTP clients use MockTransport/trust_envFalse and synthetic .invalid URL.
All loops/clients/response bodies/workers and temporary logger state are cleaned
up, including failure paths. These finite checks do not prove cancellation drain.

Next gates, not permission to execute now:

- [x] Write this bounded design and same-module desired-behavior tests only.
- [ ] Independent SOURCE review of BOTH complete new files and current source;
  explicitly review capability narrowing, response protocol and seed eligibility.
- [ ] Explicit sole-slot transfer after parent verifies this final SOURCE freeze;
  preceding Wegener17 controls RED is reported terminal/released. Capture fresh
  source/plan/test/runner hashes and select only this exact module with the
  approved isolated launcher above. No raw pytest, collection-only shortcut,
  application imports, live requests, or runner changes.
- [ ] Run once to actual terminal; record collected count/expected behavioral
  failures, warnings/skips/duration/handle and before-after hashes. Do not call
  an import/collection/setup failure successful RED. Do not restart a live handle.
- [ ] Separate implementation authorization for the two production files, then
  independently reviewed GREEN/relevant compatibility regressions. In particular,
  audit prior injected-client tests rather than silently allowing unsafe fallback.
  Revalidate mutable-binding abort, same-instance state isolation and ordinary
  chat preservation during that review; this is not comprehensive B2 acceptance.

All previously stated token/runtime/real-backend and P8 positive gates remain
unchanged. No token upper bound is invented, no host asset is inspected, no
live/Web/scientific acceptance is claimed. No source/runner edits, execution,
commit or push are authorized by this preparation revision.

## Plato SOURCE approval and supplemental preparation (still HOLD)

Parent reports **Plato SOURCE APPROVE, no P1/P2** for the preceding strict
design/RED revision, before these supplemental edits:

| Reviewed file | SHA-256 |
|---|---|
| Plan | `C0CC10F27560D07B878C201913F0CAF408766E84C029362FA24A5B0BE48CFDD5` |
| Test module | `4AA386D949C1D48BA85E9B12854EB8A21011F651C3A16A3C0DAC454918ADCECB` |

That approval is SOURCE only, not an observed RED or a test-slot grant. Current
slot owner at that supplement was **Halley fresh58** (parent-supplied); this worker
had no scientific execution handle. Parent will transfer RED separately. Production remains limited
to the two named files in any future implementation grant, with no preflight
relaxation for old fakes. This turn edits only this plan and the same test module.

### New tests in this revision

1. `test_same_generator_failure_state_does_not_leak_into_next_success`: same
   actual generator and model for two execute entries, all three call modes and
   description/optimization. First entry is all-failed5, failed-then-recovered
   full, or mixed partial; second is a disjoint pure-success batch. Assert exact
   per-entry HTTP spans, distinct result objects, original first outcome retained,
   and no old candidates/error/partial/failed-round warning in the second result.
   Optimization retains only its legitimate successful seed. No recreation of
   the generator to hide state leakage.
2. `test_capability_loss_between_rounds_discards_valid_partial_batch`: all modes,
   description/optimization, missing or unverified replacement method. The first
   real HTTP response supplies valid CCN; the injected binding changes at that
   transport boundary. The second real helper must reject capability, invoke no
   replacement client, stop retrying and discard the earlier valid partial/seed.
   Assert exactly one HTTP entry, two real call-helper entries and fixed
   tool_unavailable with dataNone/no quality/empty formatted/no stale warnings.
3. `test_running_loop_bridge_uses_captured_method_without_rereading_binding`:
   deterministically change the injected binding at nested executor.submit,
   before the real child starts. The parent must already have captured the real
   generate_async method. Assert one real async HTTP call with genuine I success
   or unchanged typed HTTP failure, zero unverified calls, and no binding-property
   read on the child thread (including the failure path).
   Observed submit delegates to the original executor; no helper/client method
   is replaced and no timing sleeps/races are used.

Test harness additions are observation/setup only: per-execute HTTP spans, a
synthetic-transport response callback, and a mutable binding whose property
returns the original bound method unchanged and records read-thread IDs. These
are inputs exercising preflight/capture, not newly trusted production adapters.
Captured in-flight method completion remains allowed; loss blocks the NEXT round.
All clients/loops/workers still use the existing cleanup boundary. None of the
new tests has been imported, collected or run; no collected-count guess is made.

### Existing fake fixtures: exact source-only migration proposal

Read-only search at pinned HEAD covered every test file referencing the real
LLMMolecularGenerator or its private generation helpers. The following **nine
existing files** contain positive generation/helper fixtures requiring adjustment
under the approved identity preflight. They are NOT edited or authorized now.
This is an impact prediction from source, not a report of observed test failures.

Migration rule M: construct the actual reviewed OllamaModel (explicit four fields,
no host assembly) with sync/async MockTransport clients and synthetic .invalid
endpoint; retain original response strings as HTTP200 response/doneTrue bodies.
Record prompt/options at transport entry, not in an overridden generate function.
Keep model_name and observational prompts/calls/temperatures lists where existing
assertions use them; these do not alter bound-method identity. Use a context/
yield fixture or unittest ExitStack registered with addCleanup to close both
clients and restore temporary import/logger state on assertion failure. Reuse a
small test-only context factory in this owned test module if approved; do not
add production compatibility shims or a global conftest patch. No construction
may create an unmocked/default HTTP client or inspect host configuration.

| Exact existing file | Affected fixture/symbol | Smallest proposed migration; assertions to preserve |
|---|---|---|
| `tests/test_llm_molecular_generator.py` | `FakeLLM`, `DuplicateCanonicalLLM`, `ProseThenSmilesLLM`, `CapturingLLM` | Apply M to positive execute/private-helper callers, keeping their exact scripted text and prompt list. Preserve count precedence/grammar/actual count, bounded and sanitized TARGET_EVIDENCE, one-call checks, canonical duplicate/prose filters and CountingChem.calls. Pure intent/serializer/no-dispatch tests may retain inert fakes. The two `test_*count_skips_conflicting_text_reparse` intent-unit mocks can keep their existing helper observation and count assertions, with a genuine client binding; do not turn them into claimed HTTP evidence. |
| `tests/agent/test_agent_executor.py` | `CapturingLocalModel`, `_build_agent` | M, retaining ten-structure text and `model.prompts`. Keep all omitted/explicit/actionable count assertions, query transport and invalid-input zero calls. Existing scoped chemistry doubles are routing evidence only, not new chemistry proof. |
| `tests/agent/test_generation_temperature_transport.py` | `RecordingModel` | M; append actual JSON options.temperature to the existing calls list. Keep direct/threaded/registered combinations, temperatures0.23/0.81/default0.7, distinct provenance digests, unchanged WorkflowStep, and invalid-temperature zero calls. |
| `tests/agent/test_react_agent_adapter.py` | `test_real_generator_uses_dedicated_model_and_request_temperature`, imported `RecordingModel` | Adopt the migrated recording-client fixture with cleanup. Preserve factory identity, dedicated generator identity across set_llm, forbidden main-model call, exact tools/events/tool_started count and input digests; no ReAct production changes. |
| `tests/agent/test_molecular_agent_adapter_science.py` | local `RecordingModel` in `test_nondefault_temperature_reaches_generator` | M and retain `temperatures == [0.23, 0.81]` for execute/execute_tools/supervisor. Other rig fake tools and ranking/closure assertions are not client preflight fixtures and remain untouched. |
| `tests/agent/test_supervisor_agent.py` | `FakeGeneratingLLM` in `test_supervisor_hit_to_lead_real_generator_reports_public_count` | M; retain requested_count7, one prompt, workflow/public result and existing scoped parser/chemistry test doubles. Do not change fake router/main-chat/tool fixtures used in other tests. |
| `tests/agent/test_workflow_executor.py` | `CapturingGeneratorModel` in `test_legacy_target_binding_reaches_real_generator_with_canonical_count` | M; retain legacy target/input_from binding, metadata requested_count7, quality count7 and one prompt. No workflow executor implementation changes. |
| `tests/agent/test_generator_optimization_input.py` | `boundary`, `test_seed_spelling_reaches_real_prompt`, `test_seedless_optimization_word_keeps_description_dispatch` | Give positive boundaries a real M binding; existing intent-level helper spies may stay and accept the private round-state keyword. Move prompt/call observation in the real-prompt test to HTTP. Preserve exact seed spelling/first-seed choice, count1, no-seed description dispatch, target evidence, parser-not-called and invalid/unavailable input zero-dispatch assertions. Do not use an unverified binding to mask parser validation. |
| `tests/agent/test_generation_ranking_contract_integration.py` | `SimpleNamespace(model_name="offline-sentinel")`, `candidates(intent)` in `test_actual_generator_count_temperature_and_partial_transport` | Replace only the positive client sentinel with a real M binding using the same model name; preserve this existing intent-unit seam as `candidates(intent, *, round_state=None)` with its original returned row and intent recording. It remains a producer/contract unit, not real HTTP evidence. Keep count1/10, temperature0.23, wrapped/unwrapped, source/partial/status/validator assertions. All ranker optional evidence, score, failure, sanitizer and identity assertions remain unchanged. |

The semantic negative control at
`tests/agent/test_semantic_input_gates.py::test_instruction_local_ids_skip_generation_before_model_boundary`
uses `CapturingLocalModel` but is rejected before tool_started/generator execution.
No migration is needed: retain empty prompts, absent generator-start event and
validation_error assertions. Similarly, tests using fake *tools* rather than
real LLMMolecularGenerator clients are not grounds for weakening the preflight.

Two intentional output-contract updates need explicit migration approval:
`tests/agent/test_generator_optimization_input.py::test_missing_llm_preserves_existing_preflight`
currently expects a message containing LLM;
`tests/agent/test_generation_ranking_contract_integration.py::test_missing_generation_model_is_not_reported_as_quality_success`
currently expects generic adapter error.details.raw_result. Replace only those
old formatting assertions with the approved fixed unavailable message/reason
(and typed unavailable status where normalized). Keep failure/dataNone/no-quality
and zero-dispatch requirements. Preserve invalid-count/temp/target and optimization
validation precedence before capability checking; do not reclassify bad input
as unknown capability just to make the migrated suite pass.

These proposed test edits need separate parent authorization. Do not add a
strict_errors parameter/advertised flag to FakeLLM, whitelist a test module,
patch the capability checker/signature to accept fakes, or replace a positive
generation assertion with unavailable. Do not remove ranking/target/core/count
or P8 expectations. Existing unit-level helper mocks may not be used as proof
of strict transport; the new real-chain module supplies that distinct evidence.

### Ordinary chat compatibility scope (source inventory, execution not granted)

The current owned module already covers real generate/generate_async with omitted
and explicit False strict_errors: success I, HTTP503/disconnect, malformed JSON,
error envelope, nonstring and unfinished legacy responses. Signature assertions
retain the first four parameter positions and the existing max_tokens default.
That is NOT yet a test of positional calls, the real generate_for_chat adapter,
or stream semantics. Proposed additional scope after authorization:

| Boundary | Exact source/test location and required checks |
|---|---|
| Default chat positional calls | Add focused cases in this owned test module invoking real `generate(prompt, temperature, max_tokens)` and `generate_async(prompt, temperature, max_tokens)` positionally and with defaults, through MockTransport. Assert payload defaults0.7/1500 and explicit0.23/1000, one call, legacy success/error text. A fourth positional strict flag must raise TypeError before HTTP; the new flag is keyword-only. |
| Real chat adapter | `src/web/models/__init__.py::generate_for_chat` is read-only and unchanged. Add same-module tests calling it with real Ollama + MockTransport, both positional/default adapter arguments; prove async channel selection, payload/count and legacy responses (no strict opt-in, including HTTP200 legacy nonstring stringification). Keep `tests/test_agent_platform_health_check.py::test_chat_generation_adapter_supports_canonical_and_external_models` unchanged for canonical async preference, coroutine-generate and sync-to-thread fallback; those chat-only doubles need no generation capability migration. |
| Real stream/client lifetime | `src/web/models/ollama_model.py::stream_generate` is outside production changes. Add same-module finite HTTP NDJSON success/HTTP-error/disconnect stream controls after authorization, using real method/HTTPX and explicit stream/client cleanup; check streamTrue, positional/default options, existing chunk/error behavior and unchanged signature/no strict flag. Retain existing `tests/test_model_request_lifecycle.py::test_actual_http_stream_cleanup_precedes_request_release` Ollama cases: normal/disconnect/consumer_cancel with cancel_owner variants, cleanup before lease release and exact request modes `[True]` versus chat fallback `[True, False]`. Do not add doneTrue to its nonstream fallback fixture to conceal an accidentally strict default chat path. |
| Close remains independent | Retain `tests/test_model_request_lifecycle.py::test_actual_ollama_close_attempts_both_clients` and `test_actual_ollama_close_failure_is_redacted_by_owner_only`; no close/stream/route implementation edits in this slice. |

At that supplement no ordinary-chat cases or migrations in the table had been
authored/run; its new tests were the three named state/capability families above.
The next preparation below adds chat cases, still without execution or migrations.
Later compatibility runs need explicit exact selection, offline safety
review and sole-slot grant; the existence of a test file is not permission to run
its unrelated app/health checks. All original live B2/token/drain/P8 gates remain.

## Full-module freeze: chat compatibility authored before production

Parent read the three supplemental test families and nine-file inventory and
accepted the migration principles and two fixed-unavailable output assertions.
This is NOT authorization to edit those existing tests or either production file.
Their edits stay gated on received real RED plus an explicit minimal production/
fixture-migration grant. Current permitted edits remain this plan and own module.

Parent now reports **Halley fresh58 terminal, 6107 passed** and assigns a short
Wegener17 diagnostic-controls RED first. Parent subsequently reports that short
RED directly terminal at handle `00137d`, slot released, with Wegener writing a
test helper only. This is scheduling context, not evidence for our files; no
duration/other statistics are inferred. Wait for the parent's final SOURCE check
and explicit transfer. This worker has no scientific run/handle and does not
assume that a reported release itself grants execution. Cases are now frozen;
do not expand this module before the proposed one full-module RED.

The predecessor file hashes received by parent were plan
`82F18DA1C8B5B83E390BCD6CA2C812AAED67C9B20D6A8921A2ED301B564FE9A0`
and test
`FEBE4197B469C023EDFEDCC6B9755CE66C4FF2364674F1AD86D6D386C3A1E2CD`.
They are historical, not this chat-compatibility freeze. New hashes are returned
in the handoff; no self-referential plan hash is embedded here.

### Added ordinary-chat families, still unexecuted

- `test_default_chat_positionals_keep_payload_and_legacy_result`: both real
  generate methods with prompt-only defaults, positional temperature, and all
  three existing positionals. Assert exact HTTP options, one call and legacy
  output across success, HTTP/disconnect, malformed JSON/error envelope,
  nonstring and unfinished responses. Existing omitted/False strict cases remain.
- `test_chat_cannot_pass_strict_flag_positionally`: a fourth positional argument
  raises TypeError before HTTP on both methods; no retry/client invocation.
- `test_real_generate_for_chat_preserves_legacy_routing_and_stringification`:
  the real chat adapter calls real Ollama methods. Canonical model prefers async;
  coroutine-generate binding remains async; sync-generate binding uses the real
  thread fallback. Assert channel/thread/loop, exact positional/default payload,
  one HTTP entry and legacy str(result), including nonstring response. No strict
  generation preflight is imposed on chat.
- `test_real_stream_keeps_positional_payload_chunks_and_legacy_errors`: real
  stream_generate/HTTPX over finite synthetic AsyncByteStream, split NDJSON,
  malformed-line skip, no-done trailing JSON, HTTP503 and read disconnect after
  a real chunk. Assert exact legacy chunks/error text and streamTrue/options for
  all three positional styles. Synthetic provider marker in *legacy* stream
  errors is intentionally unchanged, not allowed in strict generation errors.
- `test_real_stream_consumer_close_closes_response_before_client`: consume one
  real chunk, explicitly close the generator, require body/response closed while
  clients are still open, then close clients through the owned context.
- `test_stream_signature_remains_legacy_without_strict_keyword`: unchanged
  positional/default signature, async-generator identity, no strict keyword and
  no HTTP for an unsupported keyword.

The shared LEGACY_CHAT_CASES list is only a factoring of the original seven
behavior cases; none is removed. The new finite body tracks close_calls; all
responses/bodies and both clients must close on the real cleanup path. No
production methods, async/thread adapter or enhanced logger functions are mocked.
Import/logger setup remains isolated in the existing temporary production fixture.
No sleeps, host connection/config/asset reads or network transports are added.

### Exact next RED selection (only after explicit transfer)

Single launcher invocation with this **entire latest module**, including every
strict, generation-positive, state/capability and new ordinary-chat case:

```text
C:/Users/xkx52/.conda/envs/MedChat/python.exe -I -S -B scratch/ordinary_chat_offline_runner.py tests/agent/test_generation_transport_characterization.py
```

No -k/-m deselection, xfail, skipping old behavior families, collect-only substitute,
alternate runner or parallel test run. Runner remains SHA-256
`9F6F5A2F68272442316DD614F6BEBD62C49371669C8E5E90DC4BC50BC5BED778`.
Capture actual terminal/count/failures/warnings/skips/duration/handle and before/
after hashes; legacy compatibility positives are controls, not a reason to delete
strict RED failures. No collected count or result is predicted as an observation.

### Reusable fixture context for later authorized migrations (plan only)

Keep `mocked_client(production, script)` and its real canonical bound methods as
the client resource owner. `ScriptedHTTP.calls` records each actual transport
entry, payload/channel/thread/outcome; `responses`/`streams` prove finite mock
cleanup. Use explicit finite scripts, never an unlimited fallback response or
unverified fake generate with an added keyword. Inert intent-only contexts use
an empty script so any accidental backend call is observable and fails.

For the nine files, the minimal reusable setup is a future **test-only**
`isolated_production_context(tmp_path)` factored from the existing `production`
fixture: save cwd and MolecularChat handler/level/propagation state, enter
temporary cwd before lazy imports, yield the same class/function namespace, then
restore/close only resources it introduced. The pytest production fixture can
delegate to this context; no conftest-wide monkeypatch, production dependency or
new third helper file is needed. This refactor is not implemented in this freeze.

Pytest callers use yield/ExitStack around that setup and mocked_client. The
unittest module uses ExitStack registered immediately with self.addCleanup,
enters TemporaryDirectory, then isolated_production_context and mocked_client in
that order (cleanup reversed). No reliance on pytest injecting unittest fixtures,
calling a fixture's __wrapped__, or a Python3.11-only TestCase convenience API.
Ensure temporary paths live until clients and introduced logger handlers close.

Existing prompts/calls/temperatures assertions may retain list attributes on the
real model for observation only. Populate them via a future transport-entry
observer (before response/error handling), not by overriding/wrapping generate or
patching its identity/signature. Alternatively project those exact fields from
ScriptedHTTP.calls at the assertion boundary. Keep returned text and expected
count/temp/target/ranking values unchanged. Scoped intent-output spies described
in the inventory can retain their exact assertions and accept round_state;
they do not prove strict HTTP behavior. Changes to any of these fixtures wait
for the explicit post-RED authorization.

### Exact proposed post-migration regression selection (NOT next RED)

After real RED is received, the two production files and nine fixture files are
separately authorized, and source/safety review passes, propose the following
ordered target list for the same reviewed isolated launcher. Module entries mean
the whole file, node entries all existing parameterizations. No predicted count:

```text
tests/agent/test_generation_transport_characterization.py
tests/test_llm_molecular_generator.py
tests/agent/test_agent_executor.py
tests/agent/test_generation_temperature_transport.py
tests/agent/test_generator_optimization_input.py
tests/agent/test_generation_ranking_contract_integration.py
tests/agent/test_react_agent_adapter.py::test_real_generator_uses_dedicated_model_and_request_temperature
tests/agent/test_molecular_agent_adapter_science.py::test_nondefault_temperature_reaches_generator
tests/agent/test_molecular_agent_adapter_science.py::test_main_model_does_not_replace_generator_or_rewrite_science
tests/agent/test_molecular_agent_adapter_science.py::test_lead_explicit_count_overrides_malformed_text_count
tests/agent/test_supervisor_agent.py::test_supervisor_hit_to_lead_real_generator_reports_public_count
tests/agent/test_workflow_executor.py::test_legacy_target_binding_reaches_real_generator_with_canonical_count
tests/agent/test_semantic_input_gates.py::test_instruction_local_ids_skip_generation_before_model_boundary
tests/test_agent_platform_health_check.py::test_chat_generation_adapter_supports_canonical_and_external_models
tests/test_model_request_lifecycle.py::test_actual_http_stream_cleanup_precedes_request_release
tests/test_model_request_lifecycle.py::test_actual_ollama_close_attempts_both_clients
tests/test_model_request_lifecycle.py::test_actual_ollama_close_failure_is_redacted_by_owner_only
```

The lifecycle stream node intentionally retains its existing mocked Ollama AND
OpenAI variants; no new external-provider or live backend calls. Do not run the
whole health-check/workflow-executor modules as a shortcut: this selection excludes
unrelated health actions/subprocess tests. Keep the full generation-ranking file
to preserve optional evidence/score/domain-error/sanitizer assertions, and the
full count/target/temperature/optimization files to retain negative controls.
This is a proposed future regression set, not comprehensive B2/P8/Web acceptance,
not a new execution grant, and not authority to migrate any existing file now.

## Accepted full-module RED and minimal implementation freeze

### Actual RED record (not parent-run, not a rerun)

With explicit sole-slot transfer, this worker ran exactly the full-module command
above once using the unchanged approved launcher. Initial tool chunk `49f431`
returned live session **84090**; that handle was immediately reported, then polled
without restarting. Terminal chunk **587bf9** returned process exit **1** and
`ORDINARY_PYTEST_EXIT=1`. Pytest result: **268 failed, 177 passed in 20.45s**,
445 executed cases, **0 warnings, 0 skips**. All 268 failure reports were
`phase=call`; no collection/setup/teardown failure. No separate process PID or
end-to-end wall-clock duration was reported. The slot was explicitly released
immediately at terminal; subsequent work was only hash/output inspection.

The full returned output was retained for classification (268 failure blocks,
no truncation notice), not obtained by a second test run. Classification:

- **74 API/signature failures:** 1 strict-signature assertion, 32 strict error
  API assertions, 14 explicit-False legacy calls rejected as unknown keyword,
  6 strict successful-string calls, 8 strict-flag validation cases, 9 helper
  exception-propagation cases and 4 executor fault cases blocked on the new API.
- **194 behavior/preflight assertion failures:** real helper/generator/client
  calls establish erroneous success after five HTTP errors/disconnects; error-
  then-success results contain I, and optimization results contain CCO/I rather
  than the clean expected sequence. The rejected-envelope matrix has 66 false
  successes and 30 generic no-candidate failures instead of provider_error.
  Unknown bindings are invoked (kwargs-only example five times), signature
  preflight is absent, lost capability leaves a successful partial batch or calls
  the unverified replacement, and both bridge-capture controls call the replacement.
  Captured generation logs expose the synthetic query/seed/provider markers.
- The same-instance tests fail on their FIRST contaminated result, so they do
  **not** establish cross-invocation state leakage. The four executor fault tests
  stop at missing API, so they do **not** experimentally prove RuntimeError retry.
  These remain validation obligations for GREEN, not invented RED observations.
- All newly added ordinary-chat compatibility families passed; first-round10,
  multi-round positives and genuine I controls were retained. This is offline
  behavior evidence, not live generation, scientific acceptance or B2/P8 closure.

Parent explicitly accepted this result/classification and the slot release.
Before/after SHA-256 were **identical for all nine files**:

| Frozen RED input | Before = after SHA-256 |
|---|---|
| Plan at RED | `7CB7469D0CBFE02719E2776879656FC3CA88B2D36ECCDF3F32403A07C3D1E4EE` |
| Test module | `B5DEA14E4D8627E63CEE3FC1A58D961C6C3482C9FB778BB563F0D328669187AE` |
| Runner | `9F6F5A2F68272442316DD614F6BEBD62C49371669C8E5E90DC4BC50BC5BED778` |
| `src/agent/tools/llm_molecular_generator.py` | `0632BA9A9969D892853189C904130CECBFFFD987265664AAB6E2474F479A6E30` |
| `src/web/models/ollama_model.py` | `A5E1D6426AD2119DCA8DA19BA3C4578668F2569994674F757610A794B21B601E` |
| `src/agent/tools/base_tool.py` | `E4C7768271028A9C48298BC7005883516B4C858151FFF5BF633D6E086ACF1BB8` |
| `src/agent/contracts/generation_request.py` | `9A6A9E517ECE5AD1A0FF6C892377003DFF47551F2E6B4EC153DDD06CBE5AAED0` |
| `src/agent/tooling/generation_ranking_contract.py` | `D02E10FDE7F0224F585CA2046A65A3BF90E44B74A547AA03368CA4C6BB94786D` |
| `src/agent/tooling/adapters.py` | `9486DC0E1D4EEAF05864460DD0F44A27D5C09D61A8588D6C234E3EAE25759E8E` |

HEAD remained `5d36eee2977152fe5047dc21e260500d6c965c4b`; production Git diff was
empty throughout RED. The original parent-owned 46-pass characterization and
Sartre approval remain separate historical evidence at their original hashes.

### Authorized implementation, awaiting review and GREEN transfer

Only `ollama_model.py` and `llm_molecular_generator.py` are changed; no fixture,
runner, adapter, model-routing or other production edits. This plan records RED.

- Client: keyword-only native-bool strict_errors defaultFalse; shared strict
  envelope decoder; fixed OllamaGenerationError with closed reasons; translations
  raised outside raw except contexts. Legacy branches keep their HTTP payload,
  return strings/defaults/positionals. stream_generate, close and constructor
  are unchanged. No internal client retry is added.
- Generator: lazy canonical client import for identity/signature preflight,
  fixed unavailable error, captured method per invocation; execute preflight
  follows existing request/seed validation. The child bridge takes a required
  captured-method keyword and never reads self.llm.generate. Both coroutine
  contexts share the same owned-loop helper; the RuntimeError catch contains
  only get_running_loop, never submit/future.result. Loops close and clear their
  owned current-loop reference in finally; executor ownership/timeouts remain.
- Typed strict/unavailable errors cross the text helpers unchanged. Other
  ordinary helper failures become fixed client_error without raw context. The
  retry boundary alone marks a private invocation-local round state; unavailable
  aborts instead of retrying and fresh failure results discard partial data.
  Successful batches retain existing quality/partial metadata and add only the
  fixed failed-round warning where appropriate. No state is cached on self.
- Optimization uses existing line cleaning and SMILES validation to qualify
  actual returned content before adding the seed; empty/invalid responses cannot
  manufacture one. Genuine I, successful seed inclusion, original prompts/model/
  temperature/num_predict1000, requested count and five-round replenishment stay.
  Generator execute/helper logs no longer interpolate query/seed/provider errors.

Static diff self-review checks those boundaries and whitespace only. There is
**no GREEN result**, no Python/import/compile/collection check and no new run.
The test module must remain exactly `B5DEA14E...9187AE`; any genuine fixture issue
must first be reported rather than editing assertions to force GREEN. Migration
of the nine old fixtures waits for this module's GREEN and a separate grant.
Parent schedules Wegener's short17 controls GREEN; this worker does not own or
reacquire that slot. No commit, push, live/network/assets work or B2 completion
claim. New implementation/plan hashes are provided in the freeze handoff.

## Accepted 445 GREEN and authorized fixture-migration source freeze

This section supersedes the earlier no-GREEN/no-fixture-authorization checkpoint;
those entries remain historical evidence. Parent reports Plato's re-review of the
two frozen production files APPROVE, no P1/P2, and explicitly authorizes only the
nine inventoried test migrations, the two unavailable-output updates, reuse of a
small context in this owned module, and this plan. **No scientific slot, no
execution, no more production/runner edits, no commit/push.** Parent will review
this diff before aligning latest main aa3; this worker has not merged or moved
HEAD from `5d36eee2977152fe5047dc21e260500d6c965c4b` on
`codex/b2-generation-transport-probe`.

### Actual full-module GREEN, prior to migration

The explicitly authorized sole-slot invocation used the same full module and
unchanged approved isolated launcher, without filters or reruns. Initial chunk
`3ffe20` returned session **2168**; the same handle was polled to terminal
`6f181a`: **445 passed in 13.07s**, **0 failed, 0 warnings, 0 skips**,
process exit **0**, `ORDINARY_PYTEST_EXIT=0`. The slot was explicitly released
at terminal. No separate process PID or total wall-clock duration is inferred.

Before/after hashes matched for all nine controlled inputs: test
`B5DEA14E4D8627E63CEE3FC1A58D961C6C3482C9FB778BB563F0D328669187AE`,
plan `9110D077B099CE0F5A1E3DC1472FCD6E503BD0810DF8CDEEE15B96EF1DCC21E8`,
the runner and two production hashes listed below, plus the four unchanged
base_tool/generation_request/generation_ranking_contract/adapters hashes in the
RED table. The 445 GREEN does NOT apply to the newly migrated fixture snapshot.
Original 46-pass characterization, its hashes/Sartre SOURCE approval, and the
268-failed/177-passed RED with its API/behavior distinctions remain intact.
This is offline regression evidence, not live scientific success or full B2/P8.

### Implemented test-source migration

- The owned module now factors `isolated_production_context` from its previous
  production fixture and exposes explicitly imported `offline_models` plus
  `recorded_ollama_context`. No conftest/global autouse fixture or production shim.
  Actual Ollama instances are constructed without constructor discovery; their
  real generate/generate_async bound methods and capability checks are unchanged.
  HTTPX MockTransport, trust_env=False, synthetic .invalid URL, finite successful
  envelopes and transport-entry records provide prompt/temperature observations.
- Existing fake response strings/model names are preserved. Test-local call caps
  are finite: one for each executor/public-count path, two for repeated
  temperature paths, zero for intent-only/input rejection contexts, and at most
  five per unittest-created client. These are fixture script capacities, NOT
  backend token bounds or a replacement for generator replenishment.
  Overflow is asserted at context exit even if production catches its exception.
  Original first-round10/multi-round10/five-round/partial/I/error/chat controls
  in the owned module are unchanged; no new case family or deselection.
- Unittest uses ExitStack registered with addCleanup before entering a temporary
  directory, isolated imports/logger context and clients. Pytest uses yield plus
  ExitStack. Real close() closes both clients even on failed assertions; new
  logger handlers are closed and prior handlers/level/propagation/cwd restored.
  Registered temperature dispatch now registers registry.close as a finalizer.
- Existing scoped chemistry facades and intent-level helper spies stay at their
  original unit boundaries. The ranking integration's candidates helper accepts
  keyword-only round_state while keeping its original result and assertions.
  Its genuine client has a zero-length HTTP script: this is still intent/contract
  evidence, not transport evidence. No fake is whitelisted or given a flag to
  bypass preflight.
- All nine files retain every test function and parameter case. Original
  positives, count/temperature, seed spelling, target evidence, ranking, warning,
  error and zero-dispatch assertions remain. Optimization's Mock.generate call
  checks now observe actual transport_calls/prompts, retaining empty/one-call
  expectations. No successful result was changed to unavailable.
- The two approved missing-client assertions now require the fixed message,
  TOOL_UNAVAILABLE / generation_strict_unavailable, failure and no data/quality.
  Static source inspection shows current legacy ToolResult normalization gives
  **FAILED**, not UNAVAILABLE, when raw status is absent. The ranking test records
  that existing status explicitly; this slice does not invent a status mapping
  or change production to match a new assertion.
- Cleanup compatibility, SOURCE review point: the React autouse offline guard
  and science-adapter rig now permit only a caller named _fallback_socketpair
  from the actual stdlib socket.__file__ to invoke the native base socket connect.
  This matches the approved runner's narrow Windows loop-self-pipe exception.
  Ordinary socket.connect/connect_ex/create_connection remain blocked; HTTP
  clients still use only MockTransport. This is a static cleanup requirement,
  **not an observed teardown failure**; no Python has run in this migration.

### Exact expanded selection for the next separately granted slot

The **ordered 17 entries** in “Exact proposed post-migration regression selection
(NOT next RED)” above are now the exact frozen proposed expanded selection,
unchanged and without extra nodes. Use all six whole modules first, then the
eleven listed node IDs with all their existing parameters, in one approved
`C:/Users/xkx52/.conda/envs/MedChat/python.exe -I -S -B scratch/ordinary_chat_offline_runner.py`
invocation. No -k/-m, raw pytest, collect-only substitute or extra health/workflow
module. Actual collected count must come from the authorized run, not guessed.
The own module must be rerun as part of that selection because its fixture
context has changed since B5DEA's 445 GREEN. Source review, parent alignment
decision, fresh before hashes and explicit sole-slot transfer are still required.
Do not start merely because another worker releases its slot.

Static checks only: reviewed all nine Git diffs and shared-context source;
test-function names/order preserved by text comparison; `git diff --check`
clean. No Python/import/compile/collection/tests/network or model/config/assets
inspection. The unchanged two-production/runner hashes below were rechecked.
The final plan hash is supplied separately in the handoff (not self-embedded).

### Source freeze SHA-256

| File | SHA-256 |
|---|---|
| `tests/test_llm_molecular_generator.py` | `F076E645CBFC52FD3D4048D6609E92B1213EF4FBA85BEEE95C87BF6A660ABC0A` |
| `tests/agent/test_agent_executor.py` | `8D233CBFC2F1231CFC3E23AA7F82E408B9DCB4BF50E69CAB9ED20915AD55B2DA` |
| `tests/agent/test_generation_temperature_transport.py` | `1D55E5279038CED99C229E90F31EFE8796C349D33E43403EFAA3BD13F2924A32` |
| `tests/agent/test_generator_optimization_input.py` | `B2CE817E5C65D21EB9A4D6EF979712AC61D9224C1CC359B7910D56E64C13AFF5` |
| `tests/agent/test_generation_ranking_contract_integration.py` | `B3FA21A2685B220718F3A003ACDB6852A7AFE6B9B96A4187283B1F876316B89A` |
| `tests/agent/test_react_agent_adapter.py` | `70E0BACF4D899D7CAE8D497A51F5DA50F1BE371C0F9FA12FCD56CBE2FCB5BCE0` |
| `tests/agent/test_molecular_agent_adapter_science.py` | `AD29C4BDE522D078A22017332308A0671E56B20C708B652395DEA6CCADC62468` |
| `tests/agent/test_supervisor_agent.py` | `0D5C7C87DB1DE19A77C2D451C5A1AFB9F22054A27991C0DCD1AC7F81AC576A69` |
| `tests/agent/test_workflow_executor.py` | `28A2B34D0C4978C3B08D95F11FAC9B65E8B5273C6AB5E073AFDB5AF348AED2C2` |
| `tests/agent/test_generation_transport_characterization.py` | `67FDFDD55C1362608A31721FD2B79CA1AE7F0F9E0443998743EE2D8BBEFB2B29` |
| `src/agent/tools/llm_molecular_generator.py` | `5F59170E9BFE83D6DD10C86725FC775D2C69CC25E63B0DC7F72C4ED4533F143A` |
| `src/web/models/ollama_model.py` | `472EC2A73D638AAD8AB92CA2406C09456E9ABAC409FB1379A7F9D93FBF6DB0B1` |
| `scratch/ordinary_chat_offline_runner.py` | `9F6F5A2F68272442316DD614F6BEBD62C49371669C8E5E90DC4BC50BC5BED778` |

## Accepted expanded regression and fresh independent QUALITY

This documentation-only closeout supersedes the earlier awaiting-alignment/run
checkpoint, without rewriting its historical evidence or authorizing more work.
Parent performed ff-only alignment from the original 5d36eee baseline to landed
main `aa3cdddbc1bcdf964ae95384c1a8740e44ff4205`, reporting all **14 frozen input
hashes preserved** and no overlapping main changes. The author independently
read that exact HEAD and verified the 14-input freeze before the expanded run.
Branch remains `codex/b2-generation-transport-probe`. Parent reported Plato SOURCE
approval of the final 14 hashes and exact ordered 17-entry expanded selection,
with no blockers; source/test/runner bytes were then held throughout both runs.

Both runs used the approved 9F6F launcher and the exact 17 entries above, including
all parameterizations (six whole modules plus eleven node entries). These are
fresh post-migration results, not a relabeling of the prior 445-pass run:

| Run | Actual session / terminal | Result | Pytest duration |
|---|---|---|---|
| Author, independently observed in this task | `22276` / `ce7ecd` (initial chunk `5d0e97`) | **752 passed, 162 subtests passed, 7 warnings, 0 failures/errors/skips** | **19.63s** |
| Fresh Bohr QUALITY, terminal evidence reported and accepted by parent | `55688` / `3cc36b` | **752 passed, 162 subtests passed, 7 warnings, 0 failures/skips; APPROVE** | **17.61s** |

The author invoked the selection once, immediately reported the live handle,
polled that same session to terminal without restarting, and observed process
exit **0**, `ORDINARY_PYTEST_EXIT=0`, with no collection/setup/call/teardown failure
reports. The sole Python slot was explicitly released at terminal, before the
read-only after-hash check. No separate PID or end-to-end elapsed time is inferred.
Bohr's fresh terminal/approval and unchanged 17 hashes are parent-reported
independent evidence, not an additional run by this author. No Bohr phase-level
log or separate runner exit field is invented beyond the supplied terminal report.

### Warning details and preserved execution inputs

The author's seven warnings were three SWIG DeprecationWarnings (SwigPyPacked,
SwigPyObject and swigvarlink lack __module__) and four FastAPI `on_event`
deprecation warnings (app startup/shutdown decorators plus two framework call
sites). They were attributed to the existing lifecycle node
`test_actual_http_stream_cleanup_precedes_request_release[False-normal-ollama]`.
Bohr's parent-reported warning count is also seven; the individual warning text
was not separately supplied here, so identical text is not asserted as observed.

Before/after **all 17 distinct controlled files matched** for the author run:
the 14 frozen inputs (plan, ten test files, two production files and runner),
plus three additional selected modules. Thus all 13 selected module files are
covered, despite some having multiple selected nodes. Parent also reports all
17 hashes unchanged across the fresh Bohr run. The 13-entry source-freeze table
above remains valid; the remaining four execution hashes were:

| Execution input | Before = after SHA-256 |
|---|---|
| Plan during both runs, before this authorized documentation append | `54FE1025C1A4F570BE01B0D380BA1B1F45C2E066BCE5954131D96B0584EF6EFD` |
| `tests/agent/test_semantic_input_gates.py` | `602B1632BA4AEF430B6C1FC31DD0DD9D09A831A18D1BD171D94FF13858CBCC5E` |
| `tests/test_agent_platform_health_check.py` | `1ED3B97F1FDE0D176926FF8DF8B2ADB70856599099F8A8C08904CBABE6852184` |
| `tests/test_model_request_lifecycle.py` | `03B9064A0F4F2422B7DF286D2C33ACA7D6C20A0BDA8D48FF36D6A7ACCB1FD917` |

The original 46-pass characterization, 268-failed/177-passed RED, 445-pass GREEN
and their hashes/classifications remain distinct historical records. These new
expanded passes establish the selected offline strict-generation, fixture,
ordinary-chat and cleanup regressions only. MockTransport output is synthetic;
no live model/network scientific success, full Web acceptance, token bounds,
durable invocation/receipt control, cancel/drain closure or complete B2/P8
acceptance is claimed. All previously separate live/control gates remain open.

Parent released **only this plan** for the present append after fresh QUALITY
approval. No source/test/runner edits or new Python/test/collection/compile run
are authorized or performed. The plan's new SHA-256 and Git blob ID are returned
in the final handoff; its historical execution hash above is intentionally not
replaced. Parent owns final audit, exact staging, commit and PR; this author does
not stage, commit or push and does not reacquire a test slot.
