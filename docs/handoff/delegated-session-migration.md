> 历史集成报告：以下记录来自既有集成工作树，不是本次 P03 候选的验证结果。
> 本候选仅迁入 T07；文中 T08/P05 已完成描述不表示这些代码已在本候选中。
> 本轮精确身份和成绩见协调工作树 docs/handoff/t07-patch-review.md。

# T07 委派执行复用 WorkflowRunSession（2026-09-23）

## 范围

基于 `codex/delegated-session-baseline` 的 T02/T04 组合基线实施 T07。
`SupervisorAgent.run()` 保留规划、预检查和 Harness 入口；
`_DelegatedWorkflowExecutor` 适配为 `WorkflowExecutor` 的子类，
委派执行通过现有 `PreparedWorkflow` 和 `WorkflowRunSession` 完成。
已删除 Supervisor 中独立维护的 `_run_delegated()` 执行循环与
`_persist_delegated_result()`。领域 Specialist 仅在单步调用边界承担授权和派发；
输入绑定、语义前置条件、原始结果校验、候选对齐、证据、检查点、事件和终态均由
共享 Session 处理。`execute()`、`plan()`、`run()` 的公开职责保留。

本批直接修改 `src/agent/supervisor.py`、`src/agent/orchestrators/workflow.py`、
`src/agent/runtime/run_session.py`、`src/agent/harness/canary.py`、
`src/agent/persistence/sqlite_store.py`；新增
`src/agent/runtime/delegated_executor.py`、
`tests/agent/test_delegated_session_lifecycle.py`、
`tests/agent/test_delegated_session_parity.py`。还更新本报告与交接首页。
同一工作树里 T02/T04 的迁入文件仍未提交，详见
[前置集成报告](delegated-session-baseline.md)。原始混杂工作树未修改。

## 行为与边界

- 每次请求通过 `WorkflowExecutor.prepare()` 保留 PlanCompiler 和预检查；
  PreparedWorkflow 的快照、一次性消费与 LangGraph Session 接口沿用原实现。
- `SpecialistDispatch` 在工具调用前核对归属、当前注册表和领域工具权限，
  且在读取旧检查点前再次核对，避免撤销权限后绕过授权。工具仍通过现有
  Adapter 执行，其超时、并发槽位和 in-flight 标志未被新线程池替换。
- Session 读取 Adapter 的工具版本与适配版本来判定检查点兼容性；损坏的
  JSON 会产生 `checkpoint_deserialization_failed`。幂等工具可重算；
  非幂等工具返回人工核查错误，并将运行标记为 rejected；后续使用同一 trace
  也不会盲目重复执行。
- 已取消、已拒绝或仍为 running 的旧 trace 不重启执行、不覆盖原运行状态。
  SQLite 存储在 preflight 后以事务认领 trace；同一 trace 的并发请求（同一存储实例
  或两个指向同一数据库的实例）只有一个可执行。其他自定义 AgentStateStore 若未提供
  `claim_workflow_run`，仍沿用读后判断，跨进程原子性待其适配实现。
- 委派列表作为公开信封适配，在终态事件发出前加入结果元数据；格式化响应不再
  修改该元数据。status、warnings、evidence、artifacts、provenance 原样保留。
- 委派路径保持既有 canary cohort 边界：即使 Harness 支持 prepare，当前仍使用
  legacy 后端；shadow 保持只读模拟，不改变运行配置。单独切换委派 canary
  需要后续有界验收。

## TDD 与实际验证

在新实现前，`test_delegated_session_lifecycle.py` 的两项会因未进入共享
Session 与缺少 `prepare()` 而失败。随后测试复现了版本/权限改变时误用检查点、
损坏检查点异常、非幂等重复执行、旧 trace 状态被覆盖、终态事件缺委派列表；
对应最小修复后聚焦回归通过。`test_delegated_session_parity.py` 的首次运行
与生产文件修改并发，因此不作为旧实现基线证据。

- `python -B -m pytest tests/agent/test_delegated_session_lifecycle.py tests/agent/test_delegated_session_parity.py tests/agent/test_entrypoint_outcome_parity.py tests/agent/test_workflow_resume.py tests/agent/test_supervisor_harness_integration.py -q -p no:cacheprovider --tb=short`
  → 119 passed（非幂等终态和原子认领加固后）。
- `python -B -m pytest tests/agent tests/test_phase2_phase3_routes.py tests/test_agent_anti_hallucination_fallbacks.py tests/test_agent_platform_health_check.py -q -p no:cacheprovider --tb=short -rs`
  → 最终结果：3427 passed / 2 skipped / 7 warnings，152.46 秒，退出码 0。
  两项跳过原因分别为 Windows 目录符号链接不可用、性能测试默认禁用；
  warnings 为 SWIG 与 FastAPI `on_event` 弃用提示。
- `python -B scripts/run_agent_acceptance.py --mode contract --output outputs/agent_evaluation/t07_delegated_session_contract.json`
  → passed，34/34。invalid SMILES 的 RDKit 解析错误是负例预期输出。
- `python -m compileall -q src scripts`：通过。
- `node tests/home_agent_task_panel_test.js`：通过。
- `node tests/frontend_safe_render_test.js`：通过。
- `git diff --check`：通过。

新测试的工具产物均为 synthetic fixture；契约测试不证明 RDKit、Vina、
RG-MPNN 或外部主模型在真实科研场景的可用性。完整组合回归中两个已知 skip
分别是 Windows 目录符号链接不可用和性能测试默认禁用。

## 待完成与审查重点

### 后续复审：认领时机收紧（2026-09-23）

复审发现 `prepare()` 已写入 `running`，但调用方可能在准备后放弃执行，
导致无工具调用的悬置 run。新增回归先确认这一行为，再将 SQLite 原子认领
推迟到共享 Session 的 `start()`：准备和创建 Session 均为只读，真正开始时才
检查原 run 状态并原子认领。已占用 trace 在 legacy 和直接 LangGraph 入口
均返回结构化失败，不执行工具；无原子认领能力的自定义存储仍沿用原先
的读后判断边界。聚焦回归 `56 passed`。组合回归命令：
`python -B -m pytest tests/agent tests/test_phase2_phase3_routes.py
tests/test_agent_anti_hallucination_fallbacks.py
tests/test_agent_platform_health_check.py -q -p no:cacheprovider --tb=short -rs`
结果为 **3429 passed / 2 skipped / 7 warnings**，269.55 秒，退出码 0。
两项 skip 仍是 Windows 符号链接不可用与性能测试默认禁用。
`--mode contract` 再次 passed，`compileall` 与 `git diff --check` 通过。

本次复审仅修 T07 边界，未迁入独立 T08 靶点识别补丁。

独立只读审查尝试因子代理额度限制未执行完成，不得记为审查通过。
在提交或 PR 前需复审委派授权、SQLite 原子认领、其他 StateStore 的认领接口、
恢复语义、终态事件与 Harness cohort。
本批未提交、推送、合并、部署或调用真实外部模型。
