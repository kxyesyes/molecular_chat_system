# RG-MPNN Activity Data Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a fail-closed, reproducible data-preparation and endpoint-aware model lifecycle around the existing RG-MPNN trainer so real PDE and BuChE datasets can be validated, split, trained, registered, and traced without fabricated results.

**Architecture:** Keep the existing RG-MPNN network, `ActivityTrainer`, predictor, and atomic model registry. Add a focused dataset-contract module and preparation CLI, then extend training metadata and registry selection with an explicit `endpoint_key`; legacy global model selection remains available only for compatibility calls.

**Tech Stack:** Python 3.10+, pandas, NumPy, RDKit, PyTorch, PyTorch Geometric, scikit-learn, FastAPI, pytest.

---

## File structure

- Create `src/activity/dataset_contract.py`: manifest parsing, row validation, unit conversion, duplicate aggregation, scaffold splitting, prepared-artifact writing.
- Create `src/activity/model_card.py`: deterministic model-card payload construction and atomic JSON writing.
- Create `scripts/prepare_activity_dataset.py`: direct CLI for validation-only and preparation runs.
- Create `tests/test_activity_dataset_contract.py`: data-contract, normalization, leakage, and artifact tests.
- Create `tests/test_activity_prepare_cli.py`: real subprocess tests for the documented CLI.
- Create `tests/test_activity_endpoint_selection.py`: endpoint-aware registry, predictor, tool, and API tests.
- Modify `src/activity/trainer.py`: consume prepared manifests, evaluate held-out test data, and register complete scientific metadata.
- Modify `src/activity/model_registry.py`: registry state migration and per-endpoint active-model mapping.
- Modify `src/activity/predictor.py`: explicit endpoint selection with fail-closed mismatch handling.
- Modify `src/agent/tools/activity_predictor_tool.py`: pass requested endpoint and preserve endpoint provenance.
- Modify `src/web/routes/api_routes.py`: accept dataset manifest for training and optional endpoint for prediction/model selection.
- Modify `scripts/health_check.py`: distinguish no models, legacy-only models, and endpoint-ready models.
- Modify `docs/PROJECT_STANDARDS.md`: document real activity dataset and model-artifact rules.
- Modify `data/REGISTRY.md`: document Git-external PDE/BuChE dataset and model locations without machine-specific paths.

## Task 1: Manifest and endpoint identity contract

**Files:**
- Create: `src/activity/dataset_contract.py`
- Test: `tests/test_activity_dataset_contract.py`

- [ ] **Step 1: Write failing manifest tests**

Add tests for required fields, supported tasks, controlled relations, unit semantics, and stable endpoint identity:

```python
from pathlib import Path

import pytest

from src.activity.dataset_contract import DatasetManifest, load_dataset_manifest


def valid_manifest_payload() -> dict:
    return {
        "dataset_id": "pde5a-v1",
        "target_id": "P54750",
        "target_name": "PDE5A",
        "task_type": "regression",
        "endpoint": "pIC50",
        "units": "pIC50",
        "label_transform": "identity",
        "source": "owner-provided",
        "license": "private-research",
    }


def test_manifest_builds_stable_endpoint_key(tmp_path: Path) -> None:
    path = tmp_path / "pde5a.json"
    path.write_text(
        '{"dataset_id":"pde5a-v1","target_id":"P54750",'
        '"target_name":"PDE5A","task_type":"regression",'
        '"endpoint":"pIC50","units":"pIC50",'
        '"label_transform":"identity","source":"owner-provided",'
        '"license":"private-research"}',
        encoding="utf-8",
    )

    manifest = load_dataset_manifest(path)

    assert manifest.endpoint_key == "p54750:pic50:pic50:regression"


@pytest.mark.parametrize("field", [
    "dataset_id", "target_id", "target_name", "task_type", "endpoint",
    "units", "label_transform", "source", "license",
])
def test_manifest_rejects_missing_required_field(tmp_path: Path, field: str) -> None:
    payload = valid_manifest_payload()
    payload.pop(field)
    path = tmp_path / "invalid.json"
    path.write_text(__import__("json").dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match=field):
        load_dataset_manifest(path)
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests/test_activity_dataset_contract.py -q -p no:cacheprovider
```

Expected: collection fails because `src.activity.dataset_contract` does not exist.

