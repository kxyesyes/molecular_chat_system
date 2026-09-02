#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Input validation utilities for molecular-agent requests."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Literal

logger = logging.getLogger(__name__)

CandidateSource = Literal["labeled", "lexical"]
ValidationStatus = Literal["valid", "invalid", "unavailable"]


@dataclass(frozen=True)
class MolecularInputCandidate:
    """A possible SMILES token with extraction and validation provenance."""

    value: str
    source: CandidateSource
    validation: ValidationStatus


@dataclass(frozen=True)
class MolecularInputAnalysis:
    """One-pass molecular input analysis used at the execution boundary."""

    candidates: tuple[MolecularInputCandidate, ...]
    validation_available: bool

    @property
    def potential_smiles(self) -> tuple[str, ...]:
        return tuple(candidate.value for candidate in self.candidates)

    @property
    def valid_smiles(self) -> tuple[str, ...]:
        return tuple(
            candidate.value
            for candidate in self.candidates
            if candidate.validation == "valid"
        )

    @property
    def labeled_candidates(self) -> tuple[MolecularInputCandidate, ...]:
        return tuple(
            candidate for candidate in self.candidates if candidate.source == "labeled"
        )

    @property
    def blocks_execution(self) -> bool:
        labeled_failure = any(
            candidate.validation != "valid" for candidate in self.labeled_candidates
        )
        no_valid_candidate = bool(self.candidates) and not self.valid_smiles
        return labeled_failure or no_valid_candidate


