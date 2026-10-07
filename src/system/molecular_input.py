"""Shared molecular input validation primitives.

The class is intentionally independent from Agent and Web layers.  Domain
adapters can use it directly while the historical Agent import remains a
compatibility export.
"""

from __future__ import annotations

from typing import Any, Dict, List
import logging
import re
import sys

try:
    from rdkit import Chem
    RDKIT_AVAILABLE = True
except ImportError:
    Chem = None
    RDKIT_AVAILABLE = False

logger = logging.getLogger(__name__)


def _rdkit_available() -> bool:
    """Honor the historical test/compatibility switch without importing Agent."""
    legacy_module = sys.modules.get("src.agent.tools.base_tool")
    if legacy_module is not None and "RDKIT_AVAILABLE" in legacy_module.__dict__:
        return bool(legacy_module.__dict__["RDKIT_AVAILABLE"])
    return RDKIT_AVAILABLE


class BaseMolecularTool:
    """Common SMILES extraction and validation behavior for domain adapters."""

    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description
        self.exclude_words = {
            "calculate", "compute", "predict", "estimate", "analyze", "evaluate",
            "properties", "property", "molecular", "structure", "formula",
            "计算", "预测", "估计", "分析", "评估", "属性", "性质", "分子", "结构", "的",
        }

    def extract_smiles(self, text: str) -> List[str]:
        candidates: list[str] = []
        complex_smiles_pattern = r"[A-Za-z][A-Za-z0-9@+\-\[\]\(\)=#\.\\/:]{5,}"
        for match in re.findall(complex_smiles_pattern, text):
            candidate = match.strip().strip("`'\".,;")
            if (
                not re.match(r"^[a-zA-Z]+$", candidate)
                and len(candidate) >= 6
                and self._is_plausible_smiles_lexeme(candidate)
                and self.validate_smiles(candidate)
            ):
                candidates.append(candidate)
                logger.info("检测到复杂分子结构: %s", candidate)

        if not candidates:
            text_upper = text.upper()
            for pattern, smiles in (("CCO", "CCO"),):
                if re.search(rf"\b{re.escape(pattern)}\b", text_upper):
                    candidates.append(smiles)
                    logger.info("检测到预定义简单分子: %s -> %s", pattern, smiles)

        for pattern in (
            r"O=C\([^)]+\)[^.]{10,}",
            r"[CNO][CNO0-9\(\)\[\]=@#\-+\.]{10,}",
        ):
            for match in re.findall(pattern, text):
                candidate = match.strip().strip("`'\".,;")
                if len(candidate) >= 10 and self.validate_smiles(candidate):
                    candidates.append(candidate)
                    logger.info("检测到特殊分子模式: %s", candidate)

        valid_smiles: list[str] = []
        seen: set[str] = set()
        for candidate in candidates:
            if candidate in seen or len(candidate) < 3:
                continue
            if candidate.lower() in {"and", "the", "for", "with", "from", "this", "that"}:
                continue
            if len(candidate) <= 2 and candidate not in ["CCO"]:
                continue
            if self._is_plausible_smiles_lexeme(candidate) and self.validate_smiles(candidate):
                valid_smiles.append(candidate)
                seen.add(candidate)
                logger.info("验证成功的SMILES: %s", candidate)
        return valid_smiles

    def validate_smiles(self, smiles: str) -> bool:
        if not _rdkit_available():
            return self._basic_smiles_validation(smiles)
        try:
            try:
                from rdkit import rdBase
                with rdBase.BlockLogs():
                    mol = Chem.MolFromSmiles(smiles)
            except (AttributeError, ImportError):
                mol = Chem.MolFromSmiles(smiles)
            return mol is not None
        except Exception:
            return False

    @staticmethod
    def _is_plausible_smiles_lexeme(candidate: str) -> bool:
        value = str(candidate or "").strip()
        if not value or value.startswith(":") or value.endswith(":"):
            return False
        unbracketed = re.sub(r"\[[^\]]*\]", "", value)
        letters = "".join(character for character in unbracketed if character.isalpha())
        letters = letters.replace("Cl", "").replace("Br", "")
        return all(character in "BCNOPSFIbcnops" for character in letters)

    def _basic_smiles_validation(self, smiles: str) -> bool:
        if not smiles or len(smiles) < 1:
            return False
        if smiles.count("(") != smiles.count(")"):
            return False
        if smiles.count("[") != smiles.count("]"):
            return False
        return any(atom in smiles for atom in ["C", "N", "O", "S", "P", "F", "Cl", "Br", "I"])

    def should_use(self, query: str) -> bool:
        raise NotImplementedError("子类必须实现should_use方法")

    def execute(self, query: str) -> Dict[str, Any]:
        raise NotImplementedError("子类必须实现execute方法")

    def _create_base_result(self, query: str) -> Dict[str, Any]:
        return {
            "query": query,
            "success": False,
            "message": "",
            "data": None,
            "formatted": "",
            "reasoning": "",
        }

    def _check_rdkit(self, result: Dict[str, Any]) -> bool:
        if not _rdkit_available():
            result["message"] = "错误: RDKit未安装。请使用以下命令安装: conda install -c conda-forge rdkit"
            return False
        return True


__all__ = ["BaseMolecularTool", "Chem", "RDKIT_AVAILABLE"]
