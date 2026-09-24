# ADMET Unknown Evidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove unsupported ADMET conclusions while retaining real scientific calculations, evidence and status mechanisms throughout the parent's packages 1–8.

**Architecture:** Keep the existing properties route and ADMET producer; remove the route's unsupported five-label predictor and make unknowns explicit. Correct only local ADMET presentation and uncomputed alerts, then narrowly align the reviewed 4A validation view with those three nullable leaves. No new scientific algorithm, model, generic adapter or scoring changes.

**Tech Stack:** Existing Python/FastAPI, RDKit, optional existing adme_py, pytest, Pydantic 2 in reviewed 4A; no new dependencies/providers/assets.

---

## Authorization and prerequisites

**Written design and plan accepted; reviewed 4A integrated; bounded TDD authorized.** Parent supplied PR67's merged baseline and authorized origin/main integration followed by this plan, with **no commits after implementation or push**. Implementation is now local/uncommitted; focused and minimum-profile tests pass. Full Agent/full-repository tests await the parent's heavy slot. Independent implementation review is outstanding. Do not repeat accepted choice questions. Parent packages 1–8 retain their own deliverables and scientific-truth invariant; the eight local tasks below do not replace those packages.

Spec: `docs/superpowers/specs/2026-09-25-admet-unknown-evidence-design.md`.

Branch/original source-inspection base: `codex/admet-unknown-evidence` / `0e54a1e1038dbf51ea0462ba79c14df91ba13161`. PR64 merged at `ecd6cca`; PR67 merged at `1bba0256409a06317486530e5c1cfa6598b8e381`, tree `71b1a064862faa8f0711d5e74e1faf67386ffedd`, reviewed head `9f3ce84`. Parent reports latest CI8/8, unresolved0. Local origin/main/tree verification passed. Integration merge `6719b129ddfc431f375bab667bca2476345f6151` precedes all implementation; only the newer origin/main handoff status was selected in one documentation conflict. No cherry-pick duplication and no production conflicts. The earlier inspected sibling snapshot remains historical provenance, not the execution base.

- [x] Parent reviewed and accepted the written spec and plan, including endpoint sibling metadata, conservative dictionary rule interpretation and metadata-only row exclusion. Preserve available source/method/failure diagnostics when excluding those rows.
- [x] Parent confirmed PR64 landing at `ecd6cca`.
- [x] Parent supplied merged reviewed PR67; integrate origin/main into the clean branch before TDD, without cherry-picks or main edits.
- [x] Re-read applicable AGENTS, working status, the two production modules, reviewed `analysis_contract.py` and its tests; reconcile changed method signatures before touching code.
- [x] Focused/minimum runs use the approved isolated environment, temporary runtime directories, in-memory RDKit and frozen framework profiles. No package installation, real-service activation or scientific assets. Final runs additionally block socket connections except Windows asyncio's internal socketpair.

## Future file map

| File | Responsibility |
|---|---|
| `src/web/routes/molecule_properties_routes.py` | Existing route: truthful five-label unavailability only; retain real basics/HTTP/errors. |
| `src/agent/tools/admet_predictor.py` | Three uncomputed nulls, SA method annotation, local display/interpretation and no-assessment row inclusion. Preserve all scientific expressions. |
| `src/agent/tooling/analysis_contract.py` (reviewed 4A prerequisite) | Exactly three bool-or-null alert validators; no generic adapter/lifecycle changes. |
| `tests/test_molecule_properties_unknown.py` (new) | Isolated route's Unknown metadata, real basics and error characterization. |
| `tests/test_admet_unknown_evidence.py` (new) | Real RDKit invariance, tri-state display, rules, solubility, sparse backend, types, no mutation/no assessment. |
| `tests/test_api_route_boundary.py` | Replace only the intentionally obsolete heuristic-label assertion in this behavior batch. |
| `tests/agent/test_analysis_contract.py` (reviewed 4A prerequisite) | Narrow null acceptance and unchanged required/type/finite/status boundaries. |

Existing fallback/whole-input/domain/lifecycle/scientific/ranker tests are regression targets, not permission to edit their expectations. No edits to `main.py`, generic `adapters.py`, `base_tool.py`, factory, score/ranker, model/provider configuration, frontend or scientific assets. Algorithms, thresholds and numerical outputs are immutable in this scope; do not make bool globally nullable or convert partial rule details into a completed pass. No new docs outside the two approved documents during this phase. The present authorization permits one documentation-only local commit; future implementation commits/publication are not authorized by that permission, and there are no automatic implementation commit steps below.

## Task 1: Characterize preserved calculations and endpoint errors

**Files:** new `tests/test_molecule_properties_unknown.py`, new `tests/test_admet_unknown_evidence.py`; read existing fallback, whole-input and route-boundary tests.

- [ ] Add an isolated route fixture that registers only the actual domain route; do not import global app/lifespan or launch services.

```python
from types import SimpleNamespace
from unittest.mock import Mock
from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.web.routes.molecule_properties_routes import setup_molecule_properties_routes

def client_for_properties():
    app = FastAPI()
    setup_molecule_properties_routes(app, _support=SimpleNamespace(logger=Mock()))
    return TestClient(app)
```

- [ ] Characterize real basic values with CCO and aspirin by calling the same RDKit descriptor APIs independently in the test (not calling the route to build expected output). Preserve formulas/units/rounding and HTTP 200 on valid requests. Characterize `{}`, `{"smiles": ""}`, `{"smiles": "C1CC"}`, `{"smiles": 7}`, missing body and malformed JSON against the existing route; freeze actual error keys and status, not assumed 400s. Use the frozen full-registration test for body/schema behavior. These preservation tests should pass before changes.

```python
import pytest
from rdkit import Chem
from rdkit.Chem import Crippen, Descriptors, Lipinski, QED

@pytest.mark.parametrize("smiles", ["CCO", "CC(=O)Oc1ccccc1C(=O)O"])
def test_basic_values_are_actual_rdkit(smiles):
    mol = Chem.MolFromSmiles(smiles)
    with client_for_properties() as client:
        response = client.post("/api/molecule/properties", json={"smiles": smiles})
    body = response.json()
    assert response.status_code == 200 and body["success"] is True
    assert body["smiles"] == smiles
    assert body["properties"]["basic"] == {
        "molecular_weight": round(Descriptors.MolWt(mol), 2),
        "logp": round(Crippen.MolLogP(mol), 2),
        "hbd": Lipinski.NumHDonors(mol), "hba": Lipinski.NumHAcceptors(mol),
        "tpsa": round(Descriptors.TPSA(mol), 2),
        "rotatable_bonds": Lipinski.NumRotatableBonds(mol),
        "qed": round(QED.qed(mol), 3),
    }
```