- [ ] **Step 3: Implement the immutable manifest contract**

Create a frozen dataclass and parser with normalized identifiers:

```python
@dataclass(frozen=True)
class DatasetManifest:
    dataset_id: str
    target_id: str
    target_name: str
    task_type: Literal["regression", "classification"]
    endpoint: str
    units: str
    label_transform: str
    source: str
    license: str
    relation_column: str = "relation"
    smiles_column: str = "smiles"
    value_column: str = "value"
    duplicate_strategy: Literal["median"] = "median"
    minimum_unique_molecules: int = 100
    minimum_scaffolds: int = 10

    @property
    def endpoint_key(self) -> str:
        values = (self.target_id, self.endpoint, self.units, self.task_type)
        return ":".join(_normalize_identifier(value) for value in values)
```

`load_dataset_manifest()` must reject unknown keys, blank strings, task types outside `regression/classification`, transforms outside `identity/molar_to_pactivity/binary_threshold`, and `binary_threshold` without a finite numeric `classification_threshold`.

- [ ] **Step 4: Run tests and verify GREEN**

Run the Task 1 test command. Expected: all Task 1 tests pass.

- [ ] **Step 5: Commit Task 1**

```powershell
git add -- src/activity/dataset_contract.py tests/test_activity_dataset_contract.py
git commit -m "feat: define activity dataset manifest contract"
```

## Task 2: Fail-closed row validation and label normalization

**Files:**
- Modify: `src/activity/dataset_contract.py`
- Modify: `tests/test_activity_dataset_contract.py`

- [ ] **Step 1: Write failing validation tests**

Cover invalid SMILES, unknown units, non-finite labels, censored relations, molar conversion, binary labels, and duplicate aggregation:

```python
def test_validation_rejects_invalid_rows_and_never_coerces_censored_values() -> None:
    frame = pd.DataFrame([
        {"smiles": "CCO", "value": 100.0, "units": "nM", "relation": "="},
        {"smiles": "CC(C)((", "value": 3.0, "units": "nM", "relation": "="},
        {"smiles": "CCN", "value": float("nan"), "units": "nM", "relation": "="},
        {"smiles": "CCC", "value": 10.0, "units": "nM", "relation": ">"},
    ])

    manifest = DatasetManifest(**{
        **valid_manifest_payload(),
        "endpoint": "IC50",
        "units": "nM",
        "label_transform": "molar_to_pactivity",
    })
    result = validate_activity_dataset(frame, manifest)

    assert result.ready_for_training is False
    assert result.statistics["accepted_rows"] == 1
    assert result.statistics["rejected_by_reason"] == {
        "invalid_smiles": 1,
        "non_finite_value": 1,
        "unsupported_relation": 1,
    }
    assert result.accepted.iloc[0]["normalized_value"] == pytest.approx(7.0)


def test_duplicate_measurements_use_median_and_record_dispersion() -> None:
    frame = pd.DataFrame([
        {"smiles": "C(C)O", "value": 6.0, "units": "pIC50", "relation": "="},
        {"smiles": "CCO", "value": 8.0, "units": "pIC50", "relation": "="},
    ])

    result = validate_activity_dataset(
        frame,
        DatasetManifest(**valid_manifest_payload()),
    )

    assert len(result.accepted) == 1
    assert result.accepted.iloc[0]["canonical_smiles"] == "CCO"
    assert result.accepted.iloc[0]["normalized_value"] == 7.0
    assert result.accepted.iloc[0]["replicate_count"] == 2
    assert result.accepted.iloc[0]["replicate_range"] == 2.0
```

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests/test_activity_dataset_contract.py -q -p no:cacheprovider
```

Expected: failures because `validate_activity_dataset()` and result statistics are absent.

- [ ] **Step 3: Implement canonicalization and normalization**

Add `DatasetValidationResult` plus pure helpers:

```python
@dataclass
class DatasetValidationResult:
    accepted: pd.DataFrame
    rejected: pd.DataFrame
    warnings: list[str]
    statistics: dict[str, Any]
    input_sha256: str | None
    ready_for_training: bool


_CONCENTRATION_TO_MOLAR = {
    "M": 1.0,
    "mM": 1e-3,
    "uM": 1e-6,
    "nM": 1e-9,
    "pM": 1e-12,
}


