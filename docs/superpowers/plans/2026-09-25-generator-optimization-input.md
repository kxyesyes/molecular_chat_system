# Generator optimization input fidelity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL after parent approval: use `executing-plans` for this small sequential change; `subagent-driven-development` is an alternative only if delegated. Steps use checkbox syntax. This document is a plan, not authorization to execute.

**Goal:** Stop fragment substitution and invalid mixed-batch generation at the actual optimization execution boundary while preserving seedless description generation and existing count/evidence behavior.

**Architecture:** Reuse the existing whole-input parser inside the current optimization branch of `_analyze_generation_intent`. Add a missing-candidate exception subtype to preserve the existing no-seed fallback, correct import-unavailable typing, and handle molecular failures locally in `execute` before dispatch. Selection, Base extraction, output generation, and typed adapters remain unchanged.

**Tech Stack:** Existing Python 3.10+, pytest, real RDKit, unittest.mock; no new dependencies or running model.

---

## Gate and baseline

Design: `docs/superpowers/specs/2026-09-25-generator-optimization-input-design.md`.
Baseline: `3a68264`; branch: `codex/generator-optimization-input`.
Work only in the assigned generator-optimization-input worktree. No original dirty-tree patch imports.

- [x] Parent explicitly approved the design and this plan before **any** test/source edits or test execution.
- [ ] Recheck `git status --short`, `git branch --show-current`, `git log -1 --oneline`; preserve concurrent edits. If the baseline moved, reread only affected boundary code before proceeding.
- [ ] Keep secrets/env/assets/models/network and push/PR operations prohibited. Parent authorized committing the two design documents, then merging reviewed local `origin/main` at `1bba025`, before RED. Offline tests below require an already available development interpreter with RDKit; do not install or configure anything silently. Return the implementation frozen for parent double review.

## Exact file ownership

| Action after approval | File | Responsibility |
| --- | --- | --- |
| Create | `tests/agent/test_generator_optimization_input.py` | Real parser + actual generator boundary regressions, dispatch/prompt spies, missing/unavailable behavior. |
| Modify | `src/agent/tools/llm_molecular_generator.py` | Parser imports; tool-local prose words; optimization seed resolution; narrow pre-dispatch error handling only. |
| Modify | `src/agent/tools/molecular_input.py` | Missing-candidate subtype, split existing missing/oversized-batch check, import-error type, accurate module docstring. No grammar rewrite. |
| Modify | `tests/test_llm_molecular_generator.py` | Replace the obsolete extractor mock only in the existing optimization/evidence transport test. |

These two documents are the only changes in the current design turn. Do not edit handoff files just to widen this bounded task. No changes to Base, RXN, staged helpers, count/evidence contracts, adapters, ranks, registration, JS, models, or dependency files.

## Task 1: RED — expose actual wrong-seed dispatch

- [ ] Create the focused test module with the following harness and cases. Real RDKit is required for input tests; do not mock `extract_smiles`, `validate_smiles`, the input parser, or `_check_rdkit` in these chemical-boundary cases. Empty spy output is not a predicted molecule; success is not asserted for spy-dispatch tests.

