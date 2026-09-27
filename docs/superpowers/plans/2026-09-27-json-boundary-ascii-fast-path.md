# JSON boundary ASCII fast-path prototype

> Execute with the executing-plans/TDD skills; parent SOURCE, runner and sole-slot
> release are mandatory before execution. No subagents, commit or push authorized.

**Goal:** Characterize semantic equivalence and bounded timing of one test-only
ASCII string-count shortcut. No production optimization or timeout-fix claim.

**Initial prototype scope:** This plan and `tests/agent/test_json_boundary_ascii_fast_path.py` ONLY,
in `codex/json-boundary-ascii-fast-path`, starting HEAD
`aa3cdddbc1bcdf964ae95384c1a8740e44ff4205`. Dynamic-tree diagnostics stay frozen.
No runner creation/change, Python/import/compile/test execution during preparation.

## Frozen reference and candidate

Reference: actual `src/agent/harness/decision_bounds.py`, Git blob
`079b2315ea5baf7eee43b34ed18a32db5cf56786`; LF-normalized UTF-8 SHA256
`2B4BAC3EA35187C887312EA4A37EF3270A41A08FCB5246922EB3A92269BB142C`.
Fixture checks the whole file hash, copies the complete current function source,
and changes exactly its string-accounting block. Reverse replacement must recover
the complete original function byte-for-byte. Compile only this pinned test-local
copy into a private namespace; never patch production/global imports. This is a
temporary prototype, NOT a second maintained production validator.

Fast path requires exact native bool `html_safe`, exact native int `remaining`,
and native ASCII text without JSON escapes; HTML-safe mode also excludes `<>&`
and backtick. After the unchanged `len(text)+2` lower-bound check, byte length is
mathematically exactly `len(text)+2`. Fixed compiled regexes are code constants,
not cached validation results. All other values/kwargs execute the original
string path, including truthiness/operator side effects of nonstandard kwargs.
Keep the original final budget check, iterative stack, node/depth accounting,
cycle/alias handling, type/key checks, nonfinite/surrogate rejection and4096-bit
integer ceiling. Do not eliminate caller checks, hashes, seals, or source reads.

## Prepared checks

- [ ] Freeze/single-edit proof and fast/fallback gate controls.
- [ ] All128 ASCII characters under both HTML modes; exact byte boundaries B-1/B/B+1.
- [ ] Unicode/escape/numeric/container matrix; independent json.dumps byte oracle
  for supported native values, including HTML expansion; no coercion oracle for invalids.
- [ ] Exact-type/subclass/key, nonfinite, surrogate,4096-bit and cyclic negatives;
  depth/nodes/bytes boundaries; aliases counted per occurrence, current-object
  mutation rejected on the next call, input remains unchanged.
- [ ] Nonstandard HTML/budget/depth/node kwargs retain reference outcomes and
  observable truthiness/arithmetic traces. No new argument rejection policy.
- [ ] Bounded AB microtest: four fixed synthetic shapes,6 warmup pairs then40
  measured pairs, AB/BA alternating, one validation per measurement. Preparation,
  equality checks and statistics are outside intervals. No profiler/model/network,
  random search, adaptive sampling or speed threshold. Emit only case ID, bytes,
  sample count, p50/p95 nanoseconds for reference/prototype. Timing can regress.

## Release / stop gates

Source preparation only: no result or manufactured RED claimed. Parent reviews
these exact two files. Parent has provisioned the ignored runner for THIS tree,
reporting forward/reverse byte comparison against admission6D490 with only REPO
literal changed; SHA256
`D523A0733923625748E93D8E754A27AD26A05C761F2892A4482FDADB27D54143`.
This is environment preparation, NOT execution permission. The dynamic-tree
F2DAB runner is neither redirected nor reused. After SOURCE + runner authorization
+ explicit local slot grant, pending exact command (NOT run):

```powershell
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -I -S -B scratch/ordinary_chat_offline_runner.py tests/agent/test_json_boundary_ascii_fast_path.py
```

Static enumeration predicts30 cases; collection has NOT been executed.
Capture reference/test/plan/runner hashes before and after, every actual terminal,
all failures and aggregate timings. No auto retry or production promotion.

Equivalent behavior plus useful bounded timing is only a prerequisite for a
separately reviewed production proposal, not evidence that real Web timeout is
fixed. Insufficient benefit or meaningful mixed/short-input regression stops this
candidate; no larger synthetic input substituted to justify it. Independently
decide minimal permanent tests versus removal of prototype/benchmark later.

