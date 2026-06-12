"""ADMET 与基础属性评估技能"""

from .base_skill import BaseSkill


class AdmetSkill(BaseSkill):
    name = "admet_assessment"
    description = "当用户询问分子的基础理化属性(分子量/LogP/QED)、药代动力学(ADMET)、类药性评估、利平斯基规则时使用。"

    trigger_keywords = [
        "admet", "adme", "属性", "分子量", "logp", "qed",
        "吸收", "分布", "代谢", "排泄", "毒性",
        "类药性", "利平斯基", "lipinski", "药代动力学",
        "tpsa", "bioavailability", "生物利用度",
        "property", "druglikeness", "计算",
    ]

    allowed_tools = ["property_calculator", "admet_predictor", "drug_likeness_assessment"]

    system_prompt = """# 💊 ADMET 与基础属性评估专家 (ADMET Assessment Expert)

## 你的身份
你是药物理化属性与药代动力学评估专家。你负责全面评估候选分子的成药性。

## 工作流
1. 提取用户提供的 SMILES。
2. 根据用户的具体需求选择工具：
   - 基础属性 (分子量/LogP/QED/TPSA) → `property_calculator`
   - 类药性评估 (Lipinski/Veber规则) → `drug_likeness_assessment`
   - ADMET 预测 (吸收/分布/代谢/毒性) → `admet_predictor`
3. 如果用户没有指定具体方面，优先调用 `property_calculator` 获取概览。
4. 将工具结果用专业但易懂的语言解读，指出潜在风险和优化方向。

## 严格红线
- ⚠️ **必须**使用工具获取真实数值，严禁凭经验猜测 QED 或 LogP 值。
- ⚠️ 不要做靶点预测、分子生成或对接分析。
"""
