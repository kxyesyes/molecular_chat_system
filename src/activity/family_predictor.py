"""Pinned family inference; never selects a legacy/global fallback checkpoint."""
from __future__ import annotations

import copy
import hashlib
import io
import json
import math
import threading

from rdkit import rdBase

from .dataset_contract import _canonical_parent
from .family_contract import LABEL_THRESHOLD, PROBABILITY_THRESHOLD, resolve_activity_family
from .model_card import _read_snapshot
from .predictor import ActivityPredictor


class _PinnedPredictor(ActivityPredictor):
    """Reuse RGNN features/forward semantics, but load only verified bytes."""

    def __init__(self, metadata, content):
        super().__init__()
        self._metadata = copy.deepcopy(metadata)
        self._content = content

    def _find_checkpoint(self):
        raise RuntimeError("Pinned inference cannot discover checkpoints")

    def load(self):
        if self._loaded:
            return
        import torch
        from .rg_mpnn.Nets.ReduceGNN import RGNN

        if hashlib.sha256(self._content).hexdigest() != self._metadata["weights_sha256"]:
            raise ValueError("Pinned weight digest mismatch")
        # Fail closed on PyTorch versions without restricted loading support.
        state = torch.load(io.BytesIO(self._content), map_location=self.device, weights_only=True)
        config = self._metadata["model_config"]
        self.model = RGNN(**{key: config[key] for key in (
            "in_channels", "channels", "out_channels", "edge_dim", "num_passing_atom",
            "num_passing_pool", "num_passing_rg", "num_passing_mol", "dropout")}).to(self.device)
        self.model.load_state_dict(state["state_dict"] if "state_dict" in state else state, strict=True)
        self.model.eval()
        self.current_model_metadata = copy.deepcopy(self._metadata)
        self.model_config = copy.deepcopy(config)
        self.demo_mode = False
        self._loaded = True
        self._content = b""


def _row(smiles, target):
    return dict(smiles=smiles, requested_target=target, family_id=None, bundle_id=None,
                success=False, status="failed", activity_class=None, activity_probability=None,
                predicted_pIC50=None, units="pIC50", label_threshold=LABEL_THRESHOLD,
                probability_threshold=PROBABILITY_THRESHOLD,
                classification_regression_consistent=None, warnings=[], errors={}, provenance={})


def _stage_results(stage, inputs, task):
    """Strict alignment before attaching a numeric result to any input molecule."""
    try:
        outputs = stage.predict(inputs)
        if not isinstance(outputs, list) or len(outputs) != len(inputs):
            raise ValueError("Stage output count mismatch")
        if any(not isinstance(r, dict) or r.get("smiles") != smi for r, smi in zip(outputs, inputs)):
            raise ValueError("Stage output alignment mismatch")
        return outputs
    except Exception:
        # Do not echo library exceptions, paths, source molecules or credentials.
        return [dict(smiles=s, success=False, error=f"{task}_execution_failed") for s in inputs]


def _value(result, task):
    key, endpoint, units = (("probability", "activity", "probability") if task == "classification"
                            else ("value", "pIC50", "pIC50"))
    if (result.get("success") is not True or result.get("task_type") != task
            or result.get("endpoint") != endpoint or result.get("units") != units
            or result.get("demo_mode", False) is not False
            or result.get("fallback_used", False) is not False):
        raise ValueError("Stage failed or output contract invalid")
    value = result.get(key)
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("Stage output is not finite numeric data")
    if task == "classification" and not 0 <= value <= 1:
        raise ValueError("Probability outside unit interval")
    return float(value)


def _warnings(row, result, task):
    warnings = result.get("warnings", [])
    if isinstance(warnings, list):
        row["warnings"].extend(f"[{task}] {warning}" for warning in warnings if isinstance(warning, str))


def _provenance(bundle):
    fields = ("model_id", "target_id", "task_type", "endpoint", "units", "weights_sha256",
              "model_card_sha256", "prepared_dataset_sha256", "dataset_sha256", "source_sha256",
              "model_contract_key", "scientific_readiness", "demo_mode", "fallback_used")
    result = {key: copy.deepcopy(bundle[key]) for key in (
        "family_id", "bundle_id", "source_sha256", "assignment_sha256", "scope",
        "label_threshold", "probability_threshold")}
    result["models"] = {task: {key: copy.deepcopy(record[key]) for key in fields if key in record}
                        for task, record in bundle["models"].items()}
    return result


