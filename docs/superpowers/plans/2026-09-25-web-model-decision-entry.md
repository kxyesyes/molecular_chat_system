# Web model decision entry — approved A1 TDD and phased roadmap

> For agentic workers: use executing-plans/test-driven-development for the A1 tasks below. Parent approved A→B and the safety boundaries, but **only A1 is authorized on this branch**. A2/B need independent PRs. Do not execute deferred tasks.

**Goal:** Add server-owned admission, trusted selected-molecule/direct-binding and nonsecret configuration-generation fingerprint support, with offline tests. This does not activate the normal Web entry and does not complete P7.

**Architecture:** Existing ModelDecisionLoop / WorkflowRunSession / registry / validators / evidence / continuation remain authoritative. A1 adds bounded request preparation and minimal support to those existing boundaries. Later A2 connects the normal `/ws`; B extends evidence references for local target generation, candidate analysis and ranking.

**Tech stack:** Existing Python 3.10, Pydantic, RDKit, SQLite, asyncio, pytest. No new dependencies or execution framework.

**Design:** `docs/superpowers/specs/2026-09-25-web-model-decision-entry-design.md`.

## Parent revision and local workflow

- [x] Move this existing plan from root `plans/` into `docs/superpowers/plans/` using apply_patch, without a duplicate.
- [x] Commit only the corrected spec/plan on `codex/web-model-decision-entry`: `cd1415e`.
- [x] Merge reviewed main `1bba0256409a06317486530e5c1cfa6598b8e381` into this task branch, not into main: `2cf8e72`. Reinspected integrated analysis contracts.
- [x] At the next green boundary, merge parent-requested main `16b9157` (4B typed target): `6681225`. Target string input remains non-projecting; no new target argument role or registry edit in A1.
- [x] Execute only A1 RED→GREEN below; run focused offline tests. Parent coordinates full Agent/full suite.
- [x] Freeze local changes for parent SPEC/QUALITY; no push/PR/publication. No live model, browser, server, environment-file or scientific asset access.

Normal entry means existing `/ws`. **Do not add `/api/chat` or any HTTP surface.** Existing static workflow HTTP APIs retain all original semantics. New HTTP work is outside this package.

## Exact A1 file scope

Production files (only):

1. `src/web/decision_request.py` — new, server-only bounded initial request admission and detached request specification; fixed rejection codes; initial four-tool ceiling.
2. `src/agent/harness/decision_bounds.py` — exact AgentContext/ResolvedScientificMolecule projection, preserving strict generic JSON limits.
3. `src/agent/harness/decision_inputs.py` — exact selected structure binding, explicit-input precedence, identity digest and source-revocation guard.
4. `src/agent/harness/decision_loop.py` — consume context projection and optional server-injected config generation; existing loop/Session control unchanged.
5. `src/agent/harness/decision_continuation.py` — consistent projected identity/fingerprint and reference checks before continuation claim/reuse.

New focused tests:

- `tests/agent/test_web_decision_admission.py`
- `tests/agent/test_web_decision_references.py`
- `tests/agent/test_web_decision_fingerprint.py`

Read/reuse, do not redesign: AgentContext, ResolvedScientificMolecule, TaskRequirements, molecular/target/generation parsers, ScientificReferenceService, WorkflowRunSession, validators, typed adapters, existing fixtures.

Do not edit app/chat handler/leases/routes/socket receiver/UI/default mode, decision transport, registry/catalog, generator/ranker algorithms, B argument schemas, runtime database schema, config files or assets. A1's config generation is merely an explicit nonsecret server input to fingerprinting; actual config epoch creation/switch hooks are A2.

## A1.1 Request admission (RED, then minimal GREEN)

**Files:** new `src/web/decision_request.py`, `tests/agent/test_web_decision_admission.py`.

Proposed interface:

```python
# Server-only; no network or tool execution.
# prepare_decision_request(payload, *, session_id, trace_id,
#                          references=None, config_generation=None)
# -> PreparedDecision
# PreparedDecision exposes fresh/detached context, request_kind,
# allowed_tools, required_tools, immutable requirements and config_generation.
# DecisionAdmissionError.code is a fixed public reason, never raw input.
```

A1 supports the initial four tools only: property_calculator, drug_likeness_assessment, activity_predictor, target_database_search. No profile switch yet; no model/Planner call during admission. Effective execution authorization still belongs to existing authorized_catalog/registry; admission cannot make a missing or non-owned adapter executable.

