# PDE/BuChE 真实权重隔离验收入口设计

日期：2026-09-15。基线：main `57680aafd970e5a4bcf55bbafa4c47ff55b4226d`（PR #28）。
分支：`codex/family-real-acceptance-integration`。
用户已选择 **A：先集成验收代码，本批不读取真实权重、不执行真实模型验收**。
本设计已于2026-09-15获用户确认；不是已实现或已验证的能力声明。

## 1. 目标与完成边界

复用现有 pytest、家族预测服务、工具注册/证据、ModelDecisionLoop、ChatHandler 显式决策桥接与前端 DOM 测试，形成可显式启用的真实权重链路验收。

本批交付：离线回归通过的验收入口、明确的运行配置、结构化证据报告、独立审查及 PR。代码合并后，真实权重验收仍需另行授权并指定目录和模型组。

不包含：训练、生产模型激活、切换首页、修改公开 API/Agent 协议、读取原始训练 CSV、重算模型性能、调用外部主模型/Ollama/Vina、修改原始混杂工作树、处理独立的历史报告展示残差。

## 2. 当前实现依据与缺口

| 依据 | 当前事实及本批处理 |
|---|---|
| 历史 `9312bf5:tests/test_activity_family_real_acceptance.py` | 复制整个模型目录，按遍历顺序为每家族选最后一个 bundle；主要验证 Supervisor/旧 WebSocket。不能原样迁入新决策验收。 |
| `src/activity/model_registry.py` | 家族 bundle 包含封存的数据证据；推理无须重读 CSV。注册表事务会涉及锁文件，因此不能把“在源目录构造 registry”当严格只读。 |
| `src/activity/family_predictor.py` | 双阶段加载已固定哈希的权重，使用 restricted `weights_only=True`，重验 model card，拒绝 demo/fallback。保留这些校验。 |
| `src/activity/prediction_service.py` | API/工具共用家族服务；显式 target 不回落到全局模型。验收覆盖这一真实边界。 |
| `src/agent/contracts/task_requirements.py` | 当前 molecular_results 仅支持性质/类药性，不能把 pIC50 硬塞进去；本批不扩展 schema。 |
| `src/agent/harness/decision_loop.py`、`src/web/decision_chat.py` | 可通过显式工具权限接入逐轮模型决策与有界 WebSocket 输出。本批隔离调用，不改生产分派。 |
| `tests/test_activity_family_inference_integration.py` | 已有合成未训练 RGNN 权重的真实前向测试；可复用它建立工程回归，不能称为真实训练权重验收。 |
| `tests/activity_family_results_test.js` | 已有实际生产渲染函数的 DOM 测试。复用或显式导出测试 fixture，禁止依赖截取源代码字符串的脆弱拼接。 |

PR #28 已于本批前获授权 squash 合并，最新 CI run34950563640 全7项通过，合并树与已审查 head6ee6d1b 一致。本设计基于该合并树。

## 3. 方案选择

- **A（已选择）**：先集成默认关闭的验收入口，以合成资产和显式决策模型替身验证工程链路；随后再单独授权真实权重执行。
- B：本批同时读取指定目录、运行真实模型。需要额外的模型目录、精确 bundle ID 和实测授权，不在本批执行。

不新增另一套 Agent 或测试框架。测试中的模型替身只用于稳定产生决策；科学数值必须来自实际推理或显式标记的合成权重前向，不能由替身写入。

## 4. 显式启用与模型组选择

保留历史测试入口名称 `tests/test_activity_family_real_acceptance.py`，默认普通 pytest/CI 不访问真实模型目录。

拟定运行时输入：

- `MEDCHAT_RUN_FAMILY_REAL_ACCEPTANCE=1`：唯一显式实测开关。
- `MEDCHAT_FAMILY_ACCEPTANCE_MODELS_DIR`：用户指定的已有模型目录。
- `MEDCHAT_FAMILY_ACCEPTANCE_PDE_BUNDLE_ID`：唯一明确的 PDE 模型组。
- `MEDCHAT_FAMILY_ACCEPTANCE_BUCHE_BUNDLE_ID`：唯一明确的 BuChE 模型组。

未启用：真实验收项 skipped，不检查真实目录、注册表或权重是否存在。
已启用但缺配置、ID 无效、错家族或模型组不完整：failed，不能静默选默认/最新/最后一组，不能把配置错误当成功或可忽略 skip。

