# A2：性质与类药性工具的完整 SMILES 输入

日期：2026-09-08。分支 `codex/agent-explicit-smiles-input`；基线 `ee161d8`。
实施位置为独立 scientific-assets worktree，原项目混杂工作树未修改。

## 根因与交付

旧 BaseMolecularTool 只优先提取较长且非纯字母的结构，短串仅特判 CCO；
存在复杂结构时又跳过短串分支。因此 CCN 和长短混合输入会漏项。
旧代码还会把 cco 转为 CCO、删除末尾点号，存在改变结构的风险。

本批新增小型 `src/agent/tools/molecular_input.py`，仅迁移
property_calculator、drug_likeness_assessment 的 should_use/execute 调用。
不修改全局 BaseMolecularTool、活性/生成/对接解析，不新增 Agent/Planner/依赖。
删除两处新解析器下不可达的空结果判断；公共工具名、execute(query)、成功数据结构不变。

- 支持 CCN 等短串、单原子、混合长短批量、重复标签及仅开头一个标签的换行/分号批量。
- 每个完整结构使用真实 RDKit 验证，禁用 parseName 和 CXSMILES 扩展，保留大小写、同位素、立体符号和盐的点号。
- 整个显式批次先验证再计算；无效、空字段、未闭合引号或名称/空格扩展均失败，不从中截取有效片段。
- 独立批量字段保留非法字母后缀供校验，不能通过词法过滤静默漏项。
- 无 RDKit 明确不可用，不用启发式判断当作科研成功。
- 有界输入：文本最多 65536 字符、100 个结构、单结构最多 8192 字符。原样保留重复输入，不把数量统计当作唯一分子数；唯一性仍由 Harness 验收约束判断。
- 成功输出继续经过现有工具适配和证据网关。真实 RDKit 的 CCO+CCN 性质 → evidence ref → 类药性测试验证两分子均被消费，且下游记录上游 evidence ID。

## 输入约定及兼容取舍

推荐：

```text
SMILES: CCO
SMILES: CCN
```

或完整结构以换行/分号分隔；说明文字另起行。引号可以明确包围结构。
例如 `SMILES: "CCO"` 换行后再写英文说明。

`SMILES: CCO.` 不会再删除末尾点号后计算 CCO；原有两条提示词验收依赖这种修复行为，
现改成“歧义输入拒绝 + 清晰引号输入成功”的成对断言。此为有意收紧，不能称为所有旧提示词完全兼容。
未迁移的其他工具仍有旧解析行为，需要分批迁移；本批不修改 legacy/golden 数据文件。

自然语言仅保守提取完整 token，不负责任意文章/解释的意图理解、名称转结构、模型示例识别或自动纠错。
标签前后的独立批量字段也要校验，不忽略首个标签前的结构。
空格/逗号分隔的批量、标签值放在下一行、单独尾随的 QED 等文字不作为支持格式；
看起来以结构开头但带名称/说明的 ASCII 字段会保守拒绝，不自动猜测其意图。
调用方仍应使用可信初始输入和 task requirements 校验指定分子集合、唯一数量和指标；
本批不能代替完整语义验收或取消既有来源限制。

## TDD 与审查

首轮 38 failed / 54 passed，复现短串/批量漏项、改变大小写、截断无效字段；最小实现后 92 passed。
全量回归定位到上述 2 项旧句号兼容测试，强化为拒绝/成功成对断言。
独立审查复现“一次标签后漏掉后续行”和“非法后缀被词法过滤”两项 P2；
补 14 项 RED 后修复，聚焦四文件 119 passed（真实 RDKit）。
复审再发现首个标签前的独立字段、非法后缀与空格名称组合仍可能漏项；
新增 10 项 RED 并修复后聚焦 129 passed，保留这些回归而不只测试单一损坏形式。
最终独立复审：348 项只读真实 RDKit 检查零失败，本轮范围内无剩余 P1/P2；
保留上述严格输入限制，不声称通用自然语言解析已解决。

## 最终验证命令

使用 `C:/Users/xkx52/.conda/envs/MedChat/python.exe`，未安装新依赖。

| 命令 | 结果 |
|---|---|
| `python -B -m pytest tests/agent/test_explicit_molecular_input.py tests/agent/test_prompt_acceptance.py tests/agent/test_decision_requirements.py tests/agent/test_decision_inputs.py -q -p no:cacheprovider --tb=short` | 129 passed，8.54 秒 |
| `python -B -m pytest tests/agent tests/test_agent_decision_model.py tests/test_openai_compatible_model.py -q -rs -p no:cacheprovider --tb=short` | 1942 passed、1 skipped、7 warnings，47.34 秒 |
| `python -B -m pytest tests/test_agent_anti_hallucination_fallbacks.py tests/test_agent_platform_health_check.py tests/agent/test_real_acceptance_checks.py -q -p no:cacheprovider --tb=short` | 53 passed、7 warnings，12.38 秒 |
| `python -B scripts/run_agent_acceptance.py --mode contract` | 34/34 passed |
| `python -m compileall -q src scripts` | exit 0 |
| `git diff --check` | exit 0 |

Skipped 为未启用的 harness shadow 性能测试；warnings 为既有 SWIG/FastAPI 弃用提示。
contract 的无效结构解析日志为预期拒绝。报告位于忽略目录
`outputs/agent_evaluation/agent_acceptance_report.json`，不提交运行产物。
决策响应使用明确的测试替身，科学性质来自真实 RDKit；没有把契约验收称为外部 LLM 实网验收。

## 文件

新增：

- `src/agent/tools/molecular_input.py`
- `tests/agent/test_explicit_molecular_input.py`
- `docs/superpowers/plans/2026-09-08-agent-explicit-smiles-input.md`
- 本文档。

修改：

- `src/agent/tools/property_calculator.py`
- `src/agent/tools/drug_likeness_assessment.py`
- `tests/agent/test_decision_requirements.py`
- `tests/agent/test_decision_inputs.py`
- `tests/agent/test_prompt_acceptance.py`

关键阅读：AGENTS.md、PROJECT_STANDARDS、已批准 model-driven-agent 设计、上一批交接，
BaseMolecularTool、activity_input、两种分析工具、decision_inputs/requirements 及相关测试。
未读取凭据、训练数据或其他用户资产。

## 下一步

准备 Supervisor/ChatHandler 的隔离接入测试：验证普通聊天走主模型、科研调用走逐轮决策、澄清恢复、终态输出及前端可追溯性。
在切换线上入口前再做真实主模型验收；凭据只从运行时环境或本机密钥管理器读取。
本轮没有实网 LLM 验收，没有调用生成/训练/对接服务，没有激活生产模型、重启网站、push 或合并 main。
