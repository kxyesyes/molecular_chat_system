# A2 第三批：不可放宽的任务要求

日期：2026-09-08。分支 `codex/agent-task-requirements`；基线 `8c89fef`。
实现仍在独立 scientific-assets worktree；原项目工作树未修改。

## 交付内容

为实验性 `ModelDecisionLoop.run` 增加可选 `requirements`，不改变模型逐轮选择行动的机制，不生成固定工具序列。没有新 Agent 框架、Planner、数据库或外部依赖。

- 严格 Pydantic 契约，不接受额外字段、类型猜测、重复/冲突要求。嵌套 tuple 冻结；调用方原 dict 修改不能影响运行中的要求。
- `forbidden_tools` 为规范工具名，禁止项从目录移除，模型强行选择时在工具执行前拦截。
- 新分子要求还必须匹配实际授权目录；能力关闭、工具未注册或 owner 不匹配时，创建/领取 trace 前拒绝，不浪费模型调用。实际依赖故障仍由已授权工具返回不可用，不把权限错误混同为科研成功。
- `molecular_results` 本批只支持 property_calculator 与 drug_likeness_assessment：有效唯一分子数、每个分子的必需指标，以及可选的指定 SMILES 集合。
- 分子数使用真实 RDKit 规范化去重；不同写法不能凑数。一个观察必须独立满足全部要求，不能跨观察拼接缺失字段或累加重复分子。
- 数值必须是有限数，bool/字符串不能冒充数值；QED、计数、MW/TPSA有基础范围检查。`lipinski_compliant=False` 是有效规则判断，不等于缺失指标；完成计算不代表化合物满足成药性目标。
- 最终通过还要满足原来的必需工具、可用证据和决策协议；模型文本不能覆盖科学值或验收要求。
- `task_requirements`、`task_acceptance` 保存在模型观察、结果元数据、状态库和终态事件中。验收记录包含差项、有效唯一数量、缺失指标、证据 ID 和缺失必需工具。
- 数量不足/缺项时任务只能 partial/failed，保留已有工具结果；终态复核报错也不能继续保留 completed。
- 非空新要求参与 continuation 配置指纹；澄清恢复时更改或移除要求均拒绝，不改变原 waiting 记录。空要求保持原工具级入口兼容。

## 使用方式

```python
requirements = {
    'version': '1',
    'forbidden_tools': ['molecular_docking'],
    'molecular_results': [{
        'tool_name': 'property_calculator',
        'exact_molecule_count': 1,
        'required_metrics': ['molecular_weight', 'logp', 'tpsa', 'hbd', 'hba', 'qed'],
        'expected_smiles': ['CC(=O)Oc1ccccc1C(=O)O'],
    }],
}
result = await loop.run(context, request_kind='scientific',
    allowed_tools={'property_calculator'}, required_tools={'property_calculator'},
    requirements=requirements)
```

要求必须由可信服务端调用方提供。不能直接把浏览器/模型 JSON 当成授权；模型只收到冻结后的要求，不负责定义“什么叫成功”。未传 expected_smiles 时，只检查输出结构和数量，不宣称完成任意自然语言主体解析。需要改变任务要求时应建立新任务，不借 continuation 静默放宽旧任务。

性质指标读 `properties`，类药性指标读 `assessment.molecular_properties`、`assessment.qed_score`、`assessment.lipinski_rule_of_five.compliance`。不接受任意 JSONPath 或同名模型正文。当前不提供目标阈值优化、跨工具指标融合、生成候选数量和 Top-K 排序验收。

## 本轮发现的真实限制

真实执行 `SMILES: CCO` + `SMILES: CCN` 时，既有 `BaseMolecularTool.extract_smiles` 会漏掉短串 CCN，仅计算 CCO。本批新验收能按预期判为 **partial**，并报告数量与分子集合不匹配；没有篡改要求将它计为完整科研成功。

这不是验收字段映射错误。解析器迁移涉及工具输入兼容，已保留负例和交接证据，列为下一步优先事项；本批未修改基础工具或声称已修复这个输入缺口。

真实阿司匹林＋咖啡因批量性质、类药性计算通过；两种工具都返回 2 个符合要求的分子和完整指标。CCO 单分子澄清恢复的真实 RDKit 验收通过。外部决策模型均为明确测试替身/既有 MockTransport，不是实网 LLM 验收；没有激活生产活性模型、Ollama 或 Vina。

## 文件

新增：

- `src/agent/contracts/task_requirements.py`
- `src/agent/harness/decision_requirements.py`
- `tests/agent/test_task_requirements.py`
- `tests/agent/test_decision_requirements.py`
- `docs/superpowers/plans/2026-09-08-agent-task-requirements.md`
- 本交接文档。

修改：`src/agent/harness/decision_loop.py`、`src/agent/harness/decision_continuation.py`。

关键阅读：AGENTS.md、PROJECT_STANDARDS、已批准总体设计、第 2 批交接、现有 loop/continuation/证据校验、工具注册与性质/类药性/活性输出字段、BaseMolecularTool、已有测试。未读取密钥文件。

## 验证记录

解释器为既有 MedChat Conda Python，未安装或升级依赖。

| 命令 | 结果 |
|---|---|
| `python -B -m pytest tests/agent/test_task_requirements.py tests/agent/test_decision_requirements.py tests/agent/test_decision_loop.py tests/agent/test_decision_inputs.py tests/agent/test_decision_continuation.py -q -p no:cacheprovider --tb=short` | 131 passed（本批新增 55 项），23.06 秒 |
| `python -B -m pytest tests/agent tests/test_agent_decision_model.py tests/test_openai_compatible_model.py -q -rs -p no:cacheprovider --tb=short` | 1875 passed、1 skipped、7 warnings，42.48 秒 |
| `python -B scripts/run_agent_acceptance.py --mode contract` | 34/34 passed |
| `python -m compileall -q src scripts` | exit 0 |
| `git diff --check` | exit 0 |

Skipped 是未启用的 harness shadow 性能测试；warnings 为既有 SWIG/FastAPI 弃用提示。contract 负例打印的 RDKit parse error 是预期拒绝。报告位于忽略目录 `outputs/agent_evaluation/agent_acceptance_report.json`，不提交运行产物。

独立审查发现有效授权目录前置检查不足；新增能力开关/未注册/owner 错误三项 RED，再补最小拦截。复审 12 项通过，包括恢复被拒后 waiting/nonce 不变及合法重试；本批范围未发现剩余阻断。没有将契约通过当作真实外部模型验收。

## 下一步

优先迁移当前性质/类药性的显式 SMILES 输入解析，解决短 SMILES 和批量漏项，继续使用完整结构校验、不自行修复无效分子。随后再准备 Supervisor/ChatHandler 的隔离接入与真实主模型验收。

本批未切网站入口、未删除旧业务工作流、未重启服务、未启用生产模型；不 push/创建历史巨型 PR/合并 main。
