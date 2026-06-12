#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
反向寻靶工具封装
将 src.reverse_target.predictor.ReverseTargetPredictor 包装为标准 Agent Tool。
"""

from typing import Dict, Any
import logging
from .base_tool import BaseMolecularTool

logger = logging.getLogger(__name__)


class ReverseTargetTool(BaseMolecularTool):
    """反向寻靶预测工具：输入 SMILES → 输出预测的潜在靶点列表"""

    def __init__(self):
        super().__init__(
            name="reverse_target_predictor",
            description="基于 ChEMBL 数据库的 Morgan/MACCS 指纹相似度，反向预测输入分子可能结合的靶点。输入：包含 SMILES 的查询文本。输出：预测靶点列表。"
        )
        self._predictor = None

    def _get_predictor(self):
        """延迟加载预测器（首次调用时加载数据文件）"""
        if self._predictor is None:
            try:
                from src.reverse_target.predictor import get_predictor
                self._predictor = get_predictor()
                logger.info("✅ ReverseTargetPredictor 加载成功")
            except Exception as e:
                logger.error(f"❌ 加载反向寻靶预测器失败: {e}")
                raise
        return self._predictor

    def should_use(self, query: str) -> bool:
        query_lower = query.lower()
        keywords = [
            '靶点', '寻靶', '反向寻靶', '作用靶点', '适应症',
            '作用机制', '治什么病', 'target prediction', 'reverse target',
            '潜在靶点', '候选靶点',
        ]
        # 需要同时包含 SMILES 和靶点关键词
        has_keyword = any(kw in query_lower for kw in keywords)
        has_smiles = bool(self.extract_smiles(query))
        return has_keyword and has_smiles

    def execute(self, query: str) -> Dict[str, Any]:
        result = self._create_base_result(query)

        if not self._check_rdkit(result):
            return result

        # 提取 SMILES
        smiles_list = self.extract_smiles(query)
        if not smiles_list:
            result['message'] = "未在查询中检测到有效的 SMILES 结构。请提供分子的 SMILES 字符串。"
            return result

        smiles = smiles_list[0]
        logger.info(f"反向寻靶: {smiles}")

        try:
            predictor = self._get_predictor()
            targets = predictor.predict(smiles, threshold=0.6, top_k=10, combine_by_target=True)

            if not targets:
                result['success'] = True
                result['message'] = "未找到满足阈值的靶点匹配"
                result['formatted'] = f"## 🎯 反向寻靶结果\n\n查询分子: `{smiles}`\n\n未找到相似度 ≥ 0.6 的已知靶点匹配。可尝试降低相似度阈值。"
                return result

            # 构建格式化输出
            lines = [
                f"## 🎯 反向寻靶预测结果",
                f"",
                f"**查询分子**: `{smiles}`",
                f"**匹配靶点数**: {len(targets)}",
                f"",
                f"| 排名 | 靶点名称 | 物种 | 综合相似度 | Morgan | MACCS | 匹配分子数 |",
                f"|------|----------|------|-----------|--------|-------|-----------|",
            ]

            for i, t in enumerate(targets, 1):
                lines.append(
                    f"| {i} | {t['target_name']} | {t['organism']} | "
                    f"{t['final_similarity']:.3f} | {t['morgan_similarity']:.3f} | "
                    f"{t['maccs_similarity']:.3f} | {t.get('similar_count', 1)} |"
                )

            result['success'] = True
            result['data'] = targets
            result['formatted'] = "\n".join(lines)
            result['message'] = f"成功预测 {len(targets)} 个潜在靶点"

        except FileNotFoundError as e:
            result['message'] = f"反向寻靶数据库文件缺失: {e}。请先运行数据构建脚本。"
        except Exception as e:
            logger.error(f"反向寻靶预测失败: {e}", exc_info=True)
            result['message'] = f"预测失败: {str(e)}"

        return result
