# Tool-free consultation admission implementation plan

**Goal:** Let the configured model understand varied scientific questions when the user explicitly forbids all tool execution, while preserving the full Web scientific workflows.

**Architecture:** Recognize only a complete global no-tools clause as mode selection; this is not proof of explanatory intent. Before deterministic scientific dispatch or literature follow-ups, ask the existing strict OrdinaryIntent transport to classify the unchanged whole request. Only resolved knowledge/conversation/follow-up intents proceed to the existing Web chat runtime with empty tool authority and a sealed server-owned consultation marker. Mixed/execution/retrieval/uncertain intents ask for clarification without tools. The extra request consumes the existing turn budget and lives inside existing cancellation/allocation supervision. No new profile, provider, API flag, dependency or database schema.

**Trade-off:** Topic-independent semantic classification improves phrasing coverage at the cost of one provider request. Its classification is probabilistic; it never grants execution permission. Global no-tools wording is deliberately explicit. Existing normal scientific requests keep deterministic admission. Do not switch to semantic_v1, which has a different capability scope; do not extend exact scientific-question templates.

**Files:** new `src/agent/contracts/consultation_request.py` for shared mode detection; `src/web/decision_runtime.py` for pre-dispatch classification/budgeting; `src/web/web_request.py` for server-only preparation; `src/agent/scientific_session/web_requirements.py` for sealed zero-step rebuild; `src/agent/scientific_session/decision_policy.py` for no-new-execution guidance; new `tests/agent/test_web_tool_free_consultation.py`; maintenance documentation.

- [x] Reproduce actual frontend random-split/scaffold question: '药物发现' answers while 'ADMET 预测' asks for SMILES (done, screenshot in local acceptance record).
- [x] Add failing actual-app tests for varied Chinese/English questions, all intent rejection categories, unchanged query, zero tool executions, browser flag injection, two-call budget and cancellation.
- [x] Add candidate-evidence carryforward test: accepted records survive consultation, user claims do not become evidence.
- [x] Add bounded global mode detection, preserving quoted markers and endpoint-only prohibitions as non-global.
- [x] Skip science follow-up/compilation only for this explicit mode, classify under the configured provider gate, and reject conflicting browser references or resumed science before dispatch.
- [x] Freeze zero authority and the server marker in normal Web requirements; replay must rebuild the same zero-step plan. Browser callers cannot supply the marker.
- [x] Count the intent request against the model budget; keep the whole turn in the existing allocation deadline and cancellation scope. Intent failure never falls back to executing tools.
- [x] Run focused and affected regressions sequentially, compileall and diff checks; independent review before final frontend acceptance.
- [x] Safe-launch one server, verify the original ADMET phrasing and another domain/English phrasing, mixed task rejection, preserved prior records, and a normal scientific calculation. Capture screenshots and close the owned server.
- [x] Document exact outcomes, limits, UX/DX/Agent impact and remaining scientific-answer-quality issues. No full-goal completion claim from this slice.

Acceptance: 27 focused tests and 159 affected regressions passed after review fixes. Through the actual chat page with its configured deepseek-v4-pro model, the original ADMET question and an English docking question received explanations without molecular inputs. A conflicting prohibition and calculation prompted clarification. A new calculation returned CCO 46.07 and CCN 45.08 Da; the following tool-free turn cited both unchanged and correctly declined to infer oral absorption from mass alone. The server was stopped and its port released. Wording issues remain: the table did not honor the requested g/mol label; the model described new scaffolds as necessarily out-of-distribution and grouped IC50 with direct affinity measurements. These are not covered by the routing fix.