def concentration_to_pactivity(value: float, units: str) -> float:
    molar = value * _CONCENTRATION_TO_MOLAR[units]
    if not math.isfinite(molar) or molar <= 0:
        raise ValueError("concentration must be finite and positive")
    return -math.log10(molar)
```

For classification, accept direct labels only when they are exactly `0` or `1`. Apply a threshold only when the manifest uses `binary_threshold`, and record the threshold and direction in the quality report. Never infer a SMILES column from arbitrary object data.

- [ ] **Step 4: Run tests and verify GREEN**

Run the Task 2 test command. Expected: all tests pass and RDKit invalid-SMILES messages are captured by pytest rather than converted to accepted rows.

- [ ] **Step 5: Commit Task 2**

```powershell
git add -- src/activity/dataset_contract.py tests/test_activity_dataset_contract.py
git commit -m "feat: validate and normalize activity measurements"
```

## Task 3: Reproducible three-way scaffold split and artifacts

**Files:**
- Modify: `src/activity/dataset_contract.py`
- Modify: `tests/test_activity_dataset_contract.py`

- [ ] **Step 1: Write failing leakage and reproducibility tests**

```python
def test_three_way_scaffold_split_is_reproducible_without_leakage() -> None:
    prepared = pd.DataFrame({
        "canonical_smiles": [
            "c1ccccc1", "c1ccncc1", "C1CCCCC1", "C1CCCC1",
            "c1ccoc1", "c1ccsc1", "c1ncc[nH]1", "c1ccc2ccccc2c1",
            "c1ccc2[nH]ccc2c1", "c1ccc2ncccc2c1", "O1CCNCC1", "N1CCNCC1",
        ],
        "normalized_value": [float(value) for value in range(12)],
    })

    first = split_prepared_dataset(prepared, seed=41)
    second = split_prepared_dataset(prepared, seed=41)

    assert first.assignments == second.assignments
    assert set(first.frames) == {"train", "validation", "test"}
    assert all(not frame.empty for frame in first.frames.values())
    assert_no_cross_split_smiles_or_scaffolds(first.frames)


def test_three_way_split_rejects_insufficient_scaffolds() -> None:
    prepared = pd.DataFrame({
        "canonical_smiles": ["c1ccccc1", "c1ccncc1"],
        "normalized_value": [6.0, 7.0],
    })
    with pytest.raises(ValueError, match="three distinct.*scaffold"):
        split_prepared_dataset(prepared, seed=42)
```

- [ ] **Step 2: Run focused tests and verify RED**

Expected: failures because `split_prepared_dataset()` does not exist.

- [ ] **Step 3: Implement scaffold-group assignment**

Add a deterministic splitter that shuffles scaffold groups with `random.Random(seed)`, orders ties deterministically, and assigns complete scaffold groups toward target ratios `0.70/0.15/0.15`. Validate both canonical-SMILES and scaffold disjointness after assignment.

Add `write_prepared_dataset()` using temporary files plus `os.replace()` to emit:

```text
prepared/{dataset_id}/
├── train.csv
├── validation.csv
├── test.csv
├── dataset_manifest.json
└── quality_report.json
```

Artifact JSON stores repo-independent relative filenames and SHA-256 digests. Rejected rows are written only when an explicit private output path is supplied; the default quality report stores reason counts, not rejected source records.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the Task 3 tests and assert each emitted digest matches its file.

- [ ] **Step 5: Commit Task 3**

```powershell
git add -- src/activity/dataset_contract.py tests/test_activity_dataset_contract.py
git commit -m "feat: prepare leakage-safe activity splits"
```

## Task 4: Direct preparation CLI

**Files:**
- Create: `scripts/prepare_activity_dataset.py`
- Create: `tests/test_activity_prepare_cli.py`
- Modify: `.gitignore`

- [ ] **Step 1: Write failing subprocess tests**

Use temporary synthetic CSV data only:

```python
def cli_manifest_payload() -> dict:
    return {
        "dataset_id": "pde5a-cli-v1",
        "target_id": "P54750",
        "target_name": "PDE5A",
        "task_type": "regression",
        "endpoint": "pIC50",
        "units": "pIC50",
        "label_transform": "identity",
        "source": "synthetic-contract-test",
        "license": "test-only",
        "minimum_unique_molecules": 3,
        "minimum_scaffolds": 3,
    }


