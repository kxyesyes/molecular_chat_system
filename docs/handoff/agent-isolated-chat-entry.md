# A3 第一批：隔离聊天接入与真实模型验收

日期 2026-09-08；基线 `9c6109b`；分支 `codex/agent-isolated-chat-entry`。
实施在独立 scientific-assets worktree，原项目已有改动未覆盖。

## 交付与边界

新增 `ChatHandler.process_decision_message` 服务端显式入口，连接现有 ModelDecisionLoop。
复用 ToolRegistry、科学验证、证据、持久化和事件脱敏，不创建新 Agent/Planner/工作流框架。
隔离入口不调用旧 Supervisor.execute、Router、TaskPlanner 或总结模型；无需假造 workflow_plan 才能直出。
默认 app/WebSocket 路由、正在运行的网站与模型配置完全不变，没有自动启用开关。

可信服务端调用方必须提供 user_id/session_id/trace_id、request_kind、allowed_tools、required_tools、requirements。
这些参数不能直接从浏览器 JSON 解包；本方法本身不是身份认证或自然语言意图分类器。
不修改 caller 的上下文、共享历史或模型配置。主模型由传入的 decision_loop.model 决定，不能从 UI 临时字段覆盖。

- 普通聊天通过主模型的结构化 finish 回答，工具目录为空，不使用固定欢迎词。
- 科研结果直接取 Harness 的证据化正文，不经过主模型二次润色。
- 每个请求独立队列，实时发送 `agent_event`，然后 `agent_result` 和唯一 `complete`。
- 终态保留 trace/status/tool_result_sequence/quality/provenance/warnings/artifacts/requirements 验收记录。
- waiting_for_input 单独映射显示，返回 continuation_id；恢复仍由既有 owner/session/预算指纹校验。
- 已复现跨用户恢复拒绝、原用户合法恢复、工具禁用不被覆盖、无效结构无假性质或可视化。
- 断连取消正在等待的模型请求；不擅自重跑或恢复科学工具。外部线程/工具的物理终止及不确定状态仍由 Harness 负责。
- 异常给出通用终态错误，不输出 provider 异常原文。
- 流式事件与终态的脱敏/截断均显式标记；字典键递归脱敏；非有限诊断值替换为明确占位文本，保持标准 JSON。
- 科学原始结果对象不被显示层改写。显示端裁剪、脱敏不等于改变科研计算结论；报告另行核对实际发送的科学数据与正文。

本批只验证服务端 WebSocket 消息契约，捕获器不是实际浏览器。前端 continuation UI、正式身份会话绑定、
Supervisor/app 生产选型、会话模型切换接入、RAG 和额外科研工具仍未接入此隔离入口。
长工具操作、分子生成、活性训练、Vina、OpenSandbox 不属于本轮验收。

## 实网结果（不能称为全部通过）

只从运行时环境读取 API 配置。存在性检查全部为 true；实际配置模型为 `gemini-3.1-pro`，
provider `OpenAI-compatible`。没有读取密钥文件、打印 Key、写入凭据、启动生产服务。

| 批次 | 普通聊天 | CCO+CCN 性质 | 缺输入及恢复 | 总结果 |
|---|---|---|---|---|
| 原生工具调用 native | failed | failed，untrusted_tool_input 拦截 | failed | failed，0/3 |
| 显式 JSON 第一轮 | failed | passed，11.968 秒 | failed | partial，1/3 |
| 显式 JSON 诊断复测 | passed，8.453 秒 | passed，11.985 秒 | failed，13.562 秒 | partial，2/3 |

诊断复测的澄清请求本身成功，没有擅自计算；恢复时外部模型返回不符合结构化决策 schema 的响应，
公开原因码为 `invalid_decision_schema`，工具没有执行，终态明确失败。
前两轮没有收集更细的公开协议原因码，不猜测为鉴权、网络、token 截断或某个具体字段错误。
新增诊断代理只记录成功状态、公开错误码/原因码，不保留模型原始响应或私有推理。

两轮 JSON 结果波动，不能据此宣称稳定。没有改变协议约束、允许模型自填工具参数或退回旧 Planner 来刷通过率。
性质成功条件为真实 RDKit、指定两分子、完整 MW/LogP/TPSA/HBD/HBA/QED、唯一正确工具、
输入输出摘要及 evidence ID，并检查最终正文未截断、传输科学数据与结果对象一致；不是 LLM 自评。
此小样本不包含生物活性/对接真假结论，也不构成完整科研平台验收。

