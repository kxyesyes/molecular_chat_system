# Analytical clause / target role boundary implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking. Do not dispatch subagents or execute this plan until parent release.

**Goal:** Recognize complete analytical clauses without treating their analytical items as unknown targets, while retaining every original explicit target and ambiguity guard.

**Architecture:** Add a deterministic positional recognizer inside the shared target-request contract. Compute complete clause intervals once and advance an ordered cursor while collecting inferred unlabelled-list starts. Do not rewrite the query or filter final unknown identities; all other parser paths consume the original text unchanged.

**Tech Stack:** Existing Python 3.10-compatible `re`, dataclasses and pytest; unchanged offline test launcher. No new dependency or Web import.

---

## Authority, baseline and file boundary

Drafted from clean `codex/analysis-target-role-boundary` HEAD `d8e3eea` in the
independent `analysis-target-role-boundary` worktree. Read `AGENTS.md`,
`docs/PROJECT_STANDARDS.md`, and the complete
`docs/superpowers/specs/2026-09-27-analysis-target-role-boundary-design.md`.
Parent reports Banach SOURCE approval of the grammar/safety design at `d8e3eea`,
with the final RED-versus-compatibility wording correction now in docs-only HEAD
`804be52`. That spec update is read and preserved. This implementation plan still
requires parent release; design approval is not executed TDD or scientific
evidence. Laplace owns the scientific
slot. No Python, imports, probes, tests, runner creation or process manipulation
is permitted until parent explicitly releases the corresponding step and slot.

Current drafting write scope is this plan only. Future implementation scope:

| File | Responsibility / permission |
|---|---|
| `src/agent/contracts/target_request.py` | Sole production edit: private recognizer and list-start exclusion |
| `tests/agent/test_target_analysis_phrase.py` | Sole new test file: behavior, preservation, scanning bounds |
| This plan | Actual commands, behavioral RED/GREEN, failures and review evidence |
| Design document | Parent/reviewer-owned; no edit by this drafting worker |

All existing target identity, selection, routing and Planner tests stay unchanged.
No edits to `src/target_identifiers.py`, `_ACTION_WORDS`, `_LABEL`, `_FOR`,
`_FOLLOWING`, `_CANDIDATE_SELECTION`, `_QUALIFIED`, journal, admission, tool,
scientific producer, runner isolation, frontend or Task6 files. Do not alter the
dirty `dynamic-bindings-b1` tree or its ledger. No commit, push, merge, live model,
host asset discovery or deployment is authorized by this plan.

## Source facts and selected bounded choices

`analyze_target_request` currently builds `list_matches` by scanning `_IDENTIFIER`
and `_FOLLOWING`, requiring a known-target anchor. In the unchanged request
`评估分子量和类药性和 ADMET 和 PDE5A 活性；SMILES: CCO; CCN`, ADMET incorrectly
starts such a list. Suppressing only ADMET is insufficient: QED or uppercase LOGP
before ADMET can start the same list. Suppress every inferred start inside a
fully recognized analytical clause, not every occurrence of a particular value.

New private helpers (all defined in Task 2):

- `_analysis_item(query, position, end)` returns `(next_position, role)` or
  `None`; role is internal `admet`, `property` or `activity`, not a public enum.
- `_analysis_clause(query, start, end)` returns a boolean after consuming the
  whole interval, with at least one ADME/ADMET and one known-target activity.
- `_analysis_clause_intervals(query)` returns ordered disjoint `(start, end)`
  tuples, excluding semicolon/newline delimiters themselves.

Use ASCII horizontal spaces/tabs inside clauses; semicolon (`;`/`；`), CR and LF
are hard boundaries. Permit one optional `请` or case-insensitive `please` at the
start; English `please` requires horizontal separation. English action/item/join
words use ASCII identifier guards so adjacent Chinese is possible but ADMET2,
propertiesXYZ and `androgen` cannot be accepted as complete tokens. One final
`. ! ? 。 ！ ？` is allowed. Do not add slash/or joins, optional unknown prose,
new target aliases, Unicode digit conversion or a second assessment action.

These are bounded recommendations under delegated design choices. If final
SOURCE changes the grammar, revise this plan before implementation, not the
positive query or an existing assertion.

## Task 1 — public behavior RED and preservation controls

**Files:** Create `tests/agent/test_target_analysis_phrase.py` only, after release.

