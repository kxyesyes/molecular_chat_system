# T09 第三增量：故障恢复与浏览器验收

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. 测试先行；复现缺陷后仅作最小修复，独立规格及质量审查。

**Goal:** 验证跨轮科研引用在取消、故障、重放及浏览器刷新/服务重启时保持归属、科学来源和实际输入一致。

**Architecture:** 基于已批准的科研引用设计及 `212e454`，复用实际 ChatHandler、Supervisor、SQLite、首页 JS 和既有会话中间件。只增加离线测试与显式 loopback 浏览器测试夹具；不建立新生产执行入口，不调用外部模型，不修改数据库 schema。

**Tech Stack:** pytest、临时 SQLite、FastAPI、Uvicorn、Node、可用浏览器自动化工具。

分支 `codex/scientific-reference-continuity`；原始工作树保持不动。当前授权仅本地实施与测试，不推送/合并/部署。

## Task 1：故障、取消、持久化重放

新增 `tests/agent/test_scientific_reference_resilience.py`，复用现有测试的 `confirmed`/`Tool` 合成夹具。

- [x] 先核对现有 store/web/execution 测试，避免复制已有单元断言。
- [x] 使用实际 ChatHandler + Supervisor + SQLite 验证两次新实例执行同一引用，成功步骤不重复调用，来源有效期不续期。
- [x] 用线程事件控制工具在执行中暂停，取消等待方后释放；断言任务最终排空、工具最多调用一次、来源记录不被覆盖。
- [x] 在实际路由/服务上注入存储读写异常或锁，断言不报告 confirmed、恢复不返回科研结果，释放后原视图可恢复。
- [x] 验证来源失败/取消/损坏或恰好到期时，真实 chat 路径不调用科学工具且明确澄清；恢复保留 partial/warnings。

测试结构示例（沿用已存在的 helper）：

```python
store, svc, pointer = confirmed(tmp_path)
selected = svc.resolve('计算属性', pointer, {'ordinal': 2}, session_id='owner', enable_tools=True)
first = SupervisorAgent(tools=tools, state_store=store).execute('计算属性', session_id='owner', resolved_molecule=selected)
reopened = SQLiteAgentStateStore(store.db_path)
again = SupervisorAgent(tools=tools, state_store=reopened).execute('计算属性', session_id='owner', resolved_molecule=selected)
assert first['trace_id'] == again['trace_id']
assert tools['property_calculator'].calls == ['CCN']
```

若新测试首次通过，记录为现有行为验证，不制造 RED。若失败，记录实际错误、用系统化调试定位后才修改相关生产边界，不扩大重构。

## Task 2：真实浏览器离线夹具及首页恢复

新增 `tests/scientific_reference_browser_lab.py` 和对应 `tests/agent/test_scientific_reference_browser_lab.py`。
夹具只使用临时运行目录、合成候选及离线工具替身；挂载仓库真实首页模板/静态文件、ChatHandler、会话中间件、引用 API。

- [x] 先验证夹具启动不加载用户配置或任何真实模型，HTTP→WebSocket→引用 API 的身份一致；测试夹具与生产入口明确隔离。
- [x] CLI 仅监听 `127.0.0.1`；通过指定临时目录支持停止后重新启动，默认日志不记录消息/凭据。
- [x] 浏览器实际输入生成请求、看到挂载卡片/确认状态，输入“第二个”并捕获服务端实际工具输入。
- [x] 刷新和同目录重启后恢复原集合，不重新生成；清除后刷新不恢复；新标签页不凭用户最近 run 自动恢复。
- [x] 对浏览器发现的问题先补实际 Node/模块失败回归，再做最小修复。若浏览器不可用，记录阻断，不把假DOM当真实浏览器通过。
- [x] 停止临时服务、关闭自建标签页；6017无监听。临时数据目录删除被工具策略拒绝，未绕过，未宣称全部清理；遗留路径见交接。

不向正式 app 添加测试控制端点。夹具中的离线回答、合成候选、计数器不代表真实生成模型或科研性能验收。

## Task 3：整合、文档和审查

- [x] 更新 `docs/AGENT_MAINTENANCE.md` 的科研续接定位和限制，不改写历史合并事实。
- [x] 新增 `docs/handoff/scientific-reference-acceptance.md`：覆盖矩阵、实际命令、RED/GREEN、浏览器步骤、skip/限制和清理证据。
- [x] 独立 SPEC→QUALITY 审查；修复后增量复审。
- [x] 冻结本批源码与测试，复用既有隔离 runner 运行全部 `tests/agent` 和五个 Web 模块；5609 passed、7 skipped、7 warnings，402.86s；7路径哈希一致。五个首页Node、三个JS语法、临时字节码编译和diff通过。
- [x] 精确暂存11路径、本地提交；分支和提交通过交接及本轮回复记录。CI、PR发布、具体合并授权仍是后续门禁，不因本地验收完成自动执行。

## 验证命令

隔离 runner 来自 `docs/superpowers/plans/2026-09-24-rag-service-extraction.md`，仅替换repo为当前工作树；清除模型环境并隔离配置/数据库，Python使用MedChat Conda环境。

```powershell
$runner | & $python -B -c "import sys; exec(sys.stdin.read())" tests/agent/test_scientific_reference_resilience.py tests/agent/test_scientific_reference_browser_lab.py
$runner | & $python -B -c "import sys; exec(sys.stdin.read())" tests/agent tests/test_agent_session.py tests/test_agent_session_entrypoints.py tests/test_agent_task_ownership.py tests/test_phase2_phase3_routes.py tests/test_web_app_lifecycle.py
node tests/home_scientific_references_test.js
node tests/home_structured_molecule_render_test.js
node tests/home_agent_task_panel_test.js
node tests/home_workflow_completion_behavior_test.js
node tests/frontend_safe_render_test.js
git diff --check
```