```python
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.agent.tools import base_tool
from src.agent.tools import llm_molecular_generator as generator_module
from src.agent.tools import molecular_input
from src.agent.tools.llm_molecular_generator import LLMMolecularGenerator


@pytest.fixture
def boundary(monkeypatch):
    from rdkit import Chem
    assert Chem.MolFromSmiles('CCO') is not None
    llm = SimpleNamespace(model_name='test-only', generate=Mock(return_value=''))
    tool = LLMMolecularGenerator(llm_model=llm)
    dispatch = Mock(return_value=[])
    monkeypatch.setattr(tool, '_generate_with_retry', dispatch)
    return tool, dispatch, llm


INVALID = [
    'optimize SMILES: CCO)((',
    'optimize SMILES: CCO]',
    'optimize SMILES: CCO.',
    'optimize SMILES: cco',
    'optimize SMILES: CC(C)((',
    'optimize SMILES: [NH4+].',
    'optimize SMILES: CCO molecule-name',
    'optimize SMILES: "CCO',
    'optimize SMILES: CCO |bad-extension|',
    'optimize CCO |bad-extension|',
    'optimize SMILES: CCO |$C1;C2;O3$|',
    '优化 CCO 和 CCN)INVALID 的溶解性',
    'optimize CCO and CCOfoo',
    'optimize SMILES: CCO; CC(C)((',
    'optimize SMILES: CCO\nCCN)INVALID',
    'optimize SMILES: CC(C)((\nSMILES: CCO',
    'optimize SMILES: CCO\nSMILES:',
    'optimize SMILES:',
    'optimize SMILES: optimize',
]


@pytest.mark.parametrize('query', INVALID)
@pytest.mark.parametrize('structured', [False, True])
def test_invalid_input_never_dispatches(boundary, query, structured):
    tool, dispatch, llm = boundary
    request = ({'query': query, 'metadata': {'requested_count': 2}}
               if structured else query)
    result = tool.execute(request)
    dispatch.assert_not_called()
    llm.generate.assert_not_called()
    assert result['success'] is False
    assert result['data'] is None and result['formatted'] == ''
    assert result['error']['code'] == 'invalid_input'
    assert result['error']['details'] == {'reason': 'invalid_optimization_input'}
    assert 'quality' not in result
```

- [ ] Run `python -m pytest tests/agent/test_generator_optimization_input.py -q`. Expected RED: baseline dispatch spy is called with a truncated/remaining seed or with no seed; this must fail on dispatch assertions, not imports/fixture setup. Do not start a model to reproduce it.

## Task 2: RED — pin preservation and failure classification

- [ ] Append these tests. Passing characterization tests are legitimate alongside failing safety regressions.