def test_validate_only_reports_status_without_writing_training_files(tmp_path: Path) -> None:
    input_path = tmp_path / "input.csv"
    input_path.write_text(
        "smiles,value,units,relation\n"
        "c1ccccc1,7.0,pIC50,=\n"
        "c1ccncc1,6.5,pIC50,=\n"
        "C1CCCCC1,6.0,pIC50,=\n",
        encoding="utf-8",
    )
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(cli_manifest_payload()), encoding="utf-8")
    output_dir = tmp_path / "prepared"

    completed = subprocess.run([
        sys.executable,
        "scripts/prepare_activity_dataset.py",
        "--input", str(input_path),
        "--manifest", str(manifest_path),
        "--output-dir", str(output_dir),
        "--validate-only",
    ], cwd=PROJECT_ROOT, capture_output=True, text=True, check=False)

    assert completed.returncode == 0
    payload = json.loads(completed.stdout)
    assert payload["ready_for_training"] is True
    assert not output_dir.exists()


def test_cli_returns_nonzero_when_quality_gate_fails(tmp_path: Path) -> None:
    input_path = tmp_path / "invalid.csv"
    input_path.write_text(
        "smiles,value,units,relation\nCC(C)((,7.0,pIC50,=\n",
        encoding="utf-8",
    )
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(cli_manifest_payload()), encoding="utf-8")
    completed = subprocess.run([
        sys.executable,
        "scripts/prepare_activity_dataset.py",
        "--input", str(input_path),
        "--manifest", str(manifest_path),
        "--output-dir", str(tmp_path / "prepared"),
        "--validate-only",
    ], cwd=PROJECT_ROOT, capture_output=True, text=True, check=False)
    assert completed.returncode == 2
    assert json.loads(completed.stdout)["ready_for_training"] is False
```

- [ ] **Step 2: Run subprocess tests and verify RED**

Run:

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests/test_activity_prepare_cli.py -q -p no:cacheprovider
```

Expected: failure because the script does not exist.

- [ ] **Step 3: Implement the CLI**

The script must bootstrap the project root for direct invocation, parse mutually exclusive `--validate-only/--prepare`, emit one UTF-8 JSON document to stdout, and return:

- `0`: quality gate passed;
- `2`: dataset or scientific contract rejected;
- `1`: unexpected runtime failure.

Add Git-ignore rules for `data/activity/raw/`, `data/activity/prepared/`, `data/activity/models/`, and `outputs/activity/` while retaining documentation files.

- [ ] **Step 4: Run subprocess tests and verify GREEN**

Run the Task 4 command under normal UTF-8 and `PYTHONIOENCODING=gbk:strict`; both must complete without Unicode encoding failure.

- [ ] **Step 5: Commit Task 4**

```powershell
git add -- .gitignore scripts/prepare_activity_dataset.py tests/test_activity_prepare_cli.py
git commit -m "feat: add activity dataset preparation CLI"
```

## 实施检查点（2026-09-07）

任务 1–5 已实现并通过规范与代码质量审查。CLI 使用说明见
`docs/activity_data_preparation.md`，端点模型注册说明见
`docs/activity_model_registry.md`，本轮验证及剩余任务见
`docs/handoff/activity-data-readiness.md`。
任务 6 已实现内部 prepared 训练、模型卡与三路评估，扩展回归 584 passed、5 skipped；
规范与代码质量审查均通过。使用说明见 `docs/activity_prepared_training.md`。
任务 7–9 尚未完成；Web/Agent 推理迁移尚未上线，本分支也未合并 main。

## Task 5: Endpoint-aware atomic model registry

**Files:**
- Modify: `src/activity/model_registry.py`
- Modify: `src/activity/trainer.py`
- Create: `tests/activity_test_support.py`
- Create: `tests/test_activity_endpoint_selection.py`
- Modify: `tests/test_activity_model_registry.py`

- [ ] **Step 1: Write failing registry migration and isolation tests**

