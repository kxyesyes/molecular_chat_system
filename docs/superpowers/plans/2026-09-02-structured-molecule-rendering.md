# Structured Molecule Rendering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent invalid user SMILES and explanatory model text from creating unrelated molecule cards, while preserving traceable cards for RDKit-validated `CandidateSet@1` tool output.

**Architecture:** The Router distinguishes potential structure tokens from RDKit-valid SMILES and requests correction before scientific execution. ChatHandler treats failed scientific Agent runs as terminal and emits `molecule_candidates` only from strictly restored successful/partial `CandidateSet@1` observations. The homepage validates that event through a pure JavaScript helper and never scans assistant prose for structures.

**Tech Stack:** Python 3.10+, FastAPI WebSockets, RDKit, Agent dataclass contracts, native JavaScript, Node.js VM/static tests, pytest.

---

## File map

- `src/agent/utils/validators.py`: separate potential token extraction from RDKit-valid extraction.
- `src/agent/routing/hybrid.py`: route with validated SMILES state and identify invalid supplied structures.
- `src/web/chat_handler.py`: terminate failed scientific runs and serialize trusted candidate observations.
- `src/web/static/js/home/molecule_candidates.js`: normalize structured candidate events without DOM access.
- `src/web/static/js/home/main.js`: queue and render normalized candidates; remove prose scanning.
- `src/web/templates/index.html`: load the new helper before homepage main code.
- `tests/agent/test_routing_prompt_matrix.py`: invalid/valid route-gate regression tests.
- `tests/agent/test_chat_handler_agent_events.py`: failure and candidate-event integration tests.
- `tests/home_structured_molecule_render_test.js`: browser contract and static sink checks.
- `tests/frontend_safe_render_test.js`: safe rendering assertions.

### Task 1: Reject invalid user SMILES before scientific execution

**Files:**
- Modify: `tests/agent/test_routing_prompt_matrix.py`
- Modify: `src/agent/utils/validators.py`
- Modify: `src/agent/routing/hybrid.py`
- Modify: `src/web/chat_handler.py`

- [ ] **Step 1: Write failing Router tests**

Append:

```python
@pytest.mark.parametrize(
    "prompt",
    [
        "请分析这个 SMILES 的成药性：CC(C)((。",
        "请全面分析这个分子的成药性：CC(C)((",
    ],
)
def test_invalid_smiles_requires_correction_before_execution(prompt):
    decision = HybridSkillRouter().decide(prompt)

    assert decision.selected_skill == "comprehensive_evaluation"
    assert decision.requires_confirmation is True
    assert any("invalid SMILES" in reason for reason in decision.reasons)


def test_valid_smiles_does_not_request_smiles_correction():
    decision = HybridSkillRouter().decide(
        "请全面分析阿司匹林的成药性。SMILES: CC(=O)Oc1ccccc1C(=O)O。"
    )

    assert decision.selected_skill == "comprehensive_evaluation"
    assert not any("SMILES" in reason for reason in decision.reasons)
```

- [ ] **Step 2: Verify red status**

Run:

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\agent\test_routing_prompt_matrix.py -q -p no:cacheprovider
```

Expected: invalid cases fail because the current lexical regex treats the literal label `SMILES` as molecular input.

- [ ] **Step 3: Expose potential SMILES extraction**

Refactor `InputValidator`:

```python
def extract_potential_smiles_candidates(self, text: str) -> list[str]:
    candidates = list(self.smiles_pattern.findall(text))
    for token in text.split():
        clean_token = re.sub(r"[,\.!?;，。！？；：:]+$", "", token)
        if self._looks_like_smiles(clean_token):
            candidates.append(clean_token)

    potential = []
    seen = set()
    for candidate in candidates:
        candidate = candidate.strip()
        if candidate in seen or not self._looks_like_smiles(candidate):
            continue
        seen.add(candidate)
        potential.append(candidate)
    return potential

def extract_smiles_candidates(self, text: str) -> list[str]:
    return [
        candidate
        for candidate in self.extract_potential_smiles_candidates(text)
        if validate_smiles_with_rdkit(candidate)
    ]