```python
@pytest.mark.parametrize('seed', [
    'CCO', 'OCC', 'C', 'CO', '[Na+].[Cl-]',
    '[13CH3][C@@H](O)C(=O)[O-]', 'C%12CCCCC%12',
    'N#Cc1ccc(C(=O)O)cc1',
])
@pytest.mark.parametrize('prefix', [
    'optimize SMILES: ', 'improve SMILES: ', 'modify SMILES: ', '优化 ',
])
def test_seed_reaches_dispatch_unchanged(boundary, seed, prefix):
    tool, dispatch, _ = boundary
    tool.execute(prefix + seed)
    dispatch.assert_called_once()
    intent = dispatch.call_args.args[0]
    assert intent['type'] == 'optimization'
    assert intent['base_smiles'] == seed


@pytest.mark.parametrize('batch', [
    'SMILES: CCO\nSMILES: CCN', 'CCO\nCCN', 'SMILES: CCO; CCN',
    'SMILES: CCO\nSMILES: CCO', 'SMILES: CCO; N#Cc1ccccc1',
])
def test_all_valid_batch_uses_only_first_seed(boundary, batch):
    tool, dispatch, _ = boundary
    tool.execute('optimize ' + batch)
    dispatch.assert_called_once()
    assert dispatch.call_args.args[0]['base_smiles'] == 'CCO'


def test_supported_legacy_prose(boundary):
    tool, dispatch, _ = boundary
    tool.execute('optimize CCO into 2 molecules')
    assert dispatch.call_args.args[0]['base_smiles'] == 'CCO'
    # Baseline execution confirms optimization prose defaults to one.
    assert dispatch.call_args.args[0]['count'] == 1


def test_seed_spelling_reaches_real_prompt():
    llm = SimpleNamespace(model_name='test-only', generate=Mock(return_value='CCN'))
    tool = LLMMolecularGenerator(llm_model=llm)
    result = tool.execute('optimize SMILES: [13CH3][C@@H](O)C(=O)[O-]', mol_count=1)
    assert result['success'] is True  # Pipeline contract only; fake LLM output.
    llm.generate.assert_called_once()
    assert 'Original SMILES: [13CH3][C@@H](O)C(=O)[O-]\n' in llm.generate.call_args.args[0]


@pytest.mark.parametrize('structured', [False, True])
def test_seedless_optimization_word_keeps_description_dispatch(monkeypatch, structured):
    tool = LLMMolecularGenerator(llm_model=object())
    description = Mock(return_value=['CCO'])
    optimization = Mock(side_effect=AssertionError('No optimization seed'))
    monkeypatch.setattr(tool, '_generate_with_llm', description)
    monkeypatch.setattr(tool, '_optimize_with_llm', optimization)
    query = 'Generate 2 molecules with improved solubility'
    request = ({'query': query, 'metadata': {'requested_count': 1},
                'outputs': {'target': {'gene_symbol': 'PDE5A',
                                       'source_record_id': 'O76074', 'source': 'UniProt'}}}
               if structured else query)
    result = tool.execute(request, mol_count=1)
    assert result['success'] is True
    description.assert_called_once()
    assert description.call_args.args[0]['base_smiles'] is None
    if structured:
        assert 'PDE5A' in description.call_args.args[0]['target_evidence']
    optimization.assert_not_called()


def test_de_novo_and_target_generation_do_not_parse_seeds(boundary, monkeypatch):
    tool, dispatch, _ = boundary
    parser = Mock(side_effect=AssertionError('Not a seeded optimization'))
    monkeypatch.setattr(generator_module, 'parse_molecular_smiles', parser, raising=False)
    requests = [
        'Generate 2 molecules',
        {'query': 'Generate 7.5 molecules',
         'metadata': {'requested_count': 2},
         'outputs': {'target': {'gene_symbol': 'PDE5A',
                                'source_record_id': 'O76074', 'source': 'UniProt'}}},
    ]
    for request in requests:
        dispatch.reset_mock()
        tool.execute(request)
        dispatch.assert_called_once()
        intent = dispatch.call_args.args[0]
        assert intent['count'] == 2 and intent['base_smiles'] is None
        if isinstance(request, dict):
            assert 'PDE5A' in intent['target_evidence']
    parser.assert_not_called()


def test_parser_unavailable_is_not_invalid_structure(boundary, monkeypatch):
    tool, dispatch, llm = boundary
    parser = Mock(side_effect=molecular_input.MolecularInputUnavailable('private diagnostic'))
    monkeypatch.setattr(generator_module, 'parse_molecular_smiles', parser, raising=False)
    result = tool.execute('optimize SMILES: CCO')
    dispatch.assert_not_called()
    llm.generate.assert_not_called()
    assert result['success'] is False
    assert result['data'] is None and result['formatted'] == ''
    assert result['error']['code'] == 'tool_unavailable'
    assert result['error']['details']['reason'] == 'optimization_input_validation_unavailable'
    assert 'private diagnostic' not in str(result)


def test_missing_rdkit_preserves_existing_preflight(boundary, monkeypatch):
    tool, dispatch, llm = boundary
    monkeypatch.setattr(base_tool, 'RDKIT_AVAILABLE', False)
    result = tool.execute('optimize SMILES: CCO')
    assert result['success'] is False and result['data'] is None
    assert 'RDKit' in result['message']
    assert result.get('error', {}).get('code') != 'invalid_input'
    dispatch.assert_not_called()
    llm.generate.assert_not_called()


def test_missing_llm_preserves_existing_preflight(boundary):
    tool, dispatch, _ = boundary
    tool.llm = None
    result = tool.execute('optimize SMILES: CCO')
    assert result['success'] is False and 'LLM' in result['message']
    dispatch.assert_not_called()


def test_should_use_does_not_call_new_parser_or_generator(boundary, monkeypatch):
    tool, dispatch, llm = boundary
    parser = Mock(side_effect=AssertionError('Selection is separate'))
    monkeypatch.setattr(generator_module, 'parse_molecular_smiles', parser, raising=False)
    assert tool.should_use('Generate 2 molecules')
    assert not tool.should_use('Please analyze SMILES: CCO')
    parser.assert_not_called()
    dispatch.assert_not_called()
    llm.generate.assert_not_called()


def test_parser_missing_is_distinct_from_explicit_empty(boundary):
    tool, _, _ = boundary
    with pytest.raises(molecular_input.MolecularInputMissing):
        molecular_input.parse_molecular_smiles('Generate molecules with improved solubility', tool)
    with pytest.raises(ValueError) as exc:
        molecular_input.parse_molecular_smiles('optimize SMILES:', tool)
    assert not isinstance(exc.value, molecular_input.MolecularInputMissing)


def test_parser_import_failure_is_unavailable(boundary, monkeypatch):
    import builtins
    tool, dispatch, llm = boundary
    original_import = builtins.__import__

    def blocked_import(name, *args, **kwargs):
        if name == 'rdkit':
            raise ImportError('private dependency diagnostic')
        return original_import(name, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(builtins, '__import__', blocked_import)
        with pytest.raises(molecular_input.MolecularInputUnavailable):
            molecular_input.parse_molecular_smiles('SMILES: CCO', tool)
        result = tool.execute('optimize SMILES: CCO')
    assert result['error']['code'] == 'tool_unavailable'
    assert 'private dependency diagnostic' not in str(result)
    dispatch.assert_not_called()
    llm.generate.assert_not_called()
```

