# A3 第三批：native 决策函数边界与 DeepSeek 复测

日期：2026-09-08；基线 `f7c5e06`；分支 `codex/agent-native-decision-boundary`。
实施位置为独立 scientific-assets worktree。原项目已有改动保持不动；本轮不切换生产、不重启、不合并 main。

## 原因与最小修复

前轮真实 native 验收三轮均为 2/3，失败为双分子性质请求的 `invalid_function_call`。
额外只读诊断观察到：返回对象、ID、类型均符合要求，但函数名为 `property_calculator`，不是声明且强制选择的 `agent_decision`。
不能把这个错误解释为接口权限不足，也不能静默将错误名称改成合法名称后执行。

在现有 `src/agent/decision_transport.py` 的 native payload 中新增明确系统提示：

- 唯一可调用 API 函数为 `agent_decision`，tool/clarify/finish 均提交 decision envelope。
- 科研工具名属于 `decision.tool_name`，不是可直接调用的原生 function.name。
- 参数继续使用 `input_ref`；version 和必需字段遵循原 schema。
- 执行仍由 Harness 校验、绑定真实输入后完成。

函数描述同步说明这层区别；JSON 提示和返回解析器不变。未增加新框架、依赖、路由或重试层。
输入历史先经过原校验再复制，原消息内容、native call ID 与观察配对不变；新增提示只属于本次外发请求，不追加到持久化历史。
既有一次 schema 纠错规则不变；`invalid_function_call` 不进入纠错，错误调用、越权、虚构证据仍失败关闭。

## TDD 与独立审查

先添加 native 请求提示测试，得到 **1 failed / 82 passed**，失败点正是缺少 native 协议说明。
补充三种错误函数名（property_calculator/run_docking/unknown_tool）的有效 envelope 拒绝测试，以及错误函数名不触发 loop 纠错/工具执行的回归。
实现后含隔离聊天的聚焦测试 **106 passed**。
旧 native 历史测试由整列表相等调整为去除新增系统提示后的历史后缀相等，同时保留 caller 不变、ID、tool_choice 和单次请求断言。

独立只读审查运行 83 项聚焦测试并检查 diff，无本轮可操作问题。审查未读取真实配置或调用外部 API；实网结果来自主任务执行。

## 真实外部模型结果

按用户明确授权，将临时凭据仅注入测试子进程环境；请求模型 `deepseek-v4-pro`，provider 为 OpenAI-compatible。
未写入项目配置、报告或 Git；每批进程结束前移除临时凭据变量。未执行服务商权限变更，本轮无 HTTP 403。
六份 JSON 报告的密钥格式扫描为零命中。原始 provider 回复、私有推理和密钥不进入报告。

| 案例 | 验收内容 | native 三轮 | JSON 三轮 |
|---|---|---|---|
| CHAT-001 | 普通聊天调用主模型，不调用科研工具，保留正文与单终态 | 3/3 | 3/3 |
| CHAT-002 | CCO+CCN 实际 RDKit，两个正确结构及 MW/LogP/TPSA/HBD/HBA/QED，数据与正文保真 | 3/3 | 3/3 |
| CHAT-003 | 缺输入先澄清，不自行计算；原所有者补充 CCN 后恢复并实际计算 | 3/3 | 3/3 |

| 协议 | 总状态 | pass_rate | partial/failed/skip | p50 | p95 |
|---|---|---|---|---|---|
| native | passed | 9/9，100% | 0/0/0 | 5.250 秒 | 6.782 秒 |
| JSON | passed | 9/9，100% | 0/0/0 | 5.594 秒 | 6.562 秒 |

分位数采用每协议九个案例耗时的 nearest-rank，包括 CHAT-003 的澄清及恢复。测试期间有并行本地回归，不能用这些小样本作性能优化结论。
所有模型决策均首次合规，本轮未使用 schema 纠错。成功科学结果由实际 RDKit 和服务端要求判定，不使用 LLM 自评，也未增加模拟/fallback。
之前 native 的错误在本轮三次复测未出现，不代表任何提示词或服务商响应都能保证通过；解析与权限门继续是权威边界。

## 验证命令与产物

解释器：MedChat Conda 环境的 Python。

| 实际命令 | 结果 |
|---|---|
| `python -B -m pytest tests/test_agent_decision_model.py tests/agent/test_decision_protocol_recovery.py tests/agent/test_decision_chat.py tests/agent/test_decision_chat_acceptance.py -q -p no:cacheprovider --tb=short` | 106 passed，8.61 秒 |
| `python -B -m pytest tests/agent tests/test_agent_decision_model.py tests/test_openai_compatible_model.py -q -rs -p no:cacheprovider --tb=short` | 1990 passed、1 skipped、7 warnings，118.22 秒 |
| `python -B -m pytest tests/test_agent_anti_hallucination_fallbacks.py tests/test_agent_platform_health_check.py tests/agent/test_real_acceptance_checks.py -q -p no:cacheprovider --tb=short` | 53 passed、7 warnings，13.82 秒 |
| `python -B scripts/run_agent_acceptance.py --mode contract` | passed，34 例 |
| `python -m compileall -q src scripts` | exit 0 |
| `git diff --check` | exit 0 |

Skipped 是未开启的性能测试，warnings 为既有 SWIG/FastAPI 弃用提示。未修改 JS，未新增 Node 验收。

真实验收分别执行 `python -B scripts/run_decision_chat_acceptance.py --mode native --output <报告路径>`
与 `--mode json`，每种模式三轮，六次均 passed/返回 0。
报告位于忽略目录，不提交：

- `outputs/agent_evaluation/deepseek_boundary_native_round1.json`
- `outputs/agent_evaluation/deepseek_boundary_native_round2.json`
- `outputs/agent_evaluation/deepseek_boundary_native_round3.json`
- `outputs/agent_evaluation/deepseek_boundary_json_round1.json`
- `outputs/agent_evaluation/deepseek_boundary_json_round2.json`
- `outputs/agent_evaluation/deepseek_boundary_json_round3.json`

本轮修改：transport、两个测试文件；新增本交接与 `docs/superpowers/plans/2026-09-08-agent-native-decision-boundary.md`。

## 边界与下一步

这是隔离 ChatHandler 捕获器验收，不是真实浏览器。native/JSON 是决策协议，不是 UI 流式开关；决策请求仍为非流式，现有事件队列保持流式发送。
未覆盖生成、RG-MPNN、Vina、RAG 或所有生产入口。状态库临时创建并清理，报告提供 trace/evidence/hash 摘要，不是完整恢复包。
没有削弱工具权限、重新启用旧 Planner 或调用总结模型重写科学数值。

下一步适合进行隔离浏览器入口及身份/会话绑定验收，验证真实 WebSocket、澄清恢复、断线终态和前端显示；
先核对服务端授权方案，不直接让浏览器传入 allowed_tools/request_kind/requirements，也不自动切换正式网站。