class InputValidator:
    """Validate calculation intent and extract plausible SMILES tokens."""

    _label_pattern = re.compile(r"\bSMILES\s*[:：]\s*", re.IGNORECASE)
    _token_pattern = re.compile(
        r"(?<![A-Za-z0-9])"
        r"[A-Za-z0-9@+\-\[\]\(\)=#./\\:%]+"
        r"(?![A-Za-z0-9])"
    )
    _prose_terms = {
        "admet",
        "logp",
        "pic50",
        "qed",
        "smiles",
        "tpsa",
    }

    def __init__(self):
        self.calculation_keywords = {
            "english": [
                "calculate",
                "compute",
                "predict",
                "estimate",
                "analyze",
                "evaluate",
                "property",
                "properties",
            ],
            "chinese": ["计算", "预测", "估计", "分析", "评估", "属性"],
        }

    def contains_calculation_request(self, text: str) -> bool:
        text_lower = text.lower()
        return any(
            keyword in text_lower
            for keyword in self.calculation_keywords["english"]
        ) or any(
            keyword in text for keyword in self.calculation_keywords["chinese"]
        )

    def analyze_molecular_input(self, text: str) -> MolecularInputAnalysis:
        extracted = self._extract_candidates_with_sources(text)
        chem = _load_rdkit_chem()
        candidates = []
        for value, source in extracted:
            validation: ValidationStatus
            if chem is None:
                validation = "unavailable"
            else:
                try:
                    validation = "valid" if chem.MolFromSmiles(value) else "invalid"
                except Exception:
                    validation = "invalid"
            candidates.append(
                MolecularInputCandidate(
                    value=value,
                    source=source,
                    validation=validation,
                )
            )
        return MolecularInputAnalysis(
            candidates=tuple(candidates),
            validation_available=chem is not None,
        )

    def extract_potential_smiles_candidates(self, text: str) -> list[str]:
        return [value for value, _source in self._extract_candidates_with_sources(text)]

    def extract_smiles_candidates(self, text: str) -> list[str]:
        return list(self.analyze_molecular_input(text).valid_smiles)

    def _extract_candidates_with_sources(
        self, text: str
    ) -> list[tuple[str, CandidateSource]]:
        candidates: dict[str, CandidateSource] = {}
        for match in self._label_pattern.finditer(text):
            value = self._read_labeled_candidate(text[match.end() :])
            normalized = self._normalize_candidate(value)
            if normalized and self._looks_like_smiles(normalized, labeled=True):
                candidates[normalized] = "labeled"

        for match in self._token_pattern.finditer(text):
            normalized = self._normalize_candidate(match.group(0))
            if not normalized or not self._looks_like_smiles(normalized):
                continue
            candidates.setdefault(normalized, "lexical")
        return list(candidates.items())

    @staticmethod
    def _read_labeled_candidate(remainder: str) -> str:
        remainder = remainder.lstrip()
        if not remainder:
            return ""
        quote_pairs = {'"': '"', "'": "'", "“": "”", "‘": "’"}
        closing_quote = quote_pairs.get(remainder[0])
        if closing_quote:
            closing_index = remainder.find(closing_quote, 1)
            if closing_index > 0:
                return remainder[1:closing_index]
        match = re.match(r"[^\s,，;；。!?！？]+", remainder)
        return match.group(0) if match else ""

    @classmethod
    def _normalize_candidate(cls, text: str) -> str:
        candidate = text.strip()
        quote_pairs = {'"': '"', "'": "'", "“": "”", "‘": "’"}
        while candidate and quote_pairs.get(candidate[0]) == candidate[-1]:
            candidate = candidate[1:-1].strip()
        candidate = re.sub(r"[,\.!?;，。！？；：:]+$", "", candidate)
        while cls._has_outer_parentheses(candidate):
            candidate = candidate[1:-1].strip()
        return candidate

    @staticmethod
    def _has_outer_parentheses(text: str) -> bool:
        if len(text) < 2 or text[0] != "(" or text[-1] != ")":
            return False
        depth = 0
        for index, char in enumerate(text):
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0 and index != len(text) - 1:
                    return False
                if depth < 0:
                    return False
        return depth == 0

    @classmethod
    def _looks_like_smiles(cls, text: str, *, labeled: bool = False) -> bool:
        if not text or len(text) < 2 or any(char.isspace() for char in text):
            return False
        if text.upper() == "SMILES":
            return False
        if not re.fullmatch(r"[A-Za-z0-9@+\-\[\]\(\)=#./\\:%]+", text):
            return False
        if labeled:
            return True
        if text.lower() in cls._prose_terms:
            return False
        return cls._has_conservative_smiles_lexicon(text)

    @staticmethod
    def _has_conservative_smiles_lexicon(text: str) -> bool:
        atom_count = 0
        index = 0
        syntax = set("0123456789@+-()=#./\\:%")
        while index < len(text):
            char = text[index]
            if char == "[":
                closing = text.find("]", index + 1)
                if closing < 0 or closing == index + 1:
                    return False
                atom_count += 1
                index = closing + 1
                continue
            if char == "]":
                return False
            if text.startswith(("Cl", "Br"), index):
                atom_count += 1
                index += 2
                continue
            if char in "BCNOPSFIKbcnops":
                atom_count += 1
                index += 1
                continue
            if char in syntax:
                index += 1
                continue
            return False
        return atom_count > 0

    def validate_query(self, query: str) -> dict[str, Any]:
        result = {
            "valid": False,
            "has_calculation_request": False,
            "has_potential_smiles": False,
            "smiles_candidates": [],
            "issues": [],
        }
        if not query or not query.strip():
            result["issues"].append("Empty query")
            return result

        result["has_calculation_request"] = self.contains_calculation_request(query)
        analysis = self.analyze_molecular_input(query)
        result["smiles_candidates"] = list(analysis.valid_smiles)
        result["has_potential_smiles"] = bool(analysis.potential_smiles)

        if result["has_calculation_request"] and analysis.valid_smiles:
            result["valid"] = True
        elif result["has_calculation_request"]:
            result["issues"].append(
                "Calculation requested but no valid SMILES structures found"
            )
        elif result["has_potential_smiles"]:
            result["issues"].append("SMILES found but no calculation requested")
        else:
            result["issues"].append(
                "No calculation request or SMILES structures detected"
            )
        return result


def _load_rdkit_chem():
    try:
        from rdkit import Chem

        return Chem
    except ImportError:
        logger.warning("RDKit not available for SMILES validation")
        return None


def validate_smiles_with_rdkit(smiles: str) -> bool:
    chem = _load_rdkit_chem()
    if chem is None:
        return False
    try:
        return chem.MolFromSmiles(smiles) is not None
    except Exception:
        return False


def sanitize_input(text: str) -> str:
    if not text:
        return ""
    sanitized = re.sub(r"[<>\"'`]", "", text)
    max_length = 1000
    if len(sanitized) > max_length:
        sanitized = sanitized[:max_length]
        logger.warning("Input truncated to %s characters", max_length)
    return sanitized.strip()
