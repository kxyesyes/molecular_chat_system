# Package6 G2: generator optimization input fidelity

Date: 2026-09-25. Status: parent approved for bounded TDD; full Agent testing remains queued pending parent notification.
Baseline: `3a68264`, branch `codex/generator-optimization-input`.

## Scope and evidence

This is DESIGN/PLAN ONLY. This worktree was clean before creating these two documents. No executable source or tests were changed or run; no environment, secrets, assets, models, network, staging, commit, push, or PR operations were performed. Findings below are source-traced, not a claimed runtime reproduction.

Read `AGENTS.md`, `docs/PROJECT_STANDARDS.md`, the actual generator, shared parser, Base extractor, reverse-target integration, parser regressions, and generator tests. The actual method is `_analyze_generation_intent`, not `_analyze_intent`.

Relevant baseline locations:

| File | Evidence |
| --- | --- |
| `src/agent/tools/llm_molecular_generator.py:121` | `execute` normalizes request/evidence/count, checks RDKit and LLM, analyzes intent, then dispatches retries. |
| Same file, lines 424–468 | Optimization keywords select `optimization`; `extract_smiles(query)[0]` becomes the seed. |
| Same file, lines 277–334 and 501–540 | Retry dispatch uses optimization only with a truthy seed; otherwise uses description generation. Optimization prompt embeds the seed and may prepend it to generated candidates. |
| `src/agent/tools/base_tool.py:34` | Regex extraction, punctuation stripping, case-insensitive predefined `CCO`, candidate filtering and deduplication are not whole-input validation. |
| `src/agent/tools/molecular_input.py:44` | Full candidates, authoritative labels, batch validation, exact returned spelling, strict RDKit parser parameters. |
| `tests/agent/test_explicit_molecular_input.py` | Real parser coverage of malformed suffixes, mixed batches, salts, isotopes, names, quotes, and CX rejection in explicit fields. |
| `tests/agent/test_reverse_target_complete_input.py` | Actual-execution spy pattern, first-of-valid-batch behavior, unavailable separation. |
| `tests/test_llm_molecular_generator.py:198` | Existing optimization evidence test mocks `extract_smiles`; it cannot expose this defect. |

The historical audit is in the separate `historical-residual-disposition` worktree, as clarified by the parent. It was not read because current source provides sufficient evidence; this is not a claim that the document is missing. No original dirty files were copied. The reverse-target design at this baseline independently records the same extractor defect in another consumer.

## Exact failure

For `optimize SMILES: CCO)((`, the complex-candidate validation cannot establish the submitted structure, but the predefined case-insensitive `CCO` word-boundary search extracts `CCO`. It passes Base validation and becomes `base_smiles`. `execute` can therefore reach `_optimize_with_llm` with `Original SMILES: CCO`, not the supplied malformed structure.

For `optimize SMILES: CCO; CC(C)((`, invalid candidates are filtered away rather than rejecting the input. A remaining `CCO` seed permits generation. Output RDKit validation cannot repair this provenance error: it validates outputs, not whether the correct input molecule reached the model. Salts/bracketed isotopes can similarly lose structural information or fail seed extraction, and no extracted seed currently falls back to description generation.

This defect is at the actual execution boundary, independent of `should_use`: direct/structured calls can invoke `execute` without selection. Changing selection alone is insufficient.

## Options and recommendation

1. **Recommended: existing parser at optimization intent analysis, narrow error classification in execute.** Preserve all generation/count/evidence and output mechanics. Two production files, one new focused test file, one existing test seam adjustment.
2. Change only `should_use`: does not protect direct execution, adds validation during selection, and could unexpectedly change routing.
3. Rewrite Base extraction or introduce another parser/staged helper: affects unrelated tools and duplicates grammar. Explicitly excluded.

Recommended choices are delegated. The parent subsequently approved this design, including the missing-candidate subtype, import-unavailable classification, generator-only pipe rejection, and preservation of seedless description fallback.

## Chosen execution contract

Keep normalization, evidence serialization, temperature validation, count precedence, RDKit/LLM preflights and their ordering unchanged. Within the existing optimization-keyword branch of `_analyze_generation_intent`, call `parse_molecular_smiles(query, self)` on the complete original query. Do not parse a regex extraction, rewrite case, normalize atom spelling, canonicalize, strip a dot, or remove malformed suffixes first.

In the generator constructor only, extend its inherited `exclude_words` with `optimize`, `improve`, and `modify`. This uses the existing parser's tool-local prose vocabulary: without it, the standalone prefix before `SMILES:` in `optimize SMILES: CCO` becomes an invalid candidate. Explicit field values stay authoritative, so `SMILES: optimize` must still fail. Do not modify Base's vocabulary or shared tokenization. Base extraction does not consult this instance set, so its current selection behavior is unaffected.

Validate every parsed candidate before selecting `values[0]`. Preserve the historical single-seed interface: one request optimizes the first submitted valid structure, not one optimization per batch entry. All-valid input order is authoritative; the old extractor's accidental complex-before-simple ordering is not a compatibility requirement. Repeated valid entries do not cause repeated generator dispatch. Never keep a valid subset of a rejected batch.

Unchanged successful inputs include `CCO`, `OCC` (keep input spelling at the seed/prompt boundary), `[Na+].[Cl-]`, `[13CH3][C@@H](O)C(=O)[O-]`, `C%12CCCCC%12`, `C`, and `CO`, using the parser's supported labels/prose. Existing output canonicalization/deduplication and inclusion of the seed in optimization candidates are untouched; exact-input assertions apply to the intent and prompt, not canonicalized outputs.