本批新增离线测试必须用临时合成配置替换测试所需环境，不继承本机实测开关来误触真实文件。执行时不需要 API key；本批也不读取 API key 环境变量。

## 5. 资产隔离与运行所有权

1. 仅在显式实测子进程内，对配置源做受限只读快照；不在源目录实例化 ActivityModelRegistry、select、register 或创建锁文件。
2. 拒绝目录穿越、符号链接/Windows reparse point 和越界的资产引用。读取前验证文件类型和大小边界；实现计划明确具体限额并用超限回归测试固定。
3. 每个家族子进程仅复制自身指定 bundle 所需的两份权重、model card 及封存注册记录，整批对应四份权重；不递归复制整个目录或原始数据集。不丢弃 bundle 的封存数据证据。
4. 在临时目录构造符合当前 schema 的最小注册表，只保留所选模型组及关联记录；使用现有 registry 在副本重新验证并选择家族模型组。所有选择、锁、缓存和 Agent 状态写入副本。
5. 快照复制前后核对源注册表、所选权重和 model card 的摘要；实测结束也核对这些源文件未变。变化或读取失败即报告失败，不“修复”源资产。该检查证明约定资产未变，不声称审计了整个磁盘。
6. 推理结果中的 bundle/model ID、权重/model-card 哈希必须匹配显式选择和快照，不能仅断言字段存在。仍由现有 pinned predictor 使用 restricted load；不得回退成宽松 pickle 加载。
7. 一个受监督子进程承载一次家族的 API/Agent/WebSocket/DOM 验收，默认墙钟上限沿用历史120秒，Node子调用30秒；超时必须失败并完成已拥有子进程的终止/回收。临时工作目录由父进程分配并拥有，子进程退出后才清理。若强制终止导致源摘要复核未完成，该检查记为未完成/失败，不能声称已证明源未变；父进程不因此自动读取源文件补验。不能留下后台执行或把超时视为成功。
8. 子进程不继承无关 API key 或生产模型环境配置；仅传操作系统必要环境和本次允许的配置，禁止 `.env` 加载。退出、取消、失败路径均清理临时目录及本次缓存。

CPU 作为确定性的工程一致性验收设备；本批不把 CPU 结果推广为 GPU 性能结论。

## 6. 验收链路与用例

