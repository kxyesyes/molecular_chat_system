# B1 科学对话与 C4a 对接资源的历史设计记录

2026 年 9 月的两个开发分支分别验证了科学证据如何跨轮使用，以及对接命令取消后何时可以释放输入。本文按原始提交和文件记录仍影响维护的设计决定。文中的复测数字来自当时的分支。当前可运行的验收用例见 [B1 科学对话矩阵](../runbooks/b1_acceptance.md) 和 [C4a 对接资源验收](../runbooks/c4a_retention_acceptance.md)。

## B1 工具、证据与恢复

以下六份来源位于 `codex/dynamic-bindings-b1` 的 `73aca6505d88a631f140d4d7c89a79107002ed00`。9 月 25 日的设计和计划另有早期版本，见 `88b00edf`（[#230](https://github.com/kxyesyes/molecular_chat_system/pull/230)）。

| 原始文件 | 保留的决定和验证意图 | 对应的实现或验收 |
|---|---|---|
| `docs/superpowers/specs/2026-09-25-dynamic-tool-bindings-design.md` | #230 收录了较早设计。此后第 12–14 节明确区分工具已注册、服务端已接线、本次请求获准三件事。能力投影分别读取三组事实，参数解析只接受对应 profile 的引用形式，检索结果区分有效空集和丢弃无效返回。 | [能力投影](../../src/web/ordinary_capabilities.py)、[引用参数](../../src/agent/contracts/decision_bindings.py)、[检索结果契约测试](../../tests/test_rag_retrieval_outcome.py) |
| `docs/superpowers/plans/2026-09-25-dynamic-tool-bindings.md` | #230 收录了较早计划。后续验证先用无工具调用的用例分别检查能力投影和引用语法，再用实际检索代码验证空集、重复命中和无效结果。确认数据与索引属于同一代被单列为下一阶段。 | [能力 profile 测试](../../tests/agent/test_decision_binding_profiles.py)、[RAG 源代际集成计划](../superpowers/plans/2026-09-26-rag-owned-generation-integration.md) |
| `docs/superpowers/specs/2026-09-26-b1-decision-execution-design.md` | 服务端固定请求要求和归属。每个工具动作绑定实际输入、所用证据及来源。活性预测所需的目标与有效来源分别核对。恢复时先在独立对象上校验原观察，再安装到会话。发布前重新检查引用的来源。 | [绑定解析器](../../src/agent/scientific_session/decision_bindings.py)、[发布验收](../../tests/agent/test_decision_binding_publication.py) |
| `docs/superpowers/plans/2026-09-26-b1-decision-execution.md` | 分段验证了模型选择动作、实际工具执行、证据入账和最终接受结果。观察准备失败时记录诊断，已用工具次数和验证耗时在恢复后继续计入。同一轮恢复前后，测试比较完整观察、警告与来源，并检查失败重试不会重复执行科学工具。 | [模型循环集成计划](../superpowers/plans/2026-09-27-b1-model-loop-integration.md)、[延续集成计划](../superpowers/plans/2026-09-27-b1-continuation-integration.md)、[延续回归](../../tests/agent/test_decision_binding_continuation.py) |
| `docs/superpowers/plans/2026-09-26-rag-owned-generation.md` | 加载时把解析出的行、索引、清单和嵌入配置绑定到同一代。查询回执标明本次实际使用的数据。并发替换、取消和关闭用例同时检查返回状态、实际发出的请求及原有磁盘数据，避免只断言“尚未就绪”就误判测试成功。 | [源代际实现](../../src/rag/service.py)、[代际一致性测试](../../tests/test_rag_owned_generation.py) |
| `docs/superpowers/plans/2026-09-26-rag-receipt-consumption.md` | 工具保留生产者签发的同一次检索回执。适配器在结果规范化前后核对回执覆盖的数据和状态。三类用例沿实际工具、适配器、证据账本和会话观察追踪有效命中、有效空集、含无效数据的部分结果。 | [回执契约](../../src/rag/receipt.py)、[工具和会话回归](../../tests/agent/test_rag_receipt_consumption.py) |

## 浏览器入口、诊断与摘要复用

本节的五份来源同属提交 `73aca6505d88a631f140d4d7c89a79107002ed00`。

| 原始文件 | 历史结论及适用范围 | 当前核对入口 |
|---|---|---|
| `docs/superpowers/specs/2026-09-26-b1-normal-web-design.md` | 原设计让同一 WebSocket 上的准入、模型请求、科学动作和清理共用时限、资源租约和归属。进入等待状态时要保留可恢复的会话，发送完成帧前要等相关工作实际结束。后来浏览器使用独立的 `medchat-web-v1` profile，由服务端编排步骤并发放步骤引用。 | [Web 绑定策略](../../src/agent/scientific_session/binding_policy.py)、[Web 运行时](../../src/web/decision_runtime.py) |
| `docs/superpowers/plans/2026-09-26-b1-normal-web.md` | 从应用挂载的 WebSocket 入口检查实际准入、断线、澄清、来源变更和清理。#229 保留了旧矩阵行号。原生工具调用和 JSON 两种模式的浏览器用例核对实际分子量、输入和来源。原四工具执行次序在隔离 B1 测试中核对。 | [矩阵及逐行说明](../runbooks/b1_acceptance.md)、[科学用例](../../tests/agent/test_b1_portable_science.py)、[Web 生命周期](../../tests/agent/test_web_decision_runtime_lifecycle.py) |
| `docs/handoff/2026-09-27-b1-task5-review-correction.md` | 原复核分清了用例导入错误和实际澄清失败。修正后的验证要求无效补充输入保留原等待记录和剩余预算，有效补充输入继续原请求。当时一次组合运行超时，后续两次 463 项离线复测通过。该次超时原因仍未确定。 | [延续回归](../../tests/agent/test_decision_binding_continuation.py)、[Pi Web 输入预算](../../tests/agent/test_pi_web_input_budget.py) |
| `docs/superpowers/plans/2026-09-28-b1-record-digest-reuse.md` | 每个 B1 解析器只复用已通过校验的原始记录字符串对应的摘要，上限为 128 项和 512 KiB UTF-8 键字节。每次读都重新解析并检查可变的归属、来源和工具版本，恢复后从空缓存开始。历史测量对六条已预热的性质记录做重复校验，八组样本中每组十二次校验的中位耗时减少约 7.27%。冷启动、整轮 Web 请求的耗时需分别测量。 | [B1 绑定解析器](../../src/agent/scientific_session/decision_bindings.py)、[摘要复用回归](../../tests/agent/test_b1_record_digest_reuse.py) |
| `docs/handoff/remaining-through-step8.md` | 此分支比 `9aeac3a2` 的同名文件多 3,330 行，记录了当时的准入、RAG 与反向寻靶来源、摘要测量及工具超时检查点。分支内完成的专项用例、移植后的矩阵行和真实服务验收各有独立记录。当前矩阵保留原行号及适用范围，主交接表继续保留原有时间线。 | [主交接表](remaining-through-step8.md)、[当前 B1 矩阵](../runbooks/b1_acceptance.md) |

## C4a 命令归属与输入租约

以下两份来源位于 `codex/docking-physical-settlement` 的 `e64683dbe9ac293f337200dc6182d7753dc51352`。

| 原始文件 | 保留的决定和验证意图 | 对应的实现或验收 |
|---|---|---|
| `docs/superpowers/plans/2026-09-28-docking-physical-settlement.md` | 命令创建较晚、根进程已退出或清理仍在进行时，后台工作者继续持有输入租约。只有确认命令及相关资源结束后才能清理本任务输出并释放租约。#227 单独验证执行工厂失败的释放顺序。#229 检查永久清理失败时仍占用的容量和输入资源。 | [命令生命周期](../../src/docking/adapters/base.py)、[输入租约回归](../../tests/task_runtime/test_docking_consent.py)、[C4a 验收](../runbooks/c4a_retention_acceptance.md) |
| `docs/superpowers/plans/2026-09-28-c4a-command-test-owner.md` | 外部测试进程独立确认子进程树结束，生产工作者持有资源的观察值与测试进程自己的清理结果分别记录。历史方案使用本机固定的子进程和文件路径。#229 将洁净退出、根进程已退但子进程仍存活、取消后才返回的进程创建等对照迁入仓库内的可移植测试。 | [测试运行器](../../scripts/run_c4a_acceptance.py)、[测试进程归属与回执回归](../../tests/test_c4a_portable_acceptance.py) |
