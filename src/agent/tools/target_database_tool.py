#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
靶点数据库搜索工具封装
将 src.target_search.service.TargetSearchService 包装为标准 Agent Tool。
"""

from typing import Dict, Any
import logging
from .base_tool import BaseMolecularTool

logger = logging.getLogger(__name__)


class TargetDatabaseTool(BaseMolecularTool):
    """靶点数据库搜索工具：输入靶点名/基因名/疾病名 → 输出靶点结构信息"""

    def __init__(self):
        super().__init__(
            name="target_database_search",
            description="通过基因名(如PDE5A)、蛋白质名、UniProt ID 或疾病关键词，检索本地靶点数据库获取靶点信息和三维结构索引。输入：靶点名称或疾病关键词。"
        )
        self._service = None

    def _get_service(self):
        """延迟加载 TargetSearchService"""
        if self._service is None:
            try:
                from src.target_search.service import TargetSearchService
                self._service = TargetSearchService()
                logger.info("✅ TargetSearchService 加载成功")
            except Exception as e:
                logger.error(f"❌ 加载靶点搜索服务失败: {e}")
                raise
        return self._service

    def should_use(self, query: str) -> bool:
        query_lower = query.lower()
        keywords = [
            'pde', '结构', '蛋白质', '基因', 'uniprot', 'alphafold',
            'pdb', '晶体结构', '靶点信息', '靶点数据库', '查找靶点',
            '对接推荐',
        ]
        return any(kw in query_lower for kw in keywords)

    def execute(self, query: str) -> Dict[str, Any]:
        result = self._create_base_result(query)

        # 对于靶点搜索，输入是关键词而不是 SMILES
        search_query = query.strip()
        logger.info(f"靶点数据库搜索: {search_query}")

        try:
            service = self._get_service()
            search_result = service.search_targets(search_query)
            targets = search_result.get("results", [])

            if not targets:
                result['success'] = True
                result['message'] = "未找到匹配的靶点"
                result['formatted'] = f"## 🗄️ 靶点数据库搜索结果\n\n查询关键词: `{search_query}`\n\n数据库中未找到匹配的靶点信息。"
                return result

            lines = [
                f"## 🗄️ 靶点数据库搜索结果",
                f"",
                f"**查询关键词**: `{search_query}`",
                f"**匹配靶点数**: {len(targets)}",
                f"",
            ]

            for i, t in enumerate(targets, 1):
                gene = t.get('gene_symbol', 'N/A')
                protein = t.get('protein_name', 'N/A')
                uniprot = t.get('uniprot_id', 'N/A')
                organism = t.get('organism', 'N/A')
                struct_count = t.get('structure_count', 0)
                has_exp = "✅" if t.get('has_experimental_structure') else "❌"
                has_af = "✅" if t.get('has_alphafold_structure') else "❌"
                match_reason = t.get('match_reason', '')

                lines.append(f"### {i}. {gene} ({protein})")
                lines.append(f"- **UniProt**: {uniprot}")
                lines.append(f"- **物种**: {organism}")
                lines.append(f"- **结构数量**: {struct_count}")
                lines.append(f"- **实验结构**: {has_exp} | **AlphaFold**: {has_af}")
                lines.append(f"- **匹配来源**: {match_reason}")
                lines.append("")

            result['success'] = True
            result['data'] = targets
            result['formatted'] = "\n".join(lines)
            result['message'] = f"找到 {len(targets)} 个匹配靶点"

        except Exception as e:
            logger.error(f"靶点数据库搜索失败: {e}", exc_info=True)
            result['message'] = f"搜索失败: {str(e)}"

        return result
