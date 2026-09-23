> 历史来源文档：以下设计、实施记录和测试数不是本轮 P05 候选的验收结果。
> 当前精确身份、失败及复测结果见协调工作树 docs/handoff/p05-session-ownership-patch-review.md。

# Agent 匿名浏览器会话归属隔离（2026-09-23）

状态：实现、独立规格/质量审查及最终串行回归完成。本批未发布。

工作树：`D:/MedChat/molecular_chat_system_worktrees/delegated-session-baseline`。
分支：`codex/delegated-session-baseline`，基准提交 `ad76179`。
本批代码未暂存、提交、推送、创建 PR、合并、部署或调用真实外部模型。
原始混杂工作树未修改；当前工作树原有 T02/T04/T07/T08 改动保留。

## 修复前后

| 边界 | 修复前 | 当前实现 |
|---|---|---|
| 浏览器主体 | Web 请求没有贯穿到 Agent 的可信归属 | 服务端签发匿名 Cookie，HTTP/WS scope 传递内部 session ID |
| 客户端 metadata | 不能作为可信身份使用，入口缺少统一清理 | 工作流提交剔除 user_id/session_id/owner_session_id；Supervisor 不从 metadata 认领运行 |
| trace / 幂等重试 | 未限定 owner 的查找、状态认领可能覆盖原归属 | 浏览器键按会话散列；SQLite 原子比较归属、query、skill、key 和状态，冲突拒绝且不执行工具 |
| 任务投影 | 可跨浏览器读取、列出、取消 Agent 任务 | Agent 任务创建时原子绑定 owner；非 owner、未知/缺 Cookie、旧 NULL owner 统一不可见 |
| HTTP/WS 安全边界 | 无匿名会话 Cookie 与 Origin 校验 | WS 必须有效 Cookie 和同源 Origin；HTTP 修改请求拒绝提供的异源/损坏 Origin；响应 private,no-store |
| 过期会话 | SQLite 锁等待可能使校验使用过时的时间 | 在获得写锁后取时间，过期令牌不能被续活 |
| 合法澄清续接 | 初版归属修复把更新后的 query 当作非法重放 | 保留既有 continuation 原子认领；认领后的恢复核对 owner/status/skill/key，不重复认领 |
| 非 Agent 事件 | 初版任务接线导致事件读取触发 Temporal 刷新 | 从持久投影判定类型，离线事件读取不启动/刷新后端 |
| 备用任务列表 | 过滤前截断可能漏掉可见任务，等时间排序不稳定 | 过滤后按 updated_at/task_id 降序；有界读取分页，排序后截断 |

不改变科学结果、校验器、provenance、warnings、artifacts、非幂等超时保护及
错误状态语义。测试中的模型/科学工具替身不构成真实科研模型验收。

## 持久化与兼容边界

- Cookie 为 `medchat_agent_session`，HttpOnly、SameSite=Lax、Path=/；HTTPS 加 Secure。
  明文 HTTP 仅允许 loopback 开发。闲置有效期 90 天，有效 HTTP 访问续期；
  WS 只在握手读取会话，不为每条消息写库。
- 会话 SQLite 只保存令牌摘要，不保存原始浏览器令牌。默认在仓库外用户配置目录，
  可用绝对路径 `MEDCHAT_AGENT_SESSION_DB` 覆盖；不自动迁移旧资产。
- 运行/任务跨工作树连续性仍要求 `AGENT_STATE_DB`、`MEDCHAT_TASK_DB_PATH`
  指向稳定持久路径。仅保持 Cookie 和会话库不能搬运另一工作树中的运行数据。
- 旧无归属 Agent 记录保留，不自动认领、删除或迁移。浏览器清理 Cookie、换资料目录
  或会话过期后不能凭 trace ID 找回旧任务。匿名会话不是账号认证。
- 高层执行拒绝不具备原子能力的自定义 StateStore。低层本地
  `start_run(exclusive=False)` 的原有覆盖契约保留；Web 不走该不受保护的兼容入口。
