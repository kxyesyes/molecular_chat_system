# T02：委派与聊天入口的科学终态一致性

## 范围与基线

- 日期：2026-09-20；基线 `d89a48b`；分支 `codex/delegated-outcome-parity`。
- 独立工作树：`D:/MedChat/molecular_chat_system_worktrees/delegated-outcome-parity`。
- 已读取任务书 T02、AGENTS.md、PROJECT_STANDARDS.md、契约/委派/Session/工具适配/
  验证器/检查点/API 路径及相关测试。
- T01 留在 `codex/rag-row-mapping-parity` 工作树，未覆盖或混入本批；原始工作树未改动。
- 不推送、建 PR、合并、部署、重启服务或调用真实外部模型。未读取凭据。

## 复现与规则

实际 `SupervisorAgent.run()` 与 `execute()` 使用相同合成 ToolResult，经真实工具适配、
执行、事件与临时 SQLite 持久化：

- `success=True + PARTIAL`：原委派返回 succeeded，聊天返回 partial，但两者观察落库均
  被写成 succeeded。现在统一 partial，终态事件/摘要/持久化不再提升为完整完成。
- `success=False + PARTIAL`：沿用既有保守契约，若没有其他成功观察，运行整体 failed；
  原观察仍为 partial，原数据、格式化内容、warnings/evidence/artifacts 及来源均保留。
  真实 ActivityPredictorTool 模块测试只替换预测服务边界，证明分类阶段结果不会丢失。
  这不是实际权重或科研预测验收。
- 全部明确成功且无结构化错误才能 completed（对外 run 拼写仍为 succeeded）。
- 混合成功/失败沿用现有 partial 规则；必需失败停、可选失败按原策略继续；成功的
  partial 可继续下游，但最终不能 completed。取消/拒绝观察和原因保留。
- 空计划仍 failed；run 的既有 result=null 信封不变。非法状态和矛盾 success/error
  降级失败，不能输出完整成功文案。错误没有因格式化或回调而消失。
- 聊天外层 `success` 仍表示有结果可展示，HTTP `success` 仍表示请求接受；科学判定
  使用内部 result.status/success，不改变所有外部布尔字段的兼容语义。

## 实现

- `src/agent/contracts/result.py`：共享汇总排除含结构化错误的成功观察。
- `src/agent/supervisor.py`：委派复用共享汇总；fresh/reused 均经过既有科学验证器与
  candidate alignment；终态事件带科学结果，输出 warnings/evidence/artifacts；持久化
  保留原始观察状态与失败数据。未重写委派循环或迁移到另一运行时。
- `src/agent/specialists/base.py`：子任务状态使用共享汇总，而不是只看布尔值。
- `src/agent/orchestrators/workflow.py`：共享状态文案/观察落库状态；partial 成功观察
  可复用，但保持 partial；检查点恢复拒绝显式失败、错误或负面状态，原版本/输入/
  工具身份检查不变，损坏缓存仍有警告并重跑。
- 缺完整观察或显式成功标志的旧检查点不再默认成功；外层 partial 不被内层 succeeded
  提升。复用后重验证若改变状态/错误，会追加保存降级观察，保留原历史、不重跑科学工具。
- `src/agent/runtime/run_session.py`：失败观察完整保存在执行记录与检查点中，不丢数据。
- `src/agent/validators/result_validator.py`：统一降级矛盾布尔/状态/错误，未知状态明确失败。
- `src/agent/tools/base_tool.py`：旧 dict 适配保留显式状态和部分数据，不吞掉结构化错误。

## 测试文件

- 新增 `tests/agent/test_entrypoint_outcome_parity.py`：68 项跨入口/事件/持久化/API/
  家族部分结果/候选覆盖/检查点/字典适配/空计划回归。
- `tests/agent/test_supervisor_delegation.py`：旧 fixture 返回非列表候选性质，现应 partial
  并在必需步骤停止；保留 count 传递及委派断言，新增失败原因断言。
- `tests/agent/test_decision_spec_findings.py`：矛盾错误观察现在发 tool_failed；攻击性回调
  测试跟随该事件，继续验证 seal 之前/之后的错误不能被抹除。
- `tests/agent/test_dynamic_run_session.py`：未指定 outcome 的矛盾观察现在自动 failed；
  显式请求 completed 仍拒绝，显式 partial 行为不变。
- `tests/agent/test_supervisor_runtime_integration.py`：完整成功/幂等测试改用 3 个合法合成
  候选及对齐的下游列表，保留完整成功与只执行一次的断言，不再使用不符合契约的占位字典。
- `tests/agent/test_workflow_resume.py`：手写恢复 fixture 补充明确 success=true；缺失标志
  的反例由新增测试验证 fail closed。

## 实际命令与结果

Python 均使用 `C:/Users/xkx52/.conda/envs/MedChat/python.exe`，pytest 参数为
`-B -m pytest ... -q -p no:cacheprovider --tb=short`。

| 阶段 | 命令/范围 | 结果 |
|---|---|---|
| 基线 | supervisor_delegation / supervisor_agent / workflow_run_session | 122 passed |
| 初始 RED | test_entrypoint_outcome_parity.py | 17 failed、1 passed |
| 初步 GREEN | 上述基线 + 新矩阵 | 140 passed |
| 矛盾状态 RED | 新矩阵扩展 | 8 failed、28 passed |
| 首次 Agent 回归 | tests/agent | 6 failed、3281 passed、2 skipped；已逐项处理而非隐藏 |
| 旧字典状态 RED | 新矩阵 -k legacy_dictionary | 8 failed |
| 域/委派联合回归 | 新矩阵 + family_activity_tool + supervisor_delegation | 231 passed |
| 复审问题 RED | 缺失/矛盾检查点、重验证持久化、后备格式化 | 6 failed 与 4 failed，分别修复 |
| 最终聚焦 | 新矩阵 + workflow_resume + supervisor_runtime_integration | 86 passed（包含 68 项新矩阵） |
| 最终联合回归 | tests/agent + tests/test_phase2_phase3_routes.py + tests/test_agent_anti_hallucination_fallbacks.py + tests/test_agent_platform_health_check.py（另加 -rs） | exit 0；3356 passed、2 skipped、7 warnings；130.44 秒 |
| 编译/前端兼容 | compileall -q src scripts；node tests/home_agent_task_panel_test.js；node tests/frontend_safe_render_test.js；git diff --check | 全部 exit 0 |

最终联合回归在最终代码快照上重新运行；设置 `MEDCHAT_RUN_FAMILY_REAL_ACCEPTANCE=0`。
两项 skipped 分别为本机不可用的目录符号链接测试、默认禁用的性能测试；不算通过。
7 个警告为既有 SWIG / FastAPI 生命周期弃用警告。独立只读复审已确认无剩余阻断问题，
最终回归通过后未再修改业务代码。本批未重跑全仓 `pytest tests`，也未进行真实模型验收。

## 后续

- T03：实际长历史/RAG 场景复现提示词头部截断，分区预算保留当前用户问题和约束。
- T07 的完整委派执行 Session 迁移未实施；本批只修状态、验证、来源与持久化边界。
- 本批尚未提交；T01/T02 是两个独立待审查工作树，不代表 main 已具备这些修复。