- [ ] RED: assert missing module/function as an explicit feature assertion, not unexplained import-error collection.
- [ ] Admit ordinary chat with empty required/allowed tools; later A2 must genuinely call external model, not generate canned success in admission.
- [ ] Admit explicit property/likeness requests with all original SMILES subjects, requested metrics and mandatory tools; preserve explicit false Lipinski results.
- [ ] Missing structure stays scientific and retains mandatory tool obligations (clarification occurs in the later loop). Invalid explicit input rejects and cannot be rescued by selection.
- [ ] Admit user-target activity and explicit target search using existing parsers; do not use candidate provenance/model inference to pick target.
- [ ] Reject unsupported generation/ranking/ADMET/reverse/RAG/docking execution and compound requests requiring them; no property-only completion. Distinguish explanation of a topic from execution.
- [ ] Ambiguous/unrecognized execution rejects with fixed clarification code, not chat finish. Explicit exclusions cannot be silently dropped.
- [ ] Strict boolean flags/options; bool-as-count, NaN, invalid count and oversize input fail. Existing generation-only mol_count is validated (1–10, absent/null defaults to 5) but is not an analysis-subject count. Quantified analysis prose requires clarification in this bounded A1; it cannot silently override the complete subject list. Keep whole structure validation, no regex fragment rescue.
- [ ] Browser identity/capabilities/tools/requirements/config generation/backend/resolved structure are forbidden. Existing timestamp/client_id are ignored for ownership; IDs supplied as function args are authoritative.
- [ ] Call ScientificReferenceService.resolve only after bounded validation, with server session and tools flag; source/ACK/ordinal checks remain there. Result must be an exact trusted resolved type, not arbitrary objects.
- [ ] Returned context mutations and original payload mutations cannot change immutable prepared obligations or selected molecule.
- [ ] Run focused RED, implement smallest support module, run GREEN.

Concrete first test shape:

```python
def test_admission_feature_is_present_before_authority_tests():
    import importlib.util
    assert importlib.util.find_spec("src.web.decision_request") is not None, (
        "A1 server-only request admission is missing"
    )
```

Required parameter table:

| User request | Required admission result |
|---|---|
| 你好; tools/rag off | chat, no tools, exact query retained |
| 解释 logP 是什么 | chat, not measurement |
| 计算 logP 和分子量；SMILES: CCO | scientific/property, expected CCO and both metrics |
| 计算性质及类药性；SMILES: CCO; CCN | both tools, both whole subjects |
| 请计算分子性质 | scientific/property obligation, no invented subject |
| 预测 BuChE 活性；SMILES: CCO | scientific/activity with user target preserved |
| 查询 EGFR 的靶点结构 | scientific/target lookup |
| 生成5个分子并排序前三个 | unsupported in A1 |
| 计算 CCO 的 ADMET 和性质 | whole request unsupported |
| 对接这个分子 | unsupported, no staged helper |
| 科学计算; tools=false | tools disabled, not unrestricted chat |
| invalid explicit structure + confirmed old selection | reject new invalid input, no old-structure dispatch |

Command:
`python -B -m pytest tests/agent/test_web_decision_admission.py -q -p no:cacheprovider`.

## A1.2 Trusted context projection/direct binding (RED, then minimal GREEN)

**Files:** `decision_bounds.py`, `decision_inputs.py`, `decision_loop.py`, `decision_continuation.py`; new `test_web_decision_references.py`.

- [ ] RED: actual loop with server-confirmed selected molecule currently rejects non-JSON resolved dataclass. Use existing temporary-store `confirmed` and `setup_loop` fixtures. No loop/Session/resolver mocks.
- [ ] Exact AgentContext and resolved dataclass shape projected before deepcopy/privacy/hash; reject subclasses, arbitrary nested objects, cycles, oversized fields and malformed/extra attributes without calling their hooks. Generic validate_json stays unchanged.
- [ ] Keep original query separate from exact stored canonical SMILES. Properties/likeness receive the bound structure; activity receives structure plus original query and user target.
- [ ] Preserve original/canonical candidate fields; no recanonicalization of the selected input at this boundary. Explicit input overrides any prior selection, including invalid explicit structure.
- [ ] Digest includes effective selection pointer/compound ID/revision/exact structure and user target; plain non-reference contexts retain compatibility.
- [ ] Source owner/revision/ACK checked before model dispatch, before each selected action and before duplicate-action reuse; existing Session dispatch guard stays in place.
- [ ] Reject revoked source on waiting continuation before CAS; no model/tool execution and waiting record unchanged.
- [ ] Candidate partial-source/new explicit selection semantics remain existing reference semantics; do not promote original generation or widen automated partial-batch reuse.
- [ ] Run intended RED, implement only these boundary changes, run focused GREEN and existing scientific-reference/decision-input regressions.

