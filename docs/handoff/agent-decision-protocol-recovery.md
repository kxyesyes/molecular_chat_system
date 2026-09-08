# A3 第二批：决策协议诊断与有界纠错

日期：2026-09-08。基线 `9f64a9c`，分支 `codex/agent-decision-protocol-recovery`。
实施目录为 scientific-assets 独立工作树。原项目既有混杂改动未覆盖；没有生产网站切换、重启、main 合并或大型历史 PR。

## 结论与交付

本轮补齐了原设计允许的一次协议纠错，并提高失败诊断的可追踪性。代码与隔离契约可验证；
**当前不能宣称真实模型 schema 遵循问题已经解决或达到稳定上线条件**：复测后期上游开始返回 HTTP 403。
此前 `invalid_decision_schema` 具体是哪一个字段不合规，仍未获得实网复现证据，不推断为缺 version、错误 input_ref 或 token 截断。

- 严格 Pydantic schema 不变，不补填字段、不解析 Markdown 包围的 JSON、不接受任意工具参数。
- schema 错误只保留最多 8 项固定字段路径和错误类型；未知键与索引变为 `*`，未知类型为 `validation_error`。
  不传播错误消息、输入值、Pydantic context、原始 provider body 或私有推理。
- 同一 trace 最多一次 schema 纠错。只对 `INVALID_OUTPUT + invalid_decision_schema` 生效。
  HTTP 错误、模型拒绝、非法 JSON、工具越权、不可信输入、伪造证据均不会触发此纠错。
- 纠错是下一次正常模型请求，受同一模型请求次数和截止时间约束；错误决策不执行工具，不偷偷切换模式或旧 Planner。
- 纠错提示仅随下一次请求发送，随后丢弃，不污染后续工具观察或用户澄清；原生调用 ID 与观察配对保持完整。
- `protocol_repairs` 持久化到任务与 continuation。补充输入、重开状态库都不能重置次数。
- continuation 配置指纹新增协议版本 2。**旧版本等待快照不能跨版本继续**，需新开任务；不涉及数据库结构迁移，也没有切换生产。
- 修复审查发现的相邻兼容问题：Python 3.10 的 `asyncio.TimeoutError` 与其他超时统一进入既有超时原因码，
  不再误记为 `decision_runtime_failed`。该码仍沿用 `task_deadline_exceeded`，没有新增网络重试。
- 隔离验收报告增加严格过滤的 schema 坐标及数值 HTTP 状态，不记录环境变量内容、密钥或上游原文。

## 真实外部模型执行

全部使用运行时环境变量中的 OpenAI-compatible 配置，实际模型为 `gemini-3.1-pro`；未读取 `.env`。
仅运行普通聊天、CCO+CCN 真实 RDKit 性质、缺输入→澄清→CCN 性质三例，不涉及活性模型、分子设计、对接或生产状态库。

| 本轮批次 | CHAT-001 | CHAT-002 | CHAT-003 | 总状态 |
|---|---|---|---|---|
| JSON 修复前诊断 | passed，4.594 秒 | passed，54.031 秒 | failed，25.032 秒，恢复时 HTTP 错误 | partial，2/3 |
| JSON 加入纠错后第一轮 | failed，HTTP 错误 | failed，HTTP 错误 | failed，HTTP 错误 | failed，0/3 |
| native 增加 HTTP 状态诊断后 | failed，HTTP 403 | failed，HTTP 403 | failed，HTTP 403 | failed，0/3 |

前两批没有记录数值 HTTP 状态，不能倒推它们也是 403。最后一批三个请求均明确为 403，随后停止实网调用。
403 的账户、网关或模型权限原因仅凭状态码无法确定；需要维护者在服务商处核实，不打印或重新索取明文密钥。
没有重复三轮通过数据，没有有效的修复后性能/稳定性结论。**单元测试中的决策替身不属于真实外部模型通过案例。**

报告为忽略目录中的真实执行结果，不提交：

- `outputs/agent_evaluation/decision_protocol_before_report.json`
- `outputs/agent_evaluation/decision_protocol_json_round1.json`
- `outputs/agent_evaluation/decision_protocol_native_round1.json`

