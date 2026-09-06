from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any


class CandidateRanker:
    """Rank validated generated molecules from available scientific evidence."""

    name = "candidate_ranker"
    version = "1"
    description = (
        "Deterministically rank validated molecular candidates from RDKit "
        "properties and optional trusted ADMET/activity evidence."
    )

    def execute(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, Mapping):
            return self._error("invalid_input", "Ranking input must be an object")

        metadata = payload.get("metadata")
        outputs = payload.get("outputs")
        if not isinstance(metadata, Mapping) or not isinstance(outputs, Mapping):
            return self._error(
                "invalid_input",
                "Ranking input requires metadata and workflow outputs",
            )

        top_n = metadata.get("docking_top_n")
        if type(top_n) is not int or top_n < 1:
            return self._error("invalid_top_n", "Top-N must be a positive integer")

        try:
            candidates = self._validated_candidates(outputs.get("molecules"))
            properties = self._evidence_by_candidate(
                outputs.get("properties"), candidates, "properties"
            )
            admet = self._evidence_by_candidate(
                outputs.get("admet"), candidates, "admet"
            )
            activity = self._evidence_by_candidate(
                outputs.get("activity"), candidates, "activity"
            )
        except _RankingInputError as exc:
            return self._error(exc.code, str(exc))

        warnings: list[str] = []
        ranked: list[dict[str, Any]] = []
        unrankable: list[dict[str, str]] = []
        missing_property_count = 0
        ignored_admet_count = 0
        ignored_activity_count = 0

        for canonical_smiles, candidate in candidates.items():
            property_record = properties.get(canonical_smiles)
            property_score = self._property_score(property_record)
            if property_score is None:
                missing_property_count += 1
                unrankable.append(
                    {
                        "candidate_id": candidate["candidate_id"],
                        "canonical_smiles": canonical_smiles,
                        "reason": "missing_real_property_evidence",
                    }
                )
                continue

            admet_score = self._admet_score(admet.get(canonical_smiles))
            activity_score = self._activity_score(activity.get(canonical_smiles))
            missing_evidence: list[str] = []
            if admet_score is None:
                missing_evidence.append("admet")
                ignored_admet_count += 1
            if activity_score is None:
                missing_evidence.append("activity")
                ignored_activity_count += 1

            available = [(property_score, 0.50)]
            if admet_score is not None:
                available.append((admet_score, 0.25))
            if activity_score is not None:
                available.append((activity_score, 0.25))
            total_weight = sum(weight for _, weight in available)
            score = sum(value * weight for value, weight in available) / total_weight
            ranking_evidence = {
                "property_score": round(property_score, 6),
                "admet_score": (
                    round(admet_score, 6) if admet_score is not None else None
                ),
                "activity_score": (
                    round(activity_score, 6)
                    if activity_score is not None
                    else None
                ),
                "weights_used": {
                    "properties": 0.50 / total_weight,
                    "admet": (
                        0.25 / total_weight if admet_score is not None else None
                    ),
                    "activity": (
                        0.25 / total_weight if activity_score is not None else None
                    ),
                },
                "missing_evidence": list(missing_evidence),
            }
            ranked.append(
                {
                    "candidate_id": candidate["candidate_id"],
                    "canonical_smiles": canonical_smiles,
                    "score": round(score, 6),
                    "ranking_evidence": ranking_evidence,
                    "missing_evidence": list(missing_evidence),
                    "docking_ready_for_preparation": True,
                }
            )

        ranked.sort(key=lambda item: (-item["score"], item["canonical_smiles"]))
        for rank, item in enumerate(ranked, start=1):
            item["rank"] = rank

        unrankable.sort(key=lambda item: item["canonical_smiles"])
        if missing_property_count:
            warnings.append(
                f"Excluded {missing_property_count} candidate(s) without complete "
                "RDKit property evidence"
            )
        if ignored_admet_count:
            warnings.append(
                f"ADMET evidence was absent or unusable for {ignored_admet_count} "
                "ranked candidate(s)"
            )
        if ignored_activity_count:
            warnings.append(
                "Activity evidence was absent, demo, fallback, or unusable for "
                f"{ignored_activity_count} ranked candidate(s)"
            )

        if not ranked:
            return self._error(
                "no_rankable_candidates",
                "No candidate has complete RDKit property evidence",
                warnings=warnings,
            )

        data = {
            "requested_top_n": top_n,
            "ranked_candidate_count": len(ranked),
            "top_candidates": ranked[:top_n],
            "ranked_candidates": ranked,
            "unrankable_candidates": unrankable,
        }
        return {
            "success": True,
            "message": f"Ranked {len(ranked)} validated molecular candidate(s)",
            "data": data,
            "formatted": self._format(data),
            "warnings": warnings,
            "evidence": [
                {
                    "type": "deterministic_candidate_ranking",
                    "method": "property_admet_activity_weighted_normalization",
                    "ranked_candidate_count": len(ranked),
                }
            ],
            "quality": {
                "output_contract": "CandidateRanking@1",
                "deterministic": True,
                "scientific_claim_scope": "candidate_prioritization",
            },
        }

    @classmethod
    def _validated_candidates(cls, value: Any) -> dict[str, dict[str, str]]:
        records = value.get("candidates") if isinstance(value, Mapping) else value
        if not isinstance(records, Sequence) or isinstance(records, (str, bytes)):
            raise _RankingInputError(
                "invalid_candidate_set", "Validated molecule set must be a list"
            )

        candidates: dict[str, dict[str, str]] = {}
        for index, record in enumerate(records, start=1):
            if not isinstance(record, Mapping):
                raise _RankingInputError(
                    "invalid_candidate_set", "Candidate records must be objects"
                )
            smiles = record.get("canonical_smiles") or record.get("smiles")
            canonical = cls._canonicalize(smiles)
            if canonical is None:
                raise _RankingInputError(
                    "invalid_candidate_set",
                    "Validated molecule set contains an invalid SMILES",
                )
            if canonical in candidates:
                raise _RankingInputError(
                    "invalid_candidate_set",
                    "Validated molecule set contains duplicate canonical SMILES",
                )
            candidate_id = record.get("candidate_id")
            candidates[canonical] = {
                "candidate_id": (
                    candidate_id.strip()
                    if isinstance(candidate_id, str) and candidate_id.strip()
                    else f"candidate-{index:03d}"
                ),
                "canonical_smiles": canonical,
            }
        if not candidates:
            raise _RankingInputError(
                "invalid_candidate_set", "Validated molecule set is empty"
            )
        return candidates

    @classmethod
    def _evidence_by_candidate(
        cls,
        value: Any,
        candidates: Mapping[str, Any],
        evidence_name: str,
    ) -> dict[str, Mapping[str, Any]]:
        if value is None:
            records: Any = []
        elif isinstance(value, Mapping) and isinstance(value.get("data"), list):
            records = value["data"]
        else:
            records = value
        if not isinstance(records, Sequence) or isinstance(records, (str, bytes)):
            raise _RankingInputError(
                "invalid_candidate_evidence",
                f"{evidence_name} evidence must be a list",
            )

        indexed: dict[str, Mapping[str, Any]] = {}
        for record in records:
            if not isinstance(record, Mapping):
                raise _RankingInputError(
                    "invalid_candidate_evidence",
                    f"{evidence_name} evidence records must be objects",
                )
            canonical = cls._canonicalize(record.get("smiles"))
            if canonical is None or canonical not in candidates:
                raise _RankingInputError(
                    "invalid_candidate_evidence",
                    f"{evidence_name} evidence references an unknown candidate",
                )
            if canonical in indexed:
                raise _RankingInputError(
                    "invalid_candidate_evidence",
                    f"{evidence_name} contains duplicate candidate evidence",
                )
            indexed[canonical] = record
        return indexed

    @staticmethod
    def _property_score(record: Mapping[str, Any] | None) -> float | None:
        if not isinstance(record, Mapping):
            return None
        properties = record.get("properties")
        if not isinstance(properties, Mapping):
            return None
        qed = properties.get("qed")
        logp = properties.get("logp")
        if not CandidateRanker._finite_number(qed) or not CandidateRanker._finite_number(logp):
            return None
        qed_value = min(max(float(qed), 0.0), 1.0)
        return 0.70 * qed_value + 0.30 * CandidateRanker._logp_window_score(
            float(logp)
        )

    @staticmethod
    def _admet_score(record: Mapping[str, Any] | None) -> float | None:
        if not isinstance(record, Mapping):
            return None
        admet = record.get("admet")
        if not isinstance(admet, Mapping):
            return None
        method = admet.get("prediction_method")
        risk_count = admet.get("risk_count")
        total_endpoints = admet.get("total_endpoints")
        if not isinstance(method, str) or not method.strip():
            return None
        if (
            admet.get("demo_mode") not in (None, False)
            or admet.get("fallback_used") not in (None, False)
            or any(marker in method.casefold() for marker in ("demo", "fallback"))
        ):
            return None
        if type(risk_count) is not int or type(total_endpoints) is not int:
            return None
        if risk_count < 0 or total_endpoints < 1 or risk_count > total_endpoints:
            return None
        return 1.0 - min(risk_count / max(total_endpoints, 1), 1.0)

    @staticmethod
    def _activity_score(record: Mapping[str, Any] | None) -> float | None:
        if not isinstance(record, Mapping) or record.get("success") is not True:
            return None
        provenance = record.get("model_provenance")
        if not isinstance(provenance, Mapping):
            return None
        if (
            provenance.get("demo_mode") is not False
            or provenance.get("fallback_used", False) is not False
            or not any(
                provenance.get(key)
                for key in ("model_id", "model_path", "weights_sha256")
            )
        ):
            return None
        value = record.get("normalized_activity")
        if value is None:
            value = record.get("probability")
        if not CandidateRanker._finite_number(value):
            return None
        return min(max(float(value), 0.0), 1.0)

    @staticmethod
    def _logp_window_score(logp: float) -> float:
        if logp < -1.0 or logp > 5.0:
            return 0.0
        if logp < 1.0:
            return (logp + 1.0) / 2.0
        if logp <= 3.0:
            return 1.0
        return (5.0 - logp) / 2.0

    @staticmethod
    def _finite_number(value: Any) -> bool:
        return type(value) in (int, float) and math.isfinite(float(value))

    @staticmethod
    def _canonicalize(value: Any) -> str | None:
        if not isinstance(value, str) or not value.strip():
            return None
        try:
            from rdkit import Chem

            molecule = Chem.MolFromSmiles(value.strip())
            return Chem.MolToSmiles(molecule) if molecule is not None else None
        except Exception:
            return None

    @staticmethod
    def _format(data: Mapping[str, Any]) -> str:
        rows = [
            "## Candidate prioritization",
            "",
            "| Rank | Candidate | Canonical SMILES | Score | Missing evidence |",
            "|---:|---|---|---:|---|",
        ]
        for candidate in data["top_candidates"]:
            missing = ", ".join(candidate["missing_evidence"]) or "None"
            rows.append(
                f"| {candidate['rank']} | {candidate['candidate_id']} | "
                f"`{candidate['canonical_smiles']}` | {candidate['score']:.4f} | "
                f"{missing} |"
            )
        rows.extend(
            [
                "",
                "Scores prioritize measured/calculated evidence that is present. "
                "Missing optional assessments are not replaced with synthetic values.",
            ]
        )
        return "\n".join(rows)

    @staticmethod
    def _error(
        code: str,
        message: str,
        *,
        warnings: list[str] | None = None,
    ) -> dict[str, Any]:
        normalized_code = (
            "invalid_output"
            if code in {"invalid_candidate_set", "invalid_candidate_evidence"}
            else "invalid_input"
            if code in {"invalid_input", "invalid_top_n"}
            else "internal_error"
        )
        return {
            "success": False,
            "message": message,
            "data": None,
            "formatted": "",
            "warnings": list(warnings or []),
            "error_code": code,
            "error": {
                "code": normalized_code,
                "message": message,
                "details": {"reason": code},
            },
            "quality": {"output_contract": "CandidateRanking@1"},
        }


class _RankingInputError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


__all__ = ["CandidateRanker"]