- [ ] Recheck branch/HEAD and final design approval. Confirm no other scientific
  process is running through parent coordination; an observation timeout is not
  permission to start a second run. Require the approved local launcher with only
  its `REPO` literal substituted for this independent tree. Parent must provision
  and verify that copy; do not run the Task6-tree launcher against this checkout.
- [ ] Write this complete first section of the new test file. These tests call
  real parser/Router/Planner APIs; no mocked target extraction or changed query.

```python
"""Analytical role parsing only; no model/scientific readiness claim."""
import pytest

from src.agent.contracts import AgentContext
from src.agent.contracts import target_request as boundary
from src.agent.planning.task_planner import TaskPlanner
from src.agent.routing import HybridSkillRouter


EXACT_QUERY = '评估分子量和类药性和 ADMET 和 PDE5A 活性；SMILES: CCO; CCN'


@pytest.mark.parametrize('query', [
    pytest.param(EXACT_QUERY, id='exact-reproducer'),
    '评估 QED 和 ADMET 和 PDE5A 活性',
    '评估 ADMET 和 QED 和 PDE5A 活性',
    '评估 LOGP 和 ADMET 和 PDE5A 活性',
    '评估 ADMET 和 LogP 和 PDE5A 活性',
    '评估 PDE5A 活性和 QED 和 ADMET',
    '评估 PDE5A 活性和 ADMET 和 QED',
    '请预测 ADME、PDE5A 的活性。',
    'Please ASSESS QED and admet and pde5a activity!',
    'calculate molecular weight, properties, ADMET, PDE5A potency',
    'compute drug-likeness and ADME and PDE5A pIC50',
    'analyse drug likeness and ADMET and PDE5A IC50',
    'analyze Lipinski and ADMET and PDE5A activity',
    'evaluate\tTPSA\tand\tHBD and HBA and ADMET and PDE5A activity',
    '预测分子性质及分子属性与理化性质并性质、属性，成药性和ADMET和PDE5A活性',
    'unrelated prose; 评估 QED 和 ADMET 和 PDE5A 活性\nSMILES: CCO; CCN',
])
def test_complete_analysis_retains_target_without_analytical_unknown(query):
    original = query.encode('utf-8')
    request = boundary.analyze_target_request(query)
    assert query.encode('utf-8') == original
    assert request.targets == ('PDE5A',)
    assert request.unknown == ()
    assert not request.qualified
    assert not request.needs_clarification
    # A successful exemption need not manufacture explicit-label authority.
    assert not request.explicit


@pytest.mark.parametrize('label', ['target:', 'against ', 'for '])
@pytest.mark.parametrize('prefix', ['', '评估 QED 和 ADMET 和 PDE5A 活性；'])
def test_explicit_admet_occurrence_is_never_exempted(label, prefix):
    query = prefix + label + 'ADMET and PDE5A'
    request = boundary.analyze_target_request(query)
    assert request.targets == ('PDE5A',)
    assert request.unknown == ('ADMET',)
    assert request.explicit and request.needs_clarification
    assert 'admet' not in boundary._ACTION_WORDS


@pytest.mark.parametrize('clause, unknown, targets, qualified', [
    ('评估 UNKNOWN42 和 PDE5A 活性', ('UNKNOWN42',), ('PDE5A',), False),
    ('评估 PDE5A 和 UNKNOWN42 活性', ('UNKNOWN42',), ('PDE5A',), False),
    ('评估 ADMET2 和 PDE5A 活性', ('ADMET2',), ('PDE5A',), False),
    ('评估 PDE5A 和 ADMET2 活性', ('ADMET2',), ('PDE5A',), False),
    ('评估 ADMET 和 PDE5A 活性 和 PDE4A 活性', (), ('PDE5A', 'PDE4A'), False),
    ('评估 ADMET 和 PDE5A 活性 和 BuChE 活性', (), ('PDE5A', 'BCHE'), False),
    ('评估 ADMET 和 PDE5A 活性；target: UNKNOWN42', ('UNKNOWN42',), ('PDE5A',), False),
    ('评估 ADMET 和 PDE5A 活性；not against PDE5A', (), ('PDE5A',), True),
    ('评估 ADMET 和 PDE5A 活性；switch target: PDE4A', (), ('PDE5A', 'PDE4A'), True),
    ('评估 ADMET 和 PDE5A 活性；selective', (), ('PDE5A',), True),
])
def test_ambiguity_survives_parser_router_and_planner(clause, unknown, targets, qualified):
    # Generation intent is explicit because Router's existing ambiguity guard
    # is a generation guard, not a promise to reject every analytical query.
    query = clause + '; Design 2 molecules'
    request = boundary.analyze_target_request(query)
    assert request.targets == targets
    assert request.unknown == unknown
    assert request.qualified is qualified
    assert request.needs_clarification
    decision = HybridSkillRouter().decide(query)
    assert decision.selected_skill is None and decision.requires_confirmation
    for skill in ('target_driven_design', 'molecular_design', 'hit_to_lead_optimization'):
        context = AgentContext(query=query, active_skill=skill, trace_id='analysis-role-test')
        plan = TaskPlanner().plan(context)
        assert context.query == query
        assert not plan.steps
        assert plan.metadata['reason'] == 'target_clarification_required'
```

