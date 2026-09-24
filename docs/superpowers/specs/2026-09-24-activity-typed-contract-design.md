# T10-A 活性预测 Adapter 最小类型契约设计

日期：2026-09-24。状态：方案和本文已获用户确认，进入实施与 TDD；尚未发布。

## 1. 目标与批准边界

用户批准：兼容现有字符串/结构化输入，复用既有科学校验，完整保留成功、部分成功、失败及来源信息；不改阈值、不训练或启用模型，也不混入 docking 或 Planner 重构。

本批仅将 Registry 中 `activity_predictor` 的 `LegacyQueryInput(query: Any)` / `output_schema=None` 迁移为明确契约。类型校验不代表模型真实可用、科研准确性或真实权重验收通过。

独立分支：`codex/activity-typed-contract-pr`，基线为 PR #49 squash 提交 `4476568b7da9aed7338118206badbc09cc410468`。原项目工作树和已合并 RAG 分支保持不变。

## 2. 已核实的现状

- `src/agent/tooling/factory.py`：仅 RAG 已有专用输入/输出契约；活性工具仍使用通用适配器。
- `src/agent/tools/activity_input.py::parse_activity_input`：已有整段 SMILES、靶点歧义及结构化输入校验；接受字符串，或含 `query`、`smiles`、`target` 的 Mapping。结构化 SMILES 接受字符串、列表、元组，保留顺序和重复项。文本上限 65536，单个结构上限 8192，显式结构列表 1–100 项。
- `src/agent/runtime/delegated_executor.py::SpecialistDispatch`：将实际输入包装为 `{"query": deepcopy(input_data)}`。现有这条链路可以传递嵌套结构化输入；不能把通用 schema 的存在描述成已经证实的下游数据丢失。
- `src/agent/tools/activity_predictor_tool.py`：无靶点时调用既有单模型分支；有靶点时调用家族服务，核对输入/结果数量、顺序、靶点及家族，复用 `summarize_predictions` 和 `ActivityResultValidator`。家族失败不能回退到其他模型。
- `src/agent/validators/domain_validators.py::ActivityResultValidator`：家族行已有阈值、数值、分类/回归一致性、阶段状态、双模型哈希和 demo/fallback 检查。不得再复制一套同义规则。
- `src/activity/predictor.py`：单模型实际输出为 `task_type/endpoint/units` 配合 `value` 或 `probability`，失败行含 `error`；不是统一的 `pIC50` 字段。已有 `_validate_prediction_metadata` 可校验模型元数据，不能为了契约重新加载模型。
- `execute_tool_compat` 已负责旧字典到 `ToolResult` 的转换；旧失败字典的数据可能保存在错误详情的 `raw_result`，不是所有失败都在顶层 `data`。迁移必须保留现有位置，不能以“规范化”为由丢失这些信息。

## 3. 方案选择

1. **采用：活性专用边界 Adapter + 非投影校验视图。** 与已合并 RAG 的模式一致，保留原工具和领域服务。只校验、不用 schema 导出的投影替换结果。
2. 不采用仅添加类型注解：不能在运行时拦截畸形返回值。
3. 不采用重写预测服务或通用工具框架：会扩大兼容、模型加载和执行生命周期风险，不属于本批。

## 4. 输入契约与数据流

新增 `src/agent/tooling/activity_contract.py`，由工厂仅为 `activity_predictor` 选择专用 Adapter。外部适配器接受以下形式，最终交给原工具一次执行：

| 调用形式 | 边界行为 |
|---|---|
| 原始字符串 | 保留原文传给工具 |
| `{"query": "预测文本"}` | 解包为原文，保持当前 Registry 行为 |
| `{"query": {"query": "预测文本", "smiles": ["CCO"], "target": "PDE5A"}}` | 解包一层，保留当前委派协议 |
| `{"query": "预测文本", "smiles": ["CCO"], "target": "PDE5A"}` | 作为完整结构化输入传给工具，不静默丢弃顶层科学字段 |
| `{"smiles": "CCO", "target": "PDE5A"}` | 保留领域工具已支持的结构化调用，未提供 query 时使用其既有空字符串语义 |

规则：

