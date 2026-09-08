# 隔离聊天接入实施计划

承接已批准的 model-driven-agent 设计与上一批交接。基线 9c6109b。
分支 codex/agent-isolated-chat-entry；不切换运行网站或修改默认 app 路由。

## 设计

新增 ChatHandler 的显式服务端方法，接收既有 ModelDecisionLoop 和可信 AgentContext、
request_kind、工具授权、requirements、continuation 参数。复用 AgentEventBus、
现有事件发送与脱敏函数、AgentResult 契约；不伪造 workflow_plan 来绕过旧渲染分支。
隔离入口不调用旧 Supervisor.execute/Router/TaskPlanner，也不在结果后再调用聊天模型。
默认 WebSocket 入口保持不变；不能通过客户端 JSON 启用实验后端或设置授权。

普通聊天也实际调用主模型，但目录为空。科研结果来自 Harness 的证据化最终答案。
事件通过每请求队列实时转发；终态 agent_result/complete 保留 trace、状态和恢复 ID。
用户/会话必须由服务端绑定。断连取消等待中的决策任务，不自动重试科研操作。
输出脱敏/大小限制显式标记，不静默展示截断结果；原始证据仍由 Harness 持久化。

## 步骤

- [x] TDD：模型调用、无固定回复、真实 RDKit 批量及证据消费、实时事件、失败闭环。
- [x] 澄清恢复、权限缩减、无模型/依赖、异常和断连测试。
- [x] 新增隔离实网验收脚本：只读运行时环境变量、临时状态库、公开合成提示词；不运行重型工具。
- [x] 执行真实模型小规模验收：native 0/3，JSON 两轮 1/3、2/3；如实记录失败，不切旧 Planner。
- [x] 全量回归、contract、compileall、独立只读审查和交接完成，精确提交本任务文件。

实网验收尚未通过上线门禁。下一步先定位模型 `invalid_decision_schema`，再重复验收，不切生产网站。

## 范围

修改 src/web/chat_handler.py，新增 src/web/decision_chat.py、tests/agent/test_decision_chat.py、
scripts/run_decision_chat_acceptance.py 及相关测试/交接。
不改前端、Supervisor 默认入口、生产配置或模型选择；本批不负责浏览器身份与 continuation UI。
实网报告只记录 provider/model/成功状态/耗时和工具验收摘要，不记录凭据或模型私有推理。
