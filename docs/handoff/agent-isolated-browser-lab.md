# 隔离浏览器验收入口交接（2026-09-08）

## 结论与边界

完成批准的本地临时浏览器验收入口：真实 DeepSeek 决策、真实 RDKit 性质计算、澄清续接、断线重连、跨会话拒绝。复用现有 ModelDecisionLoop、ChatHandler 和 SQLiteAgentStateStore，没有新增静态科研 Planner，没有接管生产网页。

总体结论：本次传输与会话范围通过；**不代表完整科研 Agent 或生产身份系统验收通过**。无效 SMILES 的澄清提示仍过于笼统，旧性质工具的解释文案仍有过度推断，见后续建议。未运行分子生成、RG-MPNN、Vina 或全套黄金科研真实案例。

- 工作树：`scientific-assets`；分支：`codex/agent-isolated-browser-lab`；基线：`242d497`。
- 原始项目工作树的既有改动保持不变；未改生产 app、ChatHandler、主页、模型配置、权限或 main。
- 临时服务只绑定 `127.0.0.1:6012`，验收结束已停止，监听数为 0；临时 SQLite 目录已清除，匹配目录数为 0；测试浏览器页已关闭。
- 未上传密钥、cookie、续接令牌、模型原始响应或临时数据库。提供方凭据仅供临时测试进程使用；报告只记录非敏感结果。
- 未 push、创建累计历史大型 PR 或合并 main。

## 文件范围

新增：

- `src/web/decision_lab.py`：独立 FastAPI 工厂、会话与 WebSocket 边界。
- `scripts/run_decision_browser_lab.py`：显式本地启动器、运行时环境变量、临时数据库生命周期。
- `src/web/static/decision_lab/index.html`、`app.js`、`style.css`：纯文本安全渲染的验收页。
- `tests/agent/test_decision_lab.py`、`test_decision_lab_cli.py`：服务器与启动器回归。
- `tests/decision_lab_ui_test.js`：页面契约和状态机回归。
- `docs/superpowers/plans/2026-09-08-agent-isolated-browser-lab.md`：已批准实施计划。
- 本交接文档。

关键复用/审查文件包括 `src/web/chat_handler.py`、`src/agent/harness/decision_loop.py`、`src/agent/openai_compatible_model.py`、`src/agent/persistence/sqlite_store.py`、`src/agent/persistence/redaction.py`、`src/agent/contracts/decision.py`、`src/agent/tooling/factory.py`、`src/agent/tools/property_calculator.py`、`tests/agent/test_decision_chat.py` 和 `test_decision_loop.py`；它们不属于本轮修改文件。

## 会话与执行契约

- 服务器指定固定验收输入和验收义务；浏览器只传 case ID 或 continuation ID 加补充文本。固定案例不是预先确定的工具执行顺序；实际动作由模型与 harness 决定。
- 服务器生成用户、会话、trace、工具权限与验收条件。严格拒绝浏览器伪造身份、权限、请求类型及未知字段。
- 精确校验 Origin、Host、loopback peer；禁用 proxy headers；无跨域开放或管理员弹窗。
- Cookie 为 HttpOnly、SameSite=Strict、限定路径，有效期 30 分钟；最多 16 个会话，每会话一个活动 WebSocket；每进程最多 128 个新任务，每会话最多 8 个待续接任务。
- 这是 HTTP loopback 临时会话，不是生产登录：没有 Secure cookie、持久化登录或多租户认证承诺。不要绑定公网或放在反向代理后当作生产权限方案。
- 仅真实 `PropertyCalculator` 可用于科研测试；普通聊天无科研工具权限。模型请求最多 4 次、工具尝试最多 2 次，决策循环 90 秒预算。
- `agent_result` 负责显示结果，只有 `complete` 能解除 busy/设置续接；服务器登记续接所有权后才发 `complete`。
- 重连不自动重放；重置撤销旧会话、关闭旧连接并清除页面结果。无效补充文本不消耗已有 continuation。
- 动态文本仅使用 textContent；没有 innerHTML、远程 CDN、API key 输入框、localStorage 或由文本猜测生成的分子卡片。
- 页面保留最新 200 条事件；既有 bridge 的显示脱敏/截断提示会保留，不能把展示摘要当成完整原始审计存档。
- 断开连接取消异步模型任务并释放会话，但不承诺已经启动的同步科学线程或外部物理进程立即停止。

## 真实执行记录

提供方：DeepSeek，模型：`deepseek-v4-pro`，模式：native；直接使用实际浏览器与本地 WebSocket，不以 MockTransport 代替外部模型成功证据。正常路径在最终 UI 版本复验；最后的发送失败清理补丁随后由故障注入测试验证，未重新启动真实服务注入网络故障。

