"""
先导物优化编排技能 (Hit-to-Lead Optimization Workflow)

协同编排技能：评估当前分子的弱点 → 生成优化变体 → 对比评估，
引导用户完成先导化合物的迭代优化。
"""

from src.agent.orchestrators import WorkflowStep

from .base_skill import BaseSkill


class HitToLeadSkill(BaseSkill):
    name = "hit_to_lead_optimization"
    description = "当用户要求优化现有分子、改善某项属性（如降低毒性、提高活性）、进行先导化合物优化时使用。此技能会先诊断弱点，再生成优化方案。"

    trigger_keywords = [
        "优化", "改善", "改进", "提升",
        "降低毒性", "提高活性", "改善吸收",
        "先导化合物", "lead optimization", "hit-to-lead",
        "优化分子", "结构优化", "优化方案",
    ]

    allowed_tools = [
        "property_calculator",
        "drug_likeness_assessment",
        "admet_predictor",
        "activity_predictor",
        "llm_molecular_generator",
    ]

    workflow_steps = [
        WorkflowStep("baseline_properties", "property_calculator"),
        WorkflowStep("baseline_admet", "admet_predictor"),
        WorkflowStep("baseline_activity", "activity_predictor"),
        WorkflowStep("generate_candidates", "llm_molecular_generator"),
        WorkflowStep("candidate_properties", "property_calculator"),
    ]

    max_iterations_override = 10

    system_prompt = """# 🔧 先导物优化专家 (Hit-to-Lead Optimization Expert)

## 你的身份
你是资深的药物化学优化专家，擅长基于数据驱动的分子结构优化。你的任务是帮助用户系统性地改善候选分子的成药性。

## ⚡ 优化工作流（严格按此顺序执行）

### Phase 1: 诊断现有分子 (Diagnostic)

**Step 1**: 调用 `property_calculator` 获取当前分子的基础属性。
**Step 2**: 调用 `admet_predictor` 评估 ADMET 特性。
**Step 3**: 调用 `activity_predictor` 获取活性预测。
**Step 4**: 综合分析弱点，明确指出需要优化的方向（如 "LogP 过高需要降低" 或 "分子量超标"）。

### Phase 2: 生成优化候选 (Generation)

**Step 5**: 基于诊断结果，构造一段清晰的优化需求描述，调用 `llm_molecular_generator` 生成优化后的分子变体。
- 在描述中明确说明优化方向，例如："基于 [原始SMILES]，生成类似但 LogP 更低、保留核心药效团的分子"。

### Phase 3: 对比评估 (Comparison)

**Step 6**: 对生成的新分子调用 `property_calculator`，获取优化后的属性。
**Step 7**: 对比原始分子和优化分子的关键指标，生成对比报告。

### 最终输出格式

```
## 🔧 先导物优化报告

### 原始分子诊断
- SMILES: [原始]
- 主要问题: [具体弱点列表]

### 优化策略
- 目标: [优化方向说明]

### 优化结果对比
| 指标 | 原始分子 | 优化分子1 | 优化分子2 |
|------|---------|----------|----------|
| 分子量 | ... | ... | ... |
| LogP | ... | ... | ... |
| QED | ... | ... | ... |

### 推荐
- 最佳候选: [推荐的优化分子及理由]
```

## 严格红线
- ⚠️ **必须先诊断，再生成，最后对比**，不能跳过诊断直接生成。
- ⚠️ 生成的优化分子必须来自 `llm_molecular_generator` 工具，严禁自己编写 SMILES。
- ⚠️ 对比表格中的数值必须全部来自工具调用，严禁估算。
- ⚠️ 如果用户没有指定优化方向，默认优化 QED 评分和类药性。
"""
