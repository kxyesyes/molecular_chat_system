# ADMET unknowns and evidence-preserving presentation

Date: 2026-09-25. Status: **written design and plan reviewed and accepted by parent; awaiting parent-supplied merged 4A baseline before TDD**.

## 1. Scope, baseline, and authority

Design worktree: `admet-unknown-evidence`; branch: `codex/admet-unknown-evidence`; original source-inspection base: `0e54a1e1038dbf51ea0462ba79c14df91ba13161`. Parent confirms **PR64 merged at `ecd6cca`**, superseding the earlier pending status. Parent also reports **4A PR67 has all 8 CI checks passing and is awaiting integration**. These are parent-supplied status updates, not a fresh remote CI audit. This worktree has not integrated a new source baseline; wait for the parent to supply the merged 4A baseline before TDD.

The parent goal's **packages 1–8 must all explicitly retain scientific truth**: no invented results, unknown-to-negative conversion, model/experimental claims from rules, lost source/version, numerical projection, or status laundering. This narrow behavior correction supports that invariant; it neither renumbers those packages nor declares them complete.

Only this spec and `docs/superpowers/plans/2026-09-25-admet-unknown-evidence.md` may be written in this task. Parent now authorizes updating, explicitly staging and locally committing these two documents only; this supersedes the initial no-commit restriction for documentation, not for implementation. No source/test implementation, test execution, push, PR creation, merge, secrets, provider calls, model/asset access, dependency installation, or main changes. Brainstorming's written-design review gate has been satisfied; writing-plans records the accepted prospective TDD sequence. Do not ask again about accepted recommended choices or start work while the merged dependency baseline is absent.

Later implementation is limited to the properties route, ADMET producer/presentation, associated tests, and a narrowly coordinated 4A validation adjustment. No generic adapters, scoring/ranking, molecular parser, scientific algorithm, factory redesign, or broader frontend work.

## 2. Verified source and tests

Read `AGENTS.md`, relevant `docs/PROJECT_STANDARDS.md`, the files below, recent history and clean initial status. Findings are source inspection, not runtime test results.

| Location at the base | Actual behavior |
|---|---|
| `src/web/routes/molecule_properties_routes.py:58–92` | Instantiates `ADMETPredictor`, probes nonexistent `_predict_admet_properties`; missing method and exceptions produce heuristic five-field labels, including unconditional `hepatotoxicity: Low`, without provenance. The actual producer defines no such private method. |
| Same route, basic calculation and outer handler | Seven real RDKit basic values; preserves input SMILES. Invalid structures return `success: false, properties: null`; even the caught missing-SMILES `HTTPException` is returned through the legacy HTTP-200 failure envelope. |
| `src/agent/tools/admet_predictor.py:199–303` | Real descriptor calculations plus existing deterministic ADME heuristics; `prediction_method: rdkit_rules`, RDKit version. PAINS/Brenk/ZINC are hard-coded `False`, not computed. Ghose and leadlikeness are empty dictionaries. SA is a bounded local complexity formula, not an identified trained score. |
| Same file, `format_admet_result` and four rule formatters | Missing BBB and alert leaves default to false; missing WLogP defaults to zero. Empty rule mappings are reported as passes. Several formatters accept truthy/nonfinite/wrong-type inputs or fail on sparse data. |
| Same file, three narrative generators | Missing BBB implies reduced CNS risk; even explicit BBB false is overinterpreted as safety. Missing Lipinski is narrated as failure. Substring `Soluble` matches `Poorly Soluble` and `Very Poorly Soluble`; lowercase `insoluble` also hits `soluble`. Empty assessment can claim completion. |
| `tests/test_api_route_boundary.py:608` | `test_historical_properties_heuristic_is_not_scientific_validation` deliberately freezes legacy five labels for mechanical extraction, with an explicit non-evidence comment. |
| `tests/test_admet_predictor_fallback.py` | Confirms real fallback execution, row and method, but not unknown alert semantics. |
| `tests/agent/test_admet_whole_input.py` | Whole-input fidelity, multiple complete inputs, invalid suffix rejection without fragment dispatch, and parser/dependency-unavailable distinctions. Keep these assertions. |
| `tests/agent/test_domain_result_validators.py` | Requires method provenance; controlled partial `adme_py` fixture verifies method and package version. |

