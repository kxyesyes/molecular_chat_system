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
        return any(
            candidate.validation != "valid" for candidate in self.candidates
        )


class InputValidator:
    """Validate calculation intent and extract plausible SMILES tokens."""

    _NON_MOLECULAR_NOTATIONS = frozenset(
        {
            "C++",
            "C#",
            "S9+",
            "BBB",
            "PPB",
            "CNS",
            "PK",
            "S9",
            "ADMET",
            "LOGP",
            "QED",
            "TPSA",
            "IC50",
            "P450",
        }
    )

    _label_pattern = re.compile(
        r"(?<![A-Za-z0-9_])SMILES\s*[:：]\s*",
        re.IGNORECASE,
    )
    _token_pattern = re.compile(
        r"(?<![A-Za-z0-9])"
        r"[A-Za-z0-9@+\-\[\]\(\)=#./\\:%]+"
        r"(?![A-Za-z0-9])"
    )

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
        if not extracted:
            return MolecularInputAnalysis(candidates=(), validation_available=True)
        chem = _load_rdkit_chem()
        candidates: dict[str, MolecularInputCandidate] = {}
        for value, source in extracted:
            validation: ValidationStatus
            if chem is None:
                validation = "unavailable"
            else:
                try:
                    validation = "valid" if chem.MolFromSmiles(value) else "invalid"
                except Exception:
                    validation = "unavailable"

            if validation == "invalid" and chem is not None:
                inner = self._square_delimited_inner(value)
                if inner:
                    try:
                        if chem.MolFromSmiles(inner):
                            value = inner
                            validation = "valid"
                    except Exception:
                        validation = "unavailable"

            candidate = MolecularInputCandidate(
                value=value,
                source=source,
                validation=validation,
            )
            existing = candidates.get(value)
            if existing is None or source == "labeled":
                candidates[value] = candidate
        return MolecularInputAnalysis(
            candidates=tuple(candidates.values()),
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
            remainder = text[match.end() :]
            value = self._read_labeled_candidate(remainder)
            normalized = self._normalize_candidate(value) or value.strip()
            if normalized:
                candidates[normalized] = "labeled"
            list_scope = re.split(r"[;；。!?！？]", remainder, maxsplit=1)[0]
            for item in re.split(r"[,，]", list_scope)[1:]:
                value = self._read_labeled_candidate(item)
                normalized = self._normalize_candidate(value) or value.strip()
                if not normalized or not self._is_labeled_list_candidate(normalized):
                    break
                candidates[normalized] = "labeled"

        for match in self._token_pattern.finditer(text):
            normalized = self._normalize_candidate(match.group(0))
            if not normalized or not self._is_contextual_unlabeled_candidate(
                normalized,
                text,
            ):
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
        return match.group(0) if match else remainder[0]

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

    @staticmethod
    def _square_delimited_inner(text: str) -> str:
        if len(text) < 3 or text[0] != "[" or text[-1] != "]":
            return ""
        depth = 0
        for index, char in enumerate(text):
            if char == "[":
                depth += 1
            elif char == "]":
                depth -= 1
                if depth == 0 and index != len(text) - 1:
                    return ""
                if depth < 0:
                    return ""
        return text[1:-1].strip() if depth == 0 else ""

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
        return cls._has_conservative_smiles_lexicon(text)

    @classmethod
    def _is_contextual_unlabeled_candidate(cls, token: str, text: str) -> bool:
        """Classify an unlabeled token using structure evidence and nearby cues."""
        if cls._is_non_molecular_notation(token):
            return cls._has_explicit_molecular_cue(token, text)
        if not cls._looks_like_smiles(token):
            return False
        if cls._is_domain_notation(token):
            return cls._has_explicit_molecular_cue(token, text)
        return (
            cls._has_strong_structural_syntax(token)
            or cls._is_common_simple_structure_token(token)
            or cls._has_explicit_molecular_cue(token, text)
        )

    @staticmethod
    def _is_common_simple_structure_token(token: str) -> bool:
        return bool(re.fullmatch(r"C{2,}(?:(?:Cl|Br|[CNOSPFIK]))*", token))

    @classmethod
    def _is_domain_notation(cls, token: str) -> bool:
        if not re.search(r"[/\-]", token):
            return False
        parts = re.split(r"[/\-]", token)
        return bool(parts) and all(
            re.fullmatch(r"[A-Z]{1,5}\d*", part)
            and not cls._is_common_simple_structure_token(part)
            for part in parts
        )

    @staticmethod
    def _has_explicit_molecular_cue(token: str, text: str) -> bool:
        escaped = re.escape(token)
        cue = r"(?:SMILES|molecule|structure|compound|分子|结构|化合物)"
        wrapper = r"\s*[:：=]?\s*[\"'“”‘’(]*"
        return bool(
            re.search(
                rf"{cue}{wrapper}{escaped}(?![A-Za-z0-9])",
                text,
                re.IGNORECASE,
            )
        )

    @classmethod
    def _is_labeled_list_candidate(cls, value: str) -> bool:
        if cls._is_non_molecular_notation(value):
            return False
        if value == "X" or re.fullmatch(r"C+X", value):
            return True
        if not cls._looks_like_smiles(value, labeled=True):
            return False
        return bool(
            cls._has_conservative_smiles_lexicon(value)
            or cls._has_strong_structural_syntax(value)
        )

    @classmethod
    def _is_non_molecular_notation(cls, value: str) -> bool:
        upper = value.upper()
        return upper in cls._NON_MOLECULAR_NOTATIONS or bool(
            re.fullmatch(r"(?:C/)?C(?:\+\+|#)\d*", upper)
        )

    @classmethod
    def _has_strong_structural_syntax(cls, text: str) -> bool:
        if any(char in "[]@+()=#.:/%\\" for char in text):
            return True
        if "-" in text and not cls._is_domain_notation(text):
            return True
        return cls._has_paired_ring_marker(text)

    @staticmethod
    def _has_paired_ring_marker(text: str) -> bool:
        markers = re.findall(r"%\d{2}|\d", text)
        for marker in set(markers):
            positions = [
                match.start()
                for match in re.finditer(re.escape(marker), text)
            ]
            for left, right in zip(positions, positions[1:]):
                if right - left <= len(marker):
                    continue
                if left == 0 or right == 0:
                    continue
                if text[left - 1].isalpha() and text[right - 1].isalpha():
                    return True
        return False

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

        if (
            result["has_calculation_request"]
            and analysis.validation_available
            and analysis.valid_smiles
            and not analysis.blocks_execution
        ):
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
    except (ImportError, OSError, RuntimeError):
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