@staticmethod
def _looks_like_smiles(text: str) -> bool:
    if text.upper() == "SMILES":
        return False
    if not text or len(text) < 2 or any(char.isspace() for char in text):
        return False
    if not re.fullmatch(r"[A-Za-z0-9@+\-\[\]\(\)=#./\\:%]+", text):
        return False
    return any(element in text for element in (
        "C", "N", "O", "S", "P", "F", "Br", "Cl", "I"
    ))
```

- [ ] **Step 4: Route using RDKit-valid candidates**

Import `InputValidator` and replace the lexical `has_smiles` assignment:

```python
from src.agent.utils.validators import InputValidator

input_validator = InputValidator()
potential_smiles = input_validator.extract_potential_smiles_candidates(text)
valid_smiles = input_validator.extract_smiles_candidates(text)
has_smiles = bool(valid_smiles)
has_invalid_smiles = bool(potential_smiles) and not has_smiles
```

Pass the invalid flag into confirmation logic:

```python
confirmation_reasons = self._confirmation_reasons(
    selected,
    lower,
    has_smiles,
    has_invalid_smiles,
)
```

Update the helper signature and molecular-input branch:

```python
@staticmethod
def _confirmation_reasons(
    skill: str,
    lower: str,
    has_smiles: bool,
    has_invalid_smiles: bool = False,
) -> list[str]:
    reasons = []
    molecular_skills = {
        "admet_assessment",
        "activity_prediction",
        "reverse_target_prediction",
        "comprehensive_evaluation",
        "hit_to_lead_optimization",
    }
    if skill in molecular_skills and not has_smiles:
        reasons.append(
            "The provided SMILES is invalid and must be corrected before execution"
            if has_invalid_smiles
            else "A valid SMILES input is required before execution"
        )
```

Replace only the current molecular-input `if` block; leave the following
`docking_simulation`, `molecular_design`, and `admet_assessment` branches in
their current order so their existing confirmation reasons are still appended.

- [ ] **Step 5: Return a precise invalid-input message**

Update `ChatHandler._route_clarification()`:

```python
@staticmethod
def _route_clarification(route_decision) -> str:
    if not route_decision or not route_decision.requires_confirmation:
        return ""
    reasons = " ".join(route_decision.reasons)
    if "invalid SMILES" in reasons:
        return (
            "提供的 SMILES 无效，无法进行成药性或性质计算。请检查括号、"
            "环编号和原子价后重新提交；本次未计算 LogP、QED、pIC50 或结合能。"
        )
    if "SMILES" in reasons:
        return (
            "要进行真实、可追溯的分子性质计算，请提供该分子的有效 "
            "SMILES。收到结构后，系统会调用 RDKit 计算，而不会由语言模型"
            "猜测数值。"
        )
    return ""
```

- [ ] **Step 6: Run focused tests**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\agent\test_routing_prompt_matrix.py tests\agent\test_prompt_acceptance.py -q -p no:cacheprovider
```

Expected: all selected tests pass.

- [ ] **Step 7: Commit the gate**

```powershell
git add -- src/agent/utils/validators.py src/agent/routing/hybrid.py src/web/chat_handler.py tests/agent/test_routing_prompt_matrix.py
git commit -m "fix: reject invalid smiles before agent execution"
```

### Task 2: Treat failed scientific Agent results as terminal

**Files:**
- Modify: `tests/agent/test_chat_handler_agent_events.py`
- Modify: `src/web/chat_handler.py`

- [ ] **Step 1: Write a failing short-circuit test**

Add:

```python
class FailedScientificAgentSystem(FakeAgentSystem):
    def execute(self, message, temperature=0.7, mol_count=5, active_skill=None):
        return {
            "success": False,
            "status": "failed",
            "final_answer": "提供的 SMILES 无效，性质计算未执行。",
            "tools_used": ["property_calculator"],
            "active_skill": "comprehensive_evaluation",
            "trace_id": "trace-invalid-1",
            "warnings": ["invalid_smiles"],
            "error": {
                "code": "invalid_input",
                "message": "Invalid SMILES",
                "details": {"reason": "invalid_smiles"},
            },
            "tool_results": {},
            "tool_result_sequence": [],
            "agent_events": [],
            "workflow_plan": {"workflow_name": "comprehensive_evaluation"},
        }


def test_failed_scientific_agent_is_terminal_and_never_calls_main_model():
    model = FakeModel()
    handler = ChatHandler(
        model=model,
        rag_service=FakeRagService(),
        agent_system=FailedScientificAgentSystem(),
        config={"inference": {"stream": False}},
    )
    websocket = FakeWebSocket()

    asyncio.run(handler._process_message(
        websocket=websocket,
        message="请分析 CC(C)(( 的成药性",
        enable_rag=False,
        enable_tools=True,
    ))

    assert model.generate_calls == 0
    assert websocket.messages[-1] == {
        "type": "complete",
        "content": "提供的 SMILES 无效，性质计算未执行。",
        "trace_id": "trace-invalid-1",
        "status": "failed",
    }
    assert not any(item["type"] == "molecule_candidates" for item in websocket.messages)
```

- [ ] **Step 2: Verify the existing main-model fallthrough**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\agent\test_chat_handler_agent_events.py::test_failed_scientific_agent_is_terminal_and_never_calls_main_model -q -p no:cacheprovider
```

Expected: fail with one main-model generation call.

- [ ] **Step 3: Implement terminal failure messages**

Add:

```python
@staticmethod
def _agent_failure_content(agent_result: Dict[str, Any]) -> str:
    final_answer = agent_result.get("final_answer")
    if isinstance(final_answer, str) and final_answer.strip():
        return final_answer.strip()
    error = agent_result.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
    return "科学工作流执行失败，未生成可信计算结果。"

async def _send_terminal_agent_failure(
    self,
    websocket: WebSocket,
    agent_result: Dict[str, Any],
) -> str:
    content = self._agent_failure_content(agent_result)
    status = str(agent_result.get("status") or "failed")
    trace_id = agent_result.get("trace_id")
    await websocket.send_text(json.dumps({
        "type": "agent_result",
        "message": content,
        "active_skill": agent_result.get("active_skill"),
        "trace_id": trace_id,
        "status": status,
        "warnings": agent_result.get("warnings") or [],
    }, ensure_ascii=False))
    await websocket.send_text(json.dumps({
        "type": "complete",
        "content": content,
        "trace_id": trace_id,
        "status": status,
    }, ensure_ascii=False))
    return content
```

Replace the current failure status-only branch in `_process_message()`:

```python
else:
    full_response = await self._send_terminal_agent_failure(websocket, agent_result)
    history.append({
        "user": message,
        "agent_used": True,
        "agent_response": full_response,
        "rag_enabled": enable_rag,
        "molecules_retrieved": 0,
        "assistant": full_response,
    })
    if len(history) > 20:
        del history[:-20]
    return
```

- [ ] **Step 4: Run ChatHandler tests**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\agent\test_chat_handler_agent_events.py -q -p no:cacheprovider
```

Expected: all tests pass, including existing non-workflow model interpretation.

- [ ] **Step 5: Commit failure handling**

```powershell
git add -- src/web/chat_handler.py tests/agent/test_chat_handler_agent_events.py
git commit -m "fix: stop model fallback after agent failure"
```

### Task 3: Emit strict CandidateSet WebSocket events

**Files:**
- Modify: `tests/agent/test_chat_handler_agent_events.py`
- Modify: `src/web/chat_handler.py`

- [ ] **Step 1: Write candidate-event tests**

Import the production contracts and add:

