# molecular_docking 最小类型契约设计

日期：2026-09-24。用户已同意单工具迁移方案，并明确确认本书面设计；进入 TDD 实施。

## 目标、基线与非目标

完成任务书 T10-A 的对接工具边界迁移，消除同一结构化请求在 Registry 输入形态不同时丢字段或被拒绝的问题；明确原始和规范化输出契约，不改变科学算法或执行后端。

基于 PR #50 合并后的 main `9baab4622cb88a2bbeb4beab616e54e4b478b171`。独立分支 `codex/docking-typed-contract-pr`；原始混杂工作树不动。该设计不代表 T09、Planner 整理或整个任务书已经完成。

- 只迁移 `molecular_docking`；四个 staged helper 的 `execute()` 继续引导/拒绝，不接入其 `run()`。
- 不改 HTTP、Planner、Supervisor 生产模式、模型、阈值或数据库，不启动 Vina/Temporal/OpenSandbox。
- 不将文件存在或合成测试 pose 当成实际 Vina 科学验收；本批是代码契约与安全回归。

## 已验证的现状

实际模块：`src/agent/tooling/factory.py`、`adapters.py`、`tools/molecular_docking.py`、`runtime/delegated_executor.py`、`specialists/agents.py`。

真实 Registry + MolecularDocking，服务边界注入明确不可用替身，并禁止网络/实际 docking，五输入矩阵如下：

| 输入 | 当前结果 |
|---|---|
| 直接结构字典 | 缺 query，被通用 schema 拒绝 |
| 一层 query 包装结构字典 | 正常进入领域工具，准确返回环境不可用 |
| query 文本 + 顶层结构字段 | 顶层结构字段丢失，只向工具传文本 |
| 原始文本 | Registry 拒绝，未进入领域工具 |
| query 包装文本 | 工具明确拒绝缺 receptor/ligand/box |

Registry 单独可接受缺 pose 的合成结果，但继续调用实际 `AgentResultValidator` 会拒绝并清除科学数据。不能据此声称正式聊天链路已绕过校验。

既有 `src/docking/schemas.py` 的 `DockingRequest` 是有默认 box 的内部作业结构，要求 ligand_path，不能直接当作支持 SMILES 且必须显式 box 的聊天输入。`DockingJobResult` 也不是当前工具完整观察 envelope。保持两者身份和调用者不变；新增的是传输视图，不是平行作业执行模型。

## 选择与职责

采用单工具专用 Adapter，继承既有 `LegacyPythonToolAdapter`；复用 `execute_tool_compat` 作为唯一结果规范化核心。只改输入 schema 不足以校验输出；一次迁移五个工具会引入未授权执行和兼容风险，均不采用。

新增 `src/agent/tooling/docking_contract.py` 放兼容输入视图、输出视图和薄 Adapter；工厂只为 canonical `molecular_docking` 选择它。现有别名、owner、side_effects、idempotent=False、一次尝试和 lazy readiness 保持不变。schema 版本为类元数据 `1`，不强行插入或改写领域输出。

## 输入边界

1. 支持原始字符串、`{query: string}`、一层 `{query: structured}`、直接 structured、`{query: string, ...structured}`。含结构字段时不能退化为文本；委派现有 query 包装继续兼容。
2. 同时存在嵌套结构 query 和外层结构字段属于歧义，返回 INVALID_INPUT，不猜优先级；不接受多层包装来绕过校验。
3. 可执行结构请求必须由调用者显式提供 receptor_path、center、size，以及 ligand_path 或 smiles。未提供 box 不得通过 DockingRequest 默认值补齐，不从靶点名称推测坐标。
4. 结构字段只接受符合当前接口的类型。center/size 保持三元列表或元组：允许有限 int/float 及旧工具已支持的有限数字字符串，size 为正；边界检查不改写原值，由现有工具执行原有数值转换。不把 bool、NaN/Infinity 或任意对象变成有效输入。现有合法配置和值原样传入，不用序列化投影丢弃 runtime_config 或附加字段，不添加自动路径修正/ligand prep。
5. 文本请求仍交由实际 MolecularDocking 给出缺参拒绝；非法结构在边界返回安全错误。不能在注册/schema 检查时加载服务、模型或探测环境。路径存在、分子有效性、依赖和执行错误继续由原有领域边界负责。
6. 不把浏览器内容提升为 `job_id/progress_callback/cancel_event` 的执行控制参数；现有直接工具调用中的关键字控制和同步/异步桥不变。

## 输出边界、科学验证与错误