Read-only 4A dependency: sibling `analysis-tool-contracts`, `src/agent/tooling/analysis_contract.py` and `tests/agent/test_analysis_contract.py`. The file was initially untracked during inspection; final read-only observation was commit `52498a8bb7c9d1be4a9db002b09d5756937eab8a`, with clean status for those two paths. Source SHA256: `5c80751b8acb9b0a59f663ad4a6665f268a339433417832462077710bc3a8baf`; test SHA256: `736c77278f066cb8802f185995bcdb2fb7f6e74e447441c66c39709a0cb68e4d`. This is a moving sibling snapshot, **not certification of its independent review**. The local `main` ref and this task base do not contain this module; do not pretend otherwise or copy the sibling implementation into this batch.

4A `_ADMET_SECTIONS` currently uses strict `_BOOL` for `medicinal.pains/brenk/zinc`; `_admet` requires every known leaf for `rdkit_rules`. `adme_py` requires the six dictionaries but permits absent known leaves, validating supplied leaves strictly. Existing tests cover finite numbers, negative counts, required leaves, sparse backend sections, raw/normalized/failure snapshots, method gate, unchanged extensions and lifecycle semantics.

## 3. Alternatives and recommendation

1. **Recommended: truthful unknowns plus bounded presentation correction.** Remove the unsupported route ADMET execution branch; keep five label keys with `Unknown` and sibling metadata. Correct only the three uncomputed RDKit alert leaves to null; label real rule calculations and SA honestly. Small, explicit semantic change with preserved numerical output.
2. Keep route heuristics and add a warning. Fewer output changes, but still publishes unsupported hepatotoxicity/CYP/bioavailability labels; a warning does not create evidence. Rejected.
3. Wire the route into another backend, add alert computation or a new prediction/scoring model. Could add capability but changes scientific meaning, dependency/runtime scope and validation requirements. Explicitly out of scope, not a fallback for this task.

No capability is fabricated to make the interface look complete. Unknown is not pass, fail, low risk, or zero.

## 4. Properties endpoint contract

Keep `POST /api/molecule/properties`, registration ownership, function/signature, Body requirement, HTTP behavior, basic properties, precision, SMILES echo and failure envelopes unchanged. Remove the entire attempt to import/instantiate the ADMET tool or call a private method; this endpoint has no supported five-label assessment path. Do not wire another method into it.

After successful basic calculation, return exactly these five keys under `properties.admet`:

```json
{
  "bbb_penetration": "Unknown",
  "cyp_inhibition": "Unknown",
  "hepatotoxicity": "Unknown",
  "solubility": "Unknown",
  "bioavailability": "Unknown"
}
```

Add **one sibling** `properties.admet_metadata`, keeping ADMET's existing five-key map stable:

```json
{
  "availability": "unavailable",
  "method": "not_calculated",
  "warning": "ADMET未计算；本接口仅计算基础理化性质，不能据此判断毒性、CNS安全性或体内表现。"
}
```

`availability` describes these five requested assessments, not RDKit availability. `method` means no ADMET calculation occurred; do not claim an absent backend's identity or version. `warning` is fixed safe text, never raw exception/provider output. `success: true` continues to mean the endpoint's real basic-property calculation completed, **not** that ADMET succeeded. Failed basic calculations do not acquire this metadata or a new success envelope.

Preserve MW/LogP to two decimals, HBD/HBA/rotatable integer counts, TPSA to two decimals and QED to three decimals exactly. No new validations or HTTP mappings in this route batch; malformed JSON/body and wrong SMILES types retain characterized behavior.

**Intentional compatibility change:** old heuristic strings become `Unknown`; metadata is additive. The legacy-label characterization in `test_api_route_boundary` must be renamed/updated in the **separate approved behavior batch**, together with this route change. Do not edit PR64, delete the test, xfail it, loosen it to “any string,” regenerate frozen OpenAPI snapshots, or alter unrelated route contracts.

## 5. ADMET field semantics and numerical invariants