Concrete red regression (existing fixture signatures):

```python
import asyncio
from test_decision_loop import setup_loop, tool, finish_last
from test_scientific_reference_execution import confirmed

def test_actual_loop_binds_confirmed_structure(tmp_path, setup_loop):
    from src.agent.contracts import AgentContext
    from src.agent.tools.property_calculator import PropertyCalculator
    store, references, pointer = confirmed(tmp_path)
    query = "计算刚才第二个分子的属性"
    selected = references.resolve(query, pointer, None,
                                  session_id="owner", enable_tools=True)
    bundle = setup_loop([tool(), finish_last], [PropertyCalculator()])
    bundle.loop.store = store
    result = asyncio.run(bundle.loop.run(
        AgentContext(query, "selected-decision", user_id="owner",
                     session_id="owner", resolved_molecule=selected),
        request_kind="scientific", allowed_tools={"property_calculator"},
        required_tools={"property_calculator"},
    ))
    assert result.success, result.metadata
    assert result.tool_results[0].data[0]["smiles"] == "CCN"
```

## A1.3 Config-generation fingerprint and continuation safety

**Files:** `decision_loop.py`, `decision_continuation.py`; new `test_web_decision_fingerprint.py`.

- [ ] RED: two otherwise identical server configurations with different nonsecret generation IDs must have different fingerprints; no key, credential digest or arbitrary model attributes are inspected.
- [ ] Supply optional bounded `config_generation` to ModelDecisionLoop (keyword-only); admission returns it for later A2 assembly. Browser cannot supply it. No app epoch/switch wiring in A1.
- [ ] Absent generation retains supported core caller behavior. Reject non-string/empty/oversize/sensitive generation without provider/store actions. All values are bounded before copy/hash.
- [ ] Same-owner waiting continuation resumes under unchanged generation and original requirements; changed generation (including a credential-only change represented by server epoch) rejects before CAS/model/tool.
- [ ] Invalid owner/ref/spec/requirements/nonce cannot consume waiting record; source revocation or explicit input replacement cannot reuse stale selected evidence.
- [ ] Replayed history resolves inputs and digests using the same projected reference rules. Revise internal continuation protocol revision only as necessary for the new semantics; never execute incompatible historical snapshots.
- [ ] Regression: model/tool budgets, one-use CAS, evidence seals, no uncertain replay, immutable activity target and exact source SMILES remain intact.

Commands (focused only, no full Agent):
```powershell
python -B -m pytest tests/agent/test_web_decision_admission.py tests/agent/test_web_decision_references.py tests/agent/test_web_decision_fingerprint.py -q -p no:cacheprovider
python -B -m pytest tests/agent/test_decision_inputs.py tests/agent/test_decision_requirements.py tests/agent/test_decision_continuation.py tests/agent/test_decision_continuation_store.py tests/agent/test_decision_protocol_recovery.py tests/agent/test_decision_transport_boundaries.py tests/agent/test_scientific_reference_execution.py tests/agent/test_scientific_reference_resilience.py -q -p no:cacheprovider
```

Tests must run through the approved MedChat interpreter with isolated temp directories, disabled real network and no default app/config/data imports. Reuse the isolated-run procedure in `docs/superpowers/plans/2026-09-24-rag-service-extraction.md`; tests may use real local RDKit computations, not providers/assets. Do not treat environment/import errors as feature RED.

## A1.4 Freeze for parent SPEC/QUALITY

- [x] Review exact production diff against five-file allowlist, new tests, spec and plan; no receive/UI/default/HTTP/B changes.
- [x] Compile relevant source (or compileall with isolated bytecode target); run git diff --check.
- [x] Record RED reasons and focused GREEN results, interpreter/commands, remaining full-Agent queue and current branch/head. Do not claim CI/full suite/live science has passed.
- [x] Local freeze with no push. Parent schedules SPEC/QUALITY and full Agent; findings may require a later local correction. A1 is support-only and not P7 completion.

### Local A1 implementation / review notes (2026-09-25)

The source diff is exactly the five approved files plus three new test files. No socket, UI, HTTP, model configuration, lease or default-activation edits. The two documentation files carry the scope and evidence updates. Implementation is frozen in a local commit based on `6681225` (exact freeze SHA in the parent handoff), not a released PR and not P7 completion.

