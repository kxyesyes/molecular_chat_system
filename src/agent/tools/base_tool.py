#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Agent工具基类 - 统一SMILES提取和验证逻辑
"""

from typing import Dict, List, Optional, Any
import logging
import re

try:
    from rdkit import Chem
    RDKIT_AVAILABLE = True
except ImportError:
    RDKIT_AVAILABLE = False

logger = logging.getLogger(__name__)


class BaseMolecularTool:
    """分子工具基类，提供通用的SMILES处理功能"""

    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description

        # 通用排除词汇
        self.exclude_words = {
            'calculate', 'compute', 'predict', 'estimate', 'analyze', 'evaluate',
            'properties', 'property', 'molecular', 'structure', 'formula',
            '计算', '预测', '估计', '分析', '评估', '属性', '性质', '分子', '结构', '的'
        }

    def extract_smiles(self, text: str) -> List[str]:
        """统一的SMILES提取方法 - 优化版，减少误匹配"""
        candidates = []

        # 方法1: 完整SMILES结构优先检查（提高准确性）
        # 匹配复杂分子结构模式
        complex_smiles_pattern = r'[A-Za-z][A-Za-z0-9@+\-\[\]\(\)=#\.\\/:]{5,}'
        complex_matches = re.findall(complex_smiles_pattern, text)

        for match in complex_matches:
            candidate = match.strip().strip("`'\".,;")
            # 过滤掉纯英文单词
            if (
                not re.match(r'^[a-zA-Z]+$', candidate)
                and len(candidate) >= 6
                and self._is_plausible_smiles_lexeme(candidate)
                and self.validate_smiles(candidate)
            ):
                candidates.append(candidate)
                logger.info(f"检测到复杂分子结构: {candidate}")

        # 方法2: 检查预定义的简单分子（仅在没有复杂分子时）
        if not candidates:
            simple_molecules = {
                'CCO': 'CCO',  # 乙醇
                'cco': 'CCO',  # 乙醇（小写）
            }

            text_upper = text.upper()
            for pattern, smiles in simple_molecules.items():
                # 使用单词边界确保精确匹配
                if re.search(rf'\b{re.escape(pattern.upper())}\b', text_upper):
                    candidates.append(smiles)
                    logger.info(f"检测到预定义简单分子: {pattern} -> {smiles}")

        # 方法3: 特殊情况处理（复杂分子优先）
        special_patterns = [
            r'O=C\([^)]+\)[^.]{10,}',  # 含羰基的复杂分子
            r'[CNO][CNO0-9\(\)\[\]=@#\-\+\.]{10,}',  # 长链复杂分子
        ]

        for pattern in special_patterns:
            matches = re.findall(pattern, text)
            for match in matches:
                candidate = match.strip().strip("`'\".,;")
                if len(candidate) >= 10 and self.validate_smiles(candidate):  # 确保是复杂分子
                    candidates.append(candidate)
                    logger.info(f"检测到特殊分子模式: {candidate}")

        # 验证并去重
        valid_smiles = []
        seen = set()

        for candidate in candidates:
            if candidate in seen or len(candidate) < 3:  # 提高最小长度要求
                continue

            # 跳过明显的错误匹配
            if candidate.lower() in ['and', 'the', 'for', 'with', 'from', 'this', 'that']:
                continue

            # 跳过单字符或双字符（除非是预定义的）
            if len(candidate) <= 2 and candidate not in ['CCO']:
                continue

            if self._is_plausible_smiles_lexeme(candidate) and self.validate_smiles(candidate):
                valid_smiles.append(candidate)
                seen.add(candidate)
                logger.info(f"验证成功的SMILES: {candidate}")

        return valid_smiles

    def validate_smiles(self, smiles: str) -> bool:
        """验证SMILES有效性"""
        if not RDKIT_AVAILABLE:
            return self._basic_smiles_validation(smiles)

        try:
            try:
                from rdkit import rdBase

                with rdBase.BlockLogs():
                    mol = Chem.MolFromSmiles(smiles)
            except (AttributeError, ImportError):
                mol = Chem.MolFromSmiles(smiles)
            return mol is not None
        except:
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
        """基本SMILES验证（无RDKit时）"""
        if not smiles or len(smiles) < 1:
            return False

        # 检查括号平衡
        if smiles.count('(') != smiles.count(')'):
            return False
        if smiles.count('[') != smiles.count(']'):
            return False

        # 必须包含常见原子
        common_atoms = ['C', 'N', 'O', 'S', 'P', 'F', 'Cl', 'Br', 'I']
        if not any(atom in smiles for atom in common_atoms):
            return False

        return True

    def should_use(self, query: str) -> bool:
        """判断是否应该使用此工具（子类需要实现）"""
        raise NotImplementedError("子类必须实现should_use方法")

    def execute(self, query: str) -> Dict[str, Any]:
        """执行工具逻辑（子类需要实现）"""
        raise NotImplementedError("子类必须实现execute方法")

    def _create_base_result(self, query: str) -> Dict[str, Any]:
        """创建标准结果结构"""
        return {
            'query': query,
            'success': False,
            'message': '',
            'data': None,
            'formatted': '',
            'reasoning': ''
        }

    def _check_rdkit(self, result: Dict[str, Any]) -> bool:
        """检查RDKit是否可用"""
        if not RDKIT_AVAILABLE:
            result['message'] = "错误: RDKit未安装。请使用以下命令安装: conda install -c conda-forge rdkit"
            return False
        return True


def execute_tool_compat(tool: Any, query: Any, **kwargs: Any):
    """Run legacy tools and normalize their output into ToolResult."""
    import time

    from src.agent.contracts import (
        AgentErrorCode,
        ToolProvenance,
        ToolResult,
        WorkflowArtifact,
    )

    def normalize_artifacts(values: Any) -> list[WorkflowArtifact]:
        artifacts: list[WorkflowArtifact] = []
        for value in values or []:
            if isinstance(value, WorkflowArtifact):
                artifacts.append(value)
            elif isinstance(value, dict):
                artifacts.append(
                    WorkflowArtifact(
                        artifact_type=str(value.get("artifact_type", "file")),
                        path=str(value.get("path", "")),
                        label=str(value.get("label", value.get("path", "artifact"))),
                        mime_type=value.get("mime_type"),
                        metadata=dict(value.get("metadata") or {}),
                    )
                )
        return artifacts

    def normalize_error_code(value: Any) -> AgentErrorCode:
        try:
            return AgentErrorCode(str(value))
        except ValueError:
            return AgentErrorCode.INTERNAL_ERROR

    def normalize_provenance(raw_result: dict[str, Any]) -> ToolProvenance | None:
        if "provenance" not in raw_result or raw_result["provenance"] is None:
            return None
        try:
            provenance = ToolProvenance.from_dict(raw_result["provenance"])
        except (TypeError, ValueError):
            raise ValueError("Tool provenance failed strict validation") from None
        if provenance.tool_name != tool_name:
            raise ValueError("Tool provenance failed strict validation")
        return provenance

    tool_name = getattr(tool, "name", tool.__class__.__name__)
    start = time.perf_counter()

    try:
        raw_result = tool.execute(query, **kwargs)
        elapsed_ms = int((time.perf_counter() - start) * 1000)

        if isinstance(raw_result, ToolResult):
            raw_result.elapsed_ms = (
                raw_result.elapsed_ms
                if raw_result.elapsed_ms is not None
                else elapsed_ms
            )
            return raw_result

        if isinstance(raw_result, dict):
            try:
                provenance = normalize_provenance(raw_result)
            except ValueError:
                return ToolResult.error_result(
                    tool_name=tool_name,
                    code=AgentErrorCode.INVALID_OUTPUT,
                    message="Tool provenance failed strict validation",
                    elapsed_ms=elapsed_ms,
                )
            if raw_result.get("success", False):
                return ToolResult.success_result(
                    tool_name=tool_name,
                    data=raw_result.get("data"),
                    message=raw_result.get("message", ""),
                    formatted=raw_result.get("formatted", ""),
                    elapsed_ms=elapsed_ms,
                    warnings=list(raw_result.get("warnings") or []),
                    evidence=list(raw_result.get("evidence") or []),
                    artifacts=normalize_artifacts(raw_result.get("artifacts")),
                    quality=dict(raw_result.get("quality") or {}),
                    provenance=provenance,
                )
            raw_error = raw_result.get("error") or {}
            if isinstance(raw_error, dict):
                error_message = raw_error.get("message") or raw_result.get(
                    "message", "Tool execution failed"
                )
                error_code = raw_error.get("code")
                error_details = raw_error.get("details") or {"raw_result": raw_result}
            else:
                error_message = str(raw_error) or raw_result.get(
                    "message", "Tool execution failed"
                )
                error_code = raw_result.get("error_code")
                error_details = {"raw_result": raw_result}
            return ToolResult.error_result(
                tool_name=tool_name,
                code=normalize_error_code(error_code),
                message=error_message,
                details=error_details,
                elapsed_ms=elapsed_ms,
                warnings=list(raw_result.get("warnings") or []),
                evidence=list(raw_result.get("evidence") or []),
                artifacts=normalize_artifacts(raw_result.get("artifacts")),
                quality=dict(raw_result.get("quality") or {}),
                provenance=provenance,
            )

        return ToolResult.success_result(
            tool_name=tool_name,
            data=raw_result,
            message="Tool execution completed",
            elapsed_ms=elapsed_ms,
        )
    except TimeoutError as exc:
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        return ToolResult.error_result(
            tool_name=tool_name,
            code=AgentErrorCode.TOOL_TIMEOUT,
            message=str(exc) or "Tool execution timed out",
            elapsed_ms=elapsed_ms,
        )
    except Exception as exc:
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        return ToolResult.error_result(
            tool_name=tool_name,
            code=AgentErrorCode.INTERNAL_ERROR,
            message=str(exc),
            elapsed_ms=elapsed_ms,
        )
