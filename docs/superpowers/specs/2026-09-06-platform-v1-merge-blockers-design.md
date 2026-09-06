# MedChat Platform V1 合并阻塞修复设计

## 背景与目标

平台 V1 基线 PR #2 在本机验证后创建，但 GitHub Actions 的干净 Linux 环境暴露出依赖配置缺口。合并前审查还复现了工具超时失效、验收 replay 提升失败状态、WebSocket 异步路由绕过主模型，以及 Temporal 与 OpenSandbox 恢复边界问题。

本轮目标是修复这些明确复现的问题，使 PR #2 在干净 CI 环境可验证，并让运行时在超时、切换后端和崩溃恢复时保持真实状态。修改直接追加到 `codex/platform-v1-baseline`，作为同一个基线 PR 的审查修订。

## 方案选择

采用“先解除确定性门禁，再修恢复边界”的两批 TDD 实施方式。

第一批修复 CI、验收与同步/异步边界。这些问题有短小、确定的失败用例，可快速恢复 PR 的可信门禁。第二批修复 Temporal 旧任务控制与 OpenSandbox 创建不确定状态；这两项涉及持久状态和远端权威，需要单独的恢复测试。

不引入新的 Agent 框架或任务系统。现有 WorkflowOrchestrator、ToolAdapter、Temporal backend 和 OpenSandbox 客户端仍是权威实现。

## CI 依赖与测试范围

新增明确的 CPU CI 依赖 profile，由根 `requirements.txt` 加上当前完整测试收集所需的 Temporal、Prometheus、CPU PyTorch 和 PyTorch Geometric 组成。CI 不直接拼接 OpenSandbox broker profile，因为它与根 profile 的 `httpx` 固定版本冲突；现有 broker 测试通过接口替身运行，不要求安装 OpenSandbox SDK。

建议版本沿用当前生产兼容线：

- `temporalio==1.30.0`
- `prometheus-client==0.26.0`
- `torch==2.4.0` 的 PyPI CPU wheel
- `torch-geometric==2.6.1`

CI 使用该 profile 运行完整 pytest collection。若某项确为可选集成，应在测试内使用明确的 opt-in marker，而不是让 import error 中断 collection。

Node 测试不再维护容易漏项的手写清单。工作流枚举仓库支持的 `tests/*.js` 文件并逐项执行，使新增回归测试自动进入门禁。凭据扫描和源码编译仍在测试之后执行。

## 工具截止时间

`ToolAdapter` 保留现有线程调用兼容性，但超时路径不再通过上下文管理器隐式执行 `shutdown(wait=True)`。达到截止时间时立即返回结构化 `TOOL_TIMEOUT`，取消尚未开始的 future，并以非等待方式关闭 executor。

Python 无法安全终止已经运行的线程，因此超时后的底层函数可能继续到自身返回。适配器不得对该次超时自动重试，也不得接收迟到结果。带文件或外部进程副作用的长任务继续通过现有 Temporal/OpenSandbox/Vina 进程边界获得真正取消能力。

测试使用可释放的受控阻塞工具，验证调用在截止时间附近返回、结果为 `TOOL_TIMEOUT`、调用次数为 1，并在断言后释放线程，避免测试进程残留。

## 异步路由仲裁

首页 WebSocket 不在活动事件循环中调用包含 `asyncio.run()` 的同步路由。ChatHandler 通过 `asyncio.to_thread` 执行同步仲裁，使同步模型和异步模型都在合法上下文运行，同时避免阻塞事件循环。

为防止并发请求互相覆盖模型，`SkillRouter` 不再把请求模型写入共享的 `hybrid_router.llm`。模型作为本次调用参数传入 HybridSkillRouter 和 `_llm_arbitrate`；未显式提供时才使用构造时配置的默认模型。

模型异常仍允许回退到规则评分，但路由决策必须记录回退原因，不能让“没有调用模型”和“模型成功但选择规则结果”无法区分。

## replay 与指标语义

Replay 只使用报告中已有的可审计字段，不调用真实工具。它重新检查：

