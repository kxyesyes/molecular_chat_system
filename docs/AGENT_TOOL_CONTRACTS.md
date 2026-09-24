# Agent 工具契约与受限兼容入口

本清单覆盖 `src/agent/capabilities/catalog.py` 与 `src/agent/tooling/factory.py`，不是可用模型、数据库或科学精度的证明。构建注册表不代表工具运行成功；执行状态、领域校验与来源证据仍须逐次检查。

> 集成检查点：4B / 4C 已分别通过 PR #69 / #73 合入。清单测试在 main `0295e9960b15c850bb212e434da66824181ef546` 上原样通过；本清单与测试自身仍须独立审查和 CI 后发布。这是接口与权限边界验收，不是实际科学工具可用性的证明。

## 能力工具

表中的路径相对于仓库根目录。输入/输出模型只验证接口，不替代工具本身的科学校验，也不意味着接受任意模型提供的路径、数字或 SMILES。

| 工具名 | 输入模型 | 输出模型 | 定义模块 |
|---|---|---|---|
| `property_calculator` | `AnalysisInput` | `PropertyOutput` | `src/agent/tooling/analysis_contract.py` |
| `drug_likeness_assessment` | `AnalysisInput` | `DrugLikenessOutput` | 同上 |
| `admet_predictor` | `AnalysisInput` | `ADMETOutput` | 同上 |
| `activity_predictor` | `ActivityPredictInput` | `ActivityPredictOutput` | `src/agent/tooling/activity_contract.py` |
| `target_database_search` | `TargetSearchInput` | `TargetSearchOutput` | `src/agent/tooling/target_contract.py` |
| `reverse_target_predictor` | `ReverseTargetInput` | `ReverseTargetOutput` | 同上 |
| `llm_molecular_generator` | `GenerationInput` | `GenerationOutput` | `src/agent/tooling/generation_ranking_contract.py` |
| `candidate_ranker` | `RankingInput` | `RankingOutput` | 同上 |
| `molecular_docking` | `DockingInput` | `DockingOutput` | `src/agent/tooling/docking_contract.py` |
| `rag_search` | `RAGSearchInput` | `RAGSearchOutput` | `src/agent/tooling/rag_contract.py` |

`rag_database_search` 是 `rag_search` 的旧别名，二者指向同一个注册项，不是两次检索能力。`rxn_chemistry_agent` 在 `NON_WORKFLOW_TOOLS` 中明确排除；未知工具名不能被静默丢弃或自动获得权限。

## 四个受限兼容 helper

`src/agent/tools/docking_tools.py` 保留 `prepare_receptor`、`prepare_ligand`、`run_docking`、`get_docking_result`。它们的字符串 `execute()` 路径仅返回失败及结构化接口提示，不能执行真实对接服务；因此目前保留 `LegacyQueryInput` / 无独立输出模型这一兼容例外。

它们不属于 `CAPABILITY_CATALOG`，即使将这些名称放入请求的 allowed-tools 集合，当前模型决策目录也不能授权它们。其 `run()` / `run_from_smiles()` / `run_from_file()` 是受信任调用方使用的领域接口，不能据此推断模型可以直接调用。清单测试只验证 Agent 字符串路径的拒绝与服务零访问，不宣称覆盖所有领域接口的文件安全。

## 调用边界

- 注册表负责规范名称、别名、归属、schema、超时及调用生命周期；`available=null` 表示懒加载就绪状态未知，不等于真实工具可用。
- 模型决策权限还受 `src/agent/harness/decision_policy.py` 的受限集合、请求类型、开关、副作用与幂等策略控制。**十个已注册能力不等于十个已向普通聊天入口开放的动作。**
- 输入完整性、候选校验、活动模型状态、对接文件/能量证据及 RAG 来源等领域检查继续执行；类型通过不能单独支持科学结论。
- 输出适配不能丢弃错误、warnings、evidence、artifacts、quality 或 provenance；失败、partial、不可用与 demo/fallback 不能变成真实成功。
- 扩展字段保留不代表可信；下游只能消费通过现有领域校验、所有权检查与版本绑定的结果。不要从自然语言说明中补造科学输入或数值。
- 故意构造矛盾返回值以测试通用适配器的单元测试，应显式使用测试本地的 `LegacyPythonToolAdapter`，而非要求某个已具备领域契约的工厂工具接受无效数据。

## 维护门禁

增加或改名工具时，同步核对 capability catalog、factory、受限决策集合和对应契约测试，不能通过新增例外规避科学校验。

```powershell
python -m pytest tests/agent/test_tool_contract_inventory.py tests/agent/test_tool_registry.py tests/agent/test_registration_consistency.py -q -p no:cacheprovider
```

科学依赖测试应在仓库规定的隔离环境中运行；普通 `pytest` 命令仅说明测试范围，不授权读取本机秘密、生产模型或用户数据。真实工具验收、最终普通 Web 入口验收与本注册清单是不同层次的证据。
