# T07 前置集成基线（2026-09-22）

## 范围与状态

分支 `codex/delegated-session-baseline`，基线 `d89a48b`。本批仅集成 T07
所依赖的 T02 结果一致性与 T04 工具注册补丁，并修复组合测试发现的问题。
**尚未完成 T07 的委派循环迁移。** 无提交、推送、合并、部署或真实模型调用。

从 `delegated-outcome-parity` 工作树迁入 14 个文件，从
`tool-registration-consistency` 迁入 14 个文件。迁入时逐文件比较，规范化
换行及末尾空白后的 28 个文件内容全部一致；之后仅在目标工作树修复组合问题。
两个来源工作树的 `docs/handoff/latest.md` 未复制，各自历史报告保留。
原始混杂工作树未改动，T01/T03/T05/T06/T08 其他批次不包含在本基线中。

迁入文件清单见两个历史报告；本批额外新增
`tests/agent/test_delegated_baseline_integration.py` 和本报告，更新交接首页。
组合修复位于 `src/agent/tooling/adapters.py`、`src/agent/supervisor.py`。

## 复现与最小修复

1. T04 健康状态记录访问 `result.status.value`，对 T02 非法状态案例抛异常，
   抢在结果校验前中断。现在只将合法枚举写入健康状态，否则记录
   `failed/invalid_output`；原始结果仍原样返回给校验器，不洗白非法结果。
2. 旧名称 RAG 工具在注册表可以解析，但聊天 execute 的请求工具视图没有
   规范名称，导致工具未调用。现在在请求局部副本补规范别名，仍执行能力过滤，
   不修改共享工具映射。显式规范名称优先，保留旧接口同时提供两个名称的兼容行为；
   注册表自身的重复/冲突限制不放宽。

新增 11 项测试覆盖 execute/run、规范/旧名称、成功/partial 八种组合，
验证调用次数、来源、warnings、artifacts、持久化状态；另外覆盖非法状态遥测、
RAG 禁用与共享映射隔离、规范名称优先。测试使用合成工具，不是实际科研验收。

## 验证过程

解释器为本机 MedChat Conda 环境 Python。以下命令均在本分支工作树执行。

- 初次组合聚焦回归：3 failed / 110 passed，复现上述两个问题。
- 初次完整组合回归：1 failed / 3392 passed / 2 skipped，失败为非法状态遥测。
- 中间版本增加了不必要的原始工具别名冲突限制；完整回归出现
  3 failed / 3401 passed / 2 skipped。已移除这项额外限制，恢复规范名称优先。
- 修正后的聚焦命令：
  `python -B -m pytest tests/agent/test_delegated_baseline_integration.py tests/agent/test_chat_handler_agent_events.py tests/agent/test_entrypoint_outcome_parity.py tests/agent/test_registration_consistency.py -q -p no:cacheprovider --tb=short -rs`
  → 192 passed，7 warnings。
- 完整组合命令：
  `python -B -m pytest tests/agent tests/test_phase2_phase3_routes.py tests/test_agent_anti_hallucination_fallbacks.py tests/test_agent_platform_health_check.py -q -p no:cacheprovider --tb=short -rs`
  → 2026-09-23 最终回归：3404 passed / 2 skipped / 7 warnings，151.33 秒，退出码 0。
  跳过原因为 Windows 目录符号链接不可用、性能测试默认禁用；warnings 为
  SWIG/FastAPI on_event 弃用提示，未将跳过计为通过。
- `python -B scripts/run_agent_acceptance.py --mode contract --output outputs/agent_evaluation/t07_baseline_contract.json`
  → passed，34/34；只验证契约，不证明真实模型或科学工具可用。
- `node tests/home_agent_task_panel_test.js`：通过。
- `node tests/frontend_safe_render_test.js`：通过。
- `python -m compileall -q src scripts`：通过。
- `git diff --check`：通过。

## 下一批：实际 T07 迁移边界

2026-09-23 续跑说明：上轮终端会话已不可恢复，无法取回最终退出结果，
因此重新执行完整组合回归，不将丢失的运行视为通过。独立只读审查因额度限制
未执行完成，不能记为审查通过；本批仍需后续独立审查。

当前 `_DelegatedWorkflowExecutor.execute()` 已调用 WorkflowExecutor.prepare，
但随后仍进入 Supervisor 的 `_run_delegated()` 独立循环。已有
WorkflowRunSession 负责输入绑定、checkpoint、结果校验、证据、持久化与事件，
应复用该生命周期，而不是再创建执行器。

先补跨入口测试，再在单次工具调用边界保留 specialist 授权/派发，交由 Session
统一结算。必须保留 PreparedWorkflow 的单次消费、PlanCompiler/preflight、原始
及标准化校验、证据快照、超时 in-flight 上限、失败持久化重试不重复调用工具。
验证成功/partial/异常/非法状态、候选顺序、缺输入、取消、拒绝、损坏 checkpoint、
结算重试及并发隔离；保留 execute/plan/run 公共角色与 legacy/shadow/canary 行为。
API 的 delegations 展示需显式兼容，不能随循环删除而静默丢失。

本报告不宣称 Session 迁移、全部历史补丁集成或真实外部模型验收完成。
