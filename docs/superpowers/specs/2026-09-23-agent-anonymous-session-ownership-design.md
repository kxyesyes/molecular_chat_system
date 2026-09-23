> 历史来源文档：以下设计、实施记录和测试数不是本轮 P05 候选的验收结果。
> 当前精确身份、失败及复测结果见协调工作树 docs/handoff/p05-session-ownership-patch-review.md。

# Agent 匿名会话归属与续接隔离设计

日期：2026-09-23。状态：设计经用户批准，实现与独立审查完成；测试状态见
[实施交接](../../handoff/agent-anonymous-session-ownership.md)。工作基线：
`codex/delegated-session-baseline` 隔离工作树，含未提交的 T02/T04/T07/T08 集成。

## 目标与边界

不增加登录弹窗，通过服务端签发的持久匿名浏览器会话，隔离 Agent 运行、
幂等续接及其后台任务的读取、事件和取消操作。不能把匿名会话宣传成账号认证：
共享浏览器、复制 Cookie 或浏览器资料被盗仍可能共享任务。不能从客户端
`metadata`、请求体或 URL 中的 `user_id` / `session_id` 推断可信归属。

本批只保护 Agent 工作流及其任务投影；其他任务类型维持现有接口策略，
不借本批改造整个任务平台。旧的无归属运行和 Agent 任务保留在数据库中，
新匿名会话无法查看或续接；不自动认领、迁移或删除。科学工具、模型、
RAG、provenance、warnings 和 artifacts 的语义保持不变。不调用真实外部模型。

## 当前代码事实

- `src/web/app.py` 的 `/ws` 使用 `ChatHandler`，聊天 Agent 与
  `/api/agent/workflows/run` 均通向 `SupervisorAgent`。
- `SupervisorAgent.run()` 按未限定归属的 `idempotency_key` 查找既有 trace，
  `_build_context()` 未填充 `AgentContext.user_id` / `session_id`。
- `WorkflowOrchestrator._resolve_idempotent_context()` 也单独执行无归属查找；
  `SQLiteAgentStateStore.claim_workflow_run()` 只对比状态，还会覆盖旧归属。
- `/api/agent/workflows/run` 使用 `TaskManager.submit()`；共享的
  `/api/tasks`、`/api/tasks/{id}`、`/events`、`/cancel` 当前没有 Agent
  任务归属检查。生产路由可优先使用另一 task runtime，必须测试实际
  Agent 投影读取路径，不能只在创建时记录 owner。

## 方案选择

比较过账号登录、完全禁止匿名续接与服务端匿名会话。用户选择第三种，
接受其只能区分浏览器而不能证明人的身份。采用随机 Cookie + SQLite
会话登记，不引入登录 UI、第三方认证服务或前端持有的长期密钥。

## 会话权威边界

新建一个职责单一的 Web 会话组件：生成高熵随机令牌，数据库仅存令牌摘要、
随机会话 ID、创建/过期时间；原始令牌仅通过 `HttpOnly`、`SameSite=Lax`、
`Path=/` Cookie 传给浏览器，不写入日志、Agent metadata、任务 payload
或报告。HTTPS 下加 `Secure`；本地 `127.0.0.1` HTTP 开发才允许非
`Secure` Cookie，且不能信任未经配置的代理头判断 HTTPS。

会话闲置有效期定为 90 天；有效 HTTP 访问同步延长数据库到期时间和
Cookie 的 `Max-Age`，过期、未知或损坏的 Cookie 不恢复原身份。
普通 HTTP 入口可为无 Cookie 的浏览器签发新会话；
WebSocket 握手必须已有有效 Cookie 且 `Origin` 与服务端允许的来源一致，
否则拒绝连接。首页首次 HTTP 加载会先取得 Cookie。会话数据库放在
仓库外的本机用户配置目录（可通过独立的绝对路径配置覆盖），跨进程、
重启与代码更新可读取；若会话库不可用则拒绝受保护操作，不退化为
匿名共用运行。会话创建与续期应避免每个 WS 消息写库。当前 Agent
运行库和任务库的默认路径仍可能随工作树变化：会话身份可跨更新保留，
但旧运行/任务跨工作树续接只有在 `AGENT_STATE_DB` 和
`MEDCHAT_TASK_DB_PATH` 指向稳定的持久路径时才成立，本批不静默迁移
原有数据库。

