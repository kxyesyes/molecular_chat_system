# A2 第一批：有界模型决策循环实施计划

> **For agentic workers:** Use executing-plans and test-driven-development; request an independent code review before delivery.

**Goal:** 在隔离入口实现模型 → 受控单步执行 → 已校验观察 → 模型的实际循环，不调用静态 Planner。

**Architecture:** 复用 LangGraph 0.2.76 的 StateGraph 和 WorkflowRunSession 的单步网关，不复制适配、校验、证据及 checkpoint 管线。增加显式动态会话模式，旧固定会话默认行为不变。AgentStateStore 是唯一持久化来源。

**Tech Stack:** Python / Pydantic 2 / LangGraph / SQLite / pytest。

## 范围与退出条件

这是 A2 的第一个可测试增量，不是整个 A2/A3 完成。先允许四种只读科学工具及普通聊天；由可信调用方给出 request_kind、allowed_tools、required_tools。模型只能引用原始用户输入，不能创建或修正科学输入。跨观察字段绑定、澄清后恢复、真实外部模型验收和 Web 接线留到下一增量；当前对已存在 trace 明确拒绝重放，不能重置预算。

科学 finish 不显示模型自由正文，而显示已校验工具输出及失败状态；必须满足调用方要求，缺项不能成功。工具顺序由模型逐轮选择，不由 required_tools 集合规定。未校验 demo/fallback 不当成科研成功。默认 16 次决策、12 次工具预算、300 秒总时限。预算按 ToolSpec 最大尝试数保守预留；本批实际调用通过请求局部视图关闭 adapter 自动重试，避免任务结束后的新调用；旧入口默认重试行为不变。不启用生产模型、不改默认入口、不合并 main。

## Task 1：共用动态单步会话

Files: `src/agent/runtime/run_session.py`, `src/agent/persistence/{base,sqlite_store}.py`, `tests/agent/test_decision_loop.py`。

- [x] RED：空动态会话 start 后可 append 一步；执行后不终止；旧固定会话行为保持回归覆盖。
- [x] RED：相同 trace 的两个独立 store 连接不能覆盖已存在 run。
- [x] GREEN：添加显式 dynamic 选项、append_step / finish_dynamic 生命周期 API；单步执行仍沿用全部原有管线。
- [x] GREEN：`start_run(run, *, exclusive=False)`，exclusive 使用 INSERT 冲突报错，默认保留旧 upsert。
- [x] VERIFY：聚焦用例和旧 session/checkpoint 回归。

核心测试形态：

```python
session.start()
session.append_step(step)
assert not session.execute_step(0).terminal
assert session.results[0].quality['evidence_id']
```

## Task 2：有界动态 StateGraph

Files: `src/agent/harness/decision_loop.py`, `src/agent/harness/decision_policy.py`, `src/agent/harness/decision_execution.py`, `src/agent/tooling/adapters.py`, `tests/agent/test_decision_loop.py`。

- [x] RED：同一 query 遇到不同观察，后续模型选择不同动作；patch TaskPlanner.plan 抛错仍可执行。
- [x] RED：越权工具、模型编造参数、跨 trace evidence、未完成 required_tools、demo、预算耗尽均不能成功；重复动作只能复用已有观察。
- [x] GREEN：使用 `StateGraph` 的 decide / tool 两节点和条件边；单一 request-local 会话承载状态。
- [x] GREEN：工具目录从 registry 提取，服务端限制四工具与 request capabilities；arguments 只允许 `{"input_ref":"user"}`，服务端绑定原始 query。
- [x] GREEN：每轮保存预算/phase/decision ID；native assistant call 与对应 tool observation 成对反馈，JSON 模式显式选择，不隐式退回 Planner。
- [x] GREEN：科学输出只渲染真实观察；finish/clarify/cancel/timeout 仅产生一次终态。超时后线程状态不明则记录，不再次派发。

核心测试形态：

```python
result = asyncio.run(loop.run(context, request_kind='scientific',
    allowed_tools={'property_calculator'}, required_tools={'property_calculator'}))
assert result.metadata['backend'] == 'model_decision_loop'
assert result.tool_results[0].provenance.input_digest
```

## Task 3：回归、审查与交接

- [x] `python -B -m pytest tests/agent/test_decision_loop.py -q -p no:cacheprovider --tb=short`：40 passed。
- [x] `python -B -m pytest tests/agent tests/test_agent_decision_model.py tests/test_openai_compatible_model.py -q -rs -p no:cacheprovider --tb=short`：1784 passed、1 skipped。
- [x] `python -B scripts/run_agent_acceptance.py --mode contract --output outputs/agent_evaluation/decision-loop-contract.json`：34/34，旧契约无回归。
- [x] `python -B -m compileall -q src scripts` 和 `git diff --check`。
- [x] 独立代码审查，先复现再修复有效发现；最终复审确认本批无剩余阻断。
- [x] 新增 `docs/handoff/agent-decision-loop.md`，记录测试替身与真实工具区别、剩余 A2 范围；以本任务文件形成独立提交，不发布历史大 PR。

上述 python 命令使用现有 MedChat Conda 环境。测试不用真实 API key；不读取 .env。

## 实施与审查记录

2026-09-08：Task 1/2 已实现；先观察 17 项 RED，再逐批 GREEN。补充 HTTPX MockTransport 与真实 RDKit 集成（native / JSON）、失败观察持久化、事件调用身份、工具配置变化与结果身份检查。

独立审查复现重复取消、事件/存储异常中断收尾、超时后内部重试三个问题。新增故障注入 RED 后修复：

- 每次工具只允许一次真实尝试；请求视图不修改共享 adapter。
- 取消等待当前 journal 收尾，重复取消不提前释放会话；底层线程仍可能运行，不宣称计算进程已停止。
- 用既有事件 ID 重试通知日志交付，回调仍最多一次；单步持久化重试沿用 journal，不能重新调用已尝试工具。
- 无法完成 action journal 时显式 runtime abort，不包装成功，不自动恢复未知执行。

当前验证记录见 `docs/handoff/agent-decision-loop.md`。未启用网页入口，未执行真实外部模型请求。
