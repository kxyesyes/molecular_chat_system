# 科研反馈边界修复交接（2026-09-08）

## 范围

用户确认的最小修复：明确无效 SMILES 的澄清提示；性质报告不再把描述符/规则当作药效、吸收、成药性实验结论。基于 `444d73e`，分支 `codex/scientific-feedback-clarity`，工作树 `scientific-assets`。不改原始混杂工作树，不迁移生产入口，不配置凭据、不启用模型、不合并 main。

## 已查明原因

1. `decision_loop.py` 为防止模型在澄清问题中夹带伪造科研数值，将所有科研 clarify 替换为同一句话。这个安全边界必要，但原有展示无法区分输入无效、未提供结构或校验不可用。
2. `property_calculator.py` 的报告、简短 reasoning、附加 interpretation 分别实现了不一致的规则判断；部分路径遗漏 LogP，部分把 TPSA 计入 Lipinski 违反项，并使用“口服药物潜力优秀”“预测具有良好的膜透过性”等未经计算支持的结论。

## 实施约束

- 只在模型选择 clarify 后，由服务器调用已有 InputValidator，为当前分子输入提供固定说明。没有新增静态任务路由，没有把 LLM 原文当作科学证据。
- 仅针对必需分子工具提供结构提示；其他任务保持中性澄清。校验通过的结构不能被宣称无效；校验不可用不能冒充结构错误。
- 不回显原始输入、模型问题、missing_fields 或底层异常；屏蔽 RDKit 可能回显输入的解析日志；仅对不超过 256 UTF-8 字节的短输入进行这项同步附加解析。长输入保持中性澄清，正式工具校验边界不变。
- 保留 waiting_for_input、continuation_id、trace 和原有状态流转。续接时使用补充后的当前输入，防止沿用旧的无效诊断。
- 性质工具计算方法、结构化描述符和值保持不变；解释必须区分计算与启发式筛选，不从这些指标推出药效、安全性、选择性或实验结论。

## 文件

- `src/agent/harness/decision_clarification.py`：输入校验证据到受控澄清说明。
- `src/agent/harness/decision_loop.py`：只替换科学 clarify 的显示文本来源。
- `src/agent/tools/property_calculator.py`：报告与规则说明。
- `src/agent/utils/validators.py`：两处解析异常保留既有 `unavailable` 状态，不再作为结构无效证据；仍拒绝执行。
- `tests/agent/test_decision_clarification.py`：提示分支、伪造文本隔离、续接、WebSocket native/json 验收。
- `tests/agent/test_property_report_boundaries.py`：真实 RDKit 数值不变与规则边界。
- `docs/superpowers/plans/2026-09-08-scientific-feedback-clarity.md`：实施计划。
- 本交接文档。

## 验证记录

澄清新增测试观察到预期 RED（10 failed / 2 passed）。规格审查随后复现 parser 异常误报 invalid、反引号格式歧义误报结构错误，补充测试再次 RED（2 failed / 15 passed）。质量审查又复现超长结构同步解析阻塞控制循环：新增 4 个长输入禁止解析用例 RED→GREEN，将附加校验限制到短输入，最终澄清 21 项通过。性质报告初始 RED 为 56 failed，报告去重 RED 为 3 failed / 56 passed，最终 59 项通过。

测试模型是明确的 ScriptedModel；性质计算和输入校验使用真实本地 RDKit 2025.03.6。WebSocket 测试覆盖 native 和 json 协议，但不是付费提供方调用或真实浏览器操作。独立规格审查确认这两项符合批准范围。

独立质量复审已关闭所有发现。原 8,000 atoms 的澄清探针中，事件循环 ticker 最大间隔从 1.595 s 降至 0.056 s（单次本机对比，不是整体并发 SLA）；8,000/16,000 atoms 均绕过附加 RDKit 解析。256/257 字节边界和多字节计数已复验，续接正常。

真实计算复核：CCN 的 MW=45.08、LogP=-0.035、TPSA=26.02、HBA=1、HBD=1、QED=0.406；结构化数据不变。报告包含研究/证据边界说明，不再包含“口服药物潜力优秀”。

实际运行命令（均在 scientific-assets 工作树，Python 为本机 MedChat Conda 环境）：

```text
python -B -m pytest tests/agent/test_decision_clarification.py tests/agent/test_property_report_boundaries.py -q -p no:cacheprovider --tb=short
80 passed（最终聚焦验证；早期独立规格复审为 76 项）

python -B -m pytest tests/agent tests/test_agent_decision_model.py tests/test_openai_compatible_model.py tests/test_admin_auth_routes.py -q -rs -p no:cacheprovider --tb=short
2118 passed, 1 skipped, 7 warnings in 73.99s（最终修复后重跑）

python -B -m pytest tests/test_agent_anti_hallucination_fallbacks.py tests/test_agent_platform_health_check.py tests/agent/test_real_acceptance_checks.py -q -p no:cacheprovider --tb=short
53 passed, 7 warnings

node tests/decision_lab_ui_test.js
27 passed

python -m compileall -q src scripts
passed

python -B scripts/run_agent_acceptance.py --mode contract
34/34 passed
```

跳过项为 `tests/agent/test_harness_shadow.py:277` 的可选性能测试未启用。警告来自既有 SWIG、FastAPI on_event 弃用。上述测试集合有重叠，不累加为一个成功率。contract 报告在忽略的 `outputs/agent_evaluation/agent_acceptance_report.json`，不提交运行产物。

## 兼容与简化

- 描述符计算函数、舍入、`data` 字段、工具入口与续接协议未改变。
- `_assess_lipinski_rules` 保留原返回键；`violations_count` 现在正确地只计四项 Ro5，不再混入 TPSA。Veber 的 TPSA、可旋转键单独说明。这是修正含义，不是声称旧错误计数完全不变。
- 格式化报告只保留一次总评与证据边界，去掉重复附加 interpretation；旧 helper 入口仍可调用，统一使用相同规则结果。
- 不增加 LLM 润色过程或新工作流；规则阈值仅用于描述符启发式筛选，未修改活性分类阈值或模型。

## 限制

本轮未重新调用外部模型、未运行生成/活性/Vina 大规模真实科研案例，也未把独立验收入口接入生产网页。原始项目须经过明确的分支审查/集成后才会包含这些修复。输入说明仍受既有 InputValidator 的保守提取范围限制；不确定语义继续要求澄清，不自行修补或替换分子。
