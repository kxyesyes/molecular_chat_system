# Web Agent partial 展示 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** 让部分完成的科研任务保留成功结果、可见失败步骤，不被显示为全成功或丢成全失败。

**Architecture:** 在 ChatHandler 的返回信封边界归一化展示状态，partial 直接发送安全工具正文与有界失败摘要。复用既有脱敏、候选校验和任务清理机制，不修改执行层语义。

**Tech Stack:** Python、FastAPI/WebSocket、pytest、现有原生 JS 首页（不新增 UI）。

用户已于本轮确认同日书面设计，进入 TDD。隔离分支基线 `ee007a1`；不推送、合并本批或调用真实模型。

## 文件责任与实现边界

- 修改 `src/web/chat_handler.py`：只增状态判定与部分完成投影/发送方法，接入现有结果分支。
- 新增 `tests/agent/test_chat_handler_partial_results.py`：专项矩阵及真实 Supervisor 集成复现。
- 既有 `tests/agent/test_chat_handler_agent_events.py`：优先复用 FakeModel/FakeWebSocket/FakeRagService，不大规模搬测试。
- 新增 `docs/handoff/web-agent-partial-presentation.md`：记录 RED/GREEN、审查与残余事项。
- 纯投影先作为 ChatHandler 局部方法，禁止拆整类或新建通用结果框架。

## Task 1：Web 信封端到端最小修复（测试/实现是同一有界任务）

- [ ] 先写独立 partial 信封测试。基础输入包括 success 两值、status=partial、final_answer 中
  固定工具正文、一个成功观察和一个失败观察，以及同一个 workflow_plan；合成值不宣称真实科研预测。

```python
result = {
    "success": True, "status": "partial", "partial": True,
    "trace_id": "partial-test", "final_answer": "已完成的工具正文",
    "tools_used": ["property_calculator", "candidate_ranker"],
    "workflow_plan": {"workflow_name": "target_driven_design"},
    "tool_result_sequence": [
        {"step_id": "properties", "tool_name": "property_calculator",
         "success": True, "status": "succeeded", "formatted": "已完成的工具正文"},
        {"step_id": "ranking", "tool_name": "candidate_ranker",
         "success": False, "status": "failed", "message": "ranking unavailable",
         "error": {"code": "internal_error", "message": "ranking unavailable"}},
    ],
}
```

真实运行 `ChatHandler._process_message`，捕获 FakeWebSocket；核心断言：

```python
completed = [message for message in websocket.messages if message["type"] == "complete"]
assert len(completed) == 1
assert completed[0]["status"] == "partial"
assert completed[0]["partial"] is True
assert "已完成的工具正文" in completed[0]["content"]
assert "部分完成" in completed[0]["content"]
assert completed[0]["failed_steps"][0]["tool_name"] == "candidate_ranker"
assert model.generate_calls == 0
```

- [ ] 用下述隔离 runner 跑专项文件，保存真实 RED 数量及失败原因。测试导入/setup 错误不算 RED。
- [ ] 扩展矩阵：success 两值、无 status+partial=true、completed+partial=true、显式终态
  failed/rejected/cancelled+success=true、未知 status、畸形 success；有无计划、总结开关两值。
- [ ] 测试有界字段、同工具不同 step_id、sequence 优先不重复 fallback、skipped_steps、无正文、
  缺原因、凭据与路径污染、超限摘要、complete/历史一致、候选仍受现有校验约束。
- [ ] 最小实现状态判定，允许状态 completed/partial/failed/rejected/cancelled；显式终态优先。
  状态 missing 时 partial 必须严格 true，否则沿用 success。未知/非字符串状态走 failed。
  将原 `if agent_result.get('success')` 调整为规范状态分支，不改 Supervisor 字典。
- [ ] partial 处理前保持真实事件队列 drain；调用现有 `_send_molecule_candidate_events`，不替代
  CandidateSet 校验。组装有界失败摘要，发送 agent_result 后 complete，写同内容 history 并 return。
  不走 LLM/legacy RAG；保留外层 finally 的 finish_on_cancel。
- [ ] 复用 `_sanitize_agent_failure_text`/`_sanitize_agent_warnings`；新方法只投影设计列明字段，
  error 限 code/message，不把 details 或原始输入放入 UI。超限明确文字提示摘要被截断。
- [ ] 新方法的测试须先失败；实现后重跑专项与既有 ChatHandler 测试，修复回归而非放宽断言。
- [ ] 加入实际 Supervisor/Session 的 candidate_ranker 失败测试：合成工具进入真实执行路径，
  断言 tool_failed 存在、partial 正文有排序失败、工具成功正文仍在、无额外模型调用。
- [ ] 规格审查通过后做质量审查；任何重要反馈先补可复现测试再修复，复审通过后进入联合回归。

## Task 2：联合验证与本地交付

- [ ] 运行完整 tests/agent 和请求生命周期等23路径（列表见 ReAct/MolecularAgent 交接），
  加跑 `tests/home_workflow_completion_behavior_test.js`、`tests/home_agent_task_panel_test.js`。
- [ ] 隔离断网 contract 报告须读取 status，不能仅检查脚本退出码。
- [ ] 内存 compile src/scripts 与 git diff --check；精确扫描任务文件凭据候选，仅输出命中数量。
- [ ] 写交接，区分合成工具集成与真实模型/浏览器验收；记录首次失败、警告、跳过与未完成事项。
- [ ] 只暂存本批文件，本地 Conventional Commit；推送或合并需另外授权。

## 精确隔离命令

复用已验证临时配置/数据库 runner；测试运行不读取生产凭据、权重或索引。

```powershell
$python='C:/Users/xkx52/.conda/envs/MedChat/python.exe'
$plan=Get-Content docs/superpowers/plans/2026-09-24-rag-service-extraction.md -Raw
$m=[regex]::Match($plan,'(?s)\$runner = @''\r?\n(.*?)\r?\n''@')
if(-not $m.Success){throw 'Isolation runner missing'}
$runner=$m.Groups[1].Value.Replace('D:/MedChat/molecular_chat_system_worktrees/rag-service-extraction-pr','D:/MedChat/molecular_chat_system_worktrees/web-agent-partial-presentation')
$focus=@('tests/agent/test_chat_handler_partial_results.py','tests/agent/test_chat_handler_agent_events.py')
$runner | & $python -B -c "import sys; exec(sys.stdin.read())" @focus
if($LASTEXITCODE -ne 0){throw 'Focused tests failed'}
```

runner 内部固定 `-q -p no:cacheprovider --tb=short -rs`，传参只允许仓库相对测试路径或 node ID。
不要把 pytest flags 当作路径追加。RED 用非零退出码保留失败证据；GREEN 必须为零。
联合验证不能将前批 ReAct 的结果冒充本批结果。
