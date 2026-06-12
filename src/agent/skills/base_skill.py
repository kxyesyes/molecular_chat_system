"""
Agent Skill 基类
每个技能定义了：触发条件、专属 System Prompt、允许使用的工具子集。
"""

from typing import List, Dict, Any


class BaseSkill:
    """
    Agent Skill 基类。

    设计原则 (Context Engineering):
    1. 每个 Skill 拥有独立的 system_prompt，只包含完成当前任务的最小知识。
    2. 每个 Skill 声明 allowed_tools，Agent 运行时只会看到这些工具，
       屏蔽无关工具以减少幻觉和误调用。
    3. 通过 should_trigger() 实现快速的规则路由，
       通过 description 支持 LLM 语义路由。
    """

    # ── 子类必须覆写 ──────────────────────────────────────────
    name: str = "base_skill"
    description: str = "基础技能"
    system_prompt: str = ""
    allowed_tools: List[str] = []
    # 触发关键词（用于快速规则匹配）
    trigger_keywords: List[str] = []

    def should_trigger(self, query: str) -> bool:
        """
        基于关键词的快速触发判断。
        子类可以覆写以实现更复杂的逻辑。
        """
        if not self.trigger_keywords:
            return False
        query_lower = query.lower()
        return any(kw in query_lower for kw in self.trigger_keywords)

    def get_context(self) -> Dict[str, Any]:
        """返回技能被激活时需要注入的全部上下文。"""
        return {
            "name": self.name,
            "system_prompt": self.system_prompt,
            "allowed_tools": self.allowed_tools,
        }
