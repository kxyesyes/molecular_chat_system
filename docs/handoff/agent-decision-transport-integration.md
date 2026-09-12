# 模型决策传输与请求隐私集成

日期：2026-09-12；分支 `codex/agent-decision-transport-integration`。
历史集成计划 Task 5 的传输子批次，依赖已批准合并的 PR #19/main `15061b2`。
起点为 PR #19 审查 head `d2e04c6`，随后普通 merge 合并后的 main；未强推。

## 实现范围

- `src/agent/decision_transport.py`：一次有界模型请求，native / JSON 显式模式，
  固定 agent_decision 函数信封，不执行科学工具、不自动回退或重试。
- `src/agent/decision_privacy.py`：仅在当前请求上下文屏蔽 HTTPX/HTTPCore 原始网络日志；
  不改变无关请求的日志级别或全局静音。
- `src/agent/openai_compatible_model.py`：仅新增11行 `decide()` 接点；现有聊天不变。
- `tests/test_agent_decision_model.py` 与 `tests/agent/test_decision_transport_boundaries.py`：
  历史65项与补充边界回归。HTTPX MockTransport 和 example.invalid，不调用真实外部模型。

来源是 `9312bf5` 的 transport/privacy/adapter；复用 main 已合并的严格决策协议、错误类型。
校验消息历史/调用ID配对、请求选项、单次响应、finish reason、字节预算、压缩前拒绝、
超时/取消、并发配置快照和安全错误。传输通过仅表示模型提案格式可接收，不能替代后续
工具授权、真实执行、输入消费、科学证据验证或任务成功判断。

## TDD 与审查

历史测试先复现 `decide` 缺失 **1 failed**，移植后历史传输+120项契约 **185 passed**。
新增92项结构/序列化/预算/日志边界通过；连同旧模型适配器回归 **284 passed（-W error）**。

初轮独立规格审查发现两个继承自源代码的阻断：

1. HTTP 200 响应同时含有效 choices 与 error 时，被当成成功决策。
2. `refusal` 使用真假值判断，空对象/列表、false/0 等无效类型被当成未拒绝。

新增28项测试：**22 failed / 6 passed**；最小修复后聚焦 **312 passed（-W error）**。
任何非null顶层 error 均拒绝；refusal 只允许 null 或字符串，非空字符串保持拒绝语义。
允许缺省/null error 及 null/空字符串 refusal，保留正常兼容。失败无 decision/call ID，
错误细节固定，不回显 provider error/refusal 原文，不重试也不派发工具。

初版全 Agent + 旧新模型 + 反幻觉/平台健康为 **2259 passed / 1 skipped / 7 warnings，27.41秒**；
compileall、8个Node测试、contract通过。

修复后独立规格复审：312项及1350项额外合成HTTPX组合通过，覆盖两个模式与三种决策。
父任务最终全 Agent + 新旧适配器 + 反幻觉/平台健康：
**2287 passed / 1 skipped / 7 warnings，25.45秒**。

### 序列化前资源预算

质量审查发现原历史快照先整体 JSON 序列化，再检查结构/字节数；20层重复引用的畸形
content 在被拒绝前展开，三次探针约14MiB峰值、1.25秒（tracemalloc），不受后续网络超时保护。
补4类测试（共享嵌套、工具调用未知字段、超长单消息、聚合超限）禁止整体历史序列化，
并保留UTF-8恰好131072字节可接受、超1字节拒绝：**4 failed / 2 passed → 聚焦318 passed**。
先验证封闭浅层消息/调用结构和单字符串长度，再逐块编码累计UTF-8预算后生成隔离快照。
预算未扩大；历史 tool-call/function 的未知额外字段现在明确拒绝，不作为隐式扩展传给供应商。
增量规格复审通过：318项及144组合合法历史与源等价，20/100层别名在encoder前拒绝。
质量复审通过：独立318 passed（-W error）；同20层别名探针峰值从约14MiB降到3489字节，
耗时0.081ms（tracemalloc），零网络请求。该数据是局部合成探针，不是服务吞吐量指标。
父任务最终全Agent/新旧适配器/反幻觉/健康 **2293 passed / 1 skipped / 7 warnings，25.30秒**。
最终compileall及diff-check通过；独立双审无剩余阻断。PR CI和具体合并授权仍待完成。

## 待完成与边界

本批不接管 WebSocket/生产聊天、不更改本地 gmm-llama 分子生成、不训练/激活模型，
不读取真实 API key、CSV、权重或运行资产。现有普通聊天不自动升级为新决策循环。
证据账本/动态 session/持久化原子续接/revision3迁移/敏感澄清/隔离验收入口仍待集成。
没有真实供应商测试或目标服务器验收，不能声称生产可用。原始混杂树未覆盖。
PR #15 的关闭测试失败由独立分支修复；旧沙盒偶发 artifact_failed 保留待查，不混入本批。
