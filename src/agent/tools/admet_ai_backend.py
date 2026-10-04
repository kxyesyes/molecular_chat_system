"""Small, lazy adapter around the real ADMET-AI 1.4.0 Python API.

The package is optional at import time so contract tests and the rest of the
Agent can still start when the scientific model environment is not installed.
When it is absent this module returns no scientific values; callers must report
the capability as unavailable instead of falling back to RDKit rules.
"""

from __future__ import annotations

from hashlib import sha256
from importlib import import_module
import json
import os
from pathlib import Path
import queue
import subprocess
from threading import RLock, Thread, current_thread
from typing import Any

import math

try:
    from rdkit import Chem
except ImportError:  # pragma: no cover - exercised in dependency probes
    Chem = None


ADMET_AI_VERSION = "1.4.0"
_BACKEND_LOCK = RLock()
_DEFAULT_BACKEND: "ADMETAIBackend | None" = None
_DEFAULT_BACKEND_ERROR: str | None = None


def _canonical_smiles(value: str) -> str | None:
    if Chem is None:
        raise RuntimeError("RDKit validation is unavailable")
    if (not isinstance(value, str) or not value.strip() or len(value) > 8192
            or any(character.isspace() for character in value.strip())):
        return None
    try:
        from rdkit import rdBase
        params = Chem.SmilesParserParams()
        params.parseName = False
        params.allowCXSMILES = False
        with rdBase.BlockLogs():
            molecule = Chem.MolFromSmiles(value.strip(), params)
        return Chem.MolToSmiles(molecule) if molecule is not None and molecule.GetNumAtoms() else None
    except Exception as exc:
        raise RuntimeError("RDKit structure validation failed") from exc


def _weight_digest(package_root: Any) -> str:
    """Hash package-bundled model bytes without exposing their local path."""
    model_root = package_root.joinpath("resources", "models")
    entries: list[tuple[str, bytes]] = []

    def visit(node: Any, relative: str = "") -> None:
        if node.is_dir():
            for child in sorted(node.iterdir(), key=lambda item: item.name):
                visit(child, f"{relative}/{child.name}".lstrip("/"))
        elif node.name.endswith(".pt"):
            entries.append((relative, node.read_bytes()))

    visit(model_root)
    if not entries:
        raise RuntimeError("ADMET-AI model weights were not found")
    digest = sha256()
    for name, content in entries:
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(content)
    return f"sha256:{digest.hexdigest()}"