- [ ] Run the public positive node BEFORE editing production. Require observed
  RED for `exact-reproducer` and only variants demonstrated to fail: analytical
  ADMET/QED/LOGP appears in `request.unknown` on the relevant anchored lists.
  Already-green spelling/order variants are compatibility controls, not required
  REDs; do not alter them to force failure. Record per-case disposition. Missing imports,
  fixture failures or a misspelled expected alias do not count as behavioral RED.

```powershell
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -I -S -B scratch/ordinary_chat_offline_runner.py tests/agent/test_target_analysis_phrase.py::test_complete_analysis_retains_target_without_analytical_unknown
```

- [ ] Record exact command, handle, terminal counts, duration, warnings/skips and
  both runner/process exits. Wait for terminal before proceeding; retain failures.

## Task 2 — minimal positional recognizer and list-only exclusion

**Files:** Modify `src/agent/contracts/target_request.py` only.

- [ ] After confirmed behavioral RED, add these constants and three helpers
  before `analyze_target_request`; keep all existing definitions byte-for-byte.
  The algorithm is action → item → (join → item)* → optional punctuation → end.
  A failed clause publishes no partial interval and never seeks another action
  inside the same clause. No regex repeats a list of items.

```python
_ANALYSIS_BOUNDARY = re.compile(r'[;；\r\n]|\Z')
_ANALYSIS_SPACE = re.compile(r'[ \t]*')
_ANALYSIS_HEAD = re.compile(
    r'[ \t]*(?:请[ \t]*|please[ \t]+)?'
    r'(?:计算|预测|评估|分析|'
    r'(?:calculate|compute|predict|assess|evaluate|analyse|analyze)(?![A-Za-z0-9_-]))'
    r'[ \t]*', re.I,
)
_ANALYSIS_ADMET = re.compile(r'(?:ADMET|ADME)(?![A-Za-z0-9_-])', re.I)
_ANALYSIS_PROPERTY = re.compile(
    r'(?:分子量|分子性质|分子属性|理化性质|性质|属性|类药性|成药性|'
    r'(?:LogP|TPSA|HBD|HBA|QED|Lipinski|molecular[ \t]+weight|'
    r'properties|property|drug(?:-|[ \t]+)likeness)(?![A-Za-z0-9_-]))', re.I,
)
_ANALYSIS_ACTIVITY = re.compile(
    r'[ \t]*(?:的[ \t]*)?(?:活性|'
    r'(?:activity|potency|pIC50|IC50)(?![A-Za-z0-9_-]))', re.I,
)
_ANALYSIS_JOIN = re.compile(
    r'[ \t]*(?:和|及|与|并|、|[,，]|(?<![A-Za-z0-9_-])and(?![A-Za-z0-9_-]))[ \t]*',
    re.I,
)


def _analysis_item(query: str, position: int, end: int) -> tuple[int, str] | None:
    for pattern, role in ((_ANALYSIS_ADMET, 'admet'), (_ANALYSIS_PROPERTY, 'property')):
        if match := pattern.match(query, position, end):
            return match.end(), role
    target = TARGET_PATTERN.match(query, position, end)
    if target is not None:
        activity = _ANALYSIS_ACTIVITY.match(query, target.end(), end)
        if activity is not None:
            return activity.end(), 'activity'
    return None


def _analysis_clause(query: str, start: int, end: int) -> bool:
    head = _ANALYSIS_HEAD.match(query, start, end)
    if head is None:
        return False
    position = head.end()
    has_admet = has_activity = False
    while position < end:
        item = _analysis_item(query, position, end)
        if item is None:
            return False
        position, role = item
        has_admet = has_admet or role == 'admet'
        has_activity = has_activity or role == 'activity'
        position = _ANALYSIS_SPACE.match(query, position, end).end()
        if position < end and query[position] in '.!?。！？':
            position = _ANALYSIS_SPACE.match(query, position + 1, end).end()
            return position == end and has_admet and has_activity
        if position == end:
            return has_admet and has_activity
        join = _ANALYSIS_JOIN.match(query, position, end)
        if join is None:
            return False
        position = join.end()
    return False  # No item after an action or a trailing join.


def _analysis_clause_intervals(query: str) -> tuple[tuple[int, int], ...]:
    intervals = []
    start = 0
    for boundary in _ANALYSIS_BOUNDARY.finditer(query):
        end = boundary.start()
        if _analysis_clause(query, start, end):
            intervals.append((start, end))
        start = boundary.end()
    return tuple(intervals)
```

