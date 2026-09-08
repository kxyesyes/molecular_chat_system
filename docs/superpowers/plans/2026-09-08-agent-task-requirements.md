# Agent 任务要求验收实施计划

> **For agentic workers:** Use executing-plans and test-driven-development, followed by independent code review.

**Goal:** 在已批准逐轮 Agent 的同一循环中，将可信请求要求扩展到分子数量、指标和禁止动作，不能由模型放宽。

**Architecture:** 新增严格不可变 TaskRequirements；结果校验复用 ToolResult、EvidenceLedger 和 RDKit。要求从服务端显式传入 run，不从模型正文或 context.metadata 猜测；冻结并参与 continuation 配置指纹。只判断成果，不生成固定工具顺序。

**Tech Stack:** Python、Pydantic 2、RDKit、既有 LangGraph 和 SQLite。

基线 `8c89fef`，独立分支 `codex/agent-task-requirements`，原项目混杂工作树保持不变。使用原 Conda Python。用户已批准总体设计第 6 节与上一批建议；本次不改 Web/默认入口、不用真实 API、不启用生产权重。

## 1. 严格任务契约

Files: 新建 `src/agent/contracts/task_requirements.py`、`tests/agent/test_task_requirements.py`。

- [x] 写 schema RED：额外字段、数字字符串/bool 计数、超限、重复工具条目、未知指标、不适用指标、冲突禁止项均拒绝。
- [x] 实现 frozen/strict Pydantic 契约，嵌套 tuple；输入 dict 转为严格 JSON 校验，实例重新校验后复制，不信任 model_construct。
- [x] GREEN 后验证 JSON roundtrip 和调用期间原 dict 修改不影响内部快照。

```python
requirements = {
    'version': '1',
    'forbidden_tools': ['molecular_docking'],
    'molecular_results': [{
        'tool_name': 'property_calculator', 'exact_molecule_count': 2,
        'required_metrics': ['molecular_weight', 'logp', 'qed'],
        'expected_smiles': ['CCO', 'CCN'],
    }],
}
```

数量本批指**一个已验证工具观察**中的有效唯一分子数，不是调用次数、总行数，也不跨观察拼凑指标。分子规范化后重复、不合法、缺字段、NaN/Infinity、bool 冒充数值均不能通过。expected_smiles 可选；提供时要求同一分子集合；未提供时不声称验证了任意自然语言主体语义。类药性通过与否属于计算结果，False 不等于指标缺失。

指标映射只覆盖真实 property_calculator.properties 与 drug_likeness_assessment.assessment 的字段。活性/靶点仍沿用既有工具/领域证据门，不为未迁移的生成/Top-K 伪造数量验收。

## 2. 成果验收与主循环接线

Files: 新建 `src/agent/harness/decision_requirements.py`；修改 `decision_loop.py`、`decision_continuation.py`；新建 `tests/agent/test_decision_requirements.py`。

- [x] RED：请求 2 个仅返回 1 个不得成功；重复/无效分子、缺 QED、主体替换、跨观察凑数不能满足要求。
- [x] 实现显式字段映射，返回有界 task_acceptance（satisfied、checks、reason_codes、evidence_ids、有效唯一数量与缺失指标），不渲染模型声明。
- [x] RED：禁止项在模型强行选择时调用数仍为零；要求与授权/必需工具冲突在创建 trace 前拒绝；复审补齐实际能力开关、registry 和 owner 授权检查。
- [x] run 增加可选 requirements；默认保持既有工具级行为。约束展示在 system 提示和结构化 tool observation 中；最终结果/状态库/终态事件包含相同验收记录。
- [x] 不满足成果要求时明确 partial/failed，保留已验证成果与差项，不把工具 success 当作任务 completed。

```python
result = await loop.run(context, request_kind='scientific',
    allowed_tools={'property_calculator'}, required_tools={'property_calculator'},
    requirements=requirements)
assert result.metadata['task_acceptance']['satisfied'] is False
assert result.success is False
```

## 3. 恢复、回归与交接

- [x] RED/GREEN：澄清后同要求恢复可执行；更改计数/指标/禁止项或移除约束均拒绝且 waiting 记录不变。
- [x] 真实 RDKit 批量性质/类药性：阿司匹林与咖啡因结果数和完整字段通过；CCO+CCN 旧解析漏项明确 partial（解析器本批不改）。
- [x] 聚焦：`python -B -m pytest tests/agent/test_task_requirements.py tests/agent/test_decision_requirements.py tests/agent/test_decision_loop.py tests/agent/test_decision_inputs.py tests/agent/test_decision_continuation.py -q -p no:cacheprovider --tb=short`：131 passed。
- [x] 完整：`python -B -m pytest tests/agent tests/test_agent_decision_model.py tests/test_openai_compatible_model.py -q -rs -p no:cacheprovider --tb=short`：1875 passed、1 skipped。
- [x] 旧 `run_agent_acceptance.py --mode contract`：34/34；compileall、diff check 通过；独立复审 12 项通过。
- [x] 更新 `docs/handoff/agent-task-requirements.md`，准确记录替身/真实计算边界；精确暂存提交，不 push/PR/合并或重启。
