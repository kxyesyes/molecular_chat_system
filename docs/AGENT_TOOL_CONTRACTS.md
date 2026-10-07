# Agent 工具契约与受限兼容入口

本清单覆盖 `src/agent/capabilities/catalog.py` 与 `src/agent/tooling/factory.py`，不是可用模型、数据库或科学精度的证明。构建注册表不代表工具运行成功；执行状态、领域校验与来源证据仍须逐次检查。

核对日期：2026-10-07。源码入口与模式见 [维护指南](AGENT_MAINTENANCE.md)，
当前基线和实际验证见 [latest handoff](handoff/latest.md)。本页不声明部署或科学验收通过。

## 能力工具

表中的路径相对于仓库根目录。输入/输出模型只验证接口，不替代工具本身的科学校验，也不意味着接受任意模型提供的路径、数字或 SMILES。

| 工具名 | 输入模型 | 输出模型 | 定义模块 |
|---|---|---|---|
| `property_calculator` | `AnalysisInput` | `PropertyOutput` | `src/agent/tooling/analysis_contract.py` |
| `drug_likeness_assessment` | `AnalysisInput` | `DrugLikenessOutput` | 同上 |
| `admet_predictor` | `ADMETInput` | `ADMETOutput` | 同上 |
| `activity_predictor` | `ActivityPredictInput` | `ActivityPredictOutput` | `src/agent/tooling/activity_contract.py` |
| `target_database_search` | `TargetSearchInput` | `TargetSearchOutput` | `src/agent/tooling/target_contract.py` |
| `reverse_target_predictor` | `ReverseTargetInput` | `ReverseTargetOutput` | 同上 |
| `llm_molecular_generator` | `GenerationInput` | `GenerationOutput` | `src/agent/tooling/generation_ranking_contract.py` |
| `candidate_ranker` | `RankingInput` | `RankingOutput` | 同上 |
| `lead_optimization_verifier` | `LeadOptimizationInput` | `LeadOptimizationOutput` | `src/agent/tooling/optimization_contract.py` |
| `molecular_docking` | `DockingInput` | `DockingOutput` | `src/agent/tooling/docking_contract.py` |
| `rag_search` | `RAGSearchInput` | `RAGSearchOutput` | `src/agent/tooling/rag_contract.py` |

`rag_database_search` 是 `rag_search` 的旧别名，二者指向同一个注册项，不是两次检索能力。`rxn_chemistry_agent` 在 `NON_WORKFLOW_TOOLS` 中明确排除；未知工具名不能被静默丢弃或自动获得权限。

## 四个受限兼容 helper

`src/agent/tools/docking_tools.py` 保留 `prepare_receptor`、`prepare_ligand`、`run_docking`、`get_docking_result`。它们的字符串 `execute()` 路径仅返回失败及结构化接口提示，不能执行真实对接服务；因此目前保留 `LegacyQueryInput` / 无独立输出模型这一兼容例外。

它们不属于 `CAPABILITY_CATALOG`，即使将这些名称放入请求的 allowed-tools 集合，当前模型决策目录也不能授权它们。其 `run()` / `run_from_smiles()` / `run_from_file()` 是受信任调用方使用的领域接口，不能据此推断模型可以直接调用。清单测试只验证 Agent 字符串路径的拒绝与服务零访问，不宣称覆盖所有领域接口的文件安全。

## 调用边界

- 注册表负责规范名称、别名、归属、schema、超时及调用生命周期；`available=null` 表示懒加载就绪状态未知，不等于真实工具可用。
- 模型决策权限还受 `src/agent/harness/decision_policy.py` 的受限集合、请求类型、开关、副作用与幂等策略控制。**能力注册不等于已向普通聊天入口开放动作。** 工具数量以 catalog/factory 和清单测试为准，不以历史迁移批次为准。
- 输入完整性、候选校验、活动模型状态、对接文件/能量证据及 RAG 来源等领域检查继续执行；类型通过不能单独支持科学结论。
- 输出适配不能丢弃错误、warnings、evidence、artifacts、quality 或 provenance；失败、partial、不可用与 demo/fallback 不能变成真实成功。
- 扩展字段保留不代表可信；下游只能消费通过现有领域校验、所有权检查与版本绑定的结果。不要从自然语言说明中补造科学输入或数值。
- 故意构造矛盾返回值以测试通用适配器的单元测试，应显式使用测试本地的 `LegacyPythonToolAdapter`，而非要求某个已具备领域契约的工厂工具接受无效数据。

## 结果与真实性检查

权威数据结构是 [contracts/result.py](../src/agent/contracts/result.py) 中的
`ToolResult`，状态定义在 [contracts/scientific.py](../src/agent/contracts/scientific.py)。
必须保留工具身份、success/message/data/formatted，以及 error、warnings、
evidence、artifacts、quality、status、provenance；不要另建同名但不兼容的 JSON 契约。
具体字段的类型/默认值以源码为准，状态枚举、质量标记和验收报告状态是不同层级。

| 观察/质量 | 科研验收含义 |
|---|---|
| passed/success | 仍需领域证据、模型身份和实际产物；只返回 success=true 不够 |
| partial | 保留已完成数据及缺失步骤，不表述为全部完成 |
| failed/unavailable/无效输入 | 保留结构化错误，不补造科学数值 |
| demo_mode/fallback 标记 | 不是另一种“真实成功”；规则/演示输出需明确范围，不冒充模型预测 |
| skipped | 验收未运行，不是工具成功状态或通过证明 |
| available=null | 懒加载可用性未知，不是执行观察或科学可用性证明 |

检查来源与下游消费：

- 描述符应来自 RDKit；ADMET 标明 backend/prediction method、不可用项，规则不称实验。
- 活性必须有真实模型身份和非 demo 证据；没有真实权重不能给模拟 pIC50。
- 生成必须绑定本地生成模型、有效/唯一数量和候选身份；筛选应消费这些同一候选。
- `lead_optimization_verifier` 比较 baseline/candidate 的匹配证据与约束；
  `supported/evaluated_not_improved/unverified` 是优化判断，不自动等于整个任务成功。
- 靶点/反向寻靶/RAG 保留数据库或相似度来源；低可信、无命中、服务不可用不能混淆。
- 对接必须有完整受控输入、真实数值能量、pose 文件与来源；沙盒路径还保留隔离/hash/cleanup 证据。
- provenance 和 artifact 应按现有脱敏/所有权/版本规则传递，报告优先用相对路径。

主模型只提出受控动作和解释证据，不制造工具数值；专用分子生成器不被主模型配置覆盖。
schema 通过只说明数据形状，不证明这些真实性条件已成立。

## 维护门禁

增加或改名工具时，同步核对 capability catalog、factory、受限决策集合和对应契约测试，不能通过新增例外规避科学校验。

```powershell
python -m pytest tests/agent/test_tool_contract_inventory.py tests/agent/test_tool_registry.py tests/agent/test_registration_consistency.py -q -p no:cacheprovider
```

科学依赖测试应在仓库规定的隔离环境中运行；普通 `pytest` 命令仅说明测试范围，不授权读取本机秘密、生产模型或用户数据。真实工具验收、最终普通 Web 入口验收与本注册清单是不同层次的证据。