```python
from src.agent.contracts import CandidateRecord, CandidateSet, ObservationStatus


def candidate_set_payload():
    candidate = CandidateRecord.from_smiles(
        candidate_index=1,
        source_index=1,
        original_smiles="C(C)O",
        canonical_smiles="CCO",
        generation_provenance={"tool_name": "llm_molecular_generator"},
        metadata={},
    )
    return CandidateSet(
        requested_count=1,
        candidates=(candidate,),
        status=ObservationStatus.SUCCEEDED,
    ).to_dict()


class CandidateAgentSystem(FakeAgentSystem):
    def execute(self, message, temperature=0.7, mol_count=5, active_skill=None):
        return {
            "success": True,
            "status": "completed",
            "final_answer": "已生成 1 个经 RDKit 验证的候选。",
            "tools_used": ["llm_molecular_generator"],
            "active_skill": "molecular_design",
            "trace_id": "trace-candidates-1",
            "tool_results": {},
            "tool_result_sequence": [{
                "step_id": "molecular_design",
                "tool_name": "llm_molecular_generator",
                "success": True,
                "status": "succeeded",
                "message": "ok",
                "data": candidate_set_payload(),
                "quality": {"output_contract": "CandidateSet@1"},
                "provenance": {
                    "tool_name": "llm_molecular_generator",
                    "model_name": "gmm-llama:latest",
                },
                "warnings": [],
            }],
            "agent_events": [],
            "workflow_plan": {"workflow_name": "molecular_design"},
        }


def test_successful_candidate_set_emits_traceable_molecule_event():
    handler = ChatHandler(
        model=FakeModel(),
        rag_service=FakeRagService(),
        agent_system=CandidateAgentSystem(),
        config={"inference": {"stream": False}},
    )
    websocket = FakeWebSocket()
    asyncio.run(handler._process_message(
        websocket=websocket,
        message="随机生成一个分子",
        enable_rag=False,
        enable_tools=True,
    ))

    events = [item for item in websocket.messages if item["type"] == "molecule_candidates"]
    assert len(events) == 1
    assert events[0]["candidate_set"]["candidates"][0]["canonical_smiles"] == "CCO"
    assert events[0]["source"]["model_name"] == "gmm-llama:latest"
    assert events[0]["trace_id"] == "trace-candidates-1"


def test_forged_candidate_contract_emits_no_molecule_event():
    result = CandidateAgentSystem().execute("generate")
    result["tool_result_sequence"][0]["quality"]["output_contract"] = "CandidateSet@2"
    assert ChatHandler._molecule_candidate_messages(result) == []
```

- [ ] **Step 2: Verify red status**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\agent\test_chat_handler_agent_events.py -q -p no:cacheprovider
```

Expected: candidate tests fail because no strict serializer exists.

- [ ] **Step 3: Implement strict candidate serialization**

Import `CandidateSet` and `ObservationStatus`, then add:

```python
@staticmethod
def _molecule_candidate_messages(agent_result: Dict[str, Any]) -> list[dict[str, Any]]:
    messages = []
    for item in agent_result.get("tool_result_sequence") or []:
        if not isinstance(item, dict) or item.get("success") is not True:
            continue
        if item.get("status") not in {"succeeded", "partial"}:
            continue
        if (item.get("quality") or {}).get("output_contract") != "CandidateSet@1":
            continue
        try:
            candidate_set = CandidateSet.from_dict(item.get("data"))
        except (TypeError, ValueError):
            logger.warning(
                "Rejected malformed CandidateSet@1 from tool %s",
                item.get("tool_name"),
            )
            continue
        if candidate_set.status not in {
            ObservationStatus.SUCCEEDED,
            ObservationStatus.PARTIAL,
        } or not candidate_set.candidates:
            continue
        provenance = item.get("provenance") or {}
        messages.append({
            "type": "molecule_candidates",
            "trace_id": agent_result.get("trace_id"),
            "source": {
                "tool_name": item.get("tool_name"),
                "model_name": provenance.get("model_name"),
                "status": item.get("status"),
            },
            "candidate_set": candidate_set.to_dict(),
            "warnings": list(item.get("warnings") or []),
        })
    return messages
```

Send these messages before the existing successful `agent_result` message:

```python
for candidate_message in self._molecule_candidate_messages(agent_result):
    await websocket.send_text(json.dumps(candidate_message, ensure_ascii=False))
