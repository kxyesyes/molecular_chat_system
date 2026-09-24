# Web model decision entry — approved A1 TDD and phased roadmap

> For agentic workers: use executing-plans/test-driven-development for the A1 tasks below. Parent approved A→B and the safety boundaries, but **only A1 is authorized on this branch**. A2/B need independent PRs. Do not execute deferred tasks.

**Goal:** Add server-owned admission, trusted selected-molecule/direct-binding and nonsecret configuration-generation fingerprint support, with offline tests. This does not activate the normal Web entry and does not complete P7.

**Architecture:** Existing ModelDecisionLoop / WorkflowRunSession / registry / validators / evidence / continuation remain authoritative. A1 adds bounded request preparation and minimal support to those existing boundaries. Later A2 connects the normal `/ws`; B extends evidence references for local target generation, candidate analysis and ranking.

**Tech stack:** Existing Python 3.10, Pydantic, RDKit, SQLite, asyncio, pytest. No new dependencies or execution framework.

**Design:** `docs/superpowers/specs/2026-09-25-web-model-decision-entry-design.md`.

## Parent revision and local workflow

- [ ] Move this existing plan from root `plans/` into `docs/superpowers/plans/` using apply_patch, without a duplicate.
- [ ] Commit only the corrected spec/plan on `codex/web-model-decision-entry`.
- [ ] Merge reviewed main `1bba0256409a06317486530e5c1cfa6598b8e381` into this task branch, not into main. Reinspect integrated analysis contracts.
- [ ] Execute only A1 RED→GREEN below; run focused offline tests. Parent coordinates full Agent/full suite.
- [ ] Freeze local changes for parent SPEC/QUALITY; no push/PR/publication. No live model, browser, server, environment-file or scientific asset access.

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
- [ ] Strict boolean flags/options; bool-as-count, NaN, invalid count, oversize input, conflicting mol_count fail. Keep whole structure validation, no regex fragment rescue.
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
| 计算 SMILES: CCO 的 logP 和分子量 | scientific/property, expected CCO and both metrics |
| 计算 SMILES: CCO; CCN 的性质及类药性 | both tools, both whole subjects |
| 请计算分子性质 | scientific/property obligation, no invented subject |
| 预测 SMILES: CCO 对 BuChE 的活性 | scientific/activity with user target preserved |
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

- [ ] Review exact production diff against five-file allowlist, new tests, spec and plan; no receive/UI/default/HTTP/B changes.
- [ ] Compile relevant source (or compileall with isolated bytecode target); run git diff --check.
- [ ] Record RED reasons and focused GREEN results, interpreter/commands, remaining full-Agent queue and current branch/head. Do not claim CI/full suite/live science has passed.
- [ ] Local freeze with no push. Parent schedules SPEC/QUALITY and full Agent; findings may require a later local correction. A1 is support-only and not P7 completion.

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