- 本地无归属调用仍保留幂等键重绑定和排他启动异常契约；浏览器的显式 trace 冲突则拒绝。
- 原计划键编码由 NUL 拼接调整为 JSON 数组 `[version, session_id, key]` 后 SHA256，
  避免分隔符歧义；只在 Supervisor 的客户端键入口散列一次。
- 生产 `TaskRuntime` 仍使用 `TaskStore`，没有修改其公开 API。无 store 的自定义适配器
  如需跨页读取，`list` 应显式接受 `offset`，按 `(updated_at, task_id)` 降序返回，
  以空页表示结束。最多扫描 10,000 条；重复页、异常大页、无法确认过滤完整性返回
  `503 Task runtime pagination unavailable`，不伪造完整列表。无 offset 的旧适配器
  保留有界单页兼容，完整返回页先排序再截断。该限制是明确的兼容边界。
- 非 Agent 任务的现有公开策略未升级为账号权限体系。本批不宣称整个管理面已安全。

## 测试和审查证据

解释器均为 `C:/Users/xkx52/.conda/envs/MedChat/python.exe`，下文简写 `$py`。
测试数据库使用临时目录；不读取或输出真实模型密钥。

1. 新增入口测试复现 HTTP/聊天未传 owner，修复后通过。完整接线测试
   `tests/agent/test_browser_session_integration.py` 使用真实 HTTP、TaskManager 线程、
   Supervisor 和 SQLite，工具为 fixture；验证同浏览器复用、不同浏览器同原始键隔离、
   任务读取/事件/取消/列表隔离。父任务 1 passed，独立复审亦通过。
2. Task2 第一轮红测 24 failed/44 passed，扩展红测 19 failed/85 passed；
   后续聚焦转绿。动态续接兼容回归在第一轮组合中复现并修正，没有删掉失败测试。
3. HTTP Origin 的 113 项红测、Cache-Control 的 6 项红测转绿；
   会话模块最终 166 passed/2 POSIX-only skipped，包含真实 SQLite 锁等待测试。
4. 任务事件/备用分页审查问题 5 项红测转绿；等时间和不同时间排序红测
   2 failed → 2 passed。排序补丁后独立规格复审 49 passed/3 fixture skips。
5. 独立审查：会话/入口规格与质量通过；运行归属规格复审 194 passed，
   独立质量审查 112 ownership tests passed；任务规格及独立质量审查通过。
   最终任务质量复审含线程接线用例：34 passed/3 fixture skips，无未解决本批审查意见。

### 主回归命令

```powershell
& $py -B -m pytest tests/agent tests/test_activity_family_contract.py tests/test_target_search_fallback.py tests/test_phase2_phase3_routes.py tests/test_agent_anti_hallucination_fallbacks.py tests/test_agent_platform_health_check.py -q -p no:cacheprovider --tb=short -rs
```

- 第一轮：33 failed / 3888 passed / 4 skipped。失败来自新检查与合法动态澄清续接、
  本地排他启动异常、无归属幂等重绑定的兼容冲突；已最小修复。
- 第二轮：5 failed / 3925 passed / 4 skipped，641.96 秒。
  五项均为未改动 `test_decision_continuation_store.py` 的 near_budget 长文本子进程
  超过 10 秒。停止并行大套件后，原阈值不变地单独复跑：5 passed/313 deselected，
  12.08 秒。不能从这次复跑推断所有环境下都稳定。
- 最终串行结果：**3930 passed / 4 skipped / 7 warnings**，176.12 秒，退出码 0。
  四项跳过为两项 Windows 符号链接权限、默认禁用的性能测试、未开启的真实在线靶点检索。
  warnings 为已有 SWIG/FastAPI 生命周期弃用提示。没有修改长文本测试或降低阈值。

