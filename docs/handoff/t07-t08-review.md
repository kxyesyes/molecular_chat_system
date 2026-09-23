> 历史集成报告：以下记录不是本次 P05 候选的验证结果。
> 本批以冻结 P04 为前置精确重建 P05；旧成绩不作为本候选通过的依据。
> 本轮精确身份和成绩见协调工作树 docs/handoff/p05-session-ownership-patch-review.md。

# T07/T08 集成后审查（2026-09-23）

范围：`codex/delegated-session-baseline` 隔离工作树；不改原始混杂工作树，
不推送、合并、部署或启用外部模型。T07 经独立只读审查；T08 的路由边界
已做本地代码与测试审查。

## 已证实并修复

1. 委派的非幂等工具超时后，Adapter 已返回 `tool_timeout`，但底层线程仍可
   继续运行。旧 trace 的失败 checkpoint 不被 `_compatible_checkpoint` 复用，
   导致再次执行同一工具。先用带真实线程等待的测试复现二次调用，再让
   Session 对非幂等工具的任何不兼容/失败旧 checkpoint 保守拒绝，
   结果标记 `uncertain_prior_execution`，不猜测已完成与否。
2. 委派 Legacy Adapter 在将字典交给兼容归一化前未检查矛盾状态。
   `success: true, status: failed` 可丢失原始失败语义。先以真实
   `Supervisor.run()` 与 SQLite checkpoint 红测复现，再在原始输出边界
   返回 `invalid_output`；没有把字典中的数值或格式化文本当作成功证据。
3. 含普通问候词的明确 EGFR/BuChE 结构检索曾被闲聊短路；先复现两项
   红测，再让普通闲聊判定尊重已识别靶点，保留纯问候无需科研工具的行为。

## 当时尚未解决、阻断提交/PR 的审查意见

2026-09-23 后续更新：下列 P1 运行归属及 P2 自定义 StateStore 问题已由
[匿名浏览器会话归属隔离](agent-anonymous-session-ownership.md) 实施并独立复审通过。
原始问题记录保留以便追踪；终态事件与状态更新的事务一致性仍未解决。
这不等于批准整体提交混杂补丁、合并或部署。

- **P1 运行归属**：`SupervisorAgent.run()` 的公开参数与 `_build_context()`
  没有可信的 `user_id` / `session_id` 来源；SQLite 的重认领仅按状态比较，
  idempotency key 查找也没有所有者约束。不能把客户端 metadata 当作
  身份证明。本批不擅自定义新鉴权来源、修改所有入口；在明确可信主体
  如何从 Web/API/后台任务传递后，应添加跨用户同 trace/key 红测并实施
  owner-bound CAS。此前不能宣称多用户重放隔离已完成。
- **P2 自定义 StateStore**：未实现 `claim_workflow_run` 的存储仍退化为
  非原子读后启动。SQLite 默认路径已经使用原子认领；其它实现需要
  原子接口或 fail-closed 的兼容决策与测试。
- 终态事件与 run 状态更新不是同一事务；若后者失败可能出现短暂或持久
  分叉。此顺序不是本批引入，仍需独立一致性设计。

## 验证

- 两项 T07 审查红测已复现并转绿；聚焦
  `test_delegated_session_lifecycle.py`、`test_delegated_session_parity.py`、
  `test_tool_adapters.py`：38 passed。
- T08 问候路由聚焦：168 passed（详见 target-identity-integration.md）。
- 完整组合回归：
  `python -B -m pytest tests/agent tests/test_activity_family_contract.py
  tests/test_target_search_fallback.py tests/test_phase2_phase3_routes.py
  tests/test_agent_anti_hallucination_fallbacks.py
  tests/test_agent_platform_health_check.py -q -p no:cacheprovider --tb=short -rs`
  → **3804 passed / 4 skipped / 7 warnings**，156.45 秒，退出码 0。
  跳过原因：两项 Windows 符号链接不可用、性能测试默认禁用、
  真实权威靶点在线检索未开启。
- `python -B scripts/run_agent_acceptance.py --mode contract --output
  outputs/agent_evaluation/t07_t08_review2_contract.json`：passed。
  RDKit 无效 SMILES 解析提示是预期负例。
- `python -m compileall -q src scripts`、`git diff --check`：通过。

测试中的科学工具输出是受控 fixture，不是外部科研真实性证明。