### Preserve description-only generation

The current substring keywords also match `Generate 2 molecules with improved solubility`. Its seedless optimization intent historically falls back to `_generate_with_llm`. Do not turn absence of a molecular candidate into a new failure for this case.

Add `MolecularInputMissing(ValueError)` in the shared parser, raised **only** at the existing `not values` branch after scanning a nonempty valid-length string. Leave empty/non-string/overlong input, empty explicit fields, invalid structures, and oversized batches as ordinary `ValueError`. Existing callers catching `ValueError` retain their behavior. Only the generator catches `MolecularInputMissing` locally and retains `base_smiles=None`, preserving the existing fallback. Do not catch general `ValueError` as missing. No new optional grammar mode or candidate-extraction helper is needed.

Ordinary generation without any current optimization keyword must never call the input parser at all. Target evidence is not SMILES input and is never fed to it. Do not change the optimization keyword vocabulary or replace it with `has_generation_intent`; routing/count grammar is a different concern. This does not promise support for arbitrary new prose/target-name syntax that the parser does not recognize.

### Reject invalid input and distinguish unavailable validation

Catch errors narrowly around `_analyze_generation_intent` inside `execute`, before `_generate_with_retry`:

- Ordinary molecular `ValueError`: use existing `_invalid_input_result` with a fixed message, `error.code=invalid_input` and `details.reason=invalid_optimization_input`. Leave `success=False`, `data=None`, `formatted=''`, no generated success/quality metadata. No retry/model dispatch.
- `MolecularInputUnavailable`: fixed message and `error.code=tool_unavailable`, with nonempty safe `details.reason=optimization_input_validation_unavailable`. No raw exception string or fabricated invalid-structure diagnosis, and no retry/model dispatch. Catch this before its `ValueError` parent.
- Existing Base RDKit preflight failure remains its existing RDKit-unavailable message/result, not `invalid_input`. Do not rewrite Base or broaden preflight/result semantics for de novo generation.

The parser currently wraps parser-parameter and RDKit validation exceptions as `MolecularInputUnavailable`, but import failure still raises plain `ValueError`. Change only that import-failure exception class to `MolecularInputUnavailable`, retaining its safe RDKit message. This closes the late-import failure case even when the cached Base preflight previously passed. It does not enable heuristic fallback. Other consumers already catching `ValueError` remain compatible; reverse-target benefits from its existing unavailable catch.

Do not move broad exception catches to the count-normalization block: that would mislabel bad SMILES as `malformed_requested_count`. Do not put molecular catches around model execution or result formatting.

### CX and grammar boundaries

The parser rejects CX/name suffixes in authoritative `SMILES:` fields and structure-leading bare fields (`parseName=False`, `allowCXSMILES=False`). However, in natural-language prose its tokenizer can ignore a pipe extension following a valid token, e.g. `optimize CCO |bad-extension|`. Parser reuse alone is insufficient to promise CX rejection here.

Add one optimization-only lexical guard: reject a query containing `|` before parsing it. Pipes are not accepted plain-SMILES syntax; CX extensions must not be silently discarded. This is intentionally conservative: pipe-separated optimization prose is unsupported too. Do not add a CX parser, strip extensions, or change shared tokenization. De novo queries outside the current optimization branch remain untouched. Literal pipe-containing seedless optimization prose is the explicit small compatibility restriction needed for this rejection contract.

Whole-input fidelity means full parser-recognized candidates/authoritative fields and whole-batch validation, not that every natural-language word is a molecule. Preserve supported legacy `optimize CCO into 2 molecules` and Chinese prose. Explicit fields reject molecule names; arbitrary unnamed prose is not a new molecular grammar. Tests must cover both unlabeled malformed molecular tokens and explicit bad fields, not claim universal free-text parsing correctness.

## Selection and parallel ownership

Leave `should_use` byte-for-byte unchanged. It remains the current generation-intent selector, including its existing Base-extractor call; this task does not assert it is cost-free. Do not add the new parser, model access, retries, dependency checks, or new keyword/selection semantics to it. An invalid optimization may still select this tool and then fail safely in execution.

Do not modify `src/agent/tools/base_tool.py`, `src/agent/tooling/adapters.py`, generation request contracts, rankers, schemas, registry, routes, RXN, or staged helper files. Package4C/`generation-ranking-contracts` owns the parallel typed-adapter work. G2 tests raw `LLMMolecularGenerator.execute`; it does not implement adapter status/provenance mappings. The two branches need integration regression later, not shared-file edits now.

## Acceptance and approval gate

After parent approval, TDD must first expose invalid input dispatch on the baseline, using actual generator execution and real RDKit with only model/dispatch spies. Cover both string and mapping inputs, exact seeds/prompts, all-valid versus mixed batches, no-candidate legacy fallback, parser/import unavailable, initial RDKit/LLM preflights, unchanged selection, and count/evidence regressions.

No test spy result is scientific evidence. No actual LLM, model, network, live database, environment file, or asset is needed. Evidence at design time was static only. Required implementation scope is detailed in the companion plan. The parent has authorized TDD, the initial two-document commit and merge of reviewed `origin/main` at `1bba025`. Focused/minimal tests may run; full Agent/full-suite testing waits for parent notification. Freeze the implementation for parent double review without pushing or creating a PR.