- 不把数字、布尔值、bytes、对象或无效列表强制转换成文本；显式 `None` 不当作字段缺省。模型实例必须重新验证，不能通过 `model_construct` 绕过校验。
- `query` 的嵌套兼容仅一层。外层科学字段与嵌套 query 同时出现时返回明确 `INVALID_INPUT`，不猜测哪一层优先。
- 保留交给领域工具的结构化对象中的未知附加字段，不赋予执行含义；不能因模型序列化把现有字段过滤掉。委派外层仅用作 query 包装，不把外层附加字段合并进内层科学参数。原始文本、目标标识、结构顺序及重复项保持不变。
- 只检查传输形状；化学有效性、靶点解析、现有限额继续由 `parse_activity_input` 执行。不可另写正则提取、截断或自动修复 SMILES。
- 未指定 target 不补默认家族；用户指定不支持或冲突靶点仍失败，不自动切换模型。
- 不预先调用预测服务，不探测权重，不在输入校验中产生新的模型缓存或全局实例。

## 5. 输出契约

采用带版本 `1` 的验证视图，覆盖原始旧字典及规范 `ToolResult`。版本为 schema 类元信息，不向科学记录插入版本字段。原始输出在 `execute_tool_compat` 前校验，规范输出在返回调用方前校验；不替换原结果为 `model_dump()`。

### 5.1 共用外壳

- `success` 必须是真实布尔值，status 必须合法；工具身份必须为当前规范工具名。
- 数据列表中的记录必须是真实字典，不能用任意对象或 schema 实例冒充传输记录。
- 对 message、formatted、warnings、evidence、artifacts、quality、provenance、error 校验实际提供字段的形状，复用既有契约类型，不硬性要求每次失败都有全量来源或 error 对象。
- 保留现有规范化会保留的未知扩展字段、原数值、完整错误详情、来源、警告及 artifact；不得凭空补 confidence、默认分数、模型路径或哈希。不承诺新增旧转换器从未暴露的顶层字段；以迁移前后合法结果的深度比较证明无额外丢失，原有脱敏照常执行。
- 畸形输出返回固定安全的 `INVALID_OUTPUT`，不在错误详情中反射原输入、整份预测、凭据或 Pydantic 错误中的原值。

### 5.2 家族预测

- 复用 `ActivityResultValidator` 检查每个完整/部分/失败记录；复用 `summarize_predictions` 检查家族批次汇总状态。不复制阈值、阶段一致性或哈希验证算法。
- 双模型执行成功但分类/回归矛盾时，保留两项真实观测，保持 partial 和复核提示；不得重算、抹掉回归结果或提升为成功。
- 分类完成但回归失败时，保留分类和阶段错误；全空失败行保持失败，不强制补齐未产生的双模型输出。
- 家族工具现有输入/输出对齐检查继续有效，不把它移到第二个执行路径。
- 记录内 `warnings` 的历史宽容行为与规范外壳的 `warnings: list[str]` 区分：现有测试允许行内异常 warning 容器原样保留，服务汇总只收集有效文本。本批不能把该兼容行为默默变为整批失败。

### 5.3 无靶点单模型预测

- 按实际 `classification` / `regression` 记录校验，不把 endpoint 自动改写为 pIC50；有限数值不接受 bool、NaN、Infinity，分类概率须位于 [0, 1]。
- 成功行保留实际 `model_provenance`；复用现有模型元数据校验，并要求明确非 demo，不构造虚假模型证据。现有旧 `activity_score`/`pic50` 声明继续交由既有领域校验，不放宽科学安全门。
- 失败行保留 smiles 和错误，不要求本来不存在的成功指标。混合成功/失败批次保留当前旧分支的汇总约定，不借此变更对外状态算法。
- 合法成功批次必须包含成功观测，不能只用空列表或全部失败行声称成功。缺值/空列表的真实失败保持原错误；不添加不存在的预测。
- 工具直接调用接口保持不变；Registry 对不满足实际生产输出契约的伪成功测试桩应补齐合成来源或断言拒绝，不改变真实科学结果。

### 5.4 状态和规范化边界

规范化仍只调用 `execute_tool_compat`。合法部分结果和失败证据保存在目前约定的位置：规范 ToolResult 保留顶层数据；旧失败字典按现有转换保留 raw_result 错误详情，既有 partial 顶层保留行为不变。