- [ ] Run the focused module again. Expected: preservation cases establish baseline behavior, dispatch safety/unavailable/new missing-subtype cases fail. An absent new exception class in its specific new-contract test is expected; the original dispatch regression must still demonstrate the independent root cause.

## Task 3: GREEN — minimal production change

- [ ] In `molecular_input.py`, update the module docstring to `Whole-structure parsing for migrated tools and optimization seeds.` Add this exception next to `MolecularInputUnavailable`:

```python
class MolecularInputMissing(ValueError):
    """Nonempty supported text contains no molecular candidates."""
```

- [ ] Replace only `if not values or len(values) > 100:` with these two guards. Retain the original initial type/blank/length guard and all token/field handling unchanged:

```python
if not values:
    raise MolecularInputMissing(_INVALID)
if len(values) > 100:
    raise ValueError(_INVALID)
```

- [ ] In its existing `except ImportError`, replace the raised `ValueError` with `MolecularInputUnavailable`, preserving the safe `RDKit 不可用，无法验证 SMILES。` message and `from None`. No other validation/canonicalization change.

- [ ] In `llm_molecular_generator.py`, add:

```python
from .molecular_input import (
    MolecularInputMissing,
    MolecularInputUnavailable,
    parse_molecular_smiles,
)
```

- [ ] Immediately after the generator's `super().__init__` call, add its three existing English optimization verbs to the parser's tool-local prose vocabulary. The parser must ignore these as instruction prefixes but still reject them inside an explicit SMILES field:

```python
self.exclude_words.update({'optimize', 'improve', 'modify'})
```

- [ ] Replace only seed extraction inside the existing optimization keyword branch. Keep the keyword test, intent defaults, objectives, count parsing and all remaining code:

```python
intent['type'] = 'optimization'
if '|' in query:
    raise ValueError('CXSMILES extensions are not supported for optimization.')
try:
    smiles_list = parse_molecular_smiles(query, self)
except MolecularInputMissing:
    smiles_list = []  # Preserve existing seedless description fallback only.
if smiles_list:
    intent['base_smiles'] = smiles_list[0]
```

- [ ] Wrap just the existing `_analyze_generation_intent` call inside `execute` with the following narrow catches. Keep the existing outer unexpected-exception handling; leave metadata injection, dispatch, formatting and result quality logic after this block unchanged:

```python
try:
    intent = self._analyze_generation_intent(
        query_text,
        requested_count=requested_count,
    )
except MolecularInputUnavailable:
    result['message'] = 'SMILES 校验暂不可用，未执行分子生成。'
    result['error'] = {
        'code': 'tool_unavailable',
        'message': result['message'],
        'details': {'reason': 'optimization_input_validation_unavailable'},
    }
    return result
except ValueError:
    return self._invalid_input_result(
        query_text,
        '优化输入中的 SMILES 无效或不受支持；请提供完整结构，不会提取片段替代。',
        details={'reason': 'invalid_optimization_input'},
    )
```

`requested_count` has already been validated before this call, so these catches do not replace the earlier count errors. Keep the unavailable catch first. Missing is handled only in the seed parser call, not swallowed at execute level.

- [ ] Run `python -m pytest tests/agent/test_generator_optimization_input.py -q`. Expected GREEN for every input/dispatch/preservation case, with real RDKit input validation. If grammar assumptions fail, stop and report the specific case to the parent; do not grow a second parser or weaken the failing safety assertion.

## Task 4: preserve existing transport/count regressions

- [ ] In `tests/test_llm_molecular_generator.py`, only in `test_structured_optimization_prompt_uses_sanitized_target_evidence`, replace the obsolete `mock.patch.object(generator, "extract_smiles", return_value=["CCO"])` context manager with:

