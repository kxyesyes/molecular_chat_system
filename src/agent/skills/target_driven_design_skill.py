"""靶点驱动分子设计编排技能"""

from src.agent.orchestrators import WorkflowStep

from .base_skill import BaseSkill


class TargetDrivenDesignSkill(BaseSkill):
    name = "target_driven_design"
    description = "当用户要求围绕特定靶点设计、生成并初筛候选分子时使用。此技能会先检索靶点结构，再生成候选分子，并串联属性、ADMET、活性和对接评估。"

    trigger_keywords = [
        "基于", "靶点", "靶向", "pde", "egfr", "kras", "braf",
        "设计", "生成", "候选", "类药候选", "候选分子",
        "target-driven", "target driven", "target based", "design for",
    ]

    allowed_tools = [
        "target_database_search",
        "llm_molecular_generator",
        "property_calculator",
        "admet_predictor",
        "activity_predictor",
        "molecular_docking",
    ]

    workflow_steps = [
        WorkflowStep("target_search", "target_database_search"),
        WorkflowStep("molecule_generation", "llm_molecular_generator"),
        WorkflowStep("properties", "property_calculator"),
        WorkflowStep("admet", "admet_predictor"),
        WorkflowStep("activity", "activity_predictor"),
        WorkflowStep("docking", "molecular_docking"),
    ]

    max_iterations_override = 12

    system_prompt = """# 🎯 靶点驱动分子设计专家 (Target-driven Design Expert)

## 你的身份
你是靶点驱动药物设计专家，负责把用户给出的靶点需求转化为可执行的候选分子设计与初筛流程。

## 标准工作流
1. 调用 `target_database_search` 检索靶点、结构和 docking 推荐信息。
2. 调用 `llm_molecular_generator` 生成面向该靶点的候选分子。
3. 调用 `property_calculator` 计算候选分子的基础理化性质。
4. 调用 `admet_predictor` 评估 ADMET 风险。
5. 调用 `activity_predictor` 给出活性模型初筛结果。
6. 调用 `molecular_docking` 做对接占位或真实对接评估。

## 严格红线
- 必须先确认靶点结构信息，再生成候选分子。
- 不能把靶点库搜索和反向寻靶混为一谈。
- 如果对接环境或结构文件不可用，要返回清晰原因并继续给出已完成的候选分子与属性评估。
"""