- [ ] Add a preservation test for `_predict_admet_with_rdkit` using CCO/aspirin: independently calculate every descriptor/count and the existing formulas shown in spec section 5. Compare floats with tight `pytest.approx`, exact formulas/counts/labels/version, and unrounded raw values. Exclude only the three intentionally changed alert leaves and new method annotation from old-vs-new value equality. Test both real RDKit method and `execute` with `ADME_PY_AVAILABLE=False`.
- [ ] Run `python -B -m pytest tests/test_molecule_properties_unknown.py tests/test_admet_unknown_evidence.py tests/test_admet_predictor_fallback.py tests/agent/test_admet_whole_input.py -q -p no:cacheprovider`. Expected at this preservation stage: PASS; missing RDKit is an environment blocker, not permission to replace actual calculations with fake values.

## Task 2: Endpoint labels become unavailable (separate from PR64)

**Files:** route, new endpoint test, `tests/test_api_route_boundary.py`.

- [ ] Add the following red test; the present source returns legacy High/Low/Good/Moderate labels, so this must fail for the intended assertion.

```python
def test_endpoint_admet_is_unknown_without_calling_a_predictor(monkeypatch):
    import src.agent.tools as tools
    def forbidden():
        pytest.fail("properties route must not invoke ADMET")
    monkeypatch.setattr(tools, "ADMETPredictor", forbidden)
    with client_for_properties() as client:
        response = client.post("/api/molecule/properties", json={"smiles": "CCO"})
    body = response.json()
    assert response.status_code == 200 and body["success"] is True
    assert body["properties"]["admet"] == dict.fromkeys((
        "bbb_penetration", "cyp_inhibition", "hepatotoxicity",
        "solubility", "bioavailability"), "Unknown")
    assert body["properties"]["admet_metadata"] == {
        "availability": "unavailable", "method": "not_calculated",
        "warning": "ADMET未计算；本接口仅计算基础理化性质，不能据此判断毒性、CNS安全性或体内表现。",
    }
```

- [ ] Run `python -B -m pytest tests/test_molecule_properties_unknown.py -q -p no:cacheprovider`; record RED. Also test an ADMET constructor that raises a normal exception and one returning an object with the obsolete private method: neither may be invoked after the fix.
- [ ] Replace the route's entire ADMET import/probe/fallback block with the following assignments. Leave basic calculations, try/except, route signature, function name, HTTP and return envelope untouched.

```python
properties['admet'] = dict.fromkeys((
    'bbb_penetration', 'cyp_inhibition', 'hepatotoxicity',
    'solubility', 'bioavailability'), 'Unknown')
properties['admet_metadata'] = {
    'availability': 'unavailable',
    'method': 'not_calculated',
    'warning': 'ADMET未计算；本接口仅计算基础理化性质，不能据此判断毒性、CNS安全性或体内表现。',
}
```

- [ ] In `test_api_route_boundary.py`, rename `test_historical_properties_heuristic_is_not_scientific_validation` to `test_properties_endpoint_reports_unknown_admet_after_behavior_fix`. Keep its same registration path, request, HTTP and success-envelope checks; change the exact five-value expectation to Unknown and add the exact metadata expectation above. Revise the comment to explain this approved post-PR64 behavior change. Do not regenerate or edit `tests/fixtures/api_route_contract*.json`.
- [ ] Run `python -B -m pytest tests/test_molecule_properties_unknown.py tests/test_api_route_boundary.py -q -p no:cacheprovider`. Expected: GREEN, unchanged registration/OpenAPI and negative-input behavior. Unknown dependency-profile failure must remain visible; do not relax the frozen version selector.

## Task 3: Real RDKit unknown alerts and SA method provenance

**Files:** ADMET producer and new ADMET test.

- [ ] Add a red producer test (not a hand-built substitute for RDKit output).

```python
from src.agent.tools.admet_predictor import ADMETPredictor

def test_rdkit_uncomputed_alerts_and_sa_identity():
    row = ADMETPredictor()._predict_admet_with_rdkit("CCO")
    assert row["prediction_method"] == "rdkit_rules"
    assert row["backend_version"]
    for key in ("pains", "brenk", "zinc"):
        assert row["medicinal"][key] is None
    assert row["medicinal"]["synthetic_accessibility"] == pytest.approx(1.12)
    assert row["medicinal"]["synthetic_accessibility_method"] == "rdkit_complexity_heuristic"
    assert row["druglikeness"]["ghose"] == {}
    assert row["medicinal"]["leadlikeness"] == {}
```

- [ ] Run the single test with `python -B -m pytest tests/test_admet_unknown_evidence.py::test_rdkit_uncomputed_alerts_and_sa_identity -q -p no:cacheprovider`; expected RED at the hard-coded false alert.
- [ ] In the RDKit return literal, change only `pains/brenk/zinc` from False to None and add `"synthetic_accessibility_method": "rdkit_complexity_heuristic"` next to SA. No formula, count, score, range, label threshold, backend selection or version change.
- [ ] Re-run Task 1 invariance tests and this test. Expected GREEN for values; typed 4A acceptance will be fixed narrowly in Task 7 before this batch can ship.

## Task 4: Tri-state display, rules and method labels

**Files:** ADMET producer's formatters and new ADMET test.

- [ ] Add red parametrized tests for all three alerts with `True`, `False`, `None`, absent key, `0`, `1`, `"false"`, `"true"`, `[]`, `{}`. Read the individual formatted line, not a global occurrence of “未知”. Expected alert line: true `有（后端报告）`; false `无（后端报告；不等于安全）`; other values `未知（未计算或无有效结果）`. Check BBB true/false/unknown separately with no CNS conclusion.
- [ ] Add tests over `_format_ghose_results_cn`, `_format_ghose_results`, `_format_leadlikeness_cn`, `_format_leadlikeness`: `{}`, `None`, empty/unrecognized labels, mappings with missing/unknown members must not become pass/fail by default. A map such as `{"MW": "outside range"}` retains supplied failure detail; `{"MW": "within range"}` is not complete positive evidence. Exact scalar Pass/Fail/Warning may be presented by helpers without changing the raw dictionary schema.
- [ ] Run `python -B -m pytest tests/test_admet_unknown_evidence.py -q -p no:cacheprovider`; verify failures are default-negative/default-pass/missing-method defects.
- [ ] Introduce small local presentation helpers, not generic schema/adapter logic. Use strict identity checks as below; never `bool(value)` or `.get(key, False)`. Apply to the report and every narrative consumer. Add a method banner using unchanged method/version, and method-specific SA label; for adme_py with no SA method state that its method was not supplied.

```python
def _alert_text(value):
    if value is True:
        return '有（后端报告）'
    if value is False:
        return '无（后端报告；不等于安全）'
    return '未知（未计算或无有效结果）'

def _bbb_text(value):
    if value is True:
        estimate = '该方法估计可透过'
    elif value is False:
        estimate = '该方法估计不易透过'
    else:
        estimate = '未知（未计算或无有效结果）'
    return estimate + '；不能据此判断CNS活性或副作用风险'
```

