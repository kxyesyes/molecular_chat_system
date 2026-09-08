# Agent 决策协议诊断与有界纠错实施计划

基线：`9f64a9c`；分支：`codex/agent-decision-protocol-recovery`。
依据：用户已批准的 `2026-09-08-model-driven-agent-design.md` 第 4 节和上一批交接。

**Goal:** 定位实网 schema 不匹配，在不放松授权、证据和预算边界的前提下提供一次协议纠错。
**Architecture:** 复用严格 Pydantic 决策契约、现有 transport 和 LangGraph 控制循环。只传播固定白名单字段路径与错误类型，不保存错误原文；纠错作为下一次模型决策请求，不重新执行工具。
**Tech Stack:** Python、Pydantic 2、HTTPX、LangGraph、pytest、已有隔离验收脚本。

- [x] 诊断 TDD：缺失字段、错误类型、动态字段与敏感值脱敏；修改 `contracts/decision.py`、`decision_transport.py`，让验收报告记录安全原因。
- [x] 实网诊断尝试：只用运行时 API 配置运行三例；最后一批明确 HTTP 403。没有再次复现 schema 字段错误，不猜测原始响应。
- [x] 纠错 TDD：全任务最多一次，计入原请求/时间预算；只有 schema 错误可纠错；HTTP/拒绝/授权/证据错误不重试。恢复不得重置纠错预算。
- [x] 最小实现：`decision_loop.py`、`decision_policy.py`、`decision_continuation.py`；不改变生产入口或科学判定，不伪造/补填模型决策字段。
- [x] 回归：协议、循环、恢复、隔离聊天、完整 Agent、旧 contract、compileall。保留实际失败状态；最终 1985 passed、1 skipped，旧 contract 34/34 passed。
- [x] 真实 JSON/native 小样本复测尝试，独立代码审查；交接中记录已知限制和准确命令、结果。
- [ ] 上游 403 解除后，完成 schema 字段实网定位及纠错效果/重复稳定性验收；本轮不能声明成功。

文件范围为上述实现及相关测试、验收脚本、本文与交接。报告只放忽略目录 outputs；不读取 .env、不输出 API key、不记录模型响应或私有推理。无生产切换、无 main 修改、无历史大型 PR。