```powershell
& $py -B -m pytest tests/test_agent_session.py tests/test_agent_session_entrypoints.py tests/test_agent_task_ownership.py tests/test_phase2_phase3_routes.py tests/test_temporal_docking_routes.py tests/test_task_runtime.py tests/task_runtime/test_task_store.py tests/test_user_llm_routes.py tests/test_admin_auth_routes.py -q -p no:cacheprovider --tb=short -rs
```

排序补丁前 576 passed/6 skipped/7 warnings；最终补丁后串行复核为
**578 passed / 6 skipped / 7 warnings**，43.57 秒，退出码 0。
跳过为两项 POSIX 目录权限、三项在无 runtime fixture 下不适用的跨 store 场景、
一项 Windows 符号链接权限；对应 runtime fixture 的场景已执行，不是整体跳过。

### 扩展回归失败保留

额外运行上述入口/任务集合加 `tests/task_runtime` 的广泛组合，得到
1626 passed / 24 skipped / 2 failed。两个失败位于未改动的
`test_temporal_process_runner.py`，等待 `child-started` 标记超时；
该模块独立复跑为 9 passed/42.38 秒，没有放宽超时或修改实现。
24 个跳过包含 Windows 符号链接/打开文件替换限制、POSIX-only 用例和三项 fixture
不适用场景。时序敏感性仍需后续独立排查，不把首次失败抹去。

```powershell
& $py -B scripts/run_agent_acceptance.py --mode contract --output outputs/agent_evaluation/agent_session_contract.json
& $py -m compileall -q src scripts
git diff --check
```

contract 34/34 passed；无效 SMILES 的 RDKit 解析提示为预期负例。
compileall、diff 检查通过。没有修改 JavaScript，未运行前端测试。

## 本批文件

以下路径相对于本工作树；不是对当前全部混杂 diff 的认领。

- 新增：`src/web/agent_session.py`、`src/web/agent_session_config.py`。
- Web 接线：`src/web/app.py`、`src/web/chat_handler.py`、
  `src/web/routes/agent_workflow_routes.py`。
- 运行归属：`src/agent/supervisor.py`、`src/agent/orchestrators/workflow.py`、
  `src/agent/runtime/delegated_executor.py`、`src/agent/runtime/run_session.py`、
  `src/agent/persistence/base.py`、`src/agent/persistence/sqlite_store.py`。
- 任务投影：`src/task_runtime/database.py`、`src/task_runtime/store.py`、
  `src/task_runtime/manager.py`、`src/task_runtime/routes.py`。
- 新增测试：`tests/test_agent_session.py`、`tests/test_agent_session_entrypoints.py`、
  `tests/test_agent_task_ownership.py`、`tests/agent/test_run_session_ownership.py`、
  `tests/agent/test_browser_session_integration.py`。
- 兼容 fixture：`tests/conftest.py`、`tests/test_phase2_phase3_routes.py`、
  `tests/test_user_llm_routes.py`、`tests/agent/test_chat_handler_agent_events.py`、
  `tests/agent/test_entrypoint_outcome_parity.py`、`tests/agent/test_registration_consistency.py`。
- 文档：本记录、`docs/handoff/latest.md`、`docs/handoff/t07-t08-review.md`、
  `docs/superpowers/specs/2026-09-23-agent-anonymous-session-ownership-design.md`、
  `docs/superpowers/plans/2026-09-23-agent-anonymous-session-ownership.md`。

## 尚未完成与下一批

1. 独立排查大套件并行下的子进程启动/长文本 10 秒门限敏感性；保留真实超时，
   不通过放宽阈值、跳过或缩小安全检查换取全绿。补 Linux/POSIX 权限验证。
2. 整理现有 T02/T04/T07/T08 与本批重叠补丁，精确分批暂存及审查；不整体提交混杂工作树。
3. 终态事件与 run 状态更新仍不是同一事务，需要单独一致性设计。本批不声明已解决。
4. 真实部署、科学模型、跨机器存储、账户认证、非 Agent 管理面权限仍不在本批验收范围。
