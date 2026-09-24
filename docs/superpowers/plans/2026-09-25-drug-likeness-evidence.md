# 类药性证据与评分修复实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. 本轮已获书面设计实施确认，完成后独立SPEC/QUALITY审查；只本地实施和提交。

**Goal:** 修正综合评分参数与类药性解释，保留规则、公式、错误状态及公开结构。

**Architecture:** 仅修改既有DrugLikenessAssessment的调用参数和呈现文案。评分函数、RDKit描述符和适配器不重构；用真实工具与适配器回归证明变化。

**Tech Stack:** Python、RDKit、pytest、现有ToolResult适配器、临时配置/数据库隔离runner。

基线 `fe947c8`，分支 `codex/drug-likeness-evidence-fix`。设计见同日 `drug-likeness-evidence-design.md`。

## Task 1：真实工具TDD与最小修复

Files:
- Create `tests/agent/test_drug_likeness_evidence.py`。
- Modify `src/agent/tools/drug_likeness_assessment.py`。

- [x] 新增真实execute回归，分别断言0/1/多项违反、Veber与lead正反状态、各分项、总分、评级及原描述符。
- [x] 覆盖单例/批量报告、简要推理、无效输入、缺RDKit、计算失败、兼容适配后数值和来源保留。
- [x] 运行新文件，记录真实RED；正确既有行为首次通过不制造失败。
- [x] 仅修复参数与文案，再运行同一文件GREEN。

核心失败回归起点（扩展参数化夹具为20/40碳链和普萘洛尔，并独立计算RDKit基准）：

```python
from rdkit import Chem
from rdkit.Chem import QED
from src.agent.tools.drug_likeness_assessment import DrugLikenessAssessment

def test_ethanol_uses_actual_rule_results():
    result = DrugLikenessAssessment().execute('CCO')
    assert result['success']
    assessment = result['data'][0]['assessment']
    parts = assessment['overall_assessment']['component_scores']
    assert parts['lipinski'] == 1.0
    assert parts['veber'] == 1.0
    assert parts['lead_likeness'] == 0.3
    raw_qed = QED.qed(Chem.MolFromSmiles('CCO'))
    assert assessment['overall_assessment']['score'] == round(0.4 * raw_qed + 0.3 + 0.2 + 0.03, 3)
    assert '预测具有良好的口服生物利用度' not in result['formatted']
```

调用方替换为：

```python
overall_assessment = self._calculate_overall_assessment(
    qed_score,
    lipinski_violations['violation_count'],
    veber_compliance['overall_compliance'],
    lead_likeness['overall_compliance'],
)
```

本模块复用边界文字（不引入跨模块依赖）：

```python
_EVIDENCE_BOUNDARY = (
    '本结果来自RDKit描述符计算与启发式规则判断；综合评分为既有加权规则评分，'
    '不是成药成功概率，不能确定口服生物利用度或疗效。'
)
```

在每个 `format_assessment` 报告末尾增加该说明；单分子brief reasoning和批量reasoning同样保留。
单分子解释中用以下规则文字替代药代推断（原QED/总分说明仍保留）：

```python
count = lipinski['violation_count']
interpretations.append(
    '未违反所检查的Lipinski阈值' if count == 0
    else f'违反{count}项所检查的Lipinski阈值，仅作为规则筛选提示'
)
```

测试须独立断言原公式各分项及0.8/0.6/0.4评级边界，区分未舍入评分与展示舍入。
真实数据、旧字段/类型和单项RDKit数值不变；不能调整既有权重/阈值来迎合总分断言。

## Task 2：双审与聚焦回归

- [x] 独立SPEC审查设计覆盖，无缺失后再做QUALITY审查。
- [x] 对发现问题先复现再最小修复，回传增量复审。
- [x] 聚焦：新增文件、explicit_molecular_input、property_report_boundaries、decision_inputs、decision_chat。
- [x] 对照基线确认评分函数本体及规则函数未变，错误状态/资源清理未削弱。

## Task 3：最终验收与交付

Files:
- Create `docs/handoff/drug-likeness-evidence.md`，记录RED/GREEN、科学数值变化、来源/历史边界。
- Modify `docs/handoff/latest.md` 和已批准设计/本计划的完成记录。

- [x] 冻结源码/测试哈希，运行Agent及相关Web联合回归、compileall、diff检查。
- [x] 文档记录实际命令、失败/skip/warnings和独立审查结论，不将本轮称为真实模型验收。
- [x] 精确暂存本任务文件、本地commit，保持原始工作树不动；不推送/PR/合并/部署。

## 运行方式

复用 `docs/superpowers/plans/2026-09-24-rag-service-extraction.md` 中隔离runner，
仅将repo替换为当前工作树。Python为MedChat Conda；清除业务环境、使用临时目录，真实模型/canary关闭。

```powershell
$runner | & $python -B -c "import sys; exec(sys.stdin.read())" tests/agent/test_drug_likeness_evidence.py
$runner | & $python -B -c "import sys; exec(sys.stdin.read())" tests/agent/test_drug_likeness_evidence.py tests/agent/test_explicit_molecular_input.py tests/agent/test_property_report_boundaries.py tests/agent/test_decision_inputs.py tests/agent/test_decision_chat.py
$runner | & $python -B -c "import sys; exec(sys.stdin.read())" tests/agent tests/test_agent_session.py tests/test_agent_session_entrypoints.py tests/test_agent_task_ownership.py tests/test_phase2_phase3_routes.py tests/test_web_app_lifecycle.py tests/test_static_placeholder_cleanup.py
git diff --check
```

编译使用独立临时pycache路径，结束清理该路径，不触碰历史资产。全部期望PASS只是计划；实际结果写入交接。