```

- [ ] **Step 4: Run candidate contract regressions**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\agent\test_chat_handler_agent_events.py tests\agent\test_candidate_contracts.py tests\agent\test_generated_candidate_validation.py -q -p no:cacheprovider
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit the WebSocket contract**

```powershell
git add -- src/web/chat_handler.py tests/agent/test_chat_handler_agent_events.py
git commit -m "feat: emit validated molecule candidate events"
```

### Task 4: Replace prose scanning with structured frontend rendering

**Files:**
- Create: `src/web/static/js/home/molecule_candidates.js`
- Modify: `src/web/static/js/home/main.js`
- Modify: `src/web/templates/index.html`
- Create: `tests/home_structured_molecule_render_test.js`
- Modify: `tests/frontend_safe_render_test.js`

- [ ] **Step 1: Write the failing Node contract test**

Create `tests/home_structured_molecule_render_test.js`:

```javascript
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const root = path.join(__dirname, "..");
const helperPath = path.join(root, "src", "web", "static", "js", "home", "molecule_candidates.js");
const mainPath = path.join(root, "src", "web", "static", "js", "home", "main.js");
const templatePath = path.join(root, "src", "web", "templates", "index.html");

function assert(condition, message) {
  if (!condition) {
    console.error(message);
    process.exit(1);
  }
}

assert(fs.existsSync(helperPath), "Missing molecule_candidates.js");
const sandbox = { window: {}, console };
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(helperPath, "utf8"), sandbox, { filename: helperPath });
const Contract = sandbox.window.HomeMoleculeCandidates;

const candidate = {
  candidate_id: "cand-001-abcd1234",
  source_index: 1,
  original_smiles: "C(C)O",
  canonical_smiles: "CCO",
  validation: { valid: true, method: "RDKit" },
  generation_provenance: { tool_name: "llm_molecular_generator" },
  metadata: {},
  smiles: "CCO",
};
const payload = {
  type: "molecule_candidates",
  trace_id: "trace-1",
  source: { tool_name: "llm_molecular_generator", model_name: "gmm-llama:latest", status: "succeeded" },
  candidate_set: {
    version: "1",
    requested_count: 1,
    valid_count: 1,
    unique_count: 1,
    invalid_count: 0,
    duplicate_count: 0,
    candidates: [candidate],
    rejected: [],
    status: "succeeded",
  },
  warnings: [],
};

assert(Contract.normalize(payload).candidates[0].canonical_smiles === "CCO", "Valid payload must normalize");
assert(Contract.normalize({ ...payload, candidate_set: { ...payload.candidate_set, status: "failed" } }) === null, "Failed set must be rejected");
assert(Contract.normalize({ ...payload, candidate_set: { ...payload.candidate_set, candidates: [{ ...candidate, canonical_smiles: "MarvinSketch", smiles: "MarvinSketch", validation: { valid: false, method: "text" } }] } }) === null, "Prose token must be rejected");

const mainSource = fs.readFileSync(mainPath, "utf8");
assert(mainSource.includes('case "molecule_candidates"'), "Homepage must consume candidate events");
assert(!mainSource.includes("detectAndRenderMolecules("), "Homepage must not scan assistant prose");
assert(!mainSource.includes("fetchMoleculePropertiesForToolMolecule("), "Frontend must not calculate prose-derived properties");
const templateSource = fs.readFileSync(templatePath, "utf8");
assert(templateSource.indexOf("/static/js/home/molecule_candidates.js") < templateSource.indexOf("/static/js/home/main.js"), "Helper must load before main.js");
console.log("Structured molecule rendering checks passed");
```

- [ ] **Step 2: Verify red status**

```powershell
node tests/home_structured_molecule_render_test.js
```

Expected: fail because the helper is absent and prose scanning still exists.

- [ ] **Step 3: Create the pure payload normalizer**

Create `src/web/static/js/home/molecule_candidates.js`:

```javascript
(function (root) {
  "use strict";
  const acceptedStatuses = new Set(["succeeded", "partial"]);

  function normalize(payload) {
    if (!payload || payload.type !== "molecule_candidates") return null;
    const set = payload.candidate_set;
    const source = payload.source;
    if (!set || set.version !== "1" || !acceptedStatuses.has(set.status)) return null;
    if (!source || !acceptedStatuses.has(source.status)) return null;
    if (!Array.isArray(set.candidates) || set.candidates.length === 0) return null;
    if (set.valid_count !== set.candidates.length || set.unique_count !== set.candidates.length) return null;

    const seen = new Set();
    for (const candidate of set.candidates) {
      if (!candidate || typeof candidate.candidate_id !== "string") return null;
      if (typeof candidate.canonical_smiles !== "string" || !candidate.canonical_smiles) return null;
      if (candidate.smiles !== candidate.canonical_smiles) return null;
      if (!candidate.validation || candidate.validation.valid !== true || candidate.validation.method !== "RDKit") return null;
      if (seen.has(candidate.canonical_smiles)) return null;
      seen.add(candidate.canonical_smiles);
    }
    return {
      traceId: typeof payload.trace_id === "string" ? payload.trace_id : "",
      source: {
        toolName: typeof source.tool_name === "string" ? source.tool_name : "",
        modelName: typeof source.model_name === "string" ? source.model_name : "",
        status: source.status,
      },
      candidates: set.candidates,
      invalidCount: Number.isInteger(set.invalid_count) ? set.invalid_count : 0,
      duplicateCount: Number.isInteger(set.duplicate_count) ? set.duplicate_count : 0,
      warnings: Array.isArray(payload.warnings) ? payload.warnings.filter((item) => typeof item === "string") : [],
    };
  }

  root.HomeMoleculeCandidates = Object.freeze({ normalize });
})(typeof window !== "undefined" ? window : globalThis);
```

- [ ] **Step 4: Consume events and remove text detection**

Add state and a WebSocket switch branch in `main.js`:

```javascript
let pendingMoleculeCandidatePayloads = [];