- [ ] Replace ONLY the following preamble of the existing `_IDENTIFIER` loop.
  Keep `members = [match[1]]` and all existing code after it unchanged, including
  the combined `starts` tuple and its full `_FOLLOWING` traversal.

```python
    list_matches = []
    list_end = 0
    analysis_intervals = _analysis_clause_intervals(query)
    interval_index = 0
    for match in _IDENTIFIER.finditer(query):
        while (interval_index < len(analysis_intervals)
               and analysis_intervals[interval_index][1] <= match.start()):
            interval_index += 1
        if (interval_index < len(analysis_intervals)
                and analysis_intervals[interval_index][0] <= match.start()
                < analysis_intervals[interval_index][1]):
            continue
        if match.start() < list_end or not _identifier_like(match[1]):
            continue
        members = [match[1]]
```

No change to `target_mentions(query)`, `label_matches`, `for_matches`,
`TARGET_PATTERN.finditer(query)`, `explicit`, `_QUALIFIED.search(query)` or
unknown deduplication. A second `target:ADMET` outside the recognized clause must
remain unknown. Exact PDE5A/PDE4A identities cannot be reduced to PDE family.
Intervals cost O(query length) time and space; each item advances its cursor;
the consumer cursor advances at most once per interval. No suffix slicing,
rescan from a later action, or token-by-interval nested search is introduced.

- [ ] Run the complete new test file, using the same authorized launcher. Expect
  all public tests to pass. Investigate mismatches before altering any expectation;
  a new grammar need requires design approval, not broader cleanup.

```powershell
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -I -S -B scratch/ordinary_chat_offline_runner.py tests/agent/test_target_analysis_phrase.py
```

## Task 3 — complete-consumption and deterministic scan guards

**Files:** Append to `tests/agent/test_target_analysis_phrase.py` only.

- [ ] Add the following negative recognition, exact-span and growth tests. These
  are strengthened regression guards, not claimed historical RED for new helpers.
  Public behavior RED remains Task 1. Deliberately malformed analytical text
  declining recognition does not imply every such text is a journal rejection.