Initial preparation status (superseded by the evidence below): source authored, static inspection only; SOURCE and execution
authorization pending. Worker made no production, dynamic-tree, runner or process
changes. Parent's runner provisioning is separate from the two-file worker scope.

## Parent execution evidence and next source-only authorization

Parent SOURCE approved the two-file prototype freeze, then owned the sole local
Python slot and ran the exact module command above. Actual terminal `dfe89f`:
exit0, **30 passed in2.88s**, no warnings/skips; all four before/after hashes
unchanged. This is authoritative parent evidence, not a worker-run result.
Frozen test SHA256 `44321E40A9C3C4CDAA17C68F6FBBA35D4BD6A098F90C164B0637E2FFBA1006CC`;
plan-at-run SHA256 `3E3729AA5566B44CFFA8CDA3F6376C415EF55C9A4DA439873F3AED6CDFB9C653`;
reference and runner hashes are recorded above.

Fixed6 warmup/40 measured AB pairs per shape, nanoseconds:

| Synthetic wire bytes | Reference p50 / p95 | Prototype p50 / p95 |
| --- | --- | --- |
|66 ASCII|22800 /23100|3500 /4200|
|2476 ASCII containers|859850 /880100|70300 /75400|
|7682 escaped|452400 /462100|450750 /466400|
|5122 Unicode|651900 /663700|650200 /660100|

Useful bounded ASCII benefit; no timing gate, production measurement, overall Web
fix or timeout-root-cause claim. Escaped/Unicode retain fallback. Current slot is
FREE, **not granted**. This next preparation authorizes only this plan and new
`tests/agent/test_json_boundary_ascii_accounting.py`; no production change,
Python/import/test execution, runner edits, commit or dynamic-tree changes.
The temporary prototype remains byte-for-byte frozen pending parent archival
decision; it must not ship as a second maintained validator or be silently
rebaselined when the production reference changes.

## Permanent regression / minimal production proposal

Use writing-plans and test-driven-development, then executing-plans after parent
release. No subagents. Architecture stays one iterative validator, no result
cache or altered caller checks. Existing `re` import suffices.

- [x] Prepare permanent tests against actual `decision_bounds.validate_json`,
  independent `json.dumps(ensure_ascii=False, allow_nan=False)` + explicit HTML
  substitution + UTF-8 byte oracle. No copied source, benchmark or profiler.
- [ ] Parent SOURCE review the permanent tests/design and grant exact RED slot.
- [ ] Run the whole new module once against unchanged production. Two native
  bool parameterizations of `test_native_ascii_avoids_python_character_walk`
  should fail the zero-`ord`-calls assertion. Other cases are compatibility
  controls, not required failures. No import/fixture error counts as mechanism
  RED; mechanism RED is not a new reproduction of the scientific Web timeout.
- [ ] Only after genuine RED and explicit production authority, change
  `src/agent/harness/decision_bounds.py`: add exactly two compiled constants
  after existing imports and replace only string accounting after the unchanged
  `len(item)+2 > remaining` rejection. Keep every other statement/caller intact.

```python
_ASCII_ESCAPE = re.compile(r'[\x00-\x1f"\\]')
_HTML_ESCAPE = re.compile(r'[<>&`]')
```

Proposed replacement (NOT applied):

```python
            if (type(html_safe) is bool and type(remaining) is int
                    and item.isascii() and _ASCII_ESCAPE.search(item) is None
                    and (not html_safe or _HTML_ESCAPE.search(item) is None)):
                remaining -= len(item) + 2
            else:
                remaining -= 2
                for char in item:
                    code = ord(char)
                    if 0xD800 <= code <= 0xDFFF:
                        reject()
                    remaining -= (6 if html_safe and char in '<>&`' else
                                  2 if char in '"\\\b\f\n\r\t' else
                                  6 if code < 32 else
                                  1 if code < 128 else 2 if code < 2048 else
                                  3 if code < 65536 else 4)
                    if remaining < 0:
                        reject()