```python
import json
from hashlib import sha256
from pathlib import Path


def legacy_metadata(model_id: str, weights_file: str) -> dict:
    weights_path = Path(weights_file)
    return {
        "model_id": model_id,
        "weights_file": weights_path.name,
        "task_type": "regression",
        "endpoint": "pIC50",
        "units": "pIC50",
        "dataset_sha256": "a" * 64,
        "split_strategy": "scaffold",
        "random_seed": 42,
        "model_config": {"in_channels": 8, "edge_dim": 4, "channels": 16},
        "model_format": "pytorch_state_dict",
        "weights_sha256": "0" * 64,
        "metrics": {"rmse": 0.8},
    }


def endpoint_metadata(model_id: str, endpoint_key: str, weights_file: str) -> dict:
    return {
        **legacy_metadata(model_id, weights_file),
        "target_id": endpoint_key.split(":", 1)[0].upper(),
        "target_name": "PDE5A" if endpoint_key.startswith("p54750:") else "BuChE",
        "endpoint_key": endpoint_key,
        "label_transform": "identity",
        "prepared_dataset_sha256": "b" * 64,
        "split_counts": {"train": 70, "validation": 15, "test": 15},
        "split_scaffold_counts": {"train": 50, "validation": 10, "test": 10},
        "test_metrics": {"rmse": 0.8, "mae": 0.6, "r2": 0.5},
        "model_card_file": f"{model_id}_model_card.json",
        "model_card_sha256": "c" * 64,
        "scientific_readiness": "endpoint_ready",
    }


def register_endpoint_model(
    registry: ActivityModelRegistry,
    model_id: str,
    endpoint_key: str,
) -> dict:
    weights = registry.models_dir / f"{model_id}.pt"
    weights.write_bytes(b"endpoint checkpoint")
    card = registry.models_dir / f"{model_id}_model_card.json"
    card.write_text('{"demo_mode":false,"fallback_used":false}', encoding="utf-8")
    metadata = endpoint_metadata(model_id, endpoint_key, weights.name)
    metadata["weights_sha256"] = sha256(weights.read_bytes()).hexdigest()
    metadata["model_card_sha256"] = sha256(card.read_bytes()).hexdigest()
    return registry.register(metadata)


def write_prepared_dataset_fixture(
    root: Path,
    *,
    target_id: str,
    target_name: str,
    endpoint: str,
    units: str,
) -> Path:
    directory = root / "prepared" / "contract-fixture"
    directory.mkdir(parents=True)
    artifacts = {}
    rows = "canonical_smiles,normalized_value,scaffold\nCCO,7.0,\nCCN,6.0,\n"
    for split in ("train", "validation", "test"):
        path = directory / f"{split}.csv"
        path.write_text(rows, encoding="utf-8")
        artifacts[split] = {
            "file": path.name,
            "sha256": sha256(path.read_bytes()).hexdigest(),
        }
    payload = {
        "schema_version": 1,
        "dataset_id": "contract-fixture",
        "target_id": target_id,
        "target_name": target_name,
        "task_type": "regression",
        "endpoint": endpoint,
        "units": units,
        "label_transform": "identity",
        "endpoint_key": f"{target_id.lower()}:{endpoint.lower()}:{units.lower()}:regression",
        "prepared_dataset_sha256": "b" * 64,
        "artifacts": artifacts,
    }
    manifest_path = directory / "dataset_manifest.json"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    return manifest_path


def test_endpoint_active_models_are_isolated(tmp_path: Path) -> None:
    registry = ActivityModelRegistry(tmp_path)
    pde = register_endpoint_model(registry, "pde-model", "p54750:pic50:pic50:regression")
    buche = register_endpoint_model(registry, "buche-model", "p06276:pic50:pic50:regression")

    registry.select_for_endpoint(pde["endpoint_key"], pde["model_id"])
    registry.select_for_endpoint(buche["endpoint_key"], buche["model_id"])

    assert registry.get_active_for_endpoint(pde["endpoint_key"])["model_id"] == "pde-model"
    assert registry.get_active_for_endpoint(buche["endpoint_key"])["model_id"] == "buche-model"


def test_endpoint_selection_rejects_cross_target_model(tmp_path: Path) -> None:
    registry = ActivityModelRegistry(tmp_path)
    model = register_endpoint_model(registry, "pde-model", "p54750:pic50:pic50:regression")
    with pytest.raises(ValueError, match="endpoint_key"):
        registry.select_for_endpoint("p06276:pic50:pic50:regression", model["model_id"])
```

Also write a migration test proving version-1 registry state loads with `active_models_by_endpoint={}` and retains `active_model_id`.

- [ ] **Step 2: Run tests and verify RED**