实际命令（使用 MedChat Conda Python，`-B` 禁止写字节码）：

```text
python -B scripts/run_decision_chat_acceptance.py --mode json --output outputs/agent_evaluation/decision_protocol_before_report.json
python -B scripts/run_decision_chat_acceptance.py --mode json --output outputs/agent_evaluation/decision_protocol_json_round1.json
python -B scripts/run_decision_chat_acceptance.py --mode native --output outputs/agent_evaluation/decision_protocol_native_round1.json
```

三次进程均非零退出，与 partial/failed 报告一致。脚本自身约定非通过返回 2，当前 PowerShell 工具将非零脚本结果呈现为 exit 1。

## TDD 与验证

先建立 3 项安全诊断 RED，再建立 13 项纠错/预算/恢复 RED；实现后 79 项相关协议与循环/恢复用例通过。
新增一次性提示寿命与数值 HTTP 状态测试 RED→GREEN；截止与取消用确定性时钟/取消验证，不以设备快慢作为判据。
独立审查发现的 Python 3.10 超时分类问题，经真实 `asyncio.wait_for` 超时测试 RED→GREEN 最小修复。

最终聚焦测试：

```text
python -B -m pytest tests/agent/test_decision_protocol_recovery.py tests/agent/test_decision_schema_diagnostics.py tests/agent/test_decision_chat_acceptance.py -q -p no:cacheprovider --tb=short
```

结果：24 passed，6.65 秒。测试证明原生/JSON 模式、工具前/观察后纠错、原生历史配对、单次限制、预算、恢复、超时、取消和安全边界；不是 provider 成功率证明。

| 最终命令 | 实际结果 |
|---|---|
| `python -B -m pytest tests/agent tests/test_agent_decision_model.py tests/test_openai_compatible_model.py -q -rs -p no:cacheprovider --tb=short` | 1985 passed、1 skipped、7 warnings，55.90 秒 |
| `python -B -m pytest tests/test_agent_anti_hallucination_fallbacks.py tests/test_agent_platform_health_check.py tests/agent/test_real_acceptance_checks.py -q -p no:cacheprovider --tb=short` | 53 passed、7 warnings，13.59 秒 |
| `python -B scripts/run_agent_acceptance.py --mode contract` | 34/34 passed，退出 0 |
| `python -m compileall -q src scripts` | 退出 0 |
| `git diff --check` | 退出 0 |

Skipped 是未启用的 harness shadow 性能测试；warnings 为既有 SWIG/FastAPI 弃用提示。
本轮没有修改 JavaScript，不需要新增 Node 回归。
独立审查初次 25 项聚焦检查通过；超时分类修复后 3 项增量复审通过，未发现本轮范围内剩余 P1/P2。
审查没有实网调用；不把审查通过等同于真实模型纠错验收通过。

## 文件范围

修改：

- `src/agent/contracts/decision.py`
- `src/agent/decision_transport.py`
- `src/agent/harness/decision_policy.py`
- `src/agent/harness/decision_loop.py`
- `src/agent/harness/decision_continuation.py`
- `scripts/run_decision_chat_acceptance.py`
- `tests/agent/test_decision_chat_acceptance.py`

新增：

- `tests/agent/test_decision_schema_diagnostics.py`
- `tests/agent/test_decision_protocol_recovery.py`
- `docs/superpowers/plans/2026-09-08-agent-decision-protocol-recovery.md`
- 本交接。

已读关键文件：AGENTS、PROJECT_STANDARDS、已批准模型驱动设计、上一批隔离聊天交接、决策契约/transport、
decision_loop/policy/continuation、隔离验收脚本、协议/循环/恢复/聊天验收相关测试。

## 下一步

1. 维护者恢复外部模型接口可用性；仍只通过运行时环境变量提供凭据，不写入报告或 Git。
2. 重跑 JSON/native 三例验收，观察新 schema 坐标是否定位真实字段，按同一科学标准做至少三轮复测。
3. 稳定后才开展隔离浏览器 UI 与身份/会话接入；未经明确确认，不修改默认生产路径。
