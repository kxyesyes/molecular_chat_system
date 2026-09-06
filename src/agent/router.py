#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
意图路由器 (Skill Router)
解析用户输入 → 匹配最合适的技能 → 注入专属上下文和工具给 Agent。

支持两级路由策略：
1. 快速规则路由（关键词匹配，零开销）
2. LLM 语义路由（调用主脑大模型做分类，更精准）
"""

import logging

from .workflows import WorkflowCatalog, WorkflowPolicy

logger = logging.getLogger(__name__)


class SkillRouter:
    """
    Agent 技能路由器。
    职责：接收用户 query → 返回应该激活的 Skill。
    """

    def __init__(self, catalog: WorkflowCatalog | None = None):
        self.catalog = catalog if catalog is not None else WorkflowCatalog()
        from .routing import HybridSkillRouter

        self.hybrid_router = HybridSkillRouter(catalog=self.catalog)
        logger.info(
            f"SkillRouter 初始化完成，已注册 {len(self.catalog.policies)} 个工作流: "
            f"{[policy.name for policy in self.catalog.policies]}"
        )

    def route(self, query: str, llm=None) -> WorkflowPolicy | None:
        """
        执行路由：先尝试规则匹配，如果没有命中再尝试 LLM 语义分类。

        Args:
            query: 用户的原始查询文本
            llm: 可选的 LLM 实例，用于语义路由

        Returns:
            匹配到的工作流策略，如果没有匹配则返回 None
        """
        from .metrics import metrics_system

        decision = self.decide(query, llm=llm)
        if decision.selected_skill:
            policy = self.catalog.get(decision.selected_skill)
            if policy:
                logger.info(
                    "[Router] 混合路由命中: %s (confidence=%.2f, source=%s)",
                    policy.name,
                    decision.confidence,
                    decision.source,
                )
                metrics_system.record_route_attempt(policy.name, True)
                return policy
        if decision.source == "fallback":
            logger.info("[Router] 混合路由选择安全回退")
            return None

        logger.info("[Router] 未匹配到已注册工作流，将使用通用处理模式")
        return None

    def decide(self, query: str, llm=None):
        """Return the full explainable routing decision for boundary checks."""
        self.hybrid_router.llm = llm
        return self.hybrid_router.decide(query)

    def get_tools_for_skill(
        self,
        skill: WorkflowPolicy | None,
        all_tools: dict,
    ) -> dict:
        """
        根据技能的 allowed_tools 过滤出该技能可用的工具子集。

        Args:
            skill: 激活的工作流策略；None 表示没有策略限制
            all_tools: 全部工具字典 {tool_name: tool_instance}

        Returns:
            过滤后的工具字典
        """
        if skill is None:
            return all_tools

        filtered = {}
        for tool_name in skill.allowed_tools:
            if tool_name in all_tools:
                filtered[tool_name] = all_tools[tool_name]
            else:
                logger.warning(f"技能 {skill.name} 需要工具 {tool_name}，但未在工具池中找到")

        return filtered