Expected: failures because endpoint metadata and methods are absent.

- [ ] **Step 3: Implement registry state version 2**

Extend required scientific metadata with:

```python
_ENDPOINT_FIELDS = {
    "target_id", "target_name", "endpoint_key", "label_transform",
    "prepared_dataset_sha256", "split_counts", "split_scaffold_counts",
    "test_metrics", "model_card_file", "model_card_sha256",
}
```

State version 2 includes `active_models_by_endpoint: dict[str, str]`. Validate every mapping against the registered model's own endpoint key. Deleting a model removes every active mapping that points to it. Keep `select()` and `get_active()` unchanged for compatibility.

Update `_build_model_metadata()` to require endpoint-ready fields when called with a prepared dataset, while retaining the current legacy metadata builder path for existing tests and pre-existing models.

- [ ] **Step 4: Run registry tests and verify GREEN**

Run:

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests/test_activity_model_registry.py tests/test_activity_endpoint_selection.py -q -p no:cacheprovider
```

Expected: all tests pass, including concurrent registry tests.

- [ ] **Step 5: Commit Task 5**

```powershell
git add -- src/activity/model_registry.py src/activity/trainer.py tests/activity_test_support.py tests/test_activity_model_registry.py tests/test_activity_endpoint_selection.py
git commit -m "feat: register activity models by endpoint"
```

## Task 6: Prepared-data training and held-out evaluation

**Files:**
- Create: `src/activity/model_card.py`
- Modify: `src/activity/trainer.py`
- Modify: `tests/test_activity_prediction_contract.py`
- Modify: `tests/test_activity_endpoint_selection.py`

- [ ] **Step 1: Write failing training-contract tests**

```python
def test_prepared_training_requires_matching_artifact_hashes(tmp_path: Path) -> None:
    prepared = write_prepared_dataset_fixture(
        tmp_path,
        target_id="P54750",
        target_name="PDE5A",
        endpoint="pIC50",
        units="pIC50",
    )
    (prepared.parent / "train.csv").write_text("tampered", encoding="utf-8")

    with pytest.raises(ValueError, match="SHA-256"):
        load_prepared_training_data(prepared)


def test_model_card_separates_validation_and_test_metrics(tmp_path: Path) -> None:
    card = build_model_card(
        metadata=endpoint_metadata(
            "pde-model",
            "p54750:pic50:pic50:regression",
            "pde-model.pt",
        ),
        validation_metrics={"rmse": 0.7},
        test_metrics={"rmse": 0.9},
        limitations=["Validated only within the supplied chemical space."],
    )
    assert card["validation_metrics"] == {"rmse": 0.7}
    assert card["test_metrics"] == {"rmse": 0.9}
    assert card["demo_mode"] is False
    assert card["fallback_used"] is False
```

- [ ] **Step 2: Run tests and verify RED**

Expected: failures because prepared training and model-card helpers do not exist.

- [ ] **Step 3: Implement prepared training lifecycle**

Add an internal `prepared_manifest_path` argument to `submit_training_job()` and `ActivityTrainer.start_training()`. The web route resolves a server-issued dataset ID beneath `ACTIVITY_PREPARED_DATA_DIR`; direct callers may pass a validated local path. When present:

1. Verify every artifact hash.
2. Train only on `train.csv`.
3. Use `validation.csv` for epoch selection and early stopping.
4. Evaluate `test.csv` exactly once after selecting best weights.
5. Reject classification evaluation if the test set has one class.
6. Write the model card atomically before registry registration.
7. Remove weights and model-card files if registration fails.

Regression metrics are RMSE, MAE, and R². Classification metrics are ROC-AUC, PR-AUC, balanced accuracy, and confusion-matrix counts. All metrics must be finite JSON numbers.

Keep the legacy raw CSV path working, but mark its metadata with `scientific_readiness="legacy_unvalidated"`; only prepared training receives `scientific_readiness="endpoint_ready"`.

- [ ] **Step 4: Run tests and verify GREEN**

Run:

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests/test_activity_prediction_contract.py tests/test_activity_model_registry.py tests/test_activity_endpoint_selection.py -q -p no:cacheprovider
```

- [ ] **Step 5: Commit Task 6**

```powershell
git add -- src/activity/model_card.py src/activity/trainer.py tests/test_activity_prediction_contract.py tests/test_activity_endpoint_selection.py
git commit -m "feat: train RG-MPNN from prepared datasets"
```