```python
mock.patch.object(generator_module, 'parse_molecular_smiles', return_value=['CCO'])
```

This test is explicitly a prompt/evidence transport unit test, already mocking output chemistry. The new focused module independently exercises real input chemistry; do not substitute this parser mock into its fidelity tests.

- [ ] Run the focused existing-and-new regression set:

```powershell
python -m pytest tests/agent/test_generator_optimization_input.py tests/test_llm_molecular_generator.py tests/agent/test_explicit_molecular_input.py tests/agent/test_reverse_target_complete_input.py tests/agent/test_drug_likeness_evidence.py tests/agent/test_generation_temperature_transport.py -q
```

Expected: all pass. Existing generator tests cover metadata/method/text count precedence, malformed and negated counts, target-evidence sanitization, canonical output deduplication and partial counts. Keep them; do not reimplement those contracts or modify adapter tests to compensate for a failure.

## Task 5: regression, scope review, parent handoff

- [ ] Run `python -m compileall -q src scripts`. **Do not run `python -m pytest tests/agent -q` or `python -m pytest tests -q` until the parent sends the testing-slot notification**; focused/minimal commands above are authorized now. Tests use repository isolation fixtures; do not open or change real environment files. Record exact pass/fail/skip totals and dependency blockers; never label unrun tests as passed. Do not invoke `main.py`, real/all acceptance, downloads, models, or live health checks. JS/deployment/assets were not changed, so their live checks are not part of this bounded patch.
- [ ] Review `git diff --check`, `git diff --stat`, and the complete source/test diff. Confirm `should_use`, `_normalize_request`, `_generate_with_retry`, prompt builders, Base, shared grammar and all typed adapters remain unchanged. Only the two parser exception classifications and its docstring may change in the shared parser.
- [ ] Verify no repeated parser calls per generator retry: parsing happens once during intent analysis, before dispatch; the complete batch is validated once. Do not validate again in selection or per attempt.
- [ ] Hand the parent the exact changed paths, failing baseline assertion, final results, branch/HEAD, and remaining integration risk: Package4C must consume raw errors without masking their classification, and the new pipe rejection is deliberately conservative. Commit/push/PR are still unauthorized; report them as not created.

## Current design-turn verification

During the design turn, only read-only source/Git inspection and creation of the two Markdown documents were performed. No runtime failure reproduction, pytest, compileall, model or scientific execution occurred then. Parent approval subsequently opened bounded TDD; full Agent/full-suite testing remains queued. Unchecked implementation steps are not evidence of completion.

## Approved implementation execution record — frozen for parent review

The results in this section supersede the planned checklist as execution evidence.
No independent SPEC/QUALITY review is claimed by the implementer.

### Baseline and authority

- Parent approved bounded TDD, the two-document commit, and merge of reviewed `origin/main` at `1bba025`.
- Corrected the historical audit location before the document commit: it is in the separate `historical-residual-disposition` worktree, not missing; reading it was unnecessary.
- Document commit: `2efe821` (`docs: specify generator optimization input fidelity`).
- Merge commit/current HEAD: `5e84087` (`origin/main` merged without conflicts). No changes to generator/parser/AGENTS/standards were introduced by that merge.
- Implementation branch: `codex/generator-optimization-input`. Four source/test files and this execution record remain uncommitted and unstaged for parent double review. No push or PR.

### Test isolation and commands

The default Python and Anaconda base interpreter lacked RDKit; no packages were installed. All test evidence below uses the already installed `C:/Users/xkx52/.conda/envs/MedChat/python.exe` (pytest 9.1.0, RDKit 2025.03.6), as documented by the prior integration plans.

Used the checked-in isolation runner in `docs/superpowers/plans/2026-09-24-rag-service-extraction.md`, with its repository set to this worktree, temporary prefix changed to `medchat-g2-`, and the evaluation-fixture copy block removed entirely because these focused tests do not need it. The runner clears inherited non-OS environment values without displaying them, configures temporary runtime paths and disabled real-service switches, changes cwd to the temporary directory, and invokes normal pytest with:

```text
<MedChat Python> -B -m pytest <absolute test paths> -q -p no:cacheprovider --tb=short -rs
```