- 原始状态是否已经失败；
- `actual_skill` 是否符合 `expected_skill`；
- `actual_tools` 是否包含有序的 `expected_tools` 且没有 forbidden tools；
- truth checks、anti-hallucination 与 provenance 是否足够。

原始失败不能被提升为 passed。缺少重验所需字段时返回 partial 或 failed，并列出原因。

稳定性和 replay 指标新增严格的 `passed_rate`、`partial_rate`、`failed_rate`、`skipped_rate`。旧 `pass_rate` 暂时保留为兼容字段，但其语义固定为非灾难性完成率，并增加 `completion_rate` 同义字段和说明；报告展示优先使用严格状态比例。`partial` 且 score 100 的案例不再被解释为全部通过，评分与状态同时呈现。

## Temporal 旧任务控制

新任务流量配置只影响提交选择，不能改变已持久化任务的执行权威。`get` 和 `cancel` 必须根据 `TaskRecord.backend` 路由。

当当前配置为 local、但记录属于 Temporal 时，运行时延迟创建仅用于控制面的 Temporal backend，然后查询或取消原 workflow。若 Temporal SDK、连接或控制面不可用，返回明确的 backend unavailable/cancel failure，不把请求交给 LocalTaskBackend，也不声称取消已送达。旧 Temporal 任务绝不在 local 重放。

测试先创建/注入 Temporal 记录，再以 local 新流量配置重建 runtime，验证取消发往 Temporal 替身、Local backend 未接收，并覆盖控制端不可用。

## OpenSandbox 创建不确定状态恢复

`PROVISIONING` 且 `sandbox_id=None` 不代表没有远端实例。恢复流程根据稳定 `job_id` 调用已有 `destroy_by_job_id` 进行核对和清理：

- 删除数大于零：记录 cleanup succeeded，再将中断任务置为 failed；
- 明确确认不存在：允许记录 cleanup succeeded；
- 远端不可用、超时或状态不明：记录 cleanup failed/pending，并保留可重试状态；
- 从未进入 provisioning 的纯排队记录不执行远端清理。

恢复测试在远端 create 接受与 `attach_sandbox` 之间注入崩溃，随后重启 service，验证按 job_id 清理。另覆盖远端不可用时不得写入 succeeded。

## 实施顺序

1. 为 CI profile 和 Node 测试枚举增加静态契约测试，再修改 workflow。
2. 为 ToolAdapter 截止时间增加失败测试并修复。
3. 为活动事件循环中的异步路由增加失败测试并修复。
4. 为 replay 原始失败、路由/工具偏差和严格指标增加失败测试并修复。
5. 为 local 配置下旧 Temporal 任务取消增加恢复测试并修复。
6. 为 OpenSandbox provisioning 崩溃窗口增加恢复测试并修复。
7. 运行聚焦测试、全部 Agent 测试、全量 Python、全部 Node、compileall 和 contract acceptance。
8. 显式暂存本轮文件，提交并推送 PR #2，等待 GitHub Actions 在新 head 上完成。

## 验收标准

- GitHub Actions 在 PR #2 新 head 上完成依赖安装、完整 pytest、compileall、全部 Node 测试和凭据扫描。
- ToolAdapter 超时在合理调度误差内返回，调用次数为 1，结果不会被迟到线程覆盖。
- WebSocket 活动事件循环内异步主模型被调用且路由结果被采用；模型失败回退可观察。
- replay 不会把任何原始 failed、skill mismatch 或 tool mismatch 提升为 passed。
- 报告同时给出 passed、partial、failed、skipped 的独立比例。
- local 新流量模式下取消旧 Temporal 任务仍发送到原 Temporal workflow。
- provisioning 崩溃恢复按 job_id 清理远端实例；状态不明时不记录 cleanup succeeded。
- 不读取、打印或提交真实 API key，不调用真实外部模型作为单元测试依据。

## 范围边界

本轮不迁移框架、不修改科学算法、不补 RG-MPNN 权重或反向寻靶数据、不运行生产流量晋级，也不合并 PR。真实科研验收中现有的依赖 partial 将在门禁修复后重新如实报告。