输入缺失、模型不可用、超时、容量不足和取消均保留原错误类别/状态，不包装成成功。对于形状合法的失败不额外要求预测值或来源。对于含畸形科学声明的 partial/失败，先校验并拒绝，不能利用失败转换掩盖问题。

实施审查补充：家族标识不豁免同一行中明确提供的单模型预测声明。已知科学证据载体 `evidence[].prediction` 和 `error.details.raw_result` 同样校验，不递归解释任意扩展元数据。raw_result 链使用循环检测及最多16个嵌入快照的有界检查；超限明确拒绝，不截断后当成功。

## 6. 执行、兼容和资源

- 保持同一个 worker、并发槽、deadline、重试策略与 close 机制；每次合法调用只执行一次底层工具。
- 与调用方提供的 raw_validator 组合执行，不能绕过现有上游校验；其错误语义与私有契约错误分开，不吞异常冒充成功。
- 工厂只切换当前活性工具，不修改 RAG、docking、其他工具 owner/capability、授权或 canonical alias 规则；按当前 alias 表验证，不创造新的别名。
- 不改 ToolResult 公共字段、事件格式、数据库 schema、服务 API、前端卡片或生产执行模式。
- 不引入新的抽象基类、schema 包依赖或第二套执行器。若需公共适配器改动，先用复现测试证明不可在专用 Adapter 内完成，再单独确认范围。

## 7. 测试与验收

先 RED 再最小实现；只使用显式注入的合成预测、真实解析和既有离线 fixture，不能声称已测真实权重。

必测：

1. 所有输入形态、嵌套边界、None/bytes/bool、无效 SMILES、歧义靶点、数量/长度边界；无效输入不调用预测服务。
2. 单模型分类/回归、家族完整/部分/全失败、分类回归矛盾、混合批次、重复 SMILES 顺序；阈值保持 5.0 和 0.5。
3. 非有限数、伪 provenance、demo/fallback、status 矛盾、非字典记录；成功、partial、失败三种外壳均不可绕过校验。
4. warnings/evidence/artifacts/quality/error/provenance 及未知扩展字段不丢失；无 schema 投影和来源伪造。
5. 全部兼容输入经过真实 Registry + Activity 专家/委派边界，验证下游收到的 query、smiles、target 和顺序；调用一次，无无意模型回退。
6. 原始校验组合、超时/容量与资源释放、不可用工具、关闭路径；合法旧入口保持兼容，异常不泄露载荷。
7. 新契约聚焦测试、原四文件基线、全 Agent 与相关 Web/生命周期/反幻觉联合回归、离线 contract、内存编译、diff-check；CI 最低 Pydantic 2.5.0 兼容。

已执行的迁移前基线（不是本批实现结果）：

```text
MedChat Python 3.10；-B -m pytest -q -p no:cacheprovider --tb=short -rs
tests/agent/test_family_activity_tool.py
tests/agent/test_activity_input_boundaries.py
tests/test_activity_prediction_contract.py
tests/agent/test_registration_consistency.py
354 passed，0 skipped，7 warnings，14.26s，exit 0
```

该基线在 PR #49 分支运行，其 tree 与本批基线 main squash tree 相同；使用 `docs/superpowers/plans/2026-09-24-rag-service-extraction.md` 中已有隔离包装，替换 repo 工作树路径。包装清除非白名单环境、使用临时 cwd/配置/数据库、关闭 real/canary；原子测试的模型为注入桩。七项现有警告为 SWIG 与 FastAPI 生命周期弃用，未通过删断言或改生产关闭机制消除。

## 8. 写集和后续门禁

计划生产写集：新 `src/agent/tooling/activity_contract.py`，以及 `src/agent/tooling/factory.py` 的活性专用注册。测试写集：新 `tests/agent/test_activity_tool_contract.py`、必要的注册契约/委派集成断言。原领域校验和预测服务只复用，不搬迁、不改算法。

文档包括本设计、后续实施计划与完成时 handoff。当前仅提交本设计，不提交运行产物、数据、权重、配置或秘密。

本文自检后交用户书面审阅；确认后编写实施计划、TDD、独立规格审查与质量审查。发布前精确暂存、完整离线回归和最新 CI；合并仍按具体 PR 授权执行。不把本批完成当作整个 T10 或任务书完成，docking 契约、Planner 和其他未完成项独立处理。
