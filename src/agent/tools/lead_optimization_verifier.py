from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

from src.agent.property_constraints import requested_property_bounds, check_property_bounds


def _warning_value(value: Any) -> str:
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return format(float(value), ".4g")
    return "不可用" if value is None else str(value)


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

        objectives, unknown_objectives = self._parse_objectives(query)
        unverifiable_constraints = self._unverifiable_constraints(query)
        unverifiable_constraints.extend(unknown_objectives)
        bounds, unknown_bounds = requested_property_bounds(query)
        has_goals = bool(objectives or bounds)
        unverifiable_constraints.extend(unknown_bounds)
        baseline = self._first_by_smiles(baseline_rows)
        baseline_admet = self._first_by_smiles(self._rows(outputs.get("baseline_admet")))
        baseline_activity = self._first_by_smiles(self._rows(outputs.get("baseline_activity")))
        baseline_properties_row = next(iter(baseline.values()), None)
        if baseline_properties_row is not None and (
            baseline_properties_row.get("success", True) is not True
            or baseline_properties_row.get("status", "succeeded") != "succeeded"
            or baseline_properties_row.get("error") is not None
            or baseline_properties_row.get("error_code")
        ):
            baseline_properties_row = None
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
            property_row = candidate_properties.get(key)
            if property_row is not None and (
                property_row.get("success", True) is not True
                or property_row.get("status", "succeeded") != "succeeded"
                or property_row.get("error") is not None or property_row.get("error_code")
                or ("candidate_id" in property_row
                    and property_row["candidate_id"] != candidate.get("candidate_id"))
            ):
                property_row = None
            for metric, direction in objectives:
                if metric in {"activity", "potency"}:
                    baseline_evidence = self._activity_evidence(baseline_activity_row, metric=metric)
                    candidate_evidence = self._activity_evidence(candidate_activity.get(key), metric=metric)
                    if (baseline_evidence is None or candidate_evidence is None
                            or baseline_evidence[1] != candidate_evidence[1]):
                        missing.append(metric)
                        continue
                baseline_value = self._metric_value(
                    metric,
                    baseline_properties_row,
                    baseline_admet_row,
                    baseline_activity_row,
                )
                candidate_value = self._metric_value(
                    metric,
                    property_row,
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
                if metric in {"activity", "potency"}:
                    comparisons[-1].update(endpoint=baseline_evidence[1]["endpoint"],
                                           units=baseline_evidence[1]["units"])

            constraint_checks = check_property_bounds(bounds, (property_row or {}).get("properties"))
            missing.extend(check["metric"] for check in constraint_checks if check["satisfied"] is None)
            if not has_goals or missing or unverifiable_constraints:
                status = "unverified"
            elif all(item["satisfied"] for item in comparisons + constraint_checks):
                status = "supported"
            else:
                status = "evaluated_not_improved"
            results.append({
                "candidate_id": candidate.get("candidate_id") or f"candidate-{len(results) + 1:03d}",
                "smiles": smiles,
                "status": status,
                "goal_supported": status == "supported",
                "comparisons": comparisons,
                "constraint_checks": constraint_checks,
                "missing_evidence": missing,
                "unverified_constraints": list(unverifiable_constraints),
            })

        if any(item["status"] == "unverified" for item in results) or unverifiable_constraints:
            overall_status = "unverified"
        elif any(item["status"] == "supported" for item in results):
            overall_status = "supported"
        elif has_goals and all(
            item["status"] == "evaluated_not_improved" for item in results
        ):
            overall_status = "evaluated_not_improved"
        else:
            overall_status = "unverified"

        warnings: list[str] = []
        if not has_goals:
            warnings.append("未识别到明确的优化指标，未验证目标是否改善")
        failed_checks = []
        for candidate in results:
            candidate_id = candidate.get("candidate_id", "候选")
            for comparison in candidate.get("comparisons", []):
                if comparison.get("satisfied") is False:
                    failed_checks.append(
                        f"{candidate_id} 的 {comparison.get('metric', '指标')} 未满足"
                        f"（基线 {_warning_value(comparison.get('baseline_value'))}"
                        f" → 候选 {_warning_value(comparison.get('candidate_value'))}）"
                    )
            for check in candidate.get("constraint_checks", []):
                if check.get("satisfied") is False:
                    failed_checks.append(
                        f"{candidate_id} 的 {check.get('metric', '约束')} 约束未满足"
                        f"（值 {_warning_value(check.get('value'))}"
                        f"，要求 {check.get('operator', '')} {check.get('threshold', '')}）"
                    )
        if failed_checks:
            warnings.append("未满足的优化目标：" + "；".join(failed_checks[:8]))
        if any(item["status"] == "unverified" for item in results):
            warnings.append("部分候选缺少与基线对应的真实端点证据，结果未验证")
        if unverifiable_constraints:
            warnings.append("存在未解析的优化要求或未核验的结构约束，不能宣称目标已满足")
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
        return cls._parse_objectives(query)[0]

    @staticmethod
    def _parse_objectives(query: str) -> tuple[list[tuple[str, str]], list[str]]:
        """Bind a direction to one metric in its own clause, never globally.

        Negation, shared verbs and conflicting directions require clarification;
        omitting such a clause must not let the other goals certify success.
        Explicit absolute bounds are evaluated by requested_property_bounds.
        """
        metric_pattern = re.compile(
            r"(?<![a-z0-9_])(?:logp|qed|solubility|potency|activity|tpsa|mw|hba|hbd|sa(?:\s*score)?|molecular\s+weight)(?![a-z0-9_])|溶解(?:度)?|活性|效力|分子量",
            re.I,
        )
        directions = {
            "lower": r"降低|减少|下降|\b(?:lower|reduce|decrease)\b",
            "higher": r"提高|提升|增加|改善|\b(?:higher|improve|increase)\b",
            "preserve": r"保持|保留|维持|\b(?:maintain|preserve|retain|keep)\b",
        }
        objectives: list[tuple[str, str]] = []
        unknown: list[str] = []
        for clause in re.split(r"[，,;；。\n、]|\b(?:and|but|while|then)\b|并且|同时|并|且|但|和", query.casefold()):
            mentions = list(metric_pattern.finditer(clause))
            metrics = [
                "activity" if m[0] == "活性" else "potency" if m[0] == "效力"
                else "solubility" if m[0].startswith("溶解") else m[0]
                for m in mentions
            ]
            if not metrics:
                continue
            found = [direction for direction, pattern in directions.items() if re.search(pattern, clause)]
            bounds, unknown_bounds = requested_property_bounds(clause)
            # These Chinese operators encode a positive bound; other negation
            # must be checked before the absolute-bound shortcut.
            negation_text = re.sub(r"不超过|不大于|不低于|不小于", "", clause)
            negated = re.search(r"不|勿|禁止|无需|避免|\b(?:not|no|never|without|avoid|don['’]t)\b", negation_text)
            if negated:
                unknown.extend(metric + "_objective" for metric in metrics)
                continue
            if (len(mentions) == 1 and (bounds or unknown_bounds)
                    and (not found or (found == ["preserve"] and bounds))):
                continue
            quantified = re.search(r"\d", clause) and not bounds
            unsupported = any(metric not in {"logp", "qed", "solubility", "activity", "potency"} for metric in metrics)
            if (len(mentions) != 1 or len(found) != 1 or quantified or unsupported
                    or (found == ["preserve"] and metrics[0] not in {"activity", "potency"})):
                unknown.extend(metric + "_objective" for metric in metrics)
                continue
            metric, direction = metrics[0], found[0]
            if any(old_metric == metric and old_direction != direction for old_metric, old_direction in objectives):
                unknown.append(metric + "_objective")
            objectives.append((metric, direction))
        return (list(dict.fromkeys(item for item in objectives if item[0] + "_objective" not in unknown)),
                list(dict.fromkeys(unknown)))

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
        if metric in {"activity", "potency"}:
            evidence = cls._activity_evidence(activity, metric=metric)
            return evidence[0] if evidence is not None else None
        return None

    @classmethod
    def _activity_evidence(cls, row: Any, *, metric: str = "activity") -> tuple[float, dict[str, Any]] | None:
        if (not isinstance(row, Mapping) or row.get("success") is not True
                or row.get("error") or row.get("error_code")
                or any(row.get(field, "passed") not in {"passed", "succeeded"}
                       for field in ("status", "execution_status"))
                or any(field in row and row[field] is not False for field in ("demo_mode", "fallback_used"))):
            return None
        provenance = row.get("model_provenance") or row.get("provenance")
        if not isinstance(provenance, Mapping):
            return None
        if (any(field in provenance and provenance[field] is not False
                for field in ("demo_mode", "fallback_used"))
                or any(provenance.get(field, "passed") not in ("passed", "succeeded")
                       for field in ("status", "execution_status"))):
            return None
        if metric == "potency":
            keys = ("predicted_pIC50",)
        elif "models" in provenance:
            # The real family tool returns both heads. Quantitative pIC50 takes
            # precedence over confidence in its binary activity classification.
            keys = ("predicted_pIC50", "activity_probability", "probability")
        else:
            keys = ("normalized_activity", "activity_probability", "probability", "predicted_pIC50")
        for key in keys:
            if row.get(key) is None:
                continue
            value = cls._number(row[key])
            regression = key == "predicted_pIC50"
            task, endpoint, units = ("regression", "pIC50", "pIC50") if regression else (
                "classification", "activity", "probability")
            if value is None or (not regression and not 0 <= value <= 1):
                return None
            models = provenance.get("models")
            nested = "models" in provenance
            model = models.get(task) if isinstance(models, Mapping) else None
            if not nested:
                model = provenance
            if not cls._trusted_provenance(model):
                return None
            if nested and (key == "normalized_activity" or model.get("scientific_readiness") != "endpoint_ready"):
                return None
            for field, expected in (("task_type", task), ("endpoint", endpoint), ("units", units)):
                if (nested or field in model) and model.get(field) != expected:
                    return None
                # Family rows expose pIC50 units for the envelope, while each
                # model declares its own endpoint. Legacy flat rows use theirs.
                if not nested and field in row and row[field] != expected:
                    return None
            errors = row.get("errors")
            if errors and (not isinstance(errors, Mapping) or errors.get(task) or errors.get("input") or errors.get("bundle")):
                return None
            context = {
                "metric": "probability" if key == "activity_probability" else key,
                "endpoint": endpoint, "units": units,
                "model": {field: model.get(field) for field in (
                    "model_id", "model_path", "weights_sha256", "model_card_sha256",
                    "target_id", "model_contract_key", "prepared_dataset_sha256",
                )},
                "scope": {field: provenance.get(field) for field in (
                    "family_id", "bundle_id", "request", "scope", "label_threshold", "probability_threshold",
                )},
            }
            return value, context
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
        lines += ["", "| Candidate | Metric | Baseline | Candidate value | Delta | Goal met |",
                  "|---|---|---|---|---|---|"]
        for candidate in data["candidates"]:
            for item in candidate["comparisons"]:
                lines.append(f"| {candidate['candidate_id']} | {item['metric']} | {item['baseline_value']} | "
                             f"{item['candidate_value']} | {item['delta']} | {item['satisfied']} |")
        checks = [(candidate, check) for candidate in data["candidates"]
                  for check in candidate.get("constraint_checks", [])]
        if checks:
            lines += ["", "| Candidate | Constraint | Calculated value | Result |",
                      "|---|---|---|---|"]
            for candidate, check in checks:
                state = "未验证" if check["satisfied"] is None else "满足" if check["satisfied"] else "不满足"
                lines.append(f"| {candidate['candidate_id']} | {check['metric']} {check['operator']} "
                             f"{check['threshold']} {check['unit']} | {check['value']} | {state} |")
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