- [ ] Replace empty/no-failure dictionary blanket passes with unknown/partial-detail reporting; preserve actual failure details. For Lipinski/Veber use explicit recognized labels, otherwise unknown. RDKit report/interpretation must say `RDKit-rule` and `非训练模型预测、非实验结果`; SA must say local complexity heuristic, not assert actual ease of synthesis. Existing numeric SA bins may only be described as heuristic bins, never validated synthetic feasibility.
- [ ] Re-run the tests and the original fallback test. Expected GREEN with all real values retained. No contract relaxation for null BBB or dictionary types is part of this step.

## Task 5: Exact solubility categories and evidence-bounded narratives

**Files:** the three narrative generators plus report translations; new ADMET test.

- [ ] Add this red regression, plus every class in the exact mapping and empty/unrecognized classes. Run report, comprehensive assessment, interpretation and brief reasoning on the same row. For no assessment, all narrative generators must explicitly say “未形成可解释的ADME评估；缺失项目未知。” rather than pass/fail/completed/success by default.

```python
@pytest.mark.parametrize("label", ["Poorly Soluble", "Very Poorly Soluble", "Insoluble"])
def test_poor_solubility_never_becomes_favorable(label):
    tool = ADMETPredictor()
    props = {
        "prediction_method": "adme_py", "backend_version": "fixture-only",
        "physicochemical": {}, "solubility": {"class_esol": label},
        "lipophilicity": {}, "pharmacokinetics": {},
        "druglikeness": {}, "medicinal": {},
    }
    texts = [tool.format_admet_result("CCO", props),
             tool._generate_comprehensive_assessment(props),
             tool._generate_interpretation("CCO", props),
             tool._generate_brief_reasoning(props)]
    for text in texts:
        assert "具有良好的水溶性" not in text
        assert "优秀的水溶性" not in text
        assert "CNS副作用风险较低" not in text
        assert "不太可能产生CNS副作用" not in text
```

- [ ] Run the new test; expect RED because the old substring branch claims good solubility.
- [ ] Replace all substring decisions with one local exact-category table. For display only, accept stripped/case-normalized strings; unknown nonstrings return unknown without touching raw values. Use the same semantics across narrative surfaces; brief reasoning may omit solubility but cannot contradict it.

```python
_SOLUBILITY_LABELS = {
    'very soluble': '高溶解性', 'highly soluble': '高溶解性',
    'soluble': '中等溶解性', 'moderately soluble': '中等溶解性',
    'poorly soluble': '低溶解性', 'very poorly soluble': '极低溶解性',
    'insoluble': '难溶',
}

def _solubility_text(value):
    key = value.strip().lower() if isinstance(value, str) else ''
    return _SOLUBILITY_LABELS.get(key, '未知')
```

- [ ] Remove BBB-derived CNS activity/safety statements for BOTH bool values and missing values. Method-qualify GI/solubility descriptions; missing Lipinski/Veber is unknown, not a warning of failure. Make empty narratives return the exact no-assessment sentence. Update execute's message/reasoning prefixes from universal “成功预测” to “已返回…已计算性质/规则估计；缺失项目未知” when data exist, without changing success/status policy for those real data.
- [ ] Run `python -B -m pytest tests/test_admet_unknown_evidence.py tests/test_admet_predictor_fallback.py -q -p no:cacheprovider`; expected GREEN across all classes and narrative surfaces.

## Task 6: Sparse backend, finite/type handling and no fabricated completion

**Files:** ADMET local helpers, row inclusion in `execute`; new ADMET tests. No generic validation or lifecycle edits.

- [ ] Add red tests over each pure formatter/narrative for missing sections, `{}` sections, `None` sections and wrong containers. Use `deepcopy` before calls and assert deep equality after calls. For scalar numeric leaves test `None`, absent, `True`, `False`, `"1.2"`, `nan`, `inf`, `-inf`, `0`, valid negative WLogP/LogS/LogKp; count leaves additionally test `-1` and `1.5`; SA test `0`, `11`, valid `1`/`10`. Unknown type/finite cases must not yield classifications or raise. Pure display tolerance must not replace invalid raw numbers with None.
- [ ] Add a controlled `adme_py` stub using the same `ADME.calculate()` injection style as `test_adme_py_success_records_backend_method_and_package_version`; it is a contract fixture, not scientific evidence. Assert partial molecular-weight-only backend output returns its exact 46.07 and method/version, while BBB/alerts/rules remain unknown and no CNS/good-solubility claim appears. Repeat with explicit `blood_brain_barrier_permeant: False`, `pains: False`, zero valid values; explicit negatives/zero must count as observations.
- [ ] Add tests where backend sections are all empty, only version/method exist, all known leaves are unknown markers, or only unknown extensions exist. After calling `execute("CCO")`, expect no fabricated calculated row: `success is False`, `data is None`, empty formatted output, and a no-calculated-results explanation that does not diagnose valid CCO as invalid. Seed available source/method/version, safe failure reason and warnings in controlled fixtures and assert they survive the returned failure, `execute_tool_compat` normalization and reviewed 4A's failed observation/snapshot. Existing diagnostics must not be overwritten by the generic explanation or remain only in a discarded helper return. Missing diagnostics remain missing; do not invent them. Keep no-backend and backend-exception behavior under existing mechanisms.
- [ ] Run the focused tests; expected RED for missing-section access, default WLogP zero, nonfinite classifications and metadata-only success.
- [ ] Implement local read-only section/numeric helpers. Do not coerce string/bool into a number and do not clamp any raw result. The core display predicates are:

```python
import math

def _section(props, name):
    value = props.get(name)
    return value if isinstance(value, dict) else {}

def _finite_number(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False

def _count(value):
    return type(value) is int and value >= 0
```

- [ ] Add a producer-local `_has_observations(admet_props)` presence predicate, called before appending a row or setting success. Its exact allowlist is the existing leaves in the six producer sections, excluding metadata/extensions. Use finite numerics for MW/MR/TPSA/WLogP/LogS/LogKp; [0,1] for fraction CSP3; nonnegative finite solubility; nonnegative integer counts; [1,10] SA; nonempty non-unknown string formula; strict bool BBB and alerts; recognized GI/solubility/Pass-Fail-Warning labels; recognized nonempty supplied Ghose/lead detail observations. Empty/unrecognized details alone do not count. Do not recompute any property, turn bad supplied leaves into valid values, or broaden 4A. Integration into the existing loop is restricted to its condition:

```python
if admet_props and self._has_observations(admet_props):
    calculated_results.append({'smiles': smiles, 'admet': admet_props})
    formatted_outputs.append(self.format_admet_result(smiles, admet_props))
```