- 同时覆盖 legacy dict 和 canonical ToolResult。兼容实际完整成功、部分观察、无结果失败、不可用、取消、拒绝及安全诊断；严格检查已提供的状态/字段类型，拒绝 success/error/status 矛盾。
- 在现有 compat 前检查可能被转换吞掉的畸形 envelope，规范化后再次验证；不让 `model_dump()` 替代完整观察，不把状态压成单一布尔值。
- 合法返回保留数值、job、pose、warnings、evidence、artifacts、quality、provenance 及状态，遵守已有脱敏和错误 raw_result 放置规则。未知扩展元数据不被臆测为科学声明。
- 对成功或 partial 中的科学对接声明，复用 `DockingResultValidator`：正整数 pose 数、有限数值能量、输入证据与存在的 pose 文件；OpenSandbox 保留 gvisor、镜像 hash、artifact hash、cleanup 成功和非 demo/fallback provenance 校验。不能用 success=True 或 HTTP 成功替代证据。
- 不要求当前工具不能提供的 confidence、参考 RMSD 或模型版本，不填占位值。成功但没有任何实际对接结果不能伪装成完成；任务提交、排队不属于本同步工具的科学成功。
- failed/partial 也不能夹带未经接受的能量或 pose 声明。检查已知科学位置及 compat 的 `error.details.raw_result` 载体，循环或过深链明确拒绝，不递归猜测任意元数据。错误输出仅使用固定安全消息和代码，不回显 Pydantic 输入、路径、凭据或堆栈。
- 科学校验拒绝时复用现有 `AgentResultValidator` 的 docking 清理路径，不重写能量或复制清理算法；已经清除的数据、artifacts/provenance 不得从 raw_result 恢复。有效非科学失败/取消/不可用观察则保持兼容状态和安全诊断，不因套用统一成功校验而被伪成功或无差别重分类。
- 保留调用者 raw_validator 的执行与异常语义，不吞掉外部守卫异常。Registry、Session、专科委派要有一致测试，不能仅测理想化手动 Adapter。

## 执行及资源不变量

不创建第二套 executor、重试、信号量、超时或关闭机制。只复用基类调用路径。每次请求最多一次领域执行，输出校验不能重新执行 Vina。超时不能标为取消已完成，也不能释放仍执行的并发槽；取消、callback、运行身份、资源关闭遵守原有实现。

## 验证与完成门禁

先用实际工厂/专科委派/领域工具添加 RED，再做最小实现。模型和执行服务仅在边界注入可控替身，临时文件明确为合成 fixture，不调用真实服务或权重。

测试矩阵：

- 五输入形式、字段保留、嵌套歧义、无 box、无 ligand、非法三元组、空/null/布尔/非有限值。
- 实际不可用领域服务与有效合成服务，成功/partial/失败/取消；已有规范化语义等价，输入和来源不丢。
- 原始及规范化畸形输出；缺 pose、能量类型错误、计数错误、demo/fallback 声明；错误快照不得隐藏科学数据。
- 本地与 OpenSandbox 已有证据验证，不能减弱 hash/cleanup/来源；无需构造真实沙盒。
- Registry 注册/别名/owner、专科委派包装、Planner structured metadata、普通缺参 docking 拒绝。
- 一次执行、可控超时、并发槽、取消和关闭；四 helper 行为与所有非 docking Adapter 保持不变。

本轮新 main 迁移前基线：七文件 **318 passed、1 skipped、7 warnings、9 subtests passed，15.19秒，exit0**。唯一 skip 为 Windows 不支持的 POSIX process-group；warnings 为既有 SWIG/FastAPI 弃用。使用 MedChat Python 3.10、临时配置/cwd/数据库、real/canary 关闭，复用 `docs/superpowers/plans/2026-09-24-rag-service-extraction.md` 的隔离 runner（仅替换当前工作树路径）。底层命令为 `python -B -m pytest <absolute paths> -q -p no:cacheprovider --tb=short -rs`，七路径：

```text
tests/test_docking_agent_architecture.py
tests/agent/test_domain_result_validators.py
tests/agent/test_tool_adapter_compat.py
tests/test_docking_configuration.py
tests/agent/test_task_planner.py
tests/agent/test_registration_consistency.py
tests/agent/test_specialist_agents.py
```

实现后必须重跑该基线、新测试、Agent/生命周期联合回归、离线 contract、源码编译和 diff/敏感材料检查；独立规格与质量审查、当前 head CI 通过后才可提请具体 PR 合并。不将上述迁移前通过或合成输出冒充修改后通过/真实科学验收。

## 写集与交付

预计生产写集只有新 `docking_contract.py` 与 `tooling/factory.py` 的 docking 选择；新增契约/实际集成测试。现有测试若使用不合规的合成成功桩，应修正 fixture 并保留原科学断言，不删测试、加 skip 或放宽门禁。若必须改领域校验器/服务/通用 Adapter，先记录证据并重新确认扩大的边界。

本设计不改运行配置。设计、实施计划、一次交接记录及对应单主题 PR 记录每次 RED/GREEN、失败、skip 与未完成项；临时探针和报告不提交。迁移失败可独立撤回本工具接线，不回滚其他工具或已合并功能。