| Field/surface | Required semantics |
|---|---|
| `prediction_method` / `backend_version` | Keep `rdkit_rules` or `adme_py` and recorded version verbatim. Human label for `rdkit_rules`: `RDKit-rule（规则估计，非训练模型预测、非实验结果）`. `adme_py`: backend-reported calculation, no inferred trained-model/experimental identity. Unknown version stays unknown. |
| RDKit `medicinal.pains`, `brenk`, `zinc` | Emit Python `None` / JSON `null`, because this producer does not calculate them. Never replace with `False`, `0` or empty strings. |
| Supplied alert booleans | Identity checks: `True` = backend reported alert; `False` = backend reported no alert for this check only. Neither proves toxicity/safety. `None`/absent = unknown/not calculated. Integer 0/1 and strings `false`/`true` are not booleans. |
| BBB bool | True/false describes only that method's reported permeability estimate. Neither supports CNS activity, adverse-effect probability, or safety. Missing/null/invalid in presentation means unknown. RDKit still emits its real computed bool. |
| Ghose / leadlikeness | Keep RDKit `{}` payloads; empty/absent/null detail has no pass evidence. For supplied detail maps, report actual supplied failure details (`outside`) without claiming a complete assessment. A nonempty map without failure markers is not automatically a pass. Recognized scalar Pass/Fail/Warning may be displayed by pure formatters, but current 4A still requires dictionaries for these raw leaves. Do not broaden that contract. |
| Lipinski / Veber | Only exact recognized labels (presentation may strip/case-normalize strings) yield rule pass/fail/warning. Missing/null/empty/unrecognized is unknown, not failure or pass. Do not change calculation thresholds. |
| `medicinal.synthetic_accessibility` | Preserve actual numeric value/key. Add RDKit-only sibling `synthetic_accessibility_method: rdkit_complexity_heuristic`. Human label: local structural-complexity heuristic, not trained/standard validated SA score and not experimental synthesis feasibility. Do not attach this label to `adme_py` SA. |
| SA supplied by `adme_py` | Preserve number and any existing method extension. Without identified method, display “后端报告值；具体方法未提供”; do not call it a trained/standard score or assert actual ease of synthesis. |
| Sparse `adme_py` sections | Preserve supplied leaves and absent leaves without populating negative/default values. Format each section independently; absent WLogP is not zero. Retain unknown extension payloads unchanged. |
| Empty narrative evidence | Explicit “未形成可解释的ADME评估；缺失项目未知。”; never “已完成基本ADME属性分析,” a favorable checkmark, or default rule failure. |

Do not invent a dictionary completion schema for Ghose/leadlikeness: the inspected repository does not define a verified complete positive backend key set. Until separately evidenced, positive-looking detail maps show “已提供部分规则明细；完整结论未知” rather than a blanket pass. Explicit failure evidence may be named as supplied evidence, not a newly executed rule.

All existing calculations stay byte-for-byte equivalent in meaning, precision and units:

- Molecular formula and all RDKit descriptors/counts, aromatic proportion and fraction CSP3.
- Existing LogS expression: `0.16 - 1.5*logp - 0.01*(MW-40) + 0.066*rotatable_bonds + 0.066*aromatic_proportion`; solubility remains `max(0, 10**log_s * MW)` with existing `mg/mL` label. Keep legacy `log_s_esol`/`solubility_esol` keys, while labeling RDKit outputs as existing rule estimates, not certifying a published/validated model.
- Existing solubility thresholds `-1/-2/-3/-4`, Lipinski thresholds/violation allowance, Veber thresholds, GI rules, BBB rule `0 <= logp <= 5 and TPSA < 90`, and skin LogKp formula.
- SA expression `min(10, max(1, 1 + heavy_atoms/25 + rotatable_bonds/5 + rings/4))`. No replacement SA algorithm, RDKit alert catalog, extra scoring or recalibration.

## 6. Presentation and no-assessment behavior

Keep helpers inside `admet_predictor.py`, shared only across its report, comprehensive assessment, interpretation and brief reasoning. Do not create a generic result adapter or transform stored/raw observations for display.

Use exact whole-label solubility lookup (case-normalized only in display): `Very Soluble`/`Highly Soluble` = high; `Soluble`/`Moderately Soluble` = moderate; `Poorly Soluble` = low; `Very Poorly Soluble` = very low; `Insoluble` = insoluble. Unknown/empty/null/unrecognized = unknown. No substring matching. Use method-qualified language, not “excellent absorption” as an established fact.