The proposed local predicate uses the helpers above and existing `re` import; add it as a static method of `ADMETPredictor`. This tests presence of a usable reported observation, not whole-row validity, molecular safety or model authenticity. Whole-row typed rejection stays with reviewed 4A.

```python
@staticmethod
def _has_observations(props):
    if not isinstance(props, dict):
        return False
    sections = {name: _section(props, name) for name in (
        'physicochemical', 'solubility', 'lipophilicity',
        'pharmacokinetics', 'druglikeness', 'medicinal')}
    phys = sections['physicochemical']
    formula = phys.get('formula')
    if isinstance(formula, str) and formula.strip().lower() not in (
            '', 'unknown', '未知', 'n/a'):
        return True
    numbers = {
        'physicochemical': ('molecular_weight', 'molar_refractivity', 'tpsa'),
        'solubility': ('log_s_esol',),
        'lipophilicity': ('wlogp',),
        'pharmacokinetics': ('skin_permeability_logkp',),
    }
    if any(_finite_number(sections[section].get(key))
           for section, keys in numbers.items() for key in keys):
        return True
    if any(_count(phys.get(key)) for key in (
            'num_heavy_atoms', 'num_aromatic_atoms', 'num_rotatable_bonds',
            'num_h_donors', 'num_h_acceptors')):
        return True
    for section, key, low, high in (
        ('physicochemical', 'sp3_carbon_ratio', 0, 1),
        ('solubility', 'solubility_esol', 0, float('inf')),
        ('medicinal', 'synthetic_accessibility', 1, 10),
    ):
        value = sections[section].get(key)
        if _finite_number(value) and low <= value <= high:
            return True
    if type(sections['pharmacokinetics'].get('blood_brain_barrier_permeant')) is bool:
        return True
    if any(type(sections['medicinal'].get(key)) is bool
           for key in ('pains', 'brenk', 'zinc')):
        return True
    for section, key, allowed in (
        ('solubility', 'class_esol', set(_SOLUBILITY_LABELS)),
        ('pharmacokinetics', 'gastrointestinal_absorption',
         {'high', 'low', 'medium', 'moderate'}),
        ('druglikeness', 'lipinski', {'pass', 'fail', 'warning'}),
        ('druglikeness', 'veber', {'pass', 'fail', 'warning'}),
    ):
        value = sections[section].get(key)
        if isinstance(value, str) and value.strip().lower() in allowed:
            return True
    for section, key in (('druglikeness', 'ghose'), ('medicinal', 'leadlikeness')):
        details = sections[section].get(key)
        if isinstance(details, dict) and any(
                isinstance(value, str)
                and re.search(r'\b(?:within|outside)\b', value, re.I)
                for value in details.values()):
            return True
    return False
```

- [ ] Keep backend mapping/source data unchanged, including its six dictionaries and opaque extensions. Before excluding a metadata-only row, retain its already available safe source, prediction method, backend version, warnings and failure reason in the producer's existing failure-envelope metadata channels (`quality`, `warnings`, valid `provenance`), which compatibility normalization already carries into failure results and snapshots. Do not add a generic adapter or put an unassessed row into successful scientific data. Add “未获得可用的ADME计算结果；未形成评估。” and “后端未返回可用观测，不能据此判断结构无效或分子安全。” as no-assessment explanation; do not replace any existing failure reason/warnings with those generic sentences. Keep the false/None return mechanism, do not fabricate absent source/reasons and do not expose private exception payloads. Existing parser failures remain their distinct path. No new partial-state classifier, batch alignment algorithm or retry behavior.
- [ ] Re-run focused tests plus `tests/agent/test_admet_whole_input.py` and `tests/agent/test_domain_result_validators.py`. Expected GREEN. Sparse raw malformed leaves must still be rejected by reviewed 4A in Task 7 even if pure formatting handles them safely.

## Task 7: Narrow nullable alert integration with reviewed 4A

**Files:** reviewed `src/agent/tooling/analysis_contract.py`, `tests/agent/test_analysis_contract.py`. Block this task until the reviewed dependency is present; do not implement another adapter.

- [ ] Add red tests using that file's actual `CountingTool`, `analysis_rows`, `build_tool_registry` and `invalid` helpers. Replace only one alert leaf with None at a time, then all three; accept raw, normalized ToolResult, partial and failed envelopes and failed `error.details.raw_result` snapshots. Compare preserved data exactly, not just success. Explicit False must survive as False, null as None.

```python
@pytest.mark.parametrize("leaf", ["pains", "brenk", "zinc"])
@pytest.mark.parametrize("value", [None, False, True])
def test_admet_alert_tristate_is_preserved(leaf, value):
    rows = analysis_rows("admet_predictor")
    rows[0]["admet"]["medicinal"][leaf] = value
    tool = CountingTool("admet_predictor", {"success": True, "data": rows})
    registry = build_tool_registry([tool])
    try:
        result = registry.resolve(tool.name).execute({"query": "CCO"})
        assert result.success
        assert result.data == rows
        assert result.data[0]["admet"]["medicinal"][leaf] is value
    finally:
        registry.close()
```

- [ ] Add rejection matrix: each RDKit alert missing still invalid; each alert `0`, `1`, `"false"`, `[]`, `{}` invalid. Null/missing BBB, numeric fields, SA, rule dictionaries and other required known RDKit leaves still invalid. Keep existing BAD_LEAVES tests unchanged and add NaN/Inf, negative counts/solubility, number strings and bool-as-number where needed. For adme_py: absent alert remains absent, explicit null alert accepted, supplied null BBB rejected, all six section dictionaries still required.
- [ ] Run `python -B -m pytest tests/agent/test_analysis_contract.py -q -p no:cacheprovider`; expected RED for nullable alert output before the schema adjustment. Do not skip real producer characterization because Task 3 now emits nulls.
- [ ] Add only the strict nullable view and replace the three mapping entries; do not change global `_BOOL`, `_fields`, `_admet` requiredness, method gate or any adapter method.

```python
_UNCOMPUTED_ALERT = TypeAdapter(bool | None)
# Replacement medicinal entry inside _ADMET_SECTIONS:
"medicinal": dict(pains=_UNCOMPUTED_ALERT, brenk=_UNCOMPUTED_ALERT,
                  zinc=_UNCOMPUTED_ALERT, synthetic_accessibility=_SA,
                  leadlikeness=_DICT),
```

The second fragment is a dictionary entry, not a standalone statement. `_fields` continues to invoke `validate_python(..., strict=True)` unchanged.

- [ ] Re-run the entire analysis-contract suite. Expected GREEN; raw/normalized/failure-snapshot equality, timeout/worker slots, redaction, caller validators, retries, provenance and close behavior unchanged. Retain `test_adme_py_sparse_known_and_extension_leaves`: it proves structural sparseness, not backend completion. Do not convert it into a universal no-evidence success rule; Task 6 tests real producer row inclusion independently.