```

Exact native ASCII without applicable escapes has one byte per character plus
two quotes. Short-circuit native-flag/native-remaining guards precede scanning;
nonstandard kwargs keep original comparisons, per-character truthiness and
arithmetic, exceptions and no new rejection/coercion. Remaining type is checked
at each string, not only initial max_bytes. The original final budget check,
size/type/nonfinite/cycle/depth/count/surrogate/4096-bit/key checks all remain.
There is no repeated encode/walk removed elsewhere: current code already walks
once. Regexes are fixed syntax constants, never memoized object validity.

Permanent matrix (static enumeration47 cases; not collected/executed): all128
ASCII characters scalar and key/list-value, both HTML modes and B-1/B/B+1;
empty/long/SMILES/escape strings; UTF-8 width boundaries, combining/separator
characters, mixed Unicode/escapes; scalar/container/numeric4096-bit boundaries;
21 rejection kinds (nonfinite, surrogates including raw pair, overlarge integers,
nonstring keys, bytes/tuple/set/hostile object, five subclasses, three cycles);
depth32/33, nodes16384 boundary, keys count as nodes, aliases per occurrence,
mutation-to-NaN revalidation/no mutation by validator. Mechanism spy only on this
module's `ord`, delegating to builtins: eligible native ASCII makes zero calls;
14 escaped/Unicode/HTML/non-native-flag/budget fallback cases keep per-character
calls. Custom flag truthiness and exception identity, custom budget exact
operator trace, nonstandard depth/nodes, invalid budget TypeError and early
size rejection remain explicit. No test imports the prototype.

- [ ] Run same permanent module GREEN once, then the expanded regression union
  below under a separately confirmed slot. Record actual failures, terminal,
  counts and before/after production/test/plan/runner hashes; no automatic retry.
- [ ] Parent decide prototype archival/removal before release. Its reference
  pin will intentionally fail after production changes; exclude it from the
  post-change union, do not weaken its assertions or ship duplicate validators.
- [ ] Independent SOURCE/QUALITY and later original-deadline Web gate below.
  No commit/push authorized by this plan.

### Actual caller scope (read-only; no caller edits)

Direct imports/calls and projected boundary consumers, explicitly expanded:

| Source module | Boundary role |
| --- | --- |
|`src/agent/harness/decision_bounds.py`|context, observation, raw observation projection and validation|
|`src/agent/contracts/decision_bindings.py`|binding arguments, bounded model payload/proofs|
|`src/agent/contracts/ordinary_admission.py`|native admission bounds|
|`src/agent/harness/decision_bindings.py`|admission metadata, `_native`/wire/digest/record authentication and closure revalidation|
|`src/agent/harness/decision_binding_acceptance.py`|acceptance payload boundaries|
|`src/agent/harness/decision_binding_inputs.py`|journal wire and projected context|
|`src/agent/runtime/run_session.py`|observation projection and step metadata|
|`src/agent/tools/rag_search_tool.py`|current-source eligibility payload|
|`src/agent/tools/reverse_target_tool.py`|current-source eligibility payload|
|`src/agent/harness/decision_continuation.py`|continuation payload/raw data/clarification|
|`src/agent/harness/decision_execution.py`|raw/legacy observation conversion|
|`src/agent/harness/decision_history.py`|memory, system/query and history pairs|
|`src/agent/harness/decision_inputs.py`|input/observation projections and integrity digests|
|`src/agent/harness/decision_loop.py`|metadata and decision proposal guards|
|`src/agent/harness/decision_policy.py`|requirements and HTML-safe observation encoding|
|`src/agent/harness/ordinary_chat_policy.py`|context projection|
|`src/web/decision_request.py`|request payload, query and context projection|
|`src/web/decision_chat.py`|display, counters, ordinary metadata, raw/event/envelope/execution bounds|

Seals, integrity checks, receipt/source/TTL revalidation and configuration checks
are still performed on every current object. This optimization changes none of
their ordering or validity decisions. Pydantic `model_validate_json` matches are
not calls to this validator and are not used to infer coverage.

### Exact pending commands (none authorized/run in this preparation)

In this independent tree, D523 runner unchanged. RED and later GREEN use exactly:

```powershell
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -I -S -B scratch/ordinary_chat_offline_runner.py tests/agent/test_json_boundary_ascii_accounting.py
```

Expanded union: permanent matrix plus contract/input/loop/acceptance/session,
admission, history/continuation, observation, receipt eligibility and Web
request/display/runtime consumers. Existing assertions are unchanged. Paths are
explicit so no shell glob or runner switch changes collection:

```powershell
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -I -S -B scratch/ordinary_chat_offline_runner.py tests/agent/test_json_boundary_ascii_accounting.py tests/agent/test_decision_contract.py tests/agent/test_decision_requirements.py tests/agent/test_decision_inputs.py tests/agent/test_decision_loop.py tests/agent/test_decision_migration_boundaries.py tests/agent/test_decision_spec_findings.py tests/agent/test_decision_merge_blockers.py tests/agent/test_decision_binding_arguments.py tests/agent/test_decision_binding_requirements.py tests/agent/test_decision_binding_profiles.py tests/agent/test_decision_binding_inputs.py tests/agent/test_decision_binding_loop.py tests/agent/test_decision_binding_acceptance.py tests/agent/test_decision_binding_session.py tests/agent/test_decision_dynamic_bindings.py tests/agent/test_binding_analysis_clause_integration.py tests/agent/test_dynamic_run_session.py tests/agent/test_workflow_run_session.py tests/agent/test_run_session_ownership.py tests/agent/test_decision_history.py tests/agent/test_decision_continuation.py tests/agent/test_decision_continuation_store.py tests/agent/test_decision_clarification.py tests/agent/test_decision_protocol_recovery.py tests/agent/test_ordinary_admission_budget.py tests/agent/test_ordinary_semantic_admission.py tests/agent/test_ordinary_capabilities.py tests/agent/test_ordinary_chat_policy.py tests/agent/test_ordinary_continuation.py tests/agent/test_ordinary_intent_protocol.py tests/agent/test_ordinary_intent_transport.py tests/agent/test_rag_tool_contract.py tests/agent/test_rag_current_eligibility.py tests/agent/test_rag_receipt_consumption.py tests/agent/test_reverse_receipt_consumption.py tests/agent/test_decision_chat.py tests/agent/test_decision_chat_transport.py tests/agent/test_decision_chat_acceptance.py tests/agent/test_decision_transport_boundaries.py tests/agent/test_web_decision_admission.py tests/agent/test_web_decision_fingerprint.py tests/agent/test_web_decision_references.py tests/agent/test_web_decision_runtime.py tests/agent/test_web_decision_runtime_lifecycle.py tests/agent/test_web_decision_runtime_references.py tests/agent/test_ordinary_app_assembly.py tests/agent/test_ordinary_web_runtime.py tests/agent/test_ordinary_web_lifecycle.py
```

Later Web gate is separate and mandatory after production regression/SOURCE:
parent-controlled exact transfer into `dynamic-bindings-b1`, source freeze and
explicit scientific slot grant; use that tree's approved F2DAB runner, not D523.
First original complete two-wire node (native + JSON) once, preserving original
prompts/scientific assertions,3s receive,5s cleanup, tool/profile/guard behavior:

```powershell
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -I -S -B scratch/ordinary_chat_offline_runner.py tests/agent/test_b1_normal_web_runtime.py::test_b1_scientific_dataflow_is_observation_driven
```

This node alone is not whole Task6. Parent then releases the existing full Task6
Python/Node acceptance gates under its own plan. Prior real timeout failures
remain evidence; no pass inferred from synthetic timing or permanent controls.
If Web still fails, retain actual terminal and do not blind-retry, relax timing,
remove checks or expand temporary profiling. Diagnostic collector cleanup is a
separate reviewed release decision, not part of this optimization preparation.

Current handoff: permanent test source and production design ready for parent
SOURCE review; no new Python process, import, test result or production edit.

## Approved SOURCE, actual mechanism RED, archival and implementation freeze

Plato independent SOURCE approved permanent test SHA256
`D43AABD347029CE8639AB87D9A104DAD4671649935340E5F46B8D6D03199B1F0`
and design/plan SHA256
`3F1D07CB377732C93E177D59ACE8D500B06FE360B34A8A5C6612DE3F90FA7E11`.
After Bohr released the slot, parent granted exactly one permanent module RED.
Actual command, in this independent tree with unchanged D523 runner:

```powershell
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -I -S -B scratch/ordinary_chat_offline_runner.py tests/agent/test_json_boundary_ascii_accounting.py
```

Direct terminal `a24a8e`, exit1: **2 failed,45 passed in3.48s**, no warnings/skips.
Both failures were `phase=call` at permanent test line147, assertion
`eligible_ascii_used_python_character_walk`: `[json]` observed2509 `ord` calls,
`[html]`2505, each expected0. All45 compatibility cases actually passed.
No import/fixture errors, no retry and no persistent session to poll. Before
`03149b` / after `5dbd41` confirmed all five production/permanent/prototype/plan/
runner hashes unchanged. Worker released the scientific slot immediately after
terminal verification. This RED establishes the missing performance mechanism,
not an additional scientific timeout reproduction or an end-to-end fix.

Parent then archived the prototype using checked apply_patch to ignored
`scratch/test_json_boundary_ascii_fast_path.py`; original `tests/agent/` path
no longer exists. Worker read-only verification `5a52f0` confirmed archived
SHA256 `44321E40A9C3C4CDAA17C68F6FBBA35D4BD6A098F90C164B0637E2FFBA1006CC`
unchanged, along with production/permanent/plan/runner pre-edit hashes. Archive
is historical test-only evidence, not a runnable post-change regression or
second maintained validator; it will not be shipped in the tests directory.

Parent explicitly authorized implementation only: applied the two fixed regex
constants and approved ASCII branch above to `decision_bounds.py`, preserving
the original fallback statements (only indented under else) and all other
statements. No test edits, production callers, dynamic-tree changes, imports,
Python execution, deadline changes, commit or push. This entry supersedes the
earlier preparation-only status; prior observations remain historical evidence.

- [x] SOURCE-approved design and47-case permanent matrix.
- [x] Actual two-case mechanism RED and45 compatibility passes.
- [x] Parent archival with exact prototype identity preserved.
- [x] Minimal production source prepared after RED and explicit authority.
- [ ] GREEN remains **NOT RUN**; next slot transfer is parent-controlled.
- [ ] Expanded49-module regression, independent reviews and original3s/5s Web
  verification remain pending; no whole-Web success or root-cause claim.

Freeze handoff: no slot held or Python process started in this implementation
turn. Parent receives production/permanent/plan hashes for review before GREEN.

## Actual GREEN, expanded independent regression and Web limitation

The preceding unchecked GREEN preparation state is historical. Frozen production
SHA256 `0D26E2F18606074598C9FC95186A979737ACDC3E186710FB3899C0AA0723CE27`
and permanent test SHA256 `D43AABD347029CE8639AB87D9A104DAD4671649935340E5F46B8D6D03199B1F0`
passed the same one-module command: terminal1b123f,47passed1.72s, zero warnings/
skips/failures. Independent Plato SOURCE approved that exact implementation.

Both expanded runs executed the exact ordered49 command above with the unchanged
D523 runner and baseline aa3cdddbc1bcdf964ae95384c1a8740e44ff4205:

| Run | Actual session / terminal | Result | Pytest time |
|---|---|---|---|
| Author Wegener |5934 /47f5a8|4774passed,1skipped,7warnings,exit0|982.51s|
| Independent Kant QUALITY |9511 /47a1be|4774passed,1skipped,7warnings,exit0|995.69s|

All53 unique controlled hashes matched before/after each run, covering49 modules
plus production, plan, runner and archived prototype. The plan execution hash was
08E40DDCC9E736FB962D75FB93AE9753AB358AAC701B3886F32A45E6327C3E05 before this
documentation append. Author evidence d55fd3/28bbe7 and fresh4b688b/35c457 retain
the manifests. The skip is unavailable directory symlinks at
test_decision_chat_acceptance.py:149; warnings are3SWIG and4FastAPI on_event
deprecations. No retries, deadline changes or source edits during execution.
Kant independently approved with no blocking findings, then released the slot.

Parent transferred exactly0D26 into dynamic-bindings-b1 after checking old bounds
2B4BAC, unchanged D334 scientific test and F2DAB runner. Actual original native/
JSON Web gate46743/cdcd65 remained **2failed,7warnings,63.47s,exit1**: both hit
the unchanged3s receive TimeoutError, not a scientific numeric assertion failure.
All20 controlled hashes were unchanged. Temporary profiling/timing diagnostics
were enabled; both complete diagnostics located silence during acceptance closure,
but their overhead is unmeasured. This optimization is NOT a demonstrated Web
timeout fix, uninstrumented latency improvement, deadlock diagnosis or full P7/P8
completion. Existing failure evidence is preserved in the normal-Web plan.
Next Web investigation compares genuinely uninstrumented behavior without changing
the original prompts, source checks, scientific assertions or3s/5s deadlines.

After both49 runs and the Web attempt, parent fast-forwarded this independent
tree to landed strict-generation main c612c9873a04823c936d710861bf890ad30b844a.
The13 incoming paths had no overlap with all53 frozen inputs; all53 hashes were
unchanged across alignment. The two local49 runs are still aa3cddd evidence,
not reruns on c612c98; full latest-head CI remains required before merging.
No original mixed-checkout change, real model/asset activation or deployment.

Publication scope is exactly this plan, the single boundary source and the
permanent accounting regression module. Ignored prototype and temporary Web
diagnostics are excluded; no alternative validator is shipped.

### Publication whitespace correction

Staged diff inspection found one extra blank line at the permanent test's EOF,
which the earlier unstaged check did not cover while that file was untracked.
Parent removed only that empty final line; no assertion or executable statement
changed. New test SHA256 is
`FCF8346F6C2B042DDC3B4A4F862AF34151083CFA918F9265575785A4AC4E3A62`.
The two expanded local results above remain evidence for D43, not a new run on
FCF. Independent final SOURCE approved functionality and required this formatting
correction. Latest-head full CI is still required; no Web-fix claim follows.