## 运行方式

解释器：`C:/Users/xkx52/.conda/envs/MedChat/python.exe`。

```text
python -B scripts/run_decision_chat_acceptance.py --mode native --output outputs/agent_evaluation/decision_chat_native_report.json
python -B scripts/run_decision_chat_acceptance.py --mode json --output outputs/agent_evaluation/decision_chat_json_report.json
python -B scripts/run_decision_chat_acceptance.py --mode json --output outputs/agent_evaluation/decision_chat_json_diagnostic_report.json
```

报告在忽略目录，不提交。没有配置时脚本返回 skipped/exit 2；部分成功或失败返回 exit 2，不做静默降级。
测试使用临时 SQLite 隔离状态库，结束后清理。报告保留 trace 和摘要，但不是可恢复的生产状态库或完整离线回放包。
测试脚本只接受无凭据/查询串的 HTTPS endpoint；不会加载 .env，也不会跟随重定向转发 Key。

## TDD 与审查

首批 7 项 RED → 7 passed；补齐断连、状态及显示边界和脚本测试。
独立审查复现字典键泄漏、非有限诊断值中断终态、事件裁剪无标记三项问题；
6 项 RED 后修复，连同既有 ChatHandler 回归 98 passed。
脚本测试用明确决策替身与真实 RDKit；实网表格才是外部模型真实执行结果。
最终独立复审的 9 项聚焦用例及内存边界/报告探针通过，本轮范围无剩余 P1/P2；
复审没有把实网 partial 认定为上线通过。

## 最终验证记录

| 命令 | 真实结果 |
|---|---|
| `python -B -m pytest tests/agent/test_decision_chat.py tests/agent/test_decision_chat_acceptance.py tests/agent/test_chat_handler_agent_events.py -q -p no:cacheprovider --tb=short` | 98 passed，7.70 秒 |
| `python -B -m pytest tests/agent tests/test_agent_decision_model.py tests/test_openai_compatible_model.py -q -rs -p no:cacheprovider --tb=short` | 1964 passed、1 skipped、7 warnings，37.76 秒 |
| `python -B -m pytest tests/test_agent_anti_hallucination_fallbacks.py tests/test_agent_platform_health_check.py tests/agent/test_real_acceptance_checks.py -q -p no:cacheprovider --tb=short` | 53 passed、7 warnings，6.91 秒 |
| `python -B scripts/run_agent_acceptance.py --mode contract` | 34/34 passed |
| `node tests/home_agent_task_panel_test.js` | passed |
| `python -m compileall -q src scripts` | exit 0 |
| `git diff --check` | exit 0 |

Skipped 是未启用的 harness shadow 性能测试；warnings 为既有 SWIG/FastAPI 弃用提示。
contract 的 RDKit 无效输入日志为预期拒绝。实网脚本三次均 exit 2，对应 failed/partial，非程序崩溃或假通过。

## 文件与关键阅读

修改：`src/web/chat_handler.py`（显式方法及兼容的键脱敏参数）。
新增：`src/web/decision_chat.py`、`scripts/run_decision_chat_acceptance.py`、
`tests/agent/test_decision_chat.py`、`tests/agent/test_decision_chat_acceptance.py`、
`docs/superpowers/plans/2026-09-08-agent-isolated-chat-entry.md`、本文档。

已读：AGENTS.md、PROJECT_STANDARDS、上一批交接、已批准模型驱动设计、Supervisor、app 的 Agent 构造与模型更新位置、
ChatHandler 旧路由/直出/失败/事件路径、decision_loop/policy/continuation、事件总线、AgentResult/Context、
OpenAICompatibleModel/decision_transport/privacy、原有与新增相关测试。

## 下一步优先事项

1. 先定位并修复真实主模型的决策 schema 遵循/协议纠错能力。保持严格校验和请求预算，不能直接接受模型任意工具参数。
2. 对普通聊天、缺输入、恢复和完整数据流重复真实验收；达到一致的通过标准后，再做浏览器隔离 UI 验证。
3. 最后才由维护者确认生产入口、身份会话与灰度切换；本批不 push、不创建历史巨型 PR、不合并 main、不重启网站。