## Task 8: Combined regression, diff audit and parent handoff

**Files:** no new implementation scope. Record actual execution evidence in this plan only if later authorized; do not state expected runs as completed runs.

- [ ] Run the focused combination in the approved isolated environment:

```powershell
python -B -m pytest tests/test_molecule_properties_unknown.py tests/test_admet_unknown_evidence.py tests/test_admet_predictor_fallback.py tests/test_api_route_boundary.py tests/agent/test_admet_whole_input.py tests/agent/test_analysis_contract.py tests/agent/test_domain_result_validators.py tests/agent/test_scientific_contracts.py tests/agent/test_real_acceptance_checks.py tests/agent/test_candidate_ranker.py -q -p no:cacheprovider
```

Expected: PASS; fixtures named “real acceptance” here test checker logic and do not authorize a real provider/model run. Explain every skip/failure. Run the supported frozen route profiles through existing offline CI, not by installing into the user's environment.

- [ ] Run `python -B -m pytest tests/agent -q -p no:cacheprovider`, then the full offline `python -B -m pytest tests -q -p no:cacheprovider` in that isolation. Preserve status/caller-validation/lifecycle tests; do not change ranker expectations to reward unknowns. If environment dependencies prevent completion, report precise scope and blockers instead of claiming full success.
- [ ] In the later implementation phase, run `python -m compileall -q src scripts`; keep generated caches out of Git. No health/deployment/real/all acceptance run is required by this non-deployment scope. No JS changed, so Node checks are not claimed. These commands are explicitly **not run in the current design-only task**.
- [ ] Inspect production diff against the reviewed integration base. Confirm unchanged scientific expressions, counts, units, rounding, method tokens, version sources, molecular input, transport/errors/status mechanisms, generic adapters and scores. Confirm only three known leaves acquired nullability. Search updated text for the former safety/success defects and inspect each occurrence, including messages passed to model reasoning.

```powershell
git diff --check
git diff --name-only
git diff -- src/web/routes/molecule_properties_routes.py src/agent/tools/admet_predictor.py src/agent/tooling/analysis_contract.py
rg -n 'CNS副作用风险较低|不太可能产生CNS副作用|可能具有CNS活性|已完成基本ADME属性分析' src/agent/tools/admet_predictor.py
git status --short
```

- [ ] Parent implementation SPEC review (distinct from the completed written-design review): confirm exact metadata, Unknown/null/false semantics, all narrative surfaces, no scientific overclaim, sparse-row distinction, retained failure diagnostics and intentional characterization update outside PR64.
- [ ] Parent QUALITY review: confirm TDD RED/GREEN evidence, unchanged numerical output and required leaves, valid/negative input coverage, reviewed-4A integration and clean file scope. No independent review is claimed until actually performed.
- [ ] Hand off branch/base, exact files, commands and counts/skips/failures, remaining CI/review dependencies. Stop before staging/commit/push/PR/merge unless separately authorized. Subsequent publication must be a distinct behavior PR, not a rewrite of PR64; parent controls main integration.

## Historical design-only verification record

Read source and tests at the recorded bases, considered three alternatives and performed spec/plan consistency review. Parent subsequently accepted the written design/plan, confirmed PR64 merged at `ecd6cca`, reported PR67's 8 passing CI checks, authorized a documentation-only local commit and instructed waiting for the merged 4A baseline. Those parent-provided facts supersede the initial pending-review/PR64-pending/no-commit state; they are not a claim of a new remote audit. Only the two permitted Markdown documents were created/updated using apply_patch. No source/test implementation or execution occurred; no provider, secret or model/asset was used. Future implementation SPEC/QUALITY approval, automated test results and 4A merge completion are not claimed. The next action after this documentation commit is to await the parent's merged 4A baseline, not to begin dependency-missing implementation.

## Execution record — local implementation, awaiting heavy slot

### Integration and boundaries