Read sections with a dictionary-only local view. Wrong section/leaf types must not crash a pure formatter or produce positive/negative conclusions. This is **not validation repair**: raw fields remain untouched and 4A still rejects supplied malformed known leaves. Numeric display requires finite actual int/float, excluding bool; reject numeric strings for display. Preserve zero and legitimate negative LogP/LogS/LogKp; missing is not zero. Nonnegative integer counts exclude bool/fractional/negative values; SA display accepts only finite values in existing [1,10]. Do not silently clamp, round raw values or broaden contract ranges.

All four human surfaces, including `execute.message` and `execute.reasoning` prefixes, must distinguish “returned descriptors/rule estimates” from “all ADMET predicted.” BBB wording includes the method's positive/negative/unknown estimate and “不能据此判断CNS活性或副作用风险”; remove both current low-risk phrases and the inferred CNS-activity phrase. Alert false means reported no alert, not a safety endorsement.

Separate three cases:

1. Real descriptors/rules exist: retain existing successful producer/result mechanism and rows. Unknown optional assessments do not erase real values or cause an invented global failure.
2. Sparse backend row has real supported observations: retain row, method/version and original data; display missing assessments as unknown, avoid a full-assessment success claim. No new global `partial` classifier.
3. Backend returns only metadata, empty sections, unknown markers or opaque extensions: the **producer** must not count it as a calculated result. A small local presence check over recognized observation leaves, with explicit false/zero accepted, rejects this as no assessment; the existing no-calculated-results failure return remains false/None. **Excluding an unassessed row must not discard its already available source/provenance, prediction method, backend version, warnings or failure reason.** Retain them as failure diagnostics using existing envelope metadata channels (`quality`, `warnings`, valid `provenance`) and the existing normalized failure details/snapshot, not as successful scientific data. Keeping them only inside a helper's discarded local return is insufficient. Preserve existing diagnostics rather than overwriting them with the new generic no-assessment message; append that explanation. Do not fabricate absent source/reason fields or expose private exception payloads. Decide row inclusion before setting `success`. Do not change 4A's structurally valid sparse-fixture semantics into a new evidence policy. No generic adapter or status mechanism changes.

The presence check is not a scientific score or schema replacement: descriptors/formula, finite numeric observations, strict bool observations, recognized labels, or nonempty recognized rule details count; method/version, SA method, empty details, unknown text and arbitrary extensions alone do not. Formula unknown markers are empty/whitespace, `Unknown`, `未知`, `N/A` (case-insensitive where applicable). Rule-detail observations are supplied string values containing whole-word `within` or `outside`; they count only as reported details, never as a newly computed complete rule result. Unrecognized or nested detail objects do not count. It neither imputes missing values nor certifies a model. Wrong-type supplied leaves are still invalid at the typed boundary. Backend exceptions/unavailability and invalid whole input retain existing failure paths, ordering and error classification; no fallback to a different provider or valid molecular fragment.

## 7. 4A integration gate

The written design is accepted; the remaining start gate is the parent's **merged 4A PR67 baseline**, not another choice review. All 8 CI checks are reported passing, but that does not supply the merged integration commit. Wait for the parent to provide it before implementing/validating this behavior batch. Do not cherry-pick sibling work, change its branch or copy its adapter. Record the exact reviewed integration base in the execution record when supplied.

The only production contract delta permitted here is a strict `bool | None` leaf view for the **three names** `medicinal.pains`, `medicinal.brenk`, `medicinal.zinc`. These fields remain required for `rdkit_rules`; null represents not calculated, false remains an explicit negative observation. For sparse `adme_py`, a missing leaf remains missing and these three supplied nulls are allowed as unknown. Do not make `_BOOL` globally nullable or change `_fields(required=...)`.

All other required RDKit leaves, six dictionaries, method/version identifiers, finite/range/count checks, BBB strict bool, Ghose/leadlikeness dictionary types, provenance/domain gate, normalized/raw/failure-snapshot validation, redaction, deadlines, retry/close/slot ownership and extension preservation stay unchanged. `synthetic_accessibility_method` is an additive producer extension retained by existing extra-field behavior, not a new required leaf that invalidates historic rows.

