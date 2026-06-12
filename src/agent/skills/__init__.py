"""
Agent Skills 技能包系统
基于 Context Engineering 理念，实现按需加载上下文的技能路由机制。
"""

from .base_skill import BaseSkill
from .skill_registry import SkillRegistry

__all__ = ["BaseSkill", "SkillRegistry"]
