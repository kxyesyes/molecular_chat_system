# ADMET Unknown Evidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove unsupported ADMET conclusions while retaining real scientific calculations, evidence and status mechanisms throughout the parent's packages 1–8.

**Architecture:** Keep the existing properties route and ADMET producer; remove the route's unsupported five-label predictor and make unknowns explicit. Correct only local ADMET presentation and uncomputed alerts, then narrowly align the reviewed 4A validation view with those three nullable leaves. No new scientific algorithm, model, generic adapter or scoring changes.

**Tech Stack:** Existing Python/FastAPI, RDKit, optional existing adme_py, pytest, Pydantic 2 in reviewed 4A; no new dependencies/providers/assets.

---

## Authorization and prerequisites

**Written design and plan accepted by parent; TDD waits for the parent-supplied merged 4A baseline.** Parent authorizes updating and locally committing this plan and its sibling spec only. No implementation, test creation, test runs, push, PR or merge is authorized in this documentation phase. Do not repeat questions about accepted recommended choices. Code blocks below remain prospective test/implementation guidance, not files installed by this task. Parent packages 1–8 retain their own deliverables and scientific-truth invariant; the eight local tasks below do not replace those packages.

Spec: `docs/superpowers/specs/2026-09-25-admet-unknown-evidence-design.md`.

Branch/original source-inspection base: `codex/admet-unknown-evidence` / `0e54a1e1038dbf51ea0462ba79c14df91ba13161`. Parent confirms **PR64 merged at `ecd6cca`**, and reports **4A PR67 all 8 CI checks passing, awaiting integration**. These updates are parent-provided, not independently re-polled here. Do not amend PR64 or integrate source during this documentation update. The 4A file does not exist at this task's original base. Earlier read-only sibling observation is commit `52498a8bb7c9d1be4a9db002b09d5756937eab8a`; the spec records its exact source/test hashes. That historical snapshot is not a substitute for the merged baseline the parent will supply.

- [x] Parent reviewed and accepted the written spec and plan, including endpoint sibling metadata, conservative dictionary rule interpretation and metadata-only row exclusion. Preserve available source/method/failure diagnostics when excluding those rows.
- [x] Parent confirmed PR64 landing at `ecd6cca`.
- [ ] Parent supplies the merged reviewed 4A PR67 baseline; only then enter TDD and integrate the reviewed dependency through the parent's normal workflow. Do not copy a sibling module or alter main. Passing CI alone does not satisfy this start gate.
- [ ] Re-read applicable AGENTS, working status, the two production modules, reviewed `analysis_contract.py` and its tests; reconcile changed method signatures before touching code.
- [ ] Run offline in an approved isolated Python environment with real in-memory RDKit and the matching frozen FastAPI/Pydantic profile. Use temporary runtime directories and the isolated runner pattern recorded in `docs/superpowers/plans/2026-09-24-rag-service-extraction.md`; no secrets, `.env`, local scientific assets, external network, app lifespan or real provider calls. Do not install packages implicitly.

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

## Current design-only verification record

Read source and tests at the recorded bases, considered three alternatives and performed spec/plan consistency review. Parent subsequently accepted the written design/plan, confirmed PR64 merged at `ecd6cca`, reported PR67's 8 passing CI checks, authorized a documentation-only local commit and instructed waiting for the merged 4A baseline. Those parent-provided facts supersede the initial pending-review/PR64-pending/no-commit state; they are not a claim of a new remote audit. Only the two permitted Markdown documents were created/updated using apply_patch. No source/test implementation or execution occurred; no provider, secret or model/asset was used. Future implementation SPEC/QUALITY approval, automated test results and 4A merge completion are not claimed. The next action after this documentation commit is to await the parent's merged 4A baseline, not to begin dependency-missing implementation.