```python
@pytest.mark.parametrize('query', [
    'ADMET 和 PDE5A 活性',  # No action.
    '提示 评估 ADMET 和 PDE5A 活性',  # No forward search past unknown text.
    '请 please assess ADMET and PDE5A activity',  # At most one politeness prefix.
    'assessments ADMET and PDE5A activity',
    '评估 QED 和 PDE5A 活性',  # Required ADME(T) absent.
    '评估 ADMET 和 PDE5A',  # Required activity noun absent.
    '评估 ADMET PDE5A 活性',  # Whitespace is not a join.
    '评估 ADMET 和和 PDE5A 活性',
    '评估 ADMET 和 PDE5A 活性 和',
    '评估 ADMET 和 PDE5A 活性 extra',
    '评估 ADMET 和 PDE5A 活性。。',
    '评估 ADMET2 和 PDE5A 活性',
    '评估 ADMET 和 UNKNOWN42 活性',
    '评估 ADMET 和 PDE5AX 活性',
    '评估 ADMET 和 PDE5A_extra 活性',
    'assess ADMET and PDE5A activityXYZ',
    'assess ADMET androgen PDE5A activity',
    'assess ADMET or PDE5A activity',
    '评估 ADMET / PDE5A 活性',
    '评估 ADMET 和 target: PDE5A 活性',
    'assess ADMET and for PDE5A activity',
    'assess ADMET and against PDE5A activity',
    '评估 ADMET 和 PDE5A 活性 SMILES: CCO',
    '评估 ADMET 和 PDE5A 活性，SMILES: CCO',
    '评估 ADMET 和 PDE5A 活性\nextra',  # Tested separately below: first clause valid.
])
def test_incomplete_or_unknown_text_cannot_create_full_query_interval(query):
    intervals = boundary._analysis_clause_intervals(query)
    if query.endswith('\nextra'):
        assert intervals == ((0, query.index('\n')),)
    else:
        assert intervals == ()


@pytest.mark.parametrize('separator', [';', '；', '\n', '\r\n'])
def test_only_complete_clause_span_is_published(separator):
    clause = ' 请评估 QED 和 ADMET 和 PDE5A 活性！\t'
    prefix = 'SMILES: CCO' + separator
    query = prefix + clause + separator + 'target: ADMET'
    assert boundary._analysis_clause_intervals(query) == ((len(prefix), len(prefix) + len(clause)),)
    request = boundary.analyze_target_request(query)
    assert request.targets == ('PDE5A',)
    assert request.unknown == ('ADMET',)
    assert request.explicit and request.needs_clarification


@pytest.mark.parametrize('query, unknown', [
    ('评估 ADMET 和 PDE5A', ('ADMET',)),
    ('评估 QED 和 ADMET 和 PDE5A 活性 extra', ('QED', 'ADMET')),
    ('评估 ADMET 和 PDE5A 活性，SMILES: CCO', ('ADMET',)),
    ('评估 ADMET2 和 PDE5A 活性', ('ADMET2',)),
])
def test_declined_clause_retains_original_anchored_list_unknowns(query, unknown):
    assert boundary._analysis_clause_intervals(query) == ()
    request = boundary.analyze_target_request(query)
    assert request.targets == ('PDE5A',)
    assert request.unknown == unknown
    assert request.needs_clarification


@pytest.mark.parametrize('count', [16, 32, 64])
@pytest.mark.parametrize('trailing_unknown', [False, True])
def test_scanner_and_interval_cursor_have_linear_operation_growth(monkeypatch, count, trailing_unknown):
    counters = dict(items=0, intervals=0, reads=0)
    original_item = boundary._analysis_item
    original_intervals = boundary._analysis_clause_intervals

    def counted_item(*args):
        counters['items'] += 1
        return original_item(*args)

    class CountedIntervals:
        def __init__(self, values):
            self.values = values

        def __len__(self):
            return len(self.values)

        def __getitem__(self, index):
            counters['reads'] += 1
            return self.values[index]

    def counted_intervals(query):
        counters['intervals'] += 1
        return CountedIntervals(original_intervals(query))

    monkeypatch.setattr(boundary, '_analysis_item', counted_item)
    monkeypatch.setattr(boundary, '_analysis_clause_intervals', counted_intervals)
    long_clause = '评估 ' + ' 和 '.join(['QED'] * count + ['ADMET', 'PDE5A 活性'])
    if trailing_unknown:
        long_clause += ' extra'
    query = ';'.join(['评估 QED 和 ADMET 和 PDE5A 活性'] * count + [long_clause])
    request = boundary.analyze_target_request(query)
    assert request.targets == ('PDE5A',)
    assert request.unknown == (('QED', 'ADMET') if trailing_unknown else ())
    assert counters['intervals'] == 1
    assert counters['items'] == 4 * count + 2
    token_count = sum(1 for _ in boundary._IDENTIFIER.finditer(query))
    assert counters['reads'] <= 4 * token_count + 2 * (count + 1) + 8
```

The counters wrap real helpers and interval reads without changing their
outputs, clocks or the runner profiler. Item-call counts demonstrate deterministic
forward progress; the read bound rejects a token-times-interval search. Source
review must additionally inspect regexes for bounded alternatives and absence of
nested unbounded repetition; operation counts alone are not a proof of regex
complexity. Existing `_FOLLOWING` operation-bound tests remain unchanged.

- [ ] Run the new file again, then the exact ordered regression union below.
  Preserve each existing test's assertions. Expected: all pass without skips;
  report actual counts rather than inventing totals. Wait for each terminal before
  the next run. No live models, service discovery or changed timeout is required.

