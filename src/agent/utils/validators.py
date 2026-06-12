#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Input validation utilities for molecular agent
"""

import re
from typing import List, Optional, Tuple, Dict, Any
import logging

logger = logging.getLogger(__name__)


class InputValidator:
    """Input validation for molecular queries"""

    def __init__(self):
        # Common SMILES patterns
        self.smiles_pattern = re.compile(
            r'[CNOSPFBrClI][\w\d\[\]\(\)@=#\-\+\.\\/:%]*[CNOSPFBrClI\)]|[CNOSPFBrClI]'
        )

        # Property calculation keywords
        self.calculation_keywords = {
            'english': ['calculate', 'compute', 'predict', 'estimate', 'analyze', 'evaluate', 'property', 'properties'],
            'chinese': ['¡—', '„K', '0¡', '', 'Ä0', '^'', ''(']
        }

    def contains_calculation_request(self, text: str) -> bool:
        """Check if text contains property calculation request"""
        text_lower = text.lower()

        # Check English keywords
        for keyword in self.calculation_keywords['english']:
            if keyword in text_lower:
                return True

        # Check Chinese keywords
        for keyword in self.calculation_keywords['chinese']:
            if keyword in text:
                return True

        return False

    def extract_smiles_candidates(self, text: str) -> List[str]:
        """Extract potential SMILES strings from text"""
        candidates = []

        # Method 1: Regex pattern matching
        patterns = self.smiles_pattern.findall(text)
        candidates.extend(patterns)

        # Method 2: Split by whitespace and filter
        tokens = text.split()
        for token in tokens:
            # Clean punctuation
            clean_token = re.sub(r'[,\.!?;]$', '', token)
            if self._looks_like_smiles(clean_token):
                candidates.append(clean_token)

        return candidates

    def _looks_like_smiles(self, text: str) -> bool:
        """Basic heuristic to check if text could be SMILES"""
        if not text or len(text) < 2:
            return False

        # Must contain chemical elements
        chemical_elements = {'C', 'N', 'O', 'S', 'P', 'F', 'Br', 'Cl', 'I'}
        if not any(elem in text for elem in chemical_elements):
            return False

        # Check for SMILES-like characters
        smiles_chars = set('()[]=#@+-')
        has_smiles_chars = any(char in text for char in smiles_chars)

        # If no special chars, should be short and chemical-looking
        if not has_smiles_chars:
            if len(text) > 10 or not text.isalnum():
                return False

        return True

    def validate_query(self, query: str) -> Dict[str, Any]:
        """Comprehensive query validation"""
        result = {
            'valid': False,
            'has_calculation_request': False,
            'has_potential_smiles': False,
            'smiles_candidates': [],
            'issues': []
        }

        if not query or not query.strip():
            result['issues'].append("Empty query")
            return result

        # Check for calculation request
        result['has_calculation_request'] = self.contains_calculation_request(query)

        # Extract SMILES candidates
        smiles_candidates = self.extract_smiles_candidates(query)
        result['smiles_candidates'] = smiles_candidates
        result['has_potential_smiles'] = len(smiles_candidates) > 0

        # Validate overall query
        if result['has_calculation_request'] and result['has_potential_smiles']:
            result['valid'] = True
        elif result['has_calculation_request'] and not result['has_potential_smiles']:
            result['issues'].append("Calculation requested but no SMILES structures found")
        elif result['has_potential_smiles'] and not result['has_calculation_request']:
            result['issues'].append("SMILES found but no calculation requested")
        else:
            result['issues'].append("No calculation request or SMILES structures detected")

        return result


def validate_smiles_with_rdkit(smiles: str) -> bool:
    """Validate SMILES using RDKit if available"""
    try:
        from rdkit import Chem
        mol = Chem.MolFromSmiles(smiles)
        return mol is not None
    except ImportError:
        logger.warning("RDKit not available for SMILES validation")
        return True  # Assume valid if can't validate
    except Exception:
        return False


def sanitize_input(text: str) -> str:
    """Sanitize user input"""
    if not text:
        return ""

    # Remove potentially harmful characters
    sanitized = re.sub(r'[<>\"\'`]', '', text)

    # Limit length
    max_length = 1000
    if len(sanitized) > max_length:
        sanitized = sanitized[:max_length]
        logger.warning(f"Input truncated to {max_length} characters")

    return sanitized.strip()