实施审查补充：HTTP 修改请求如提供 Origin，必须同源；无 Origin 的本地/API 客户端
保留兼容。所有带会话 Cookie 的 HTTP 响应强制 `Cache-Control: private, no-store`，
避免共享缓存复用令牌；Origin 拒绝发生在会话续期和业务调用之前。

Web/API 仅把服务端解析出的会话 ID 作为可信主体传给聊天执行和后台任务
提交。`Supervisor.execute()` / `run()` 增加显式内部主体参数并填入
`AgentContext.session_id`；`user_id` 不伪称为已认证账号。直接 Python
调用可继续创建无归属本地运行，但不得接管有归属 trace。

## 运行和任务数据流

1. `/ws` 和 `/api/agent/workflows/run` 获取服务端会话；明确剔除客户端
   metadata 中的归属字段。`plan` 可保持无状态，但不得泄露既有运行数据。
2. 客户端幂等键先校验类型、长度，再与会话 ID 组合成不可逆的内部键，
   保持现有数据库唯一列；Supervisor 和 Orchestrator 的两处解析必须
   走同一归属检查。已存在相同键或显式 trace 时，对比会话、请求身份
   （query/skill）和允许续接的状态；不匹配则拒绝且不执行工具。
3. SQLite `claim_workflow_run` 在 `BEGIN IMMEDIATE` 事务内比较状态、
   原有 session ID、query/skill 和幂等身份。CAS 失败不能更新 owner、
   metadata 或 checkpoint。旧无归属记录不能被浏览器会话认领。
4. Agent 后台任务创建时把 owner 与任务记录原子绑定，后台 handler 只接收
   服务端捕获的主体，不能从用户 payload 取主体。共享任务接口对
   `agent_workflow` 做 owner-aware 查询、列表过滤、事件与取消检查；
   非 owner 和旧无归属 Agent 任务统一返回 404，避免暴露任务是否存在。
   必须同时覆盖 TaskManager 与实际 task runtime 的读取分支；若无法
   确认归属则 fail closed，不能返回投影。
5. 原有科学事件、tool_results、warnings、artifacts、失败状态继续透传；
   不在结果中增加 Cookie 或原始会话令牌。

## 错误与兼容策略

- 会话库不可用：受保护提交/读取返回明确 503；WebSocket 拒绝握手，
  不执行 Agent。跨会话或旧无归属 Agent 任务读取返回 404；运行续接
  返回结构化拒绝，绝不默默创建一个同名运行。
- 相同会话、相同请求的合法重试保持现有 checkpoint 行为；非幂等工具
  的不确定执行仍按 T07 规则拒绝重放。
- 无归属的直接 Python 科研测试保留原有本地语义，但 Web 入口不得
  使用该兼容路径。旧数据库迁移仅增加必要表/列，不删除既有记录。
- 任务列表不得借通用分页顺序使过滤后的 Agent 数据越权可见；分页
  应在归属过滤后形成稳定结果。其他任务的现有公开策略不宣称安全。

## TDD 与验收

先写并运行失败测试，再实施最小修复：

1. 两个 TestClient/浏览器分别获得会话；跨会话同 trace、同幂等键、
   Agent 任务 ID 的状态/事件/取消/列表均不能越权；同会话可正常续接。
2. 改写请求 metadata 的 `user_id` / `session_id` 或直接传旧 trace
   不能改变归属；旧无归属记录不能由新 Cookie 认领。
3. 并发认领只有一个成功，失败路径不改变原 owner、状态或 checkpoint；
   non-SQLite store 不具备原子认领时仍按现有审查项 fail closed。
4. Cookie 的安全属性、过期/重启、无 Cookie 的 WS、错误 Origin、
   会话库故障均有聚焦测试；响应、日志和持久化投影不含原始令牌。
5. 通过 Agent 聚焦、相关 Web/任务路由回归、contract acceptance、
   `compileall` 与 `git diff --check`。报告区分 fixture 与真实科研工具。

## 实施顺序与不做事项

按独立阶段实施：会话组件及入口注入 → owner-bound Agent CAS 和幂等查找
→ Agent 任务投影访问控制 → 跨入口回归和兼容性审查。测试应覆盖聊天
WebSocket 与 HTTP 工作流，不只覆盖 SQLite helper。不新增登录 UI、
不迁移全部后台任务权限、不训练或启用模型、不推送、合并或部署。