def _finite(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(float(value))


class ADMETAIBackend:
    """A cached, batch-oriented ADMET-AI 1.4.0 model boundary."""

    def __init__(
        self,
        *,
        model: Any,
        package_version: str = ADMET_AI_VERSION,
        weights_id: str,
        endpoint_info: dict[str, dict[str, str]] | None = None,
        predict_lock: RLock | None = None,
    ) -> None:
        self.model = model
        self.version = package_version
        self.weights_id = weights_id
        self.endpoint_info = endpoint_info or {}
        self._predict_lock = predict_lock or RLock()

    @classmethod
    def from_installed_package(cls) -> "ADMETAIBackend":
        try:
            package = import_module("admet_ai")
            if str(getattr(package, "__version__", "")) != ADMET_AI_VERSION:
                raise RuntimeError(
                    f"ADMET-AI {ADMET_AI_VERSION} is required; "
                    f"found {getattr(package, '__version__', 'unknown')}"
                )
            from admet_ai import ADMETModel
            from admet_ai.admet_info import get_admet_info
            from importlib import resources

            package_root = resources.files("admet_ai")
            info = get_admet_info()
            endpoint_info = {
                str(row["id"]): {
                    "name": str(row["name"]),
                    "category": str(row["category"]),
                    "task_type": str(row["task_type"]),
                    "unit": str(row["units"]),
                }
                for _, row in info.iterrows()
            }
            return cls(
                # The persistent service must not retain an unbounded molecule
                # cache across requests.  Batch size is bounded by the tool.
                model=ADMETModel(num_workers=0, cache_molecules=False),
                package_version=ADMET_AI_VERSION,
                weights_id=_weight_digest(package_root),
                endpoint_info=endpoint_info,
            )
        except Exception as exc:
            raise RuntimeError(f"ADMET-AI {ADMET_AI_VERSION} unavailable: {exc}") from exc

    def predict_batch(
        self,
        smiles: list[str],
        molecule_ids: list[str],
    ) -> list[dict[str, Any]]:
        if len(smiles) != len(molecule_ids):
            raise ValueError("SMILES and molecule ID counts do not match")

        rows: list[dict[str, Any] | None] = [None] * len(smiles)
        valid_smiles: list[str] = []
        valid_ids: list[str] = []
        valid_indices: list[int] = []
        for index, (smiles_value, molecule_id) in enumerate(zip(smiles, molecule_ids)):
            canonical = _canonical_smiles(smiles_value)
            if canonical is None:
                rows[index] = {
                    "molecule_id": molecule_id,
                    "smiles": smiles_value,
                    "canonical_smiles": None,
                    "status": "failed",
                    "error": "Invalid SMILES; ADMET-AI was not called",
                    "admet": {},
                    "warnings": ["invalid_smiles"],
                }
                continue
            valid_smiles.append(smiles_value)
            valid_ids.append(molecule_id)
            valid_indices.append(index)

        if valid_smiles:
            with self._predict_lock:
                predictions = self.model.predict(smiles=valid_smiles)
            if not hasattr(predictions, "iloc") or len(predictions) != len(valid_smiles):
                raise RuntimeError("ADMET-AI returned a row count that does not match the input")
            if list(predictions.index) != valid_smiles:
                raise RuntimeError("ADMET-AI returned a mismatched row identity index")
            for row_index, original_index in enumerate(valid_indices):
                row = predictions.iloc[row_index]
                try:
                    rows[original_index] = self._row_from_prediction(
                        valid_smiles[row_index], valid_ids[row_index], row
                    )
                except RuntimeError as exc:
                    rows[original_index] = {
                        "molecule_id": valid_ids[row_index], "smiles": valid_smiles[row_index],
                        "canonical_smiles": _canonical_smiles(valid_smiles[row_index]),
                        "status": "failed", "error": str(exc), "admet": {},
                        "warnings": ["invalid_model_output"],
                    }
        return [row for row in rows if row is not None]

    def _row_from_prediction(self, smiles: str, molecule_id: str, row: Any) -> dict[str, Any]:
        endpoints: dict[str, dict[str, Any]] = {}
        percentiles: dict[str, float] = {}
        for key, raw_value in row.to_dict().items():
            value = raw_value.item() if hasattr(raw_value, "item") else raw_value
            if not _finite(value):
                raise RuntimeError(f"ADMET-AI returned a non-finite value for {key}")
            key = str(key)
            if key.endswith("_drugbank_approved_percentile"):
                percentiles[key] = float(value)
                continue
            info = self.endpoint_info.get(key, {})
            endpoints[key] = {
                "value": float(value),
                "unit": info.get("unit", "unknown"),
                "task_type": info.get("task_type", "unknown"),
                "category": info.get("category", "unknown"),
                "name": info.get("name", key),
                "source": (
                    "rdkit_calculation"
                    if info.get("category") == "Physicochemical"
                    else "admet_ai_model"
                ),
            }

        classification = [
            key for key, endpoint in endpoints.items()
            if endpoint["task_type"] == "classification"
        ]
        adverse = [
            key for key in classification
            if endpoints[key]["category"] == "Toxicity"
            or any(marker in key for marker in ("CYP", "Pgp", "hERG", "DILI", "NR-", "SR-"))
        ]
        risk_endpoint_ids = [key for key in adverse if endpoints[key]["value"] >= 0.5]
        admet = {
            "prediction_method": "admet_ai",
            "backend_version": self.version,
            "model_version": self.version,
            "model_name": "ADMET-AI",
            "weights_id": self.weights_id,
            "demo_mode": False,
            "fallback_used": False,
            "device": str(getattr(self.model, "device", "unknown")),
            "physicochemical_source": "rdkit_calculation",
            "endpoints": endpoints,
            "units": {key: value["unit"] for key, value in endpoints.items()},
            "reference_percentiles": percentiles,
            "risk_endpoint_ids": risk_endpoint_ids,
            "risk_threshold": 0.5,
            "risk_summary_method": "adverse_classification_probability_ge_0.5",
            "risk_count": len(risk_endpoint_ids),
            "total_endpoints": len(adverse),
            # Keep legacy sections present but empty unless a direct semantic
            # mapping exists; never relabel a different ADMET-AI endpoint as
            # ESOL, WLogP, BBB bool, or a rule conclusion.
            "physicochemical": {},
            "solubility": {},
            "lipophilicity": {},
            "pharmacokinetics": {},
            "druglikeness": {},
            "medicinal": {},
        }
        return {
            "molecule_id": molecule_id,
            "smiles": smiles,
            "canonical_smiles": _canonical_smiles(smiles),
            "status": "succeeded",
            "admet": admet,
            "warnings": [
                "ADMET-AI outputs are model predictions; they are not experimental results.",
                "Physicochemical endpoints are calculated by RDKit inside ADMET-AI.",
                "Risk summary is a screening heuristic, not a safety conclusion.",
            ],
        }


class ADMETAISubprocessBackend:
    """Use a dedicated Python 3.10 environment without changing MedChat's Torch.

    ADMET-AI 1.4.0 pins ``torch==2.5.0`` while the deployment profile pins a
    different scientific stack.  This JSON-lines worker keeps the model and
    its cache in the compatible environment and exposes the same batch
    boundary to the Agent process.
    """

    def __init__(self, python_executable: str, worker_script: str | Path | None = None):
        if not python_executable or not Path(python_executable).is_file():
            raise RuntimeError("ADMET_AI_PYTHON does not point to a Python executable")
        self._assert_python310(python_executable)
        if worker_script is None:
            worker_script = Path(__file__).resolve().parents[3] / "scripts" / "admet_ai_worker.py"
        self.python_executable = str(python_executable)
        self.worker_script = str(worker_script)
        if not Path(self.worker_script).is_file():
            raise RuntimeError("ADMET-AI worker script is missing")
        self._lock = RLock()
        self._responses: queue.Queue[dict[str, Any]] = queue.Queue()
        self._process = subprocess.Popen(
            [self.python_executable, self.worker_script],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        self._reader = Thread(target=self._read_responses, daemon=True)
        self._reader.start()
        try:
            ready = self._responses.get(timeout=60)
        except queue.Empty as exc:
            self.close()
            raise RuntimeError("ADMET-AI worker did not become ready") from exc
        if ready.get("ready") is not True:
            self.close()
            raise RuntimeError(str(ready.get("error") or "ADMET-AI worker failed to start"))
        self.version = str(ready.get("version") or ADMET_AI_VERSION)
        self.weights_id = str(ready.get("weights_id") or "")
        worker_python = str(ready.get("python_version") or "")
        if (
            self.version != ADMET_AI_VERSION
            or not self.weights_id.startswith("sha256:")
            or worker_python != "3.10"
        ):
            self.close()
            raise RuntimeError("ADMET-AI worker reported invalid model identity or Python version")

    @staticmethod
    def _assert_python310(python_executable: str) -> None:
        try:
            result = subprocess.run(
                [python_executable, "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError("Unable to verify ADMET_AI_PYTHON Python version") from exc
        version = result.stdout.strip()
        if version != "3.10":
            raise RuntimeError(f"ADMET_AI_PYTHON must use Python 3.10; found {version or 'unknown'}")

    def _read_responses(self) -> None:
        stdout = self._process.stdout
        if stdout is None:
            return
        for line in stdout:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                value = {"ok": False, "error": "ADMET-AI worker returned invalid JSON"}
            if isinstance(value, dict):
                self._responses.put(value)
        self._responses.put({"ok": False, "error": "ADMET-AI worker exited"})

    def predict_batch(
        self,
        smiles: list[str],
        molecule_ids: list[str],
        *,
        timeout_seconds: float | None = None,
    ) -> list[dict[str, Any]]:
        with self._lock:
            if self._process.poll() is not None or self._process.stdin is None:
                raise RuntimeError("ADMET-AI worker is not running")
            self._process.stdin.write(json.dumps({
                "smiles": smiles,
                "molecule_ids": molecule_ids,
            }, ensure_ascii=False) + "\n")
            self._process.stdin.flush()
            try:
                response = self._responses.get(timeout=timeout_seconds)
            except queue.Empty as exc:
                self.close()
                raise TimeoutError(
                    f"ADMET-AI worker inference timed out after {timeout_seconds:g} seconds"
                ) from exc
            if response.get("ok") is not True:
                raise RuntimeError(str(response.get("error") or "ADMET-AI worker prediction failed"))
            rows = response.get("rows")
            if not isinstance(rows, list):
                raise RuntimeError("ADMET-AI worker returned no result rows")
            return rows

    def predict_batch_with_timeout(
        self,
        smiles: list[str],
        molecule_ids: list[str],
        timeout_seconds: float,
    ) -> list[dict[str, Any]]:
        return self.predict_batch(
            smiles,
            molecule_ids,
            timeout_seconds=timeout_seconds,
        )

    def close(self) -> None:
        process = getattr(self, "_process", None)
        if process is None:
            return
        if process.poll() is None:
            try:
                if process.stdin is not None:
                    process.stdin.close()
            except OSError:
                pass
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        reader = getattr(self, "_reader", None)
        if reader is not None and reader is not current_thread():
            reader.join(timeout=5)

    def __del__(self):  # pragma: no cover - interpreter shutdown cleanup
        try:
            self.close()
        except Exception:
            pass


def get_admet_ai_backend() -> ADMETAIBackend | None:
    """Return one cached local backend, or ``None`` without fabricating values."""
    global _DEFAULT_BACKEND, _DEFAULT_BACKEND_ERROR
    with _BACKEND_LOCK:
        if _DEFAULT_BACKEND is not None:
            return _DEFAULT_BACKEND
        if _DEFAULT_BACKEND_ERROR is not None:
            return None
        worker_python = os.environ.get("ADMET_AI_PYTHON", "").strip()
        if not worker_python:
            _DEFAULT_BACKEND_ERROR = (
                "ADMET_AI_PYTHON is required; configure the isolated Python 3.10 worker"
            )
            return None
        try:
            _DEFAULT_BACKEND = ADMETAISubprocessBackend(worker_python)
        except Exception as exc:
            _DEFAULT_BACKEND_ERROR = str(exc)
            return None
        return _DEFAULT_BACKEND


def admet_ai_backend_error() -> str | None:
    return _DEFAULT_BACKEND_ERROR


def reset_admet_ai_backend_cache() -> None:
    """Test/deployment hook; it does not delete package weights or caches."""
    global _DEFAULT_BACKEND, _DEFAULT_BACKEND_ERROR
    with _BACKEND_LOCK:
        backend = _DEFAULT_BACKEND
        close = getattr(backend, "close", None)
        if callable(close):
            close()
        _DEFAULT_BACKEND = None
        _DEFAULT_BACKEND_ERROR = None