Accepting null structurally does not turn it into a negative result or scientific evidence. Formatter tolerance for missing BBB does **not** authorize BBB null in a complete RDKit typed row; sparse adme_py may omit BBB, but a supplied null BBB remains invalid under reviewed 4A.

## 8. Compatibility, risks and acceptance

Intentional behavior changes: endpoint labels, three RDKit alert leaves, method-qualified text, empty-rule conclusions, exact solubility interpretation, metadata-only producer row exclusion. Stable: URL/HTTP/error/registration/OpenAPI, real basic/ADMET values and formulas/counts, backend selection and versions, parser/batch fidelity, source/evidence/extensions, existing status machinery. Do not modify ranker/scoring to compensate for less apparent evidence.

Review risks:

- Legacy consumers may assume bool alerts or a five-field endpoint heuristic. Regression-test null/false separately; state this intentional semantics change rather than claiming full value compatibility.
- An optional backend may supply unusual rule details/types. Keep raw data; conservative unknown display is preferable to a guessed pass. If verified positive backend schemas are later needed, request a separate scientific contract change.
- The accepted design must be reconciled with the eventual parent-supplied merged 4A baseline; do not substitute the earlier inspected sibling hash for it. If the merged contract differs materially, document the integration impact before implementation without reopening already accepted choices. Its sparse structural fixture is not proof of a completed scientific assessment.
- A formatter could hide malformed raw data by “normalizing” it. Deep-equality tests plus strict typed rejection must demonstrate that it does not.
- Removing old success/safety language can affect snapshots. Update only assertions that explicitly freeze these defects; do not weaken whole-input, finite-value, provenance or status tests.
- PR64 is merged at `ecd6cca` per parent confirmation. Preserve its extraction-only scope and frozen fixture profiles. This batch remains separate dependent behavior work, not a PR64 amendment.

Required offline TDD coverage:

1. Endpoint: all five Unknown values and exact metadata; no ADMET constructor use; actual RDKit basic equality; invalid/missing/wrong input and HTTP/OpenAPI compatibility.
2. Actual RDKit CCO and aspirin: formula, numbers, counts, labels and version preserved; only alert leaves become null and SA metadata is added.
3. False vs None vs missing vs true for each alert and BBB; reject display conclusions from 0/1/string booleans. No CNS safety/activity conclusion even when BBB is false/true.
4. Empty/partial/unrecognized rule dictionaries and labels; unknown is neither pass nor fail; retain explicit supplied failures without claiming completeness.
5. Every solubility class on every narrative surface; poorly/very poorly/insoluble never enters a favorable soluble branch.
6. Sparse backend rows, zero values, negative valid logarithms, numeric strings, bool-as-number, NaN/Inf, negative/fractional counts, malformed containers, no input mutation; no positive default when no assessment exists.
7. 4A: three nullable leaves accepted in raw/normalized/partial/failed snapshots; all known required non-alert leaves remain required and strict; alert omission in RDKit remains invalid; method/domain gate and extension preservation unchanged.
8. Invalid whole SMILES, mixed valid/invalid batches, missing dependencies, backend exception/empty result: no dispatch on invalid input, no fragment rescue, no synthetic success. Metadata-only producer failures retain available method/version/source and original safe failure reasons/warnings through direct return, compatibility normalization and the 4A failed observation/snapshot; add the no-assessment explanation without erasing diagnostics. Existing status/lifecycle and downstream evidence tests remain green without scoring changes.

Acceptance requires parent SPEC then QUALITY review of the bounded implementation, focused/full offline regression and truthful reporting of failures/skips/dependency limits. Tests using fixtures prove contracts, not real external ADMET or experimental validity. No production/provider/model run belongs to this batch.

## Design-only handoff

Parent has reviewed and accepted the recommended written design and plan, including metadata names, conservative rule-detail semantics and the reviewed-4A dependency, with explicit failure-diagnostic preservation reinforced above. Algorithms, thresholds, numbers and counts stay unchanged; nullable bool is limited to the three uncomputed alert leaves; partial rule details never become a complete pass. Only these two documents may now be committed locally. No pytest, compileall, health check or scientific run has been performed; read-only inspection and documentation diff/allowlist checks are the verification. Implementation remains unstarted until the parent supplies the merged 4A baseline. Acceptance of this design does not claim future implementation SPEC/QUALITY review or runtime verification.