## Task 7: Endpoint-aware inference, Agent tool, and API

**Files:**
- Modify: `src/activity/predictor.py`
- Modify: `src/agent/tools/activity_predictor_tool.py`
- Modify: `src/web/routes/api_routes.py`
- Modify: `tests/test_activity_endpoint_selection.py`
- Modify: `tests/test_activity_prediction_contract.py`

- [ ] **Step 1: Write failing inference tests**

```python
class StubEndpointPredictor:
    def __init__(self, endpoint_key: str, value: float):
        self.endpoint_key = endpoint_key
        self.value = value
        self.demo_mode = False
        self.current_model_metadata = {
            "model_id": "pde-model",
            "weights_sha256": "a" * 64,
            "endpoint": "pIC50",
            "endpoint_key": endpoint_key,
            "units": "pIC50",
            "task_type": "regression",
        }

    def predict_for_endpoint(self, smiles, endpoint_key: str):
        assert endpoint_key == self.endpoint_key
        values = [smiles] if isinstance(smiles, str) else list(smiles)
        return [{
            "smiles": item,
            "success": True,
            "task_type": "regression",
            "endpoint": "pIC50",
            "endpoint_key": endpoint_key,
            "value": self.value,
            "units": "pIC50",
        } for item in values]


def test_predict_for_endpoint_never_falls_back_to_other_target(monkeypatch) -> None:
    predictor = ActivityPredictor()
    monkeypatch.setattr(predictor, "_resolve_endpoint_model", lambda _key: None)

    result = predictor.predict_for_endpoint(
        "CCO", endpoint_key="p54750:pic50:pic50:regression"
    )

    assert result == [{
        "smiles": "CCO",
        "success": False,
        "endpoint_key": "p54750:pic50:pic50:regression",
        "error": "No active RG-MPNN model is registered for the requested endpoint.",
    }]


def test_activity_tool_preserves_requested_endpoint_provenance() -> None:
    tool = ActivityPredictorTool()
    tool._predictor = StubEndpointPredictor(
        endpoint_key="p54750:pic50:pic50:regression",
        value=6.8,
    )
    result = tool.execute({
        "query": "预测该分子的 PDE5A pIC50: CCO",
        "endpoint_key": "p54750:pic50:pic50:regression",
    })
    assert result["quality"]["model_provenance"]["endpoint_key"] == (
        "p54750:pic50:pic50:regression"
    )
```

- [ ] **Step 2: Run tests and verify RED**

Expected: failures because inference and tool calls do not accept endpoint identity.

- [ ] **Step 3: Implement isolated endpoint model loading**

Add `predict_for_endpoint(smiles, endpoint_key)` and resolve only `registry.get_active_for_endpoint(endpoint_key)`. Cache loaded model instances by `(model_id, weights_sha256)` under a lock; a model switch invalidates only the affected endpoint cache entry.

Extend the tool input contract to accept either the legacy query string or a structured mapping containing `query` and `endpoint_key`. Structured scientific workflows must provide `endpoint_key`; absent or unmatched endpoints return `success=false` and no numeric prediction.

Extend APIs:

```text
POST /api/activity/predict
  form: smiles, endpoint_key (optional compatibility field)

POST /api/activity/models/switch
  json: {"model_id": "...", "endpoint_key": "..."}

POST /api/activity/train
  form: prepared_dataset_id or legacy uploaded file
```

Do not accept arbitrary absolute prepared paths from remote clients. Resolve prepared manifests beneath configured `ACTIVITY_PREPARED_DATA_DIR` and accept a server-issued dataset ID.

- [ ] **Step 4: Run tests and verify GREEN**