Concrete choices for SPEC/QUALITY:

- Admission grants only the explicitly required subset of the initial four tools; registry ownership/capability checks remain authoritative. No planner, model routing, tool execution or canned chat answer is produced by admission.
- Browser payload is plain bounded JSON (24 KiB); query is 16 KiB; identity is a server argument. Tools/RAG flags are strict bool; temperature 0–2; optional generation count 1–10; RAG display count 1–20, matching existing UI. Enabling RAG is not permission to execute the excluded RAG tool.
- Exact subject count is derived from the full original SMILES list (up to existing parser limits), with no fragment salvage or canonical duplicate collapse. Quantified prose, exclusions, unknown execution clauses and mixed lookup/calculation need clarification. The parser accepts labeled fields terminated by newline/semicolon; trailing prose inside `SMILES:` is invalid, so the examples above place intent before the field.
- Missing structure retains scientific requirements. Explicit unknown/conflicting activity targets and ambiguous target search reject. Confirmed selected input is resolved by the existing owner/ACK service only. Properties/likeness receive the exact stored canonical string; activity additionally receives original query/user target. This object does not contain the generator's original spelling: original CandidateRecord data is not rewritten or falsely reconstructed.
- Context projection admits exact AgentContext/ResolvedScientificMolecule only, before copy/privacy/hash; arbitrary dataclasses, subclass/copy hooks, extra attributes, cycles and malformed known scalar shapes fail. Generic JSON validation remains strict. Invalid-context rejection uses a fixed trace marker instead of reading properties on a hostile object.
- Reference revalidation occurs before model calls, after model response, at input resolution before action/reuse, at the existing Session dispatch guard and before continuation CAS. Explicit replacement disables the prior selection across later clarification turns; replay applies the identical transition. Historical selected-source checks remain conservative: a revoked source used in an earlier waiting history rejects that continuation; start a new request to use an independent explicit structure.
- Optional server `config_generation` is a nonsecret opaque ID, not a key hash; absence supports existing direct callers. A2 must create/rotate it when runtime configuration changes. Waiting internal protocol revision is now 5 because reference binding/history semantics changed; revision-4 waiting snapshots are rejected, not migrated/re-executed. Public decision version and input_ref-only argument schema are unchanged.
- New 4B adapter schemas participate in existing fingerprints. A1 passes original target text to TargetSearchInput/TargetToolAdapter; dictionary/batch target inputs in that adapter do not become model-authored arguments. A counted offline service verifies unavailable lookup status is retained. No live target lookup is claimed.

TDD evidence before final integration verification:

- Initial admission: **52 expected failures**, missing-feature assertion.
- Initial reference/fingerprint: **20 failures**, including actual loop `invalid_context`, unresolved direct binding and missing generation support (one admission import also absent). After minimal core changes: **19 passed**, one still-missing admission module.
- First integrated A1: **72 passed**. Added adversarial cases reproduced mixed explain/execute omissions, unknown second actions, explicit-selection resurrection across resumptions, hostile rejection hooks, option compatibility and sensitive reference projection. Corrections produced **86 passed**.
- Known context scalar types: **6 RED**, then new A1 + existing migration boundary **172 passed**. Earlier focused existing regressions on the pre-4B integration: **668 passed**.
- After merging 4B, a new test incorrectly expected the wrapper to extract `EGFR`; source inspection confirmed non-projecting original text is correct. Corrected the test expectation, not the target contract. Separate UI-limit RED reproduced rejection of valid `rag_count=20`; admission now retains the existing 1–20 range. Do not count this mistaken target assertion as a missing-feature RED.

