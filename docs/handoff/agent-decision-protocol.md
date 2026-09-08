# 逐轮模型 Agent：A1 决策协议交接

日期：2026-09-08。分支：`codex/agent-decision-protocol`，基线 `7c8bf3a`。
工作位置：独立 scientific-assets worktree；原项目混杂改动未触碰。

## 已完成

用户已确认 [迁移设计](../superpowers/specs/2026-09-08-model-driven-agent-design.md)。
本批落实 [A1 实施计划](../superpowers/plans/2026-09-08-agent-decision-protocol.md)，只提供模型决策协议边界，不接管生产入口。

- 新增严格的 tool / clarify / finish 决策契约与统一 envelope。
- 给现有 OpenAICompatibleModel 增加异步 decide 方法；旧 generate/stream_generate 保持原行为。
- native 函数调用与显式 JSON 模式共用校验器，不自动降级、不隐式重试、不执行工具。
- 拒绝截断、多动作、重复键、非法类型、非有限数、非法 Unicode、无 evidence 引用的科学 finish。
- 科学 finish 的引用在 A1 只做结构检查；引用的存在、归属、值与单位绑定必须由 A2 验证，不能把协议通过当成科学结果通过。
- 记录调用局部的配置 provider/model、request ID、耗时、有限 usage 与明确错误；不保存私有思维链、响应正文或异常原文到日志。
- 保护超时/取消、响应大小、客户端生命周期及并发模型配置切换；避免将旧请求发送到新模型地址或使用新凭据。
- 网络日志隐私过滤仅影响当前决策请求；未全局调低日志级别。依赖升级时需要检查 HTTPX/HTTPCore logger 名称是否变化。

## 接口边界

`await model.decide(messages, mode="native")` 返回 `DecisionResponse`，含 decision/error/tool_call_id/metadata。
其 `success` 只表示收到合法模型提议，绝不表示科研工具已执行或研究任务成功。
JSON 模式必须显式设置 `mode="json"`；不支持协议的上游明确失败，不转固定 Planner。
配置模型名是请求的模型标识，不是本轮已验证了某外部服务实际使用的模型。

请求默认 max_tokens=1500、timeout_seconds=60，上限分别 8192 与 60；非法值在网络调用前拒绝。
会话输入最多 64 条消息，受 128 KiB 文档限制；决策文档上限 32 KiB、嵌套深度上限 16。
HTTP 响应流上限 128 KiB；只接受 identity Content-Encoding，不支持压缩响应自动解压。
工具调用 ID 必须完整匹配前序观察，不得重复/孤立；本接口尚未管理长任务恢复。

## 文件

新增：

- `src/agent/contracts/decision.py`：决策 schema、严格 JSON 解码。
- `src/agent/decision_transport.py`：单次有界 HTTP 决策请求。
- `src/agent/decision_privacy.py`：请求上下文网络日志保护。
- `tests/agent/test_decision_contract.py`：39 项协议测试。
- `tests/test_agent_decision_model.py`：61 项模型传输/故障/并发测试。
- 本交接和 A1 实施计划。

修改：

- `src/agent/openai_compatible_model.py`：新增 decide 入口。
- 原设计文档：标记用户已确认。

## 验证

使用已有 MedChat Conda Python；本环境不依赖 pytest-asyncio，新异步测试沿用 asyncio.run。

| 命令 | 结果 |
|---|---|
| `python -B -m pytest tests/agent/test_decision_contract.py tests/test_agent_decision_model.py tests/test_openai_compatible_model.py -q -p no:cacheprovider --tb=short` | 107 passed |
| `python -B -m pytest tests/agent tests/test_agent_decision_model.py tests/test_openai_compatible_model.py -q -rs -p no:cacheprovider --tb=short` | 1744 passed、1 skipped、7 warnings，28.29 秒 |
| `python -B scripts/run_agent_acceptance.py --mode contract --output outputs/agent_evaluation/decision-protocol-contract.json` | 34 cases，passed |
| `python -B -m compileall -q src scripts` | exit 0 |
| `git diff --check` | exit 0 |

跳过项是未启用的 harness shadow 性能测试；警告为现有 SWIG/FastAPI 弃用提示。没有将跳过项计为通过。
模型协议测试使用进程内 HTTPX MockTransport 与明确的假测试凭据，不访问真实外部 API，也未读取真实 key。
本轮未重跑整个 tests 目录、浏览器人工验收或所有真实科研案例；不能沿用前轮的全仓通过数宣称本批已做这些验证。
独立复审复现并确认修复了日志泄漏、数值溢出、Unicode、压缩扩张与配置快照等边界，无剩余 A1 阻断。

## 尚未完成及下一步

下一批 A2：复用既有 LangGraph，建立单一逐轮决策循环；通过统一工具网关执行、返回真实观察，校验来源引用与证据，落实预算/终态/恢复。先接性质、类药性、活性和靶点搜索。
随后 A3：Supervisor/ChatHandler 接线、前端终态兼容、隔离真实主模型与工具验收。
再按已确认设计扩展其余科研任务，验收后切换默认入口并清理旧固定流程。

当前仍是“协议底座已完成”，不是“自主 Agent 已完成”。未删除旧 Planner、启用生产模型、重启服务或更改现有运行实例；未 push/创建 PR/合并 main。分支有继承历史，提交到 main 前仍需整理依赖范围。
