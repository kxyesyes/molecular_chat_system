"""
技能注册中心 (Skill Registry)
自动发现并注册所有 Skill，提供统一的查询和路由接口。
"""

import logging
from typing import List, Optional, Dict, Any

from .base_skill import BaseSkill
from .molecular_design_skill import MolecularDesignSkill
from .activity_prediction_skill import ActivityPredictionSkill
from .reverse_target_skill import ReverseTargetSkill
from .target_search_skill import TargetSearchSkill
from .admet_skill import AdmetSkill
from .docking_skill import DockingSkill
from .comprehensive_evaluation_skill import ComprehensiveEvaluationSkill
from .hit_to_lead_skill import HitToLeadSkill
from .target_driven_design_skill import TargetDrivenDesignSkill
from .rag_search_skill import RAGSearchSkill

logger = logging.getLogger(__name__)


class SkillRegistry:
    """
    技能注册中心。
    负责管理所有可用的 Skill，并提供路由能力。
    """

    def __init__(self):
        self.skills: List[BaseSkill] = []
        self._register_all_skills()

    def _register_all_skills(self):
        """注册系统中所有可用的技能。
        
        注意：编排技能 (Workflow Skills) 排在原子技能之前。
        路由器使用关键词命中数最多的技能，编排技能的关键词（如"全面分析""优化"）
        在语义上更具体，能优先于原子技能匹配。
        """
        skill_classes = [
            # ── 编排技能 (Workflow Skills) ──
            ComprehensiveEvaluationSkill,
            HitToLeadSkill,
            TargetDrivenDesignSkill,
            # ── 原子技能 (Domain Skills) ──
            MolecularDesignSkill,
            ActivityPredictionSkill,
            ReverseTargetSkill,
            TargetSearchSkill,
            AdmetSkill,
            DockingSkill,
            RAGSearchSkill,
        ]
        for cls in skill_classes:
            skill = cls()
            self.skills.append(skill)
            skill_type = "编排" if hasattr(skill, 'max_iterations_override') else "原子"
            logger.info(f"注册技能 [{skill_type}]: {skill.name}")

    def get_skill_catalog(self) -> str:
        """
        生成技能目录摘要，用于传给 LLM 做语义路由。
        格式精简，降低 token 消耗。
        """
        lines = []
        for i, skill in enumerate(self.skills, 1):
            lines.append(f"{i}. **{skill.name}**: {skill.description}")
        return "\n".join(lines)

    def get_skill_by_name(self, name: str) -> Optional[BaseSkill]:
        """根据 name 精确查找技能。"""
        for skill in self.skills:
            if skill.name == name:
                return skill
        return None

    def route_by_rules(self, query: str) -> Optional[BaseSkill]:
        """
        基于关键词规则的快速路由。
        如果有多个技能匹配，选择关键词命中数最多的那个。
        """
        best_skill = None
        best_score = 0
        query_lower = query.lower()

        for skill in self.skills:
            score = sum(1 for kw in skill.trigger_keywords if kw in query_lower)
            if score > best_score:
                best_score = score
                best_skill = skill

        if best_skill:
            logger.info(f"规则路由命中: {best_skill.name} (得分: {best_score})")
        return best_skill

    def build_llm_router_prompt(self, query: str) -> str:
        """
        构建用于 LLM 语义路由的分类提示词。
        由主脑大模型（GLM-4/Qwen3）执行，返回技能名称。
        """
        catalog = self.get_skill_catalog()
        return f"""你是药物设计系统的意图分类器。请根据用户的问题，从以下技能中选择**最合适的一个**。

## 可用技能
{catalog}

## 规则
- 只返回技能名称（如 `admet_assessment`），不要输出任何其他内容。
- 如果用户的问题是闲聊、问候或与药物设计无关，返回 `none`。
- 如果用户要求"全面分析""综合评估""一键体检"，选择 `comprehensive_evaluation`。
- 如果用户要求"优化分子""改善属性""先导物优化"，选择 `hit_to_lead_optimization`。
- 如果用户提供了 SMILES 并只问"计算属性/分子量"，选择 `admet_assessment`。
- 如果用户问"这个分子能治什么病/靶点是什么"，选择 `reverse_target_prediction`。
- 如果用户用基因名/蛋白质名查结构，选择 `target_database_search`。

## 用户问题
{query}

## 你的选择（只输出技能名称）："""