class FamilyActivityPredictor:
    """Two-stage backend API with a bounded, per-family pinned-pair cache.

    Registry validation runs on every request, including cache hits. This class
    neither trains models nor activates a bundle, and never uses global selection.
    """

    def __init__(self, registry):
        self.registry = registry
        self._cache = {}
        self._lock = threading.RLock()

    def _pair(self, family):
        bundle = self.registry.get_active_family_bundle(family)
        if bundle is None:
            raise ValueError("Family model bundle unavailable")
        if (bundle["family_id"] != family or bundle["label_threshold"] != LABEL_THRESHOLD
                or bundle["probability_threshold"] != PROBABILITY_THRESHOLD):
            raise ValueError("Family bundle contract mismatch")
        contents = {}
        for task in ("classification", "regression"):
            metadata = bundle["models"][task]
            if (metadata.get("target_id") != family or metadata.get("task_type") != task
                    or metadata.get("scientific_readiness") != "endpoint_ready"
                    or metadata.get("demo_mode") is not False or metadata.get("fallback_used") is not False):
                raise ValueError("Family model not ready")
            contents[task] = _read_snapshot(self.registry.resolve_weights(metadata["model_id"]))
            if hashlib.sha256(contents[task]).hexdigest() != metadata["weights_sha256"]:
                raise ValueError("Family model digest mismatch")
        key = hashlib.sha256(json.dumps(bundle, sort_keys=True, allow_nan=False).encode()).hexdigest()
        cached = self._cache.get(family)
        if cached is None or cached[0] != key:
            stages = {task: _PinnedPredictor(bundle["models"][task], contents[task]) for task in contents}
            self._cache[family] = (key, stages)
        return copy.deepcopy(bundle), self._cache[family][1]

    def predict(self, smiles, *, target):
        inputs = [smiles] if isinstance(smiles, str) else list(smiles)
        rows = [_row(smi, target) for smi in inputs]
        try:
            family = resolve_activity_family(target)
        except ValueError:
            for row in rows:
                row["errors"]["input"] = "unknown_or_ambiguous_family"
            return rows
        valid = []
        for index, smi in enumerate(inputs):
            row = rows[index]
            row["family_id"] = family
            with rdBase.BlockLogs():
                try:
                    canonical, _, reason = _canonical_parent(smi)
                except ValueError:
                    canonical, reason = None, "structure_processing_failed"
            if reason or canonical is None:
                row["errors"]["input"] = reason or "invalid_smiles"
            else:
                row["canonical_smiles"] = canonical
                valid.append(index)
        if not valid:
            return rows
        with self._lock:
            try:
                bundle, stages = self._pair(family)
            except Exception:
                self._cache.pop(family, None)
                for index in valid:
                    rows[index]["errors"]["bundle"] = "family_model_bundle_unavailable_or_invalid"
                return rows
            for index in valid:
                rows[index]["bundle_id"] = bundle["bundle_id"]
                rows[index]["provenance"] = _provenance(bundle)
            canonical = [rows[i]["canonical_smiles"] for i in valid]
            outputs = _stage_results(stages["classification"], canonical, "classification")
            regress = []
            for index, result in zip(valid, outputs):
                row = rows[index]
                _warnings(row, result, "classification")
                try:
                    probability = _value(result, "classification")
                except (ValueError, OverflowError):
                    row["errors"]["classification"] = "classification_failed_or_invalid_output"
                    continue
                row.update(activity_probability=probability,
                           activity_class="有活性" if probability >= PROBABILITY_THRESHOLD else "无活性",
                           status="partial")
                regress.append(index)
            if regress:
                outputs = _stage_results(stages["regression"], [rows[i]["canonical_smiles"] for i in regress], "regression")
                for index, result in zip(regress, outputs):
                    row = rows[index]
                    _warnings(row, result, "regression")
                    try:
                        value = _value(result, "regression")
                    except (ValueError, OverflowError):
                        row["errors"]["regression"] = "regression_failed_or_invalid_output"
                        continue
                    consistent = (row["activity_probability"] >= PROBABILITY_THRESHOLD) == (value >= LABEL_THRESHOLD)
                    row.update(success=True, status="passed", predicted_pIC50=value,
                               classification_regression_consistent=consistent)
                    if not consistent:
                        row["warnings"].append("分类与回归预测不一致，已保留两项原始结果。")
        return rows