No real `.env`, credentials, scientific assets, indexes, models, or external services were read/called by this workflow. Model outputs are fixed test doubles, not scientific evidence. The full isolation wrapper is recoverable from that checked-in runner; it creates no persistent runner file. The corrected RED summary alone used `--tb=no` to avoid repeating identical failure traces.

### RED → GREEN evidence

| Run | Exact test scope (paths below) | Result |
| --- | --- | --- |
| First actual-execution RED | NEW `::test_invalid_input_never_dispatches[False-optimize SMILES: CCO)((]` | 1 failed, 1.44s, exit 1. Actual `execute` dispatched once with `base_smiles='CCO'` for malformed `CCO)((`. |
| Initial complete NEW RED | NEW | 73 failed, 18 passed, 4.06s, exit 1. Included one incorrect planned count expectation. |
| Corrected complete NEW RED, before production edits | NEW | 72 failed, 19 passed, 1.20s, exit 1. Missing exception-type tests also failed as expected because the new subtype did not yet exist. |
| Minimal implementation GREEN | NEW | 91 passed, 1.90s, exit 0. |
| Six-file focus GREEN | FOCUS | 391 passed, 162 subtests passed, 3.24s, exit 0. |
| Additional shared-parser consumer GREEN | CONSUMERS | 109 passed, 1.22s, exit 0. |

`NEW` is `tests/agent/test_generator_optimization_input.py`.

`FOCUS` is exactly:

```text
tests/agent/test_generator_optimization_input.py
tests/test_llm_molecular_generator.py
tests/agent/test_explicit_molecular_input.py
tests/agent/test_reverse_target_complete_input.py
tests/agent/test_drug_likeness_evidence.py
tests/agent/test_generation_temperature_transport.py
```

`CONSUMERS` is exactly:

```text
tests/agent/test_admet_whole_input.py
tests/agent/test_property_report_boundaries.py
```

The two final disjoint focused runs total 500 passed and 162 subtests passed, with no skips or warnings reported. This is not a full Agent or whole-repository run.

The only correction to the approved test blueprint was evidence-based: baseline `optimize CCO into 2 molecules` uses the existing default count **1**, not 2. The test and blueprint now preserve 1. Production generation-count code was not changed. The focused test module also explicitly covers non-string, empty, blank, overlong, and oversized-batch inputs remaining ordinary invalid input rather than `MolecularInputMissing`.

### Static checks and scope

- `compileall -q src scripts`: passed with the MedChat interpreter and a temporary `sys.pycache_prefix`; no source imports/model execution and no bytecode left in the worktree.
- `git diff --check`: no whitespace errors. New test file separately checked with `git diff --no-index --check`; no whitespace diagnostics (its diff exit 1 reflects a new file).
- AST comparison against HEAD: all 16 generator methods other than `__init__`, `execute`, and `_analyze_generation_intent` are identical, including `should_use`, normalization, retries, prompts, output filtering and formatting. No method added/removed.
- Shared parser lexical helper ASTs and grammar constants are identical. Only missing-candidate classification, import-unavailable type, and docstring were changed there; the new exception remains a `ValueError` subtype.
- Diff against `1bba025` confirms Base, all `src/agent/tooling` files, generation-request contracts and RXN are untouched. No Package4C adapter or staged-helper activation.
- Parsing occurs once during intent analysis before the retry loop, not in selection or per retry. All parsed batch members must validate before first-seed selection.

### Frozen source/test snapshot

SHA-256 of the final four implementation files (the plan record itself is excluded):

```text
src/agent/tools/llm_molecular_generator.py 0632ba9a9969d892853189c904130cecbfffd987265664aab6e2474f479a6e30
src/agent/tools/molecular_input.py 9f6dea9398050367c2e573035090c68d81e8037bf52c1d8407caeec1a154e9f4
tests/test_llm_molecular_generator.py 3ca8a7d03a40c825fab58069a159c7fa5e580fa1042c6c3ffd1e8fe3f2c3e4fb
tests/agent/test_generator_optimization_input.py 3d93ce32dbcd1736c6c1534460ec199e0909d5c97a13f7a3633884782786871e
```

### Remaining parent gates

