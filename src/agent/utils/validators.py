#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Input validation utilities for molecular-agent requests."""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


class InputValidator:
    """Validate calculation intent and extract plausible SMILES tokens."""

    def __init__(self):
        self.smiles_pattern = re.compile(
            r"(?<![A-Za-z0-9])"
            r"(?:[BCNOPSFIK]|Cl|Br)"
            r"[A-Za-z0-9@+\-\[\]\(\)=#./\\:%]*"
            r"(?![A-Za-z0-9])"
        )
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

    def extract_smiles_candidates(self, text: str) -> list[str]:
        candidates = list(self.smiles_pattern.findall(text))
        for token in text.split():
            clean_token = re.sub(r"[,\.!?;，。！？；：:]+$", "", token)
            if self._looks_like_smiles(clean_token):
                candidates.append(clean_token)
        valid = []
        seen = set()
        for candidate in candidates:
            if candidate in seen or not self._looks_like_smiles(candidate):
                continue
            if validate_smiles_with_rdkit(candidate):
                seen.add(candidate)
                valid.append(candidate)
        return valid

    @staticmethod
    def _looks_like_smiles(text: str) -> bool:
        if not text or len(text) < 2 or any(char.isspace() for char in text):
            return False
        if not re.fullmatch(r"[A-Za-z0-9@+\-\[\]\(\)=#./\\:%]+", text):
            return False
        chemical_elements = ("C", "N", "O", "S", "P", "F", "Br", "Cl", "I")
        return any(element in text for element in chemical_elements)

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
        smiles_candidates = self.extract_smiles_candidates(query)
        result["smiles_candidates"] = smiles_candidates
        result["has_potential_smiles"] = bool(smiles_candidates)

        if result["has_calculation_request"] and result["has_potential_smiles"]:
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


def validate_smiles_with_rdkit(smiles: str) -> bool:
    try:
        from rdkit import Chem

        return Chem.MolFromSmiles(smiles) is not None
    except ImportError:
        logger.warning("RDKit not available for SMILES validation")
        return True
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