```powershell
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -I -S -B scratch/ordinary_chat_offline_runner.py tests/agent/test_target_analysis_phrase.py
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -I -S -B scratch/ordinary_chat_offline_runner.py tests/agent/test_target_analysis_phrase.py tests/agent/test_target_identity_alignment.py tests/agent/test_target_selection_phrase.py tests/agent/test_target_selection_execution.py tests/agent/test_hybrid_router.py tests/agent/test_routing_prompt_matrix.py tests/agent/test_task_planner.py tests/agent/test_planner_responsibilities.py tests/agent/test_planner_step_templates.py tests/agent/test_planner_template_execution.py tests/agent/test_react_agent_workflow_routing.py
```

## Task 4 — review, freeze and separate integration gates

**Files:** Evidence in this plan only; no other implementation file is authorized.

- [ ] Inspect the production diff: only the new private recognizer and the
  `list_matches` preamble changed. Check the actual query is never reassigned,
  analytical words were not added to `_ACTION_WORDS`, and all existing scans,
  target vocabulary, identities and candidate-selection code are intact.
- [ ] Record production/test/runner SHA256 before and after the exact union,
  terminal handles, durations, exit statuses, warnings and every intermediate
  failure. Confirm the approved runner differs from its donor only at literal
  `REPO`; retain its parent-approved normalized-source comparison. An absent
  provisioned runner is a gate, not permission for raw pytest/import probes.
- [ ] Release the scientific slot after terminal. Obtain independent SOURCE
  then fresh QUALITY on the same file freeze, with an independent exact union
  only after exclusive-slot grant. Exact-head CI and inspected integration remain
  parent-owned mandatory gates; this worker does not commit, push or merge.
- [ ] Require a **separate later journal integration gate** after exact donor
  admission integration. It must carry the unchanged request through an empty
  initial B journal closure, preserving the original query, both molecules and
  all four obligations. It must also run existing invalid-whole-SMILES,
  conflicting-clarification and journal-tamper regressions from that integrated
  source. Those modules are not present at this draft baseline and must not be
  copied into this parser task or replaced with parser-output assertions.
- [ ] Require a **separate later Task6 Web gate** in the authorized Task6 tree:
  actual observation-driven property→whole-batch ADMET/likeness/activity, changed
  real property output changing the next HTTP decision, actual IDs into SQLite,
  and honest unavailable activity. Parser unit green is not that Web gate. After
  parent integrates the reviewed correction and re-grants the slot, the existing
  prepared command there is:

```powershell
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -I -S -B scratch/ordinary_chat_offline_runner.py tests/agent/test_b1_normal_web_runtime.py::test_b1_scientific_dataflow_is_observation_driven
```

This last command is **not for the parser worktree**, is not authorized to run
now, and does not replace Task6's exact-four union or remaining reference/report,
publication and frontend controls. Its two native-wire receive timeouts remain
independently unresolved; no parser change is claimed to fix either timeout.

The journal's separate unknown-only/qualified-request asymmetry remains outside
this production boundary. Do not introduce wholesale `needs_clarification`
rejection into targetless property calculations. Parser preservation, actual Web
admission rejection and journal rejection require distinct evidence.

## Draft self-review and handoff

- [x] One production file; one new test file; no shared-test edits planned.
- [x] Full intervals, QED/ADMET both orders and uppercase LOGP covered.
- [x] Complete algorithm and private helper signatures given; bounded scanner
  preserves original explicit labels, `for`, known mentions and qualifications.
- [x] Unknowns, partial clauses, labels inside clauses, SMILES outside exemption,
  multiple target identities, Router/Planner guards and operation growth mapped
  to explicit tests; unchanged regression modules inspected for actual paths.
- [x] Separate journal and Task6 Web gates retained; no global whitelist or query rewrite.
- [x] Documentation-only drafting; no test result, production implementation,
  runner change, implementation-source approval, CI success or live capability claimed.
- [x] Banach design SOURCE approval reported by parent; final validation wording
  read at `804be52`, requiring only actual failing variants to supply RED.
- [ ] Parent implementation-plan approval and implementation/slot release.

Recommended execution is inline with `executing-plans` after parent release,
using the existing review checkpoints. This recommendation is not a dispatch or
permission to start. Package7/package8 and deployment remain incomplete/closed.