Final verification used the isolated runner from `2026-09-24-rag-service-extraction.md`, with this worktree and the existing MedChat Conda Python **3.10.20**, `-B`, temporary stores/config/cache roots, cleared inherited application/credential environment, no pytest cache and network-connect denial (only Python's internal Windows asyncio socketpair permitted). No env file, model provider, server, browser or scientific asset was opened. Full Agent/full suite and SPEC/QUALITY are parent-coordinated; current full slot is Planner → 4C.

Final post-`16b9157` focused verification: **1061 passed in 100.98s**, no skips. This is a named 14-file focused suite, not full Agent or full repository:

```powershell
# Arguments to the isolated runner (which calls pytest.main with these files):
python -B -m pytest tests/agent/test_web_decision_admission.py tests/agent/test_web_decision_references.py tests/agent/test_web_decision_fingerprint.py tests/agent/test_decision_inputs.py tests/agent/test_decision_requirements.py tests/agent/test_decision_continuation.py tests/agent/test_decision_continuation_store.py tests/agent/test_decision_protocol_recovery.py tests/agent/test_decision_transport_boundaries.py tests/agent/test_scientific_reference_execution.py tests/agent/test_scientific_reference_resilience.py tests/agent/test_decision_loop.py tests/agent/test_decision_migration_boundaries.py tests/agent/test_target_tool_contract.py -q -p no:cacheprovider --tb=short -rs
git diff --check
```

Additionally compiled all five changed production files and three new tests with Python `compile(source, filename, 'exec')` in memory: **8 passed**, no bytecode written. Root `plans/2026-09-25-web-model-decision-entry.md` is absent; the corrected plan exists only here. Final diff scope is those 8 Python files plus this plan and the spec. No push, PR, CI run, full-suite run or live acceptance. Parent SPEC/QUALITY and publication are pending.

### SPEC Galileo request-changes follow-up

Review base: `aed3571e9e1a25f8c757dc937969ad04a424b98c`. Its 1061 passing focused tests are retained as history, not treated as coverage of the four newly reproduced gaps. Full Agent/full repository were never run by this worker; the parent retains the full queue after G2/ADMET/G1. A2 design in the separate `web-decision-runtime-plan` task is untouched.

All RED probes call actual admission and, if accidentally admitted, run the actual ModelDecisionLoop/registry/Session with recorded real RDKit properties (plus the real likeness tool for positive controls). Only model decisions are scripted. The RED diagnostics report admitted kind/requirements, real completion and task acceptance. GREEN requires admission rejection and zero model/tool calls, not merely a later malformed tool result.

| Group | Observed RED | Minimal correction / GREEN |
|---|---|---|
| Explanation followed by new execution | 11 failed / 8 passed, including LF, CR, CRLF and an unknown verb after semicolon | Every separate statement must be an independently admitted explanation; 19 passed at that checkpoint |
| Unsupported parallel result | 8 failed / 3 known-positive controls passed; melting point and arbitrary other endpoints completed as MW-only | Full text coverage, not any recognized noun; 11 passed |
| Explicit negative | 10 failed / 2 passed; 禁用/禁止/勿/不可/停用 and negative clauses dispatched RDKit | Negative/unknown text is not consumed by the positive admission surface; entire request clarifies; 12 passed |
| Quantified request | 10 failed / 3 passed; 两/2 × 种/款/类/组/份 became exact count 1 | No prose counts/quantifier terminals; only whole subject-derived counts; 13 passed including real two-subject control |
| Unknown chat/explanation fallback | Additional 10 failed / 31 passed, including unknown verbs without delimiters and after commas/connectors | Complete greetings/nominal topics only; uncertain free text clarifies, never inferred chat |

Implementation choices for re-review:

- The known tool/metric matches are only candidate obligations. No scientific request is admitted until all its text is covered by supported positive terminals; no result-name blacklist was expanded.
- Structures are matched case-sensitively as complete values already validated by the existing parser. Target symbols use the existing shared identifier pattern, followed by existing target/activity validation. Coverage does not rewrite the dispatched query, bind fragments or alter scientific results.
- Singular selected-reference ordinals remain permitted and still require the existing owner/ACK/range checks. Numeric/word counts with arbitrary units remain unconsumed and clarify; explicit validated batches still retain all subjects.
- Negative requests reject wholly; a prepared request with an empty forbidden_tools list is never created for those rejected inputs. There is no known-subset execution.
- A1's chat surface is now intentionally narrower: complete greetings or complete nominal explanations of bounded existing topics/metrics, up to eight coordinated topics. Multiple explanation statements each need an explicit explanation prefix. Unknown standalone actions and appended unknown imperatives do not default to chat. This may reject benign free-form requests; expanding it requires parent review and cannot be counted as already-complete P7 normal chat.
- Source/test changes for this review are only `src/web/decision_request.py` and `tests/agent/test_web_decision_admission.py`. The original five-production/three-test A1 ceiling is unchanged. No changes to loop execution, permissions, public protocols, socket/UI/default activation, B, helpers, providers or assets.

Intermediate A1 three-file focus: **171 passed in 11.70s**. Final named 14-file focused rerun and compile/diff-check evidence follow below; no full-suite authorization is inferred. Freeze for the original SPEC reviewer to re-check before QUALITY.

Parent positive-control follow-up: the first closed nominal surface actually rejected `什么是药物分子设计`, `请解释 RAG 是什么`, `什么是药物设计` and `Explain RAG` (**4 RED**, 5 mixed-action controls already passed). With the parent's explicit authorization, added only the nominal project concepts 药物（分子）设计 and RAG. The resulting **9 passed** assert empty allowed/required tools, zero tool observations, one scripted model call for explanations and pre-model rejection for appended retrieval/design/unknown actions. It does not grant RAG retrieval or design execution and does not imply arbitrary explanations are admitted.

The prior 14-file review focus reached **1138 passed in 97.88s**, before this nominal-topic expansion. The final A1 three-file focus after expansion and removing the now-unnecessary action-word fallback reached **180 passed**. These figures are separate checkpoints, not additive or full-suite results. The narrower behavior remains explicit: greetings and supported nominal project explanations work; other legitimate but uncovered free-form requests may clarify. Further coverage needs reviewed nominal/obligation support, not unsafe unknown-imperative fallback.

Final post-correction/nominal-expansion verification: **1147 passed in 92.15s**, no skips, using the same isolated 14-file command listed above (not full Agent/full repository). All five A1 production files and three test files compiled in memory (**8 passed**, no bytecode); `git diff --check` passed. Relative to review base `aed3571`, the revision is limited to admission source, its existing test file, this plan and the spec. Local freeze only, no push or PR. Original SPEC re-review is pending, then independent QUALITY; no approval is claimed. A1 remains support-only, not P7 completion, and full testing stays in the parent's authorized queue.

## Deferred A2 — separate PR, not approved for this branch

Normal `/ws` dispatch into WebDecisionEntry/ModelDecisionLoop; genuinely external normal chat; single reader lease and per-request current-model capture; app config epoch creation/replacement; cancel/drain before lease release; one socket receiver and active request; bounded events; owner-bound resume; existing candidate project/mount/ACK; safe per-socket conversational history and persistent status labels. Keep unsupported requests explicit. Preserve existing static workflow HTTP unchanged, add no HTTP route. No default activation until separately approved.

A2 TDD must assert:
- Actual normal handler calls decide, not static planner/legacy fallback; observation-dependent next action.
- Model change cannot close leased client; no nested lease deadlock; generator remains local gmm-llama:latest.
- Ping/cancel/disconnect during blocked model call work; repeated cancellation and uncertain worker settlement never replay.
- Waiting/partial/failure/rejected/cancelled stay distinct; exactly one connected complete per accepted turn.
- Candidate frames precede complete; ACK follows actual exact-key mount; no execution replay on restoration.
- Foreign/stale/changed generation/requirement resumes do not mutate or dispatch.
- Offline tests are not live model/browser acceptance.

## Deferred B — separate reviewed increments

Existing initial input_ref protocol is not generation/ranking complete. After reviewed typed boundaries, add closed target_ref/candidate-evidence/evidence_refs roles to the existing protocol/resolver, not arbitrary arguments/JSONPath/workflow DSL. Use existing semantic target-evidence/candidate validators and Session journals. Preserve full original/canonical structures and all lineage IDs.

B TDD gates:
- User count/target authoritative; generator remains local gmm-llama:latest, not main external model.
- Target-conditioned generation consumes verified matching target evidence; missing/ambiguous evidence cannot become untargeted success.
- CandidateSet validation/count/deduplication, whole-batch analysis, candidate alignment, ranking fixed-role joins and requested top-N accepted deterministically.
- Cross-set/stale/foreign/partial/untrusted evidence rejected; optional absent evidence distinguished from mandatory missing evidence.
- Requirements and continuation history updated/versioned together; repeated action reuses sealed evidence, not a new sample.
- ADMET/reverse/RAG require individual input and authenticity gates before admission despite typed adapter existence; unknown remains unknown.
- Dynamic docking side-effect/approval policy is not enabled; staged helpers never become model actions; no docking-complete claim.

## Final P7 / package-8 boundary

P7 core scope is existing normal `/ws`: genuine chat + four tools → target generation/candidate analysis/ranking. A1 is only its support foundation. A2/B need independent PRs, review and parent-coordinated CI/acceptance. Current repeated real external-model/local-generation/scientific-tool and normal-browser evidence belongs to package 8; isolated bridge/scripted/fake-generation reports cannot substitute. Missing providers/assets are blocked/partial, never scientific pass. Server deployment remains excluded.
