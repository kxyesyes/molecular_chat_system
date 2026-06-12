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
import re
from typing import Optional, Dict, Any, List

from .skills import SkillRegistry, BaseSkill

logger = logging.getLogger(__name__)


class SkillRouter:
    """
    Agent 技能路由器。
    职责：接收用户 query → 返回应该激活的 Skill。
    """

    def __init__(self):
        self.registry = SkillRegistry()
        logger.info(
            f"SkillRouter 初始化完成，已注册 {len(self.registry.skills)} 个技能: "
            f"{[s.name for s in self.registry.skills]}"
        )

    def route(self, query: str, llm=None) -> Optional[BaseSkill]:
        """
        执行路由：先尝试规则匹配，如果没有命中再尝试 LLM 语义分类。

        Args:
            query: 用户的原始查询文本
            llm: 可选的 LLM 实例，用于语义路由

        Returns:
            匹配到的 Skill 实例，如果没有匹配则返回 None
        """
        from .metrics import metrics_system

        # 第一级：快速规则路由
        skill = self.registry.route_by_rules(query)
        if skill:
            logger.info(f"[Router] 规则路由命中: {skill.name}")
            metrics_system.record_route_attempt(skill.name, True)
            return skill

        # 第二级：LLM 语义路由（如果有大模型可用）
        if llm:
            skill = self._llm_route(query, llm)
            if skill:
                logger.info(f"[Router] LLM 语义路由命中: {skill.name}")
                metrics_system.record_route_attempt(skill.name, True)
                return skill

        logger.info("[Router] 未匹配到任何技能，将使用通用处理模式")
        return None

    def _llm_route(self, query: str, llm) -> Optional[BaseSkill]:
        """
        使用 LLM 进行语义路由。
        调用主脑大模型（GLM-4/Qwen3），让它从技能目录中选择最合适的一个。
        """
        try:
            prompt = self.registry.build_llm_router_prompt(query)

            # 兼容同步/异步 LLM
            import inspect
            if inspect.iscoroutinefunction(getattr(llm, 'generate', None)):
                import asyncio
                import threading
                import queue

                result_queue = queue.Queue()

                def run_async():
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    try:
                        result = loop.run_until_complete(
                            asyncio.wait_for(llm.generate(prompt, temperature=0.1, max_tokens=50), timeout=15.0)
                        )
                        result_queue.put(result)
                    except Exception as e:
                        result_queue.put(None)
                        logger.warning(f"LLM 路由调用失败: {e}")
                    finally:
                        loop.close()

                thread = threading.Thread(target=run_async)
                thread.start()
                thread.join(timeout=20.0)

                response = result_queue.get() if not result_queue.empty() else None
            else:
                response = llm.generate(prompt) if hasattr(llm, 'generate') else None

            if not response:
                return None

            # 从 LLM 返回中提取技能名称
            skill_name = self._parse_skill_name(response)
            if skill_name and skill_name != "none":
                return self.registry.get_skill_by_name(skill_name)

        except Exception as e:
            logger.warning(f"LLM 路由出错: {e}")

        return None

    def _parse_skill_name(self, response: str) -> Optional[str]:
        """从 LLM 的返回文本中提取技能名称。"""
        response = response.strip().lower()

        # 去除可能的 markdown 格式
        response = response.replace('`', '').replace('*', '').strip()

        # 尝试精确匹配
        known_names = [s.name for s in self.registry.skills]
        for name in known_names:
            if name in response:
                return name

        # 如果 LLM 返回的就是技能名
        if response in known_names:
            return response

        return response if response != "none" else None

    def get_tools_for_skill(self, skill: BaseSkill, all_tools: dict) -> dict:
        """
        根据技能的 allowed_tools 过滤出该技能可用的工具子集。

        Args:
            skill: 激活的技能
            all_tools: 全部工具字典 {tool_name: tool_instance}

        Returns:
            过滤后的工具字典
        """
        if not skill.allowed_tools:
            return all_tools  # 如果技能没有限制，返回所有工具

        filtered = {}
        for tool_name in skill.allowed_tools:
            if tool_name in all_tools:
                filtered[tool_name] = all_tools[tool_name]
            else:
                logger.warning(f"技能 {skill.name} 需要工具 {tool_name}，但未在工具池中找到")

        return filtered