Full Agent and whole-repository pytest were **not run**, honoring the parent-managed 4B RED/GREEN queue; wait for notification before using that slot. Independent SPEC then QUALITY review, and later Package4C integration regression, remain parent-owned. No real-model/scientific acceptance, health check, network access, implementation commit, push, or PR was performed. The conservative optimization-only pipe rejection remains the documented compatibility restriction.

## Independent review checkpoint

Independent SPEC approved the frozen four-file implementation: the reviewer reproduced the baseline in memory (72 failed, 19 passed), then independently ran the six-file set (391 passed, 162 subtests passed), checking exact seed spelling and one parse across retries. Independent QUALITY approved with its own isolated 500 passed and 162 subtests passed. Both verified the recorded hashes and no changes to out-of-scope algorithms, dispatch counts or cleanup.

The parent will commit only these reviewed files/docs, integrate current reviewed main, and run focused plus full Agent tests. Full Agent is still pending; reviews and focused results are not a substitute for that gate or exact-head CI. Original dirty checkout and production assets remain untouched.

Reviewed implementation committed `86d4ee4`; merged current main through PR #70 (`bb11ded0bdb62d919ca64969730cfd2b85d77980`) as `73297bd` without conflicts. The unchanged eight focused G2/shared-parser files plus both new recovery regression files passed **556 tests and 162 subtests in 10.12s**, exit 0, via the documented isolated runner. No source changes accompanied integration. Full Agent remains queued after 4C.

## Integrated full-run failure: obsolete count-probe injection

The full Agent run on `73297bd` completed **1 failed, 6411 passed, 2 skipped, 7 warnings in 296.12s**, exit 1. Failure: `test_supervisor_agent.py::test_supervisor_hit_to_lead_real_generator_reports_public_count`, missing `quality.requested_count`. Isolated unchanged reproduction failed again in 1.35s. Skips remained directory symlinks unavailable and opt-in performance test disabled; warnings were retained.

Inspection showed this pre-existing count-transport test patches `extract_smiles` and installs a deliberately minimal fake `rdkit` without `rdBase`/parser parameters. G2 now correctly calls the complete parser, which reports validation unavailable before model dispatch. The test is not a real-chemistry test; its old injection point no longer intercepts parsing. The parent changed only that one test's parser seam to `generator_module.parse_molecular_smiles`, returning the same empty seed list, preserving every existing count/model-call assertion. No production code, error gate, algorithm, or scientific-validation test was changed. The actual workflow generator input is already structured with original query and separate baseline outputs; the old textual template is not used for that dispatch, so no template or parsing exception is needed.

This fifth test-file delta needs independent review; earlier four-file hashes/approvals remain historical evidence. A new full regression is required after focused verification. The prior failure is not erased or classified as a passing run.

Focused verification after that test-only correction: count probe + full new optimization boundary + legacy generator tests + temperature transport, **140 passed and 162 subtests passed in 1.49s**. Independent reviewer Anscombe approved the delta: exact original assertions preserved; count probe followed by real-parser tests in the same isolated process yielded **92 passed**, without monkeypatch/fake-RDKit leakage. Full Agent rerun is now in progress; no result is claimed yet.

The unchanged rerun snapshot (`73297bd` plus the reviewed test-only correction) completed **6412 passed, 2 skipped, 7 warnings in 282.81s**, exit 0, using the same isolated runner and `tests/agent`. Skips remain unavailable directory symlinks and disabled opt-in performance test. No edits occurred during this run. This is offline Agent regression, not live model/scientific acceptance. The first failed full run remains recorded above. Subsequent main/Planner integration must be verified separately; this result must not be relabelled as a run on a later commit.

After committing the reviewed test seam as `5c7b7c3`, integrated main/Planner PR #71 (`5db56b0`) without conflicts as `fa1cf74`. The same eight G2/shared-parser files, `test_supervisor_agent.py`, `test_planner_responsibilities.py`, and both recovery regression files passed **639 tests and 162 subtests in 9.65s**, exit 0. Implementation hashes remain unchanged. The branch is ready for independent-review-backed PR publication and exact-head CI; no real provider, generator, production model or deployment has been activated. Current full evidence remains the preceding pre-Planner-integration run, with the above focused compatibility check and later CI kept distinct.