Run:

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests/test_activity_prediction_contract.py tests/test_activity_endpoint_selection.py tests/agent/test_tool_registry.py -q -p no:cacheprovider
```

- [ ] **Step 5: Commit Task 7**

```powershell
git add -- src/activity/predictor.py src/agent/tools/activity_predictor_tool.py src/web/routes/api_routes.py tests/test_activity_endpoint_selection.py tests/test_activity_prediction_contract.py tests/agent/test_tool_registry.py
git commit -m "feat: route activity predictions by scientific endpoint"
```

## Task 8: Health checks, documentation, and full verification

**Files:**
- Modify: `scripts/health_check.py`
- Modify: `tests/test_activity_model_registry.py`
- Modify: `docs/PROJECT_STANDARDS.md`
- Modify: `data/REGISTRY.md`

- [ ] **Step 1: Write failing health tests**

```python
def test_activity_health_distinguishes_endpoint_ready_models(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ACTIVITY_MODEL_DIR", str(tmp_path))
    registry = ActivityModelRegistry(tmp_path)
    model = register_endpoint_model(
        registry,
        "pde-model",
        "p54750:pic50:pic50:regression",
    )
    registry.select_for_endpoint(model["endpoint_key"], model["model_id"])
    ok, detail = health_check.check_activity_models()
    assert ok is True
    assert "endpoint-ready" in detail


def test_activity_health_rejects_legacy_only_registry(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ACTIVITY_MODEL_DIR", str(tmp_path))
    weights = tmp_path / "legacy.pt"
    weights.write_bytes(b"legacy checkpoint")
    metadata = legacy_metadata("legacy", weights.name)
    metadata["weights_sha256"] = sha256(weights.read_bytes()).hexdigest()
    ActivityModelRegistry(tmp_path).register(metadata)
    ok, detail = health_check.check_activity_models()
    assert ok is False
    assert "legacy-unvalidated" in detail
```

- [ ] **Step 2: Run health tests and verify RED**

Run the two named tests. Expected: health output does not yet distinguish scientific readiness.

- [ ] **Step 3: Implement health semantics and documentation**

Health is OK only when at least one valid `endpoint_ready` model has matching weight/model-card hashes and an active endpoint mapping. Document:

- real datasets and prepared artifacts remain outside Git;
- required manifest columns and accepted units;
- validation-only and preparation commands;
- PDE and BuChE use separate endpoint keys;
- model training is not complete until real held-out metrics and model-card verification pass.

- [ ] **Step 4: Run all relevant verification**

Run:

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests/test_activity_dataset_contract.py tests/test_activity_prepare_cli.py tests/test_activity_endpoint_selection.py tests/test_activity_prediction_contract.py tests/test_activity_model_registry.py tests/test_rg_mpnn_legacy_modules.py tests/agent/test_tool_registry.py -q -p no:cacheprovider
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m compileall -q src scripts
C:\Users\xkx52\.conda\envs\MedChat\python.exe scripts/health_check.py --strict
git diff --check
```

Expected before real data/model delivery: all automated tests and compile checks pass; strict health remains nonzero with an honest message that no endpoint-ready activity model is installed.

- [ ] **Step 5: Commit Task 8**

```powershell
git add -- scripts/health_check.py tests/test_activity_model_registry.py docs/PROJECT_STANDARDS.md data/REGISTRY.md
git commit -m "docs: define endpoint-ready activity model operations"
```

## Task 9: Review and PR preparation

**Files:**
- Review all files changed since `origin/main`.

- [ ] **Step 1: Verify scope and secret safety**

```powershell
git status --short
git diff --stat origin/main...HEAD
git diff --check origin/main...HEAD
git diff origin/main...HEAD | Select-String -Pattern 'sk-[A-Za-z0-9_-]{16,}|API_KEY\s*=\s*["''][^"'']+["'']'
```

Expected: only activity-readiness code/tests/docs plus the approved design and plan; secret scan returns no matches.

- [ ] **Step 2: Run regression suite**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests/agent -q -p no:cacheprovider
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests/test_agent_anti_hallucination_fallbacks.py tests/test_agent_platform_health_check.py tests/agent/test_real_acceptance_checks.py -q -p no:cacheprovider
```

Expected: all tests pass. Any dependency skip or failure is reported verbatim and is not converted to success.

- [ ] **Step 3: Request code review and address findings**

Review against:

- no fabricated scientific values;
- no cross-target model fallback;
- no data/scaffold leakage;
- atomic artifacts and confined paths;
- backward compatibility for legacy non-scientific calls;
- no real data, model weights, or credentials in Git.

- [ ] **Step 4: Push and create a draft PR**

```powershell
git push -u origin codex/activity-data-readiness
```

Create a draft PR to `main` summarizing scope, tests, strict-health expected failure, and the remaining requirement for owner-provided PDE/BuChE data. Do not merge it automatically.