每个指定家族至少覆盖以下路径，使用固定无隐私输入 CCO、CCN 和无效输入 CC(C)((：

| 路径 | 验收要求 |
|---|---|
| 共享家族预测器 | 产生基准结构化行，保留模型组、双阶段模型证据、分类概率、类别、pIC50、warnings/errors/status。 |
| 实际 FastAPI 单条/批量接口 | 显式 PDE5A/PDE 与 BuChE/BChE 别名解析到对应指定家族组；批量顺序、失败行、重复输入按当前契约保留。 |
| 新 ModelDecisionLoop + ActivityPredictorTool | 使用显式 scripted decision model，授权仅 activity_predictor；证明实际工具消费本次 target/SMILES，并将证据作为下一轮观察传回。不得以 Supervisor 的旧路由通过替代这一项。 |
| ChatHandler 显式决策桥接 + 测试 ASGI WebSocket | 工具结果、trace、事件、warnings、终态被真实序列化；终结消息次数正确，结果和基准一致，不发送给自然语言润色模型。测试应用只在进程内构造。 |
| 现有活性结果 renderer | 将实际接口结构化结果交给生产渲染函数；显示值、类别和返回来源一致，partial/failed/null 不变成零；恶意字段保持惰性文本。DOM 测试不等于真实浏览器验收。 |

API、工具与桥接是调用同一科学服务的不同入口，不假设“HTTP API 自动驱动 Agent”。在各入口之间比较同一选择、同一输入的结果，而不是伪造一条并不存在的调用链。

可保留旧 Supervisor 最小兼容回归，但它不能算作新决策层的替代验收，也不恢复已废弃的 skill 行为。

### 科学与一致性断言

- 数值必须有限；概率在[0,1]。未格式化 CPU 浮点结果以绝对误差≤1e-6、相对误差≤1e-6比对；ID、摘要、状态、类别、输入顺序精确相同。展示层按现有四位小数等格式化规则比对，不要求 HTML 文本等于完整原始浮点。
- 保留当前 `label_threshold=5.0` 和 `probability_threshold=0.5`；不改训练、阈值或双阶段输出语义。分类/回归不一致时按现有契约保留原值及 warning，不能为通过验收篡改输出。
- 真实模式必须验证 `demo_mode=false`、`fallback_used=false`，且两份模型证据身份/哈希与副本一致。只有旗标或文件存在不够。
- 无效 SMILES、未知靶点、缺权重、错误家族、损坏摘要或缺模型阶段必须失败/partial，并保留错误、可用阶段结果和 null；不得生成替代分子或模拟 pIC50。
- 跨目标续接/输入混淆、越权工具、空结果被标成功均要被离线负例捕获。任务义务由现有 evidence gate 加验收层独立断言共同验证，不改生产 TaskRequirements schema。
- 这一小批次证明工程调用一致性和权重溯源，不证明药效、泛化能力或新实验结论；不重算或引用训练集/测试集性能作为本轮结果。

## 7. 报告与默认测试分层

复用现有脱敏及非覆盖报告路径机制，不另建评测服务。
显式实测报告输出到 `outputs/agent_evaluation/` 下新建 JSON；不提交报告、权重、源快照或测试输入资产。

报告至少包含：

- `status`（passed/partial/failed/skipped）、执行模式（synthetic_fixture/trained_weights）和 `decision_model_kind=scripted`；
- 每个家族、入口和案例的 expected/actual bundle/model ID、权重及model-card摘要、实际工具、trace、事件、耗时；
- 各一致性/反伪造检查、工具原状态、公开错误码、warnings、可用阶段与未可用阶段；
- 源资产前后未变、临时状态清理和子进程退出状态；
- 未执行外部主模型和未改变生产选择的范围说明。

使用逻辑文件标识和摘要，不输出绝对机器路径、原始训练内容、环境变量全集或原始供应商/库异常。报告只保留有界结构化数据。

“无效输入如实被拒绝”的验收可以 passed，但对应科学结果仍必须是 failed/rejected；两者分别记录。未启用实测的 skip 不能被总报告包装为真实模型已通过。

默认 CI：合成注册表/权重的真实前向、明确的决策替身、真实 RDKit、实际 ASGI 和 Node DOM；明确标注工程测试。另有真实权重测试默认 skip。缺依赖造成工程回归失败必须如实报告，不能把绿色 skip 当覆盖。

## 8. 预期改动范围与验证

预期新增 `tests/test_activity_family_real_acceptance.py` 和小型 `tests/family_real_acceptance_support.py`；离线 helper/异常路径单测放在相邻测试文件。必要时导出或提取现有 `tests/activity_family_results_test.js` 的 DOM fixture，保留全部既有断言。更新交接/历史集成台账，将 PR #28 标记为已合并。

默认不改 `src/`。若出现确实阻塞验收的生产缺陷，先建立 RED 证据并说明最小修复范围；涉及公开契约、生产接线或训练行为的扩展须另行确认。

实施采用 TDD，使用具有 RDKit/PyTorch/PyG/LangGraph 的 MedChat Conda Python，不能用缺依赖的默认解释器替代。每批先独立规格审查再质量审查，最终运行：

- 家族模型/API/工具/决策桥接及新增离线验收聚焦回归；
- Agent/model/fallback 联合回归、10个现有Node脚本与相关语法检查；
- `compileall -q src scripts`、现有 `run_agent_acceptance.py --mode contract`；
- 独立分支 draft PR 最新 Linux CI；合并需用户明确指定该 PR 授权。

本阶段不执行带真实权重配置的 pytest；未来目录和 bundle ID 由用户明确指定，不扫描猜测。

## 9. 设计自检与阶段状态

- [x] 已读取当前基线、历史验收源、相关预测/注册/决策/前端测试代码，确认独立脏工作树保护。
- [x] 方案A/B已展示；用户选择A，明确代码集成与真实运行分开。
- [x] 不涉及新视觉设计，无需视觉伴侣。
- [x] 已区分真实前向、真实训练权重、外部决策模型、DOM和真实浏览器，避免验收结论混用。
- [x] 自检范围、选择确定性、清理、失败语义、报告脱敏与不改变生产选择的约束。
- [x] 用户审阅本设计文件并确认方案A。
- [x] 设计确认后编写逐步实施计划，再实施和评审；本地工程验收与独立双审完成，发布/CI进度见[交接](../../handoff/family-real-acceptance-integration.md)。真实训练权重仍未运行。