- Clean initial branch `codex/admet-unknown-evidence`, HEAD `5a4b3a8`; local origin/main equaled the supplied PR67 merge SHA/tree. `git merge --no-edit origin/main` produced one conflict in `docs/handoff/remaining-through-step8.md` (old pending PR64 text versus main's merged status). Resolved only that hunk to origin/main via apply_patch, explicitly staged that file, and completed the authorized integration merge `6719b129ddfc431f375bab667bca2476345f6151`. The merged index differed from origin/main only by this plan and its spec. No duplicate cherry-pick, no production conflict, no main branch edit.
- Tasks 1–7 implemented in the approved seven code/test paths; this plan/spec are the only additional implementation-phase documentation writes. No generic adapter, factory, scoring, algorithm, threshold, property units or source numbers changed. No implementation commit/push/PR.
- Route returns the five exact Unknown labels and sibling unavailable/not-calculated metadata without constructing ADMET. Legacy-label characterization updated only here, after PR64; frozen OpenAPI/profile fixtures unchanged.
- ADMET uses three null uncomputed alerts, RDKit-rule/SA provenance labels, strict display-only value handling, conservative partial-rule detail text, whole-label solubility interpretation and no CNS safety/activity inference. Real calculations and historical units retained without certifying the units.
- Metadata-only output fails with `data=None`, not an invented scientific row. `quality.unassessed_admet` retains supplied method/version/source/provenance/warnings/error/reason/failure_reason; missing fields are not invented. Reuses existing bounded metadata sanitization for diagnostic fields only, so safe reasons survive without exposing private payloads. Existing compatibility normalization/failure snapshots carry that diagnostic context. No generic lifecycle/status changes.
- 4A delta is exactly one strict `TypeAdapter(bool | None)` used at pains/brenk/zinc; global `_BOOL`, all other known required leaves and validation/worker lifecycle remain unchanged.

### Offline runner and reproducible path sets

Used the PowerShell-extracted runner in `docs/superpowers/plans/2026-09-24-rag-service-extraction.md`, replacing its worktree path with this worktree and output marker with `ADMET_PYTEST_EXIT`. Interpreter: existing MedChat Conda Python; `-B`, isolated temporary cwd, cleared inherited secret/config environment, isolated SQLite/cache/user-config paths, all real-service switches disabled, no dependency installation. Checked-in evaluation case files copied by that existing runner are test fixtures, not scientific assets. All pytest invocations use `-q -p no:cacheprovider --tb=short -rs`.

BASE paths: `tests/test_molecule_properties_unknown.py`, `tests/test_admet_unknown_evidence.py`, `tests/test_admet_predictor_fallback.py`, `tests/agent/test_admet_whole_input.py`.

RED paths: `tests/test_molecule_properties_unknown.py`, `tests/test_admet_unknown_evidence.py`, `tests/agent/test_analysis_contract.py`.

FOCUS paths (the exact Task 8 focused command):

```text
tests/test_molecule_properties_unknown.py
tests/test_admet_unknown_evidence.py
tests/test_admet_predictor_fallback.py
tests/test_api_route_boundary.py
tests/agent/test_admet_whole_input.py
tests/agent/test_analysis_contract.py
tests/agent/test_domain_result_validators.py
tests/agent/test_scientific_contracts.py
tests/agent/test_real_acceptance_checks.py
tests/agent/test_candidate_ranker.py
```

The last three “acceptance/science” files above test offline validation logic/fixtures, not live providers. Minimum profile prepended the **existing** temporary CI dependency target `medchat-domain-api-profiles-20260925-b831/ci` to subprocess PYTHONPATH (no install); reported/asserted FastAPI 0.104.1 / Pydantic 2.5.0. Deployment focused profile used its existing `deployment` sibling (0.115.6 / 2.10.4). Host is 0.135.3 / 2.12.5.

Final profile commands additionally replace the runner's child `-m pytest` with the following in-memory `-c` bootstrap (remaining path/options argv unchanged). This is a process-local validation guard, not a repository or generic adapter edit:

```python
import inspect, socket, sys
original_connect = socket.socket.connect
def offline_connect(self, address):
    if any(frame.function == "_fallback_socketpair"
           and frame.frame.f_globals.get("__name__") == "socket"
           for frame in inspect.stack()):
        return original_connect(self, address)
    raise AssertionError("Offline validation cannot connect to a service")
def offline_block(*args, **kwargs):
    raise AssertionError("Offline validation cannot connect to a service")
socket.socket.connect = offline_connect
socket.socket.connect_ex = offline_block
socket.create_connection = offline_block
import pydantic, fastapi
print("OFFLINE_PROFILE=" + fastapi.__version__ + "/" + pydantic.__version__)
import pytest
sys.exit(pytest.main(sys.argv[1:]))
```

### Actual RED/GREEN results (not full-suite evidence)

| Stage | Actual result | Scope/meaning |
|---|---|---|
| Pre-change preservation baseline | 63 passed, 2.00s, exit0 | BASE before adding defect assertions; actual RDKit values/counts/formulas and legacy error shapes. |
| Initial RED | 179 failed, 532 passed, 7.80s, exit1 | RED paths before production edits: endpoint constructor, unknown/pass/safety/text defects and null alert contract rejection. |
| Initial GREEN | 859 passed, 12.22s, exit0 | RED plus API-boundary, fallback, whole-input and domain-validator files. |
| Failure diagnostic privacy RED | 1 failed, 0.96s, exit1 | `test_failure_metadata_keeps_safe_reason_without_private_payload` demonstrated unsanitized synthetic diagnostic details; no real secrets. |
| Expanded host FOCUS GREEN | 943 passed, 12.72s, exit0 | Added diagnostic safety/presence/partial-rule/SA/sparse-null cases; existing bounded sanitizer reused. |
| First minimum-profile run | 2 failed, 941 passed, 1 warning, 14.32s, exit1 | New test incorrectly assumed nonempty missing-SMILES error string across frameworks; not a numerical/production failure. |
| Final minimum FOCUS, network guard | 943 passed, 3 warnings, 12.48s, exit0 | Pydantic 2.5.0 / FastAPI 0.104.1; zero skipped. |
| Final host FOCUS, network guard | 943 passed, 1 warning, 11.93s, exit0 | Pydantic 2.12.5 / FastAPI 0.135.3; zero skipped. |
| Deployment targeted, network guard | 254 passed, 3 warnings, 6.08s, exit0 | New two test files plus `test_api_route_boundary.py`, 2.10.4 / 0.115.6; zero skipped. |

Minimum-profile failure investigation followed systematic-debugging: loaded the original route source from `git show 6719b12:src/web/routes/molecule_properties_routes.py` into an in-memory module under the CI profile; both `{}` and empty-SMILES returned HTTP-compatible failure with `error: ''`. Starlette 0.27.0 `str(HTTPException(...))` is empty; host 1.0.0 contains status/detail. Changed only the new test to assert the exact exception-string representation of its active framework. No error-handler changes or frozen-fixture normalization. This was a demonstrated profile difference, not a load-induced retry. Final guard bootstrap imports FastAPI before pytest, producing one host/two minimum or deployment AnyIO assertion-rewrite warnings; minimum additionally has the existing Pydantic protected-namespace warning, deployment an existing BlockingPortal deprecation. Warnings are retained, not suppressed.

### Static preservation and outstanding work

- Source AST comparison to `6719b12` confirms the entire `_predict_admet_with_rdkit` method is identical after normalizing only the three intentional None alert literals and removing the added SA-method metadata entry. All scientific formulas, numbers, thresholds and descriptor calls therefore remain unchanged. `should_use` and `_check_adme_backend` AST also identical. Route basic-property dict AST identical; no ADMETPredictor name remains in the route.
- All **322** tracked Python files in `src`/`scripts` memory-compiled successfully; the existing MedChat Python also ran `-m compileall -q src scripts` successfully (exit0; only ignored bytecode caches). `git diff --check` passed. No JS changes or JS test claim. No deployment/health/real/all acceptance run.
- Parent resource-coordination instruction received before long runs: focused/minimum permitted; **full Agent and full repository not started**. Reported ready for the hub heavy slot after final focus/minimum results; wait for allocation. Do not infer success from timeout retries or resource contention. No full-suite counts claimed.
- Implementation and tests are held for further verification/independent SPEC then QUALITY; no review approval is claimed. After heavy-slot full verification, refresh this record and freeze the exact snapshot. No implementation staging/commit/push is permitted.

Historical pre-P2 code/test SHA256 snapshot (superseded below; docs excluded to avoid self-reference):

```text
src/agent/tools/admet_predictor.py 9b84797ec33c773a36661d9eae4b69796638d2f043fda3bf236d35808b854821
src/agent/tooling/analysis_contract.py df1362e0fe5e7c2f9453b43f8d7292e5764d501e98f02be67c0c1f981de02871
src/web/routes/molecule_properties_routes.py 10abf4b48a620948cd2af8e3df6da05fa3d96ed9b1ef9b96b7b21af064cba5bf
tests/test_admet_unknown_evidence.py 14dafa7d9458d18115527f591a621b282e2ab3e16df47de58d765991afc960c0
tests/test_molecule_properties_unknown.py e173f5cee5efbb2dc2f8000561e3d1cad2ec99509673579ee6f18fe50af4ce33
tests/agent/test_analysis_contract.py 9409174207233bff5d7afe449e0d4f20feab650886e6c0166bc6d950cbffffcf
tests/test_api_route_boundary.py 2a470e8f05af1139d73c59a36cd59e22712951bd4d29a025490d6964916b270f
```

### SPEC Noether P2 correction and replacement freeze (2026-09-25)

Status: **ready for SPEC re-review, not SPEC-approved; no QUALITY yet**. Parent's later instruction supersedes the heavy-slot waiting instruction above: hand off the frozen snapshot and release the worker. Full Agent/full repository remain **not run**; parent owns reviewed-snapshot/latest-main heavy verification. No new merge, staging, implementation commit or push; branch remains `codex/admet-unknown-evidence`, HEAD `6719b129ddfc431f375bab667bca2476345f6151`.

Receiving-code-review/systematic-debugging/TDD: verified existing redaction/source contracts before changing production. The generic URL pass is followed by an absolute-path regex that corrupts public URLs. The failure path applies it twice. Source-specific target-evidence allowlists cannot serve as general ADMET provenance sanitization. Preserve the shared sanitizer unchanged; add only a field-level source exception after bounded sanitization, reusing its credential-only detector. See spec section 9 for exact conservative URL semantics and limitations. Source preservation is not source verification or scientific certification.

P2 delta is only `src/agent/tools/admet_predictor.py`, `tests/test_admet_unknown_evidence.py`, and these two documentation files. The other five frozen code/test files retain their previous hashes. Forty-one new cases cover the actual ADME.calculate fixture, raw/compat/typed normalization, failed/no-row and explicit False observation, nested provenance, unsafe credential/query/userinfo/token/encoded/path inputs, and bounded restoration. Numerical calculations, formulas, thresholds, historical units, status mechanism, global redaction and three-leaf-only 4A contract remain unchanged.

All new test runs used the isolated runner and hard network guard above. No live providers, models, server, assets or dependency installation. Historical **943 host / 943 minimum / 254 deployment** results above are retained as pre-P2 evidence, not relabelled as coverage of this defect. Deployment was not rerun after P2.

| P2 stage | Actual result | Scope |
|---|---|---|
| Source regression RED, before production fix | 12 failed, 28 passed, 1 warning, 1.00s, exit1 | New `test_public_source_url_survives_actual_backend` and `test_unsafe_diagnostic_sources_are_not_restored`; all 12 fail at the exact corrupted source assertion. |
| Bounded-restoration RED | 1 failed, 1 warning, 0.72s, exit1 | `test_source_preservation_does_not_bypass_diagnostic_bounds_or_secret_keys`, same public URL loss. Corrected the fixture nesting to exercise the existing depth-4 cutoff; no sanitizer contract changed. |
| ADMET file GREEN | 216 passed, 1 warning, 1.25s, exit0 | `tests/test_admet_unknown_evidence.py`. |
| Host FOCUS GREEN | 984 passed, 1 warning, 13.01s, exit0 | Same exact ten FOCUS files above; previous 943 plus 41 new cases. |
| Minimum FOCUS GREEN | 984 passed, 3 warnings, 14.27s, exit0 | Same FOCUS files, existing minimum dependency target (0.104.1 / 2.5.0). |
| Existing redaction/source contracts | 78 passed, 1 warning, 2.75s, exit0 | Paths below, host profile; no changes to these tests or production contracts. |

The 78-case command arguments to the same runner:

```text
tests/agent/test_agent_persistence.py
tests/agent/test_credential_scan_budget.py
tests/agent/test_semantic_input_gates.py::test_official_identity_urls_can_establish_source_specific_identity
tests/agent/test_semantic_input_gates.py::test_every_target_text_field_applies_nfkc_instruction_and_credential_boundary
```

No skips or resource-timeout retries. Warning types are unchanged from the earlier runner/profile record. Targeted `python -m compileall -q src/agent/tools/admet_predictor.py tests/test_admet_unknown_evidence.py` and `git diff --check` passed. These are focused/static results, not full-suite evidence.

Replacement seven-file SHA256 freeze (raw bytes, docs excluded):

```text
src/agent/tooling/analysis_contract.py df1362e0fe5e7c2f9453b43f8d7292e5764d501e98f02be67c0c1f981de02871
src/agent/tools/admet_predictor.py 33777737bd4d32c62f47008da7bb9ace02455ee8cc5d8e1a0d7390e7114150d2
src/web/routes/molecule_properties_routes.py 10abf4b48a620948cd2af8e3df6da05fa3d96ed9b1ef9b96b7b21af064cba5bf
tests/agent/test_analysis_contract.py 9409174207233bff5d7afe449e0d4f20feab650886e6c0166bc6d950cbffffcf
tests/test_admet_unknown_evidence.py b0b2eab95e307cf9641120fb912d6d897031f4734e8762db8a86e0f9eabad32f
tests/test_api_route_boundary.py 2a470e8f05af1139d73c59a36cd59e22712951bd4d29a025490d6964916b270f
tests/test_molecule_properties_unknown.py e173f5cee5efbb2dc2f8000561e3d1cad2ec99509673579ee6f18fe50af4ce33
```

Aggregate SHA256: `8583306c7351a07f613f328440d4f960349bbeffd8ea1edf0ca2e9356dfcae33`, computed from the sorted lines above, each `relative_path + " " + lowercase_sha256`, UTF-8, LF separators and final LF. Supersedes pre-P2 aggregate `4d4462ce3207bea763c97c329280bff4dc833f3f5548df85dbe6a42c87bef912`. No more changes pending this SPEC handoff; independent approval remains outstanding.

### QUALITY Feynman P2: embedded paths, correction and replacement freeze

Status: **locally corrected, frozen for the same QUALITY reviewer's re-review; not approved**. This section supersedes the previous freeze and review status, not its historical test evidence. User authorized only the local bounded correction and focused host/minimum runs. No main/PR71 integration; no staging, commit or push. Branch `codex/admet-unknown-evidence`, HEAD `6719b129ddfc431f375bab667bca2476345f6151` unchanged. **Full Agent/full repository not run** (parent G2 occupies heavy resources); no worker waits for a heavy slot.

Verified through receiving-code-review/systematic-debugging/TDD: the restoration predicate allows `:` and `//` in the parsed path, so it can undo machine-path redaction. Added behavior regressions before changing production. The production fix adds exactly four lines (two comments plus `if ':' in parsed.path or '//' in parsed.path: return False`) to `_public_source_url`; no shared sanitizer or restoration traversal change. Removing those exact four lines from current producer bytes in memory reproduces the previous producer SHA256 `33777737bd4d32c62f47008da7bb9ace02455ee8cc5d8e1a0d7390e7114150d2`, confirming no other producer changes. All scientific calculations, formulas, thresholds, legacy units, metadata methods/reasons and 4A semantics are untouched.

The 96-case embedded-path matrix exercises eight shapes: drive forward slash, nested lowercase drive, file URI with drive, file URI with POSIX path, slash-normalized UNC, backslash drive, backslash UNC and file URI with UNC authority. Each runs actual injected ADME.calculate through raw/compat/typed, both metadata-only failure and medicinal.pains=False success, and top-level/nested provenance.source. It asserts source is not restored, machine filename does not survive even in typed failure snapshots, the existing one/two-pass sanitized result is preserved, method/version/error/reason/warnings remain, failure has no scientific row, False remains False, and input is unchanged. Public reference tests now include HTTP, HTTPS, root URL and ordinary trailing-slash paths. Added explicit bounded dictionary/list-cycle tests. Previous credential/depth/item/sensitive-parent tests are retained unchanged.

Before fix, six forward-slash/URI forms failed every combination (72 failures); backslash forms were already protected. Public URLs and cycles already passed. Exact command arguments for the same isolated runner/network guard used above, unchanged between RED and GREEN:

```text
tests/test_admet_unknown_evidence.py::test_embedded_machine_paths_are_not_restored
tests/test_admet_unknown_evidence.py::test_public_source_url_survives_actual_backend
tests/test_admet_unknown_evidence.py::test_source_restoration_keeps_cycles_bounded_without_mutating_input
```

| Stage | Actual result |
|---|---|
| Behavior RED, before production change | **72 failed, 62 passed**, 1 warning, 2.31s, exit1; failure is exact equality to the unsafe original source. |
| Same behavior GREEN, after four-line change | **134 passed**, 1 warning, 1.54s, exit0. |
| Host ten-file FOCUS | **1106 passed**, 1 warning, 13.85s, exit0; FastAPI 0.135.3 / Pydantic 2.12.5. |
| Minimum ten-file FOCUS | **1106 passed**, 3 warnings, 15.62s, exit0; existing FastAPI 0.104.1 / Pydantic 2.5.0 target, no install. |

Counts are previous 984 plus 96 embedded-path cases, 24 additional public URL cases and two cycle cases. No skips, load-induced timeout or retry. Warnings remain the existing AnyIO rewrite/profile warnings documented above. Historical 943/254 and 984 results remain historical; deployment and full suites were not rerun. Targeted `python -m compileall -q src/agent/tools/admet_predictor.py tests/test_admet_unknown_evidence.py` and `git diff --check` passed.

Exact synthetic before/after evidence for Feynman's two examples (no real machine paths or credentials): before, **both success and failure restored each complete input string** below. After, the predicate returns False; a separate isolated in-memory diagnostic probe confirmed the following literal results. The behavior matrix verifies that actual raw/compat/typed producers use these same one/two-pass results.

| Synthetic input | After success (one pass) | After metadata-only failure (two passes) |
|---|---|---|
| `https://example.org/C:/private/backend.py` | `https:/[redacted]` | `https:[redacted]` |
| `https://example.org/adme/file:///C:/Users/Example/backend.py` | `https:/[redacted]//[redacted]` | `https:[redacted]/[redacted]` |

These deliberately preserve the global sanitizer's existing output, not a new URL normalizer. Ordinary `https://example.org/adme/reference` remains byte-for-byte intact. General RFC-valid path colons/repeated slashes are conservatively ineligible for source restoration; no scheme separators are removed from legitimate ordinary public sources.

Replacement seven-file SHA256 freeze (raw bytes, docs excluded; other five hashes unchanged):

```text
src/agent/tooling/analysis_contract.py df1362e0fe5e7c2f9453b43f8d7292e5764d501e98f02be67c0c1f981de02871
src/agent/tools/admet_predictor.py 43435e9e3df03a3b23de2c9fba44f50746aec8cbb7aace77141f84a15c8586fb
src/web/routes/molecule_properties_routes.py 10abf4b48a620948cd2af8e3df6da05fa3d96ed9b1ef9b96b7b21af064cba5bf
tests/agent/test_analysis_contract.py 9409174207233bff5d7afe449e0d4f20feab650886e6c0166bc6d950cbffffcf
tests/test_admet_unknown_evidence.py cfef9541d1d7326d0eae138f66b13b7d907a50ab5cd2ab61a4f1b56b68b30799
tests/test_api_route_boundary.py 2a470e8f05af1139d73c59a36cd59e22712951bd4d29a025490d6964916b270f
tests/test_molecule_properties_unknown.py e173f5cee5efbb2dc2f8000561e3d1cad2ec99509673579ee6f18fe50af4ce33
```

Aggregate SHA256: `1ef2e89d200c77b04db03d956666c3b93eea542138f4d2af3f53c06ad9f0c744`, using the same sorted-path/space/lowercase-hash/LF/final-LF UTF-8 convention above. Supersedes `8583306c7351a07f613f328440d4f960349bbeffd8ea1edf0ca2e9356dfcae33`. Only producer, ADMET regression tests and the two approved documents changed in this correction. Frozen pending Feynman re-review; no approval or full-suite claim.

### Review closure and authorized nine-file local commit

Parent now confirms **Feynman QUALITY approved** the exact `1ef2e89d200c77b04db03d956666c3b93eea542138f4d2af3f53c06ad9f0c744` snapshot, independently reporting **1184 passed, one existing warning plus boundary probes**. Parent also confirms prior SPEC approval of scientific semantics and URL preservation; the final four-line path guard is closed by QUALITY. These are parent-reported independent reviewer results, distinct from this worker's 1106 host/minimum runs. Recomputed local seven-file aggregate matches the approved hash before recording/committing. No science/code changes accompany this review record.

Explicit new authority: commit exactly the seven reviewed code/test files plus the two approved design/plan documents, then merge the latest locally fetched origin/main into the current branch. No cherry-pick duplication, push or PR. Initial observed main is `5db56b0c79ac30da1ba4646c2c567e7d2dd71cc5`; imminent G2/PR72 is not assumed integrated. Record actual merge SHA and any conflicts after executing, then run integrated host/minimum FOCUS plus collision-related checks. Previous no-commit/merge restrictions are historical; all no-provider/key/model/asset/published-production restrictions remain.

**Full Agent/full repository must not run now** (parent 4C/Russell heavy run active). Integrated light verification is pending; completion means ready for the parent's full slot, not full-suite success. Exact nine-path allowlist is the seven-file freeze above plus `docs/superpowers/specs/2026-09-25-admet-unknown-evidence-design.md` and `docs/superpowers/plans/2026-09-25-admet-unknown-evidence.md`. Stage each explicit path, verify the index allowlist and diff checks, then create the local reviewed implementation commit.

Subsequent resource authorization, before commit: parent reports 4C full completed (6676 passed, two skipped, 300.25s; independent parent evidence) and assigns this worker the heavy slot. After integrated host/minimum FOCUS is GREEN, record the exact snapshot and run **one `tests/agent` full collection** in the existing isolated offline runner. No root full suite, push or PR. G2/PR72 is still pending according to parent and may merge during the run: **no merging or editing mid-run**; any fetched-main delta is considered only afterward with light verification. This supersedes the immediately preceding heavy-slot prohibition only for that one Agent run, which has not yet started.
