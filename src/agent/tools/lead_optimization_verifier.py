from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from typing import Any


class LeadOptimizationVerifier:
    """Compare a lead and generated candidates on the same requested metrics.

    This tool only evaluates evidence already produced by scientific tools. It
    never fills a missing endpoint, calls a model, or treats generation as
    proof that an optimization target was met.
    """

    name = "lead_optimization_verifier"
    version = "1"
    description = (
        "Verify lead-optimization objectives against matched baseline and "
        "candidate evidence without inventing missing predictions."
    )

    def execute(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, Mapping):
            return self._error("invalid_input", "Optimization verification input must be an object")
        outputs = payload.get("outputs")
        if not isinstance(outputs, Mapping):
            return self._error("invalid_input", "Optimization verification requires workflow outputs")

        query = payload.get("query")
        metadata = payload.get("metadata")
        if isinstance(metadata, Mapping):
            query = metadata.get("query", query)
        if not isinstance(query, str):
            query = ""

        baseline_rows = self._rows(outputs.get("baseline"))
        candidate_rows = self._candidate_rows(outputs.get("candidates"))
        if not baseline_rows or not candidate_rows:
            return self._error(
                "missing_workflow_evidence",
                "Baseline and generated candidate evidence are both required",
            )

        objectives = self._objectives(query)
        unverifiable_constraints = self._unverifiable_constraints(query)
        baseline = self._first_by_smiles(baseline_rows)
        baseline_admet = self._first_by_smiles(self._rows(outputs.get("baseline_admet")))
        baseline_activity = self._first_by_smiles(self._rows(outputs.get("baseline_activity")))
        baseline_properties_row = next(iter(baseline.values()), None)
        baseline_admet_row = next(iter(baseline_admet.values()), None)
        baseline_activity_row = next(iter(baseline_activity.values()), None)
        candidate_properties = self._by_smiles(self._rows(outputs.get("candidate_properties")))
        candidate_admet = self._by_smiles(self._rows(outputs.get("candidate_admet")))
        candidate_activity = self._by_smiles(self._rows(outputs.get("candidate_activity")))

        results: list[dict[str, Any]] = []
        for candidate in candidate_rows:
            smiles = candidate.get("smiles") or candidate.get("canonical_smiles")
            key = self._smiles_key(smiles)
            comparisons: list[dict[str, Any]] = []
            missing: list[str] = []
            for metric, direction in objectives:
                baseline_value = self._metric_value(
                    metric,
                    baseline_properties_row,
                    baseline_admet_row,
                    baseline_activity_row,
                )
                candidate_value = self._metric_value(
                    metric,
                    candidate_properties.get(key),
                    candidate_admet.get(key),
                    candidate_activity.get(key),
                )
                if baseline_value is None or candidate_value is None:
                    missing.append(metric)
                    continue
                if direction == "lower":
                    satisfied = candidate_value < baseline_value
                elif direction == "higher":
                    satisfied = candidate_value > baseline_value
                else:
                    satisfied = candidate_value >= baseline_value
                comparisons.append({
                    "metric": metric,
                    "direction": direction,
                    "baseline_value": round(baseline_value, 8),
                    "candidate_value": round(candidate_value, 8),
                    "delta": round(candidate_value - baseline_value, 8),
                    "satisfied": satisfied,
                })

            if not objectives or missing or unverifiable_constraints:
                status = "unverified"
            elif all(item["satisfied"] for item in comparisons):
                status = "supported"
            else:
                status = "evaluated_not_improved"
            results.append({
                "candidate_id": candidate.get("candidate_id") or f"candidate-{len(results) + 1:03d}",
                "smiles": smiles,
                "status": status,
                "goal_supported": status == "supported",
                "comparisons": comparisons,
                "missing_evidence": missing,
                "unverified_constraints": list(unverifiable_constraints),
            })

        if any(item["status"] == "unverified" for item in results) or unverifiable_constraints:
            overall_status = "unverified"
        elif any(item["status"] == "supported" for item in results):
            overall_status = "supported"
        elif objectives and all(
            item["status"] == "evaluated_not_improved" for item in results
        ):
            overall_status = "evaluated_not_improved"
        else:
            overall_status = "unverified"

        warnings: list[str] = []
        if not objectives:
            warnings.append("未识别到明确的优化指标，未验证目标是否改善")
        if any(item["status"] == "unverified" for item in results):
            warnings.append("部分候选缺少与基线对应的真实端点证据，结果未验证")
        if unverifiable_constraints:
            warnings.append("存在尚未由结构工具核验的结构约束，不能宣称目标已满足")
        if overall_status != "supported":
            warnings.append("未宣称先导优化完成；请补充证据或调整约束后重新评估")

        data = {
            "status": overall_status,
            "objectives": [
                {"metric": metric, "direction": direction}
                for metric, direction in objectives
            ],
            "unverifiable_constraints": list(unverifiable_constraints),
            "baseline_smiles": next(iter(baseline)) if baseline else None,
            "candidates": results,
            "supported_candidate_count": sum(item["goal_supported"] for item in results),
        }
        return {
            "success": True,
            "message": (
                "Optimization objectives are supported by matched evidence"
                if overall_status == "supported"
                else "Optimization result is not verified"
            ),
            "data": data,
            "formatted": self._format(data),
            "warnings": warnings,
            "evidence": [{
                "type": "lead_optimization_comparison",
                "method": "same_metric_baseline_candidate_comparison",
                "status": overall_status,
            }],
            "quality": {
                "output_contract": "LeadOptimizationVerification@1",
                "scientific_claim_scope": "optimization_evidence_only",
                "goal_supported": overall_status == "supported",
            },
        }

    @classmethod
    def _objectives(cls, query: str) -> list[tuple[str, str]]:
        lowered = query.casefold()
        objectives: list[tuple[str, str]] = []
        if "logp" in lowered and re.search(r"降低|减少|下降|lower|reduce|decrease", lowered):
            objectives.append(("logp", "lower"))
        if "qed" in lowered and re.search(r"提高|提升|增加|higher|improve|increase", lowered):
            objectives.append(("qed", "higher"))
        if ("溶解" in lowered or "solubility" in lowered) and re.search(
            r"提高|提升|增加|higher|improve|increase", lowered
        ):
            objectives.append(("solubility", "higher"))
        if any(token in lowered for token in ("活性", "potency", "activity")):
            if re.search(r"保持.{0,8}(活性|potency|activity)|保留.{0,8}(活性|potency|activity)|"
                         r"(活性|potency|activity).{0,8}(保持|保留|维持|maintain|preserve|retain)", lowered):
                direction = "preserve"
            elif re.search(r"提高|提升|增加|higher|improve|increase", lowered):
                direction = "higher"
            elif re.search(r"降低|减少|下降|lower|reduce|decrease", lowered):
                direction = "lower"
            else:
                direction = "preserve"
            objectives.append(("activity", direction))
        return list(dict.fromkeys(objectives))

    @staticmethod
    def _unverifiable_constraints(query: str) -> list[str]:
        """Return explicit structural constraints not checked by this tool.

        Numerical endpoint comparisons are deliberately kept separate from
        structure-preservation claims.  Until a structural comparator is
        wired into the workflow, these constraints must force an unverified
        result instead of being silently treated as satisfied.
        """
        lowered = query.casefold()
        preservation = r"保持|保留|维持|不改变|不得改变|maintain|preserve|retain|keep"
        constraints: list[str] = []
        if re.search(preservation, lowered) and re.search(
            r"芳香环|芳香核心|芳香骨架|aromatic(?:\s|-)*(?:ring|core|scaffold)|"
            r"核心|骨架|scaffold|core",
            lowered,
        ):
            constraints.append("aromatic_core")
        if re.search(preservation, lowered) and re.search(
            r"立体|手性|构型|stereochem|chirality|configuration",
            lowered,
        ):
            constraints.append("stereochemistry")
        if re.search(preservation, lowered) and re.search(
            r"环系统|环结构|ring\s*(?:system|structure)|ring_count",
            lowered,
        ):
            constraints.append("ring_system")
        return list(dict.fromkeys(constraints))

    @classmethod
    def _metric_value(
        cls,
        metric: str,
        properties: Mapping[str, Any] | None,
        admet: Mapping[str, Any] | None,
        activity: Mapping[str, Any] | None,
    ) -> float | None:
        if metric in {"logp", "qed"}:
            values = properties.get("properties") if isinstance(properties, Mapping) else None
            return cls._number(values.get(metric) if isinstance(values, Mapping) else None)
        if metric == "solubility":
            values = admet.get("admet") if isinstance(admet, Mapping) else None
            values = values.get("solubility") if isinstance(values, Mapping) else None
            if not isinstance(values, Mapping):
                return None
            return cls._number(values.get("log_s_esol", values.get("solubility_esol")))
        if metric == "activity":
            if not isinstance(activity, Mapping) or activity.get("success") is not True:
                return None
            provenance = activity.get("model_provenance") or activity.get("provenance")
            if not cls._trusted_provenance(provenance):
                return None
            for key in ("normalized_activity", "activity_probability", "probability", "predicted_pIC50"):
                value = cls._number(activity.get(key))
                if value is not None:
                    return value
        return None

    @staticmethod
    def _trusted_provenance(value: Any) -> bool:
        return (
            isinstance(value, Mapping)
            and value.get("demo_mode") is False
            and value.get("fallback_used") is False
            and bool(value.get("model_id") or value.get("model_path") or value.get("weights_sha256"))
        )

    @staticmethod
    def _number(value: Any) -> float | None:
        return float(value) if type(value) in (int, float) and math.isfinite(float(value)) else None

    @classmethod
    def _rows(cls, value: Any) -> list[Mapping[str, Any]]:
        if value is None:
            return []
        if isinstance(value, Mapping):
            if isinstance(value.get("data"), list):
                value = value["data"]
            elif isinstance(value.get("candidates"), list):
                value = value["candidates"]
            else:
                value = [value]
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
            return []
        return [row for row in value if isinstance(row, Mapping)]

    @classmethod
    def _candidate_rows(cls, value: Any) -> list[Mapping[str, Any]]:
        return [
            row for row in cls._rows(value)
            if isinstance(row.get("smiles") or row.get("canonical_smiles"), str)
        ]

    @classmethod
    def _by_smiles(cls, rows: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
        result: dict[str, Mapping[str, Any]] = {}
        for row in rows:
            value = row.get("smiles") or row.get("canonical_smiles")
            key = cls._smiles_key(value)
            if key:
                result[key] = row
        return result

    @classmethod
    def _first_by_smiles(cls, rows: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
        return cls._by_smiles(rows)

    @staticmethod
    def _smiles_key(value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            return ""
        try:
            from rdkit import Chem

            molecule = Chem.MolFromSmiles(value.strip())
            if molecule is not None:
                return Chem.MolToSmiles(molecule)
        except Exception:
            pass
        return value.strip()

    @staticmethod
    def _format(data: Mapping[str, Any]) -> str:
        lines = [
            "## Lead optimization verification",
            "",
            f"Status: {data['status']}",
            "",
            "| Candidate | Status | Missing evidence |",
            "|---|---|---|",
        ]
        for candidate in data["candidates"]:
            lines.append(
                f"| {candidate['candidate_id']} | {candidate['status']} | "
                f"{', '.join(candidate['missing_evidence']) or 'None'} |"
            )
        return "\n".join(lines)

    @staticmethod
    def _error(code: str, message: str) -> dict[str, Any]:
        return {
            "success": False,
            "message": message,
            "data": None,
            "formatted": "",
            "warnings": [],
            "error_code": code,
            "error": {"code": code, "message": message, "details": {"reason": code}},
            "quality": {"output_contract": "LeadOptimizationVerification@1"},
        }


__all__ = ["LeadOptimizationVerifier"]