case "molecule_candidates": {
  const normalized = window.HomeMoleculeCandidates?.normalize(message);
  if (normalized) {
    pendingMoleculeCandidatePayloads.push(normalized);
  } else {
    console.warn("Rejected malformed molecule_candidates payload");
  }
  break;
}
```

Reset the queue at the start of `sendMessage()`. At the end of `completeLastMessage()`, replace `detectAndRenderMolecules(lastMessage, finalContent)` with:

```javascript
for (const payload of pendingMoleculeCandidatePayloads) {
  renderMoleculeCandidates(lastMessage, payload);
}
pendingMoleculeCandidatePayloads = [];
```

Rename the existing renderer to `renderMoleculeCandidates(messageElement, payload)`, delete all regex extraction, and initialize only from normalized data:

```javascript
function renderMoleculeCandidates(messageElement, payload) {
  const moleculesArray = payload.candidates;
  if (!messageElement || moleculesArray.length === 0) return;
  const moleculeCount = moleculesArray.length;
  const sourceLabel = [payload.source.toolName, payload.source.modelName]
    .filter(Boolean)
    .join(" · ") || "已验证工具结果";
}
```

Keep the existing `moleculeContainer`, title, carousel, image, copy-button, and
pagination DOM construction. Replace the current callback header and its first
two declarations so the loop consumes candidate records:

```javascript
pageMolecules.forEach((candidate, pageIndex) => {
  const smiles = candidate.canonical_smiles;
  const globalIndex = startIdx + pageIndex;
```

Inside each card, use safe DOM nodes:

```javascript
const smiles = candidate.canonical_smiles;
const indexLabel = document.createElement("div");
indexLabel.textContent = `#${globalIndex + 1}`;
const provenanceLabel = document.createElement("div");
provenanceLabel.textContent = sourceLabel;
cardHeader.append(indexLabel, provenanceLabel);

const summary = document.createElement("summary");
summary.textContent = "显示 SMILES";
const smilesText = document.createElement("div");
smilesText.textContent = smiles;
smilesDetails.append(summary, smilesText);
```

Delete `fetchMoleculePropertiesForToolMolecule()` and its call. Render properties only when supplied by candidate metadata:

```javascript
function renderCandidateProperties(container, candidate) {
  container.replaceChildren();
  const properties = candidate.metadata?.properties;
  if (!properties || typeof properties !== "object") {
    const unavailable = document.createElement("div");
    unavailable.textContent = "本步骤未提供经工具验证的属性";
    container.appendChild(unavailable);
    return;
  }
  const labels = { molecular_weight: "分子量", mw: "分子量", logp: "LogP", qed: "QED", tpsa: "TPSA", hbd: "HBD", hba: "HBA" };
  Object.entries(labels).forEach(([key, label]) => {
    if (!(key in properties)) return;
    const row = document.createElement("div");
    const name = document.createElement("span");
    const value = document.createElement("span");
    name.textContent = label;
    value.textContent = String(properties[key]);
    row.append(name, value);
    container.appendChild(row);
  });
}
```

- [ ] **Step 5: Load the helper and bump static versions**

In `index.html`, load:

```html
<script src="/static/js/home/molecule_candidates.js?v=20260902-structured-candidates"></script>
<script src="/static/js/home/main.js?v=20260902-structured-candidates"></script>
```

- [ ] **Step 6: Extend safe-render assertions**

Append to `tests/frontend_safe_render_test.js`:

```javascript
assert(!homeMain.includes("detectAndRenderMolecules("), "Assistant prose must not create molecule cards");
assert(!homeMain.includes("fetchMoleculePropertiesForToolMolecule("), "Frontend must not calculate inferred candidates");
const structuredRenderer = extractFunction(homeMain, "renderMoleculeCandidates");
assert(structuredRenderer.includes(".textContent = sourceLabel"), "Provenance must use textContent");
assert(structuredRenderer.includes(".textContent = smiles"), "SMILES must use textContent");
assert(!structuredRenderer.includes("工具生成"), "Cards must not hardcode provenance");
```

- [ ] **Step 7: Run frontend checks**

```powershell
node --check src/web/static/js/home/molecule_candidates.js
node --check src/web/static/js/home/main.js
node tests/home_structured_molecule_render_test.js
node tests/frontend_safe_render_test.js
node tests/home_agent_task_panel_test.js
```

Expected: all commands exit 0.

- [ ] **Step 8: Commit frontend rendering**

```powershell
git add -- src/web/static/js/home/molecule_candidates.js src/web/static/js/home/main.js src/web/templates/index.html tests/home_structured_molecule_render_test.js tests/frontend_safe_render_test.js
git commit -m "fix: render only structured molecule candidates"
```

### Task 5: End-to-end regression and runtime verification

**Files:**
- No new file is expected; corrections remain limited to files listed above.

- [ ] **Step 1: Run focused Python regressions**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\agent\test_chat_handler_agent_events.py tests\agent\test_routing_prompt_matrix.py tests\agent\test_prompt_acceptance.py tests\agent\test_candidate_contracts.py tests\agent\test_generated_candidate_validation.py -q -p no:cacheprovider
```

Expected: all selected tests pass.

- [ ] **Step 2: Run the full Agent suite**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\agent -q -p no:cacheprovider
```

Expected: all tests pass; environment-dependent skips are reported honestly and no new failure is accepted.

- [ ] **Step 3: Run JavaScript regressions**

```powershell
node tests/home_structured_molecule_render_test.js
node tests/frontend_safe_render_test.js
node tests/home_agent_task_panel_test.js
```

Expected: all scripts exit 0.

- [ ] **Step 4: Run compilation and whitespace checks**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m compileall -q src scripts
git diff --check
```

Expected: both commands exit 0 with no whitespace errors.

- [ ] **Step 5: Restart and verify the invalid request**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe main.py --no-reload
```

Submit:

```text
请分析这个 SMILES 的成药性：CC(C)((。如果 SMILES 无效，请不要返回 QED、LogP 或任何模拟性质。
```

Expected:

- one complete response explicitly says the SMILES is invalid;
- no molecule visualization container, LogP, QED, pIC50, or binding energy appears;
- Network shows no `/api/molecule/properties` or `/api/utils/smiles_to_image` request for this turn;
- the external main model is not called for this rejected scientific request.

- [ ] **Step 6: Verify a real generation request**

Submit `随机生成一个分子`.

Expected:

- only `CandidateSet@1` candidates render;
- each card uses RDKit-validated `canonical_smiles`;
- invalid/duplicate outputs appear only as counts or warnings;
- provenance identifies `llm_molecular_generator` and `gmm-llama:latest` when available;
- no hardcoded “工具生成” label appears.

- [ ] **Step 7: Inspect final branch state**

```powershell
git status --short
git log --oneline -5
```

Expected: only the pre-existing untracked `data/molecular_faiss_index.index.manifest.json` remains outside commits.
