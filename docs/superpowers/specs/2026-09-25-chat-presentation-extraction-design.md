# ChatHandler presentation and prompt extraction

## Approval and goal

Work package 2 of the user-approved remaining work through step 8. The user selected two pure modules over Presenter/Builder classes, and delegated further recommended choices. Base: merged main 6af7292. This is behavior-preserving extraction, not a new answering strategy.

## Module boundaries

1. `src/web/agent_result_presentation.py`: owns bounded failure text sanitization, warning projection, Web-only outcome selection, partial projection and authoritative failure content. Move existing algorithms rather than copy them. Constants for these algorithms have one authority; old ChatHandler imports/methods remain compatible where referenced.
2. `src/web/chat_prompt_builder.py`: owns budgeted RAG-record formatting, ordinary prompt assembly and optional Agent-evidence prompt assembly. Reuse `prompt_budget.py` and `rag_presentation.py`; do not create a second budget/formatter implementation. Receive config, effective input limit and history explicitly; no global model or application access.
3. `ChatHandler`: retains original method signatures as small delegates. It chooses explicit history versus connection history, obtains the current model/config limit, sends ordered WebSocket frames, calls models/tools, updates history, resolves references and owns request/cancellation cleanup. `_model_max_tokens`, `_finalize_model_response`, candidate/event handling and intent routing are not extracted in this package.

Class-method helper override behavior is preserved by passing the current class sanitization hooks to shared functions where required, rather than calling ChatHandler from a new module. New modules must not import ChatHandler or app.py. Compatibility wrappers are not a second implementation.

Read-only review found two additional compatibility hooks: `src.web.chat_handler.format_rag_context` is patched by an existing budget test, so the wrapper passes that current callable; effective input limit must be evaluated at its original point, not eagerly before section-budget/metadata checks. Accept a lazy limit callback at the pure assembly boundary to preserve exception ordering. Existing malformed-step warning is emitted by a caller-owned diagnostic callback at the same point; the pure module has no logger, network, database or model dependency. Direct callers may omit the diagnostic observer. No input mapping/history is mutated.

## Invariants

- Exact status precedence for completed/partial/failed/rejected/cancelled, invalid statuses and legacy success/partial booleans stays unchanged.
- Partial scientific body remains byte-for-byte intact unless its existing sensitive-material guard rejects it. Do not truncate, round or regenerate scientific claims.
- Preserve ordered failed/skipped steps, typed AgentResult metadata fallback, bounded summaries, malformed observation isolation, warnings, error fields and trace/active-skill redaction.
- Failure text selection and generic fallback remain identical. No weakening of pre-truncation secret detection.
- Existing frame types/order/counts, canonical metadata, history append identity, no-LLM partial handling and cancellation/resource behavior remain unchanged.
- RAG records and history turns are included whole or omitted under the same character budget. Preserve complete current input, status reserve, chronology, omission notices and InputBudgetExceeded behavior. This remains a character budget, not token estimation.
- Inputs, nested tool results, config and history must not be mutated by pure presentation/assembly.
- No new endpoint, schema, dependency, model load, runtime asset or production configuration.

## Tests and completion

Baseline six-file regression is 271 passed / 4 existing warnings. Add real-module tests before the extraction: fixed expected outputs captured from baseline cases, mutation guards, public/private compatibility delegation and subclass helper hooks where applicable. Do not test an independently pasted copy of the production algorithm.

RED must show the missing new boundary/delegation rather than weakening an existing assertion. GREEN must include direct functions and actual ChatHandler paths for scientific precision, sensitive text, malformed metadata, input overflow and explicit history handling. Existing tests remain, followed by full Agent/entrypoint regression, compile and relevant Node checks.

Independent SPEC then QUALITY review is required. Commit/publish one scoped draft PR; merge under the user's default authorization only after CI passes and findings are resolved. Record actual tests and failures. This package does not claim packages 3–8 complete and does not deploy.