| 场景 | 观察结果 | 证据 |
|---|---|---|
| 普通聊天 | completed；真实模型 1 次请求；0 个工具；完成后按钮解锁 | 最终 UI trace `lab-397b631b0d7a48b198ee04b0bb719dbf` |
| CCO + CCN 性质 | completed；模型 2 次请求；真实 property_calculator；精确 2 个有效唯一 SMILES，指标齐全，demo=false、fallback=false | 最终 UI trace `lab-71842a82464f40fd9262bdb56852f8c1`；证据 `evidence-1b9bc351e516ed4b7312` |
| 缺输入→断线→重连→CCN | waiting_for_input 后重连不自动执行；手动 Resume 后 completed；trace 不变；最终有效分子 1 个、缺失指标 0 | trace `lab-7893328d6c504f28b92939acac67407c`；证据 `evidence-ce28f38693c540bc23d3` |
| 无效 CC(C)(( | waiting_for_input；模型 1 次请求；0 个工具，无分子卡片/模拟性质；未宣称完成 | trace `lab-1f420ed58f0b48a68ec351c5b385a20b`；安全拒算符合预期，但提示明确性仍 partial |
| 执行中断线 | 浏览器先观察 running、Start 禁用，再断开；停止 busy，显示执行状态不确定；可重连，无自动重放 | 实际页面操作；异步取消与会话释放另有确定性回归 |
| 重置会话 | continuation 清空为 None、trace 清空；重新建立连接；不重复旧任务 | 实际页面操作与 Node 回归 |
| 跨会话续接 | 第二个独立 cookie jar 请求被 `continuation_not_owned` 拒绝；原会话仍能 completed 且 trace 一致 | 真实 HTTP/WebSocket 客户端连接运行中的同一服务；trace `lab-98a795adc3b54deb8cac62083ad83c59`；这是协议验收，不冒充第二浏览器 UI 验收 |

性质数值来自工具结构化输出：CCO MW=46.07、LogP=-0.001、TPSA=20.23、HBD=1、HBA=1、QED=0.407；CCN MW=45.08、LogP=-0.035、TPSA=26.02、HBD=1、HBA=1、QED=0.406。这里只证明计算与数据传输，不证明药效或成药性。

初轮聊天 trace `lab-9745d5afdb934ad4965d928c13e15609` 的模型耗时为 2967 ms；本轮没有进行等量三轮稳定性矩阵，不报告不具备统计意义的 p50/p95 或完整科研成功率。临时库已按设计删除，本文保留非敏感验收摘要，不宣称这些 trace 仍可在线查询。

## TDD 与审查修复

独立规格审查和代码质量审查均已完成；对复现问题先补 RED 用例再修复：

1. 旧 cookie 被撤销后，空闲 WebSocket 没有立即关闭：增加撤销信号与并发接收检查。
2. 非法澄清文本过早消费 continuation：先验证补充文本，再取出所有权上下文。
3. 二进制消息触发异常：显式以 1003 关闭。
4. `agent_result` 提前解锁 UI：等待权威 `complete`；可修正输入错误保留 continuation。
5. 发送事件、发送结果或 accept 失败，清理异常留下 active=true 导致永久拒绝重连：accept 纳入 try，drain 任务异常不会阻止 finally 释放会话。三个 OSError 故障注入用例均经历 RED→GREEN，使用同一原 cookie 重连并完成下一任务。

## 实际命令与结果

以下 `python` 均为本机 MedChat Conda Python，未安装新依赖。命令在 scientific-assets 工作树执行。

```text
python -B -m pytest tests/agent/test_decision_lab.py tests/agent/test_decision_lab_cli.py -q -p no:cacheprovider --tb=short
34 passed

python -B -m pytest tests/agent tests/test_agent_decision_model.py tests/test_openai_compatible_model.py tests/test_admin_auth_routes.py -q -rs -p no:cacheprovider --tb=short
2038 passed, 1 skipped, 7 warnings in 68.33s

python -B -m pytest tests/test_agent_anti_hallucination_fallbacks.py tests/test_agent_platform_health_check.py tests/agent/test_real_acceptance_checks.py -q -p no:cacheprovider --tb=short
53 passed, 7 warnings

node tests/decision_lab_ui_test.js
27 passed

node --check src/web/static/decision_lab/app.js
node --check tests/decision_lab_ui_test.js
passed

node tests/home_agent_task_panel_test.js
node tests/home_workflow_completion_behavior_test.js
passed

python -m compileall -q src scripts
passed

python -B scripts/run_agent_acceptance.py --mode contract
34/34 passed

python -B scripts/run_decision_browser_lab.py --port 6012 --mode native
实际启动、真实浏览器/协议验收完成；随后 Ctrl+C 停止
```

跳过项是 `tests/agent/test_harness_shadow.py:277` 的可选性能测试（performance test disabled）。警告来自既有 SWIG 与 FastAPI on_event 弃用，不是科研计算失败。contract 输出位于忽略的 `outputs/agent_evaluation/agent_acceptance_report.json`，只代表 contract，不代表真实科研整套通过。停止测试进程的 PowerShell PTY 返回 1（Ctrl+C）；已另行确认 6012 无监听且临时目录清理完成。

## 使用方式

先在启动进程中配置 `OPENAI_COMPATIBLE_API_KEY`、`OPENAI_COMPATIBLE_BASE_URL`、`OPENAI_COMPATIBLE_MODEL`；密钥只能来自运行时环境或密钥管理器，不要写入源码、报告或启动命令样例。启动器不加载 `.env`。执行上述启动命令后访问 `http://127.0.0.1:6012/decision-lab/`；明确退出服务才能清理临时数据库。

## 下一步建议（未实施）

1. 收敛用户可见的拒绝/澄清原因：保留模型安全请求补充的行为，同时明确指出无效 SMILES，而不是统一泛化提示；增加真实无效输入案例，不通过生成替代分子掩盖错误。
2. 收敛 PropertyCalculator 的旧 `formatted` 解读：真实的 MW/LogP 与 Lipinski 规则不能支撑“口服药物潜力优秀”等结论；红线测试应同时检查结构化数据和工具解释文案。现有脱敏还可能误遮罩普通中文片段，需单独回归。
3. 在独立任务设计生产身份与授权边界、用户输入到验收义务的转换、持久化会话和运维清理，再讨论接管生产入口。当前固定验收页不得直接开放为通用生产 Agent。
