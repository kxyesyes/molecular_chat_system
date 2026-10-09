# MedChat 剩余架构项完成设计

## 背景

`origin/main` 已完成科研核心与 Web 适配层的大部分收敛、终态语义和前端展示修复，但交接记录仍明确有四类未闭环问题：普通 Agent WebSocket 的等待续接仍是 socket-local、真实资产端到端验收没有统一入口、部署目标验收依赖目标环境、对接科学稳定性还没有进入验收门禁。本任务在独立分支上完成可以在仓库内落地的部分，并把不能凭本地环境证明的部分改为明确的 skipped/blocked 结果。

## 目标

1. 让等待用户补充信息的 Agent 运行在 WebSocket 断开后，可以由同一浏览器 Agent session 在新连接中从服务端 SQLite 恢复并继续；不重放正在运行的非幂等科学工具。
2. 提供只读、归属校验、脱敏的 Agent run 快照与事件查询接口，支持前端刷新恢复和审计。
3. 在既有验收脚本体系中加入运行时续接、真实资产预检和对接重复稳定性报告，而不是另起平行执行框架。
4. 更新交接文档，区分仓库内已完成、已实现但依赖部署环境、以及需要真实科研资产才能判定的事项。

## 非目标

- 不更换 FastAPI、原生 WebSocket、SQLite、现有 Agent loop 或科学工具。
- 不在断线后自动重放已经开始的 docking、生成、模型推理等非幂等工作。
- 不伪造 PDE/BuChE 权重、Vina、RAG 或外部模型验收结果。
- 不修改、提交或删除用户现有的 `data/molecular_faiss_index.index.manifest.json`。

## 设计

### 1. Durable waiting continuation

初次请求在 Agent run 已建立后，保存有限且经过 `redact_sensitive` 的 Web 请求元数据，包括请求能力开关、计数、温度、引用选择、线协议模式和等待过期时间。等待结果已经由现有 `decision_continuation` CAS 写入 SQLite；本任务只补足重新构造 `_Waiting` 所需的原始请求上下文。

新 WebSocket 收到 `resume` 时：

1. 先使用 `agent_session_id`、`trace_id`、`continuation_id` 查询 SQLite；
2. 检查运行状态、session/user 归属、续接快照形状、等待 TTL 和 continuation id；
3. 用保存的原始请求重新调用现有 `prepare_decision_request`，恢复查询指纹和允许工具集合；
4. 只在校验通过后把快照转成当前 socket 的 `_Waiting`，继续现有 CAS/validator 流程；
5. 续接成功后仍由原有 `transition_decision_continuation` 负责一次性消费，防止双重执行。

断开时：

- waiting 状态保留在 SQLite，允许新 socket 恢复；
- 已在执行的 turn 继续遵循现有取消/settle 语义，不从数据库重放；
- 应用关闭或 continuation 过期时，恢复失败并返回明确的 `continuation_unavailable`。

### 2. Read-only run recovery API

增加带 session ownership 检查的只读接口：

- `GET /api/agent/runs/{trace_id}`：返回 status、skill、query 摘要、时间、metadata 中的安全恢复信息，不返回密钥或完整敏感消息；
- `GET /api/agent/runs/{trace_id}/events`：返回该 session 可见的事件列表。

接口只读，不改变 run 状态，不提供任意重放能力。前端刷新只使用快照和事件恢复显示，不声称重新执行。

### 3. Acceptance integration

复用 `run_agent_acceptance.py`、`run_opensandbox_docking_acceptance.py` 和现有 evaluation checks：

- 增加 runtime reconnect contract case，验证等待状态断线、换 socket、同 session resume；
- 真实模式记录依赖预检：RDKit、模型权重、Ollama、target DB、RAG、Vina；缺失只产生 `partial`/`skipped`；
- 对接重复运行若没有真实 pose、能量、seed 和 manifest 一致性，不能标记科学稳定性通过；
- 外部模型只从运行时环境读取，报告仅记录 provider/model/存在性/耗时，不记录 key。

### 4. Documentation

`docs/handoff/latest.md` 顶部改为以当前 `origin/main` 实际提交为准，并记录本分支的真实验证结果。历史批次继续保留，不删除追溯内容。

## 安全与兼容性

- 继续使用 `SQLiteAgentStateStore.update_run_metadata`，由现有 redaction 保护元数据；不直接写 continuation JSON。
- 所有 API 严格校验 `agent_session_id`，不存在或不匹配时返回 404/403 语义，不泄露 run 是否存在。
- 为保持旧客户端兼容，`connection_ready` 只增加能力字段；旧的 socket-local resume 仍可用。
- 默认普通聊天策略 `a1_closed` 先实现完整续接；`semantic_v1` 若无法安全恢复意图账本则 fail closed，不降级为未经验证的执行。

## 验收标准

- 新增测试先在未实现时失败，再在实现后通过；不修改既有安全边界测试的预期。
- 同 session 换 socket 可以恢复等待 continuation；不同 session、错误 nonce、过期 continuation 均拒绝。
- 运行中断线不会产生第二次工具执行。
- run/event API 只返回本 session 数据且不泄露 secrets。
- Agent 聚焦测试、compileall、JavaScript 检查和真实资产预检均如实报告；无真实依赖时不伪造成成功。
