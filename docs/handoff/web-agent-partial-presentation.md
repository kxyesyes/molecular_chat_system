# Web Agent partial 展示修复交接

日期：2026-09-24。基线 `ee007a1`，独立分支 `codex/web-agent-partial-presentation`。
设计 `f25e4e6` 已获用户书面确认，实施计划 `c893e62`；实现尚未提交或发布。

## 问题与行为

此前真实 Supervisor/Session 经合成工具执行后，聚合 status=partial 仍因兼容 success=true
显示完整成功提示；success=false 的 partial 则进入全失败通道。成功正文没有失败步骤摘要。
事件流中的 tool_failed 与结构化观察仍存在，不能把问题描述为整个系统伪造了科研结果。

本批仅在 ChatHandler 投影：规范终态优先；partial 保留已完成正文、显示未完成步骤，
complete 携带 status/partial/trace_id/warnings/error/failed_steps；不调用主模型总结或
legacy RAG 补救，不修改 Supervisor/Session 状态、模型来源、工具校验或科学计算数值。
候选卡片沿用 CandidateSet 校验；取消/断连沿用 finally 的在途任务清理。

## 文件清单

- `src/web/chat_handler.py`：最小展示状态、partial 脱敏投影、终态发送。
- `tests/agent/test_chat_handler_partial_results.py`：新增专项矩阵和实际 Supervisor/Session 集成。
- `tests/agent/test_chat_handler_agent_events.py`：一个 Agent 信封 fixture 从观察层 succeeded
  改成规范运行层 completed；观察本身状态不变。
- `tests/agent/test_chat_input_budget.py`：partial 改为直出验收，同时保留 completed 路径的实际
  模型输入预算、来源/警告及 interpretation_budget_exceeded 门禁；直接 prompt helper 的
  partial 来源约束测试不变。
- 同日设计、实施计划、本交接：本批审计证据。

## TDD 与命令

在上述工作树使用实施计划完整 PowerShell 隔离 runner，Conda MedChat Python -B，内部
pytest 参数 `-q -p no:cacheprovider --tb=short -rs`。临时配置/数据库与生产资产隔离。

- 首次专项 RED：30 failed、14 passed，exit1。
- 科学正文/跳过 metadata RED：3 failed、46 passed，exit1。
- 畸形观察 RED：1 failed、52 passed，exit1。
- 两文件聚焦 GREEN：129 passed，8.21s，exit0。
- 一次 Windows pytest 参数 ID 路径过长是 setup 错误，不计为有效 RED；缩短测试 ID 后重跑。
- 扩展预算测试首次 6 failed、43 passed；三文件合跑首次 6 failed、172 passed，exit1。
  原因是旧用例要求 partial 调用模型总结，与已批准新规则冲突，未用放宽科学断言绕过。
- 对齐新规则并保留 completed 预算覆盖后，三文件 **182 passed，10.25s，exit0**。

三文件路径为上述三个测试文件；完整23路径联合回归由协调者另行收齐，不把聚焦通过
等同于整批完成。断网 contract 结构化报告 passed；首页 Node 两脚本均通过：

```powershell
node tests/home_workflow_completion_behavior_test.js
node tests/home_agent_task_panel_test.js
```

## 审查与未完成

规格审查、随后质量审查以及完整联合回归尚在进行；未完成前不能宣称最终通过。
未调用真实外部模型、权重、生产数据库或真实分子生成服务；未进行浏览器端到端验收。
没有改 JS 或前端架构；默认前端使用 complete.content，所以限制说明必须写在正文中。
本批只获本地设计/TDD实施授权，未推送、未创建PR、未合并或部署。
T09、其他职责拆分及生产入口切换不在本批范围；ReAct已单独发布draft PR56，未合并。

## 首轮规格审查反馈

独立规格审查未通过：partial 提前返回漏掉已经成功的 RAG 工具卡片及 source_index/provenance，
违反保留既有来源通道的要求。审查者独立三文件加取消/模型租约节点185passed，但这不能替代
未覆盖分支的正确性。已要求先补 rag_search/rag_database_search × success 两值 RED，再复用
既有卡片投影；禁止新增检索/模型调用。质量审查尚未启动，不将首次审查写成通过。

首次23路径联合回归：3 failed、5544 passed、9 skipped、7 warnings、9 subtests，309.26s，
exit1。两项是 test_chat_local_cleanup 仍把 partial 当模型总结路径；将用 completed fixture
保留原预算终态/历史只写一次断言。另一个 Windows docking 子进程测试未生成 child_pid_file，
相关源码和测试均未修改；单独原样复测1passed、5.82s、exit0。首次失败保留，原因尚未定位，
不声称本批修复了对接进程时序，也不增加超时或删除断言。

RAG保留新增12例（两alias×两success×rag_count 0/1/5）实际RED：12failed、182passed，exit1；
全部因缺少rag_info失败。进一步修复与复审尚待收齐。

RAG/预算清理测试联跑RED 14failed、201passed后，复用 `_send_agent_rag_results` 给 completed
和partial共同投影既有检索结果，history保留完整数量；没有新检索/模型调用。四文件GREEN
215passed、11.46s；独立规格复审APPROVED，独立四文件215passed、11.92s，来源问题已关闭。
质量审查和修复后的联合回归进行中；预算清理fixture调整增加 `test_chat_local_cleanup.py`
到本批文件清单，不删除原三字段终态与历史仅写一次保护。

RAG修复后23路径联合回归 **5559 passed、9 skipped、7 warnings、9 subtests passed，329.76s，
exit0**；仍待质量审查，不能用此替代最终审查。9项跳过分别为Windows符号链接权限2、
显式关闭性能测试1、POSIX目录权限2、双任务库/配置runtime条件3、POSIX进程组1。
同一最终生产代码断网contract报告passed、306源文件内存compile、两项Node首页回归及
diff检查均通过。首次对接子进程失败保留，不将重跑通过说成根因已修复。

## 首轮质量审查反馈

质量审查未通过，三个合成输入可复现问题：新增partial元信息沿用旧文本sanitizer时漏拦
Cookie/Set-Cookie/refresh_token；warnings及顶层error仅在结构化字段，前端只读content而
不可见；可选RAG元信息畸形会把其他有效partial正文降成通用failed。已退回按TDD逐项修复，
不改全局状态或前端，不吞WebSocket发送/取消异常，不新增检索。独立215聚焦+3生命周期
测试虽通过，不能证明这三个遗漏分支正确；上述5559仅代表修复前已收齐的一轮联合结果。

质量反馈修复：新增回归RED 24failed、68passed；补原始值凭据守卫（不改变其他消费者的
全局旧sanitizer），把安全warnings/error写入正文，逐条隔离畸形RAG记录且send留在恢复catch
之外。四文件GREEN244passed；取消/模型租约套件37passed、7弃用警告。通用后处理失败用例
仍保留原断言，故障注入点移到非可选RAG处理。质量复审和最后23路径回归正在执行。

独立质量复审APPROVED，三项问题已关闭；独立四文件244passed、13.42s，模型请求生命周期
37passed、7弃用警告、4.31s。所有本批审查进程已结束，无未解决审查意见；最后完整联合
回归仍在进行，不以独立聚焦测试代替。6个未提交任务文件凭据候选扫描零命中。

## 最终本地交付

最终生产版本23路径隔离联合回归：**5588 passed、9 skipped、7 warnings、9 subtests passed，
292.03s、exit0**。9项跳过仍为上述平台/显式opt-in/运行条件原因。最终代码的断网contract
报告passed、306源文件内存compile、git diff --check通过；首页两项Node测试通过，未修改JS。
没有仍在运行的测试或审查进程。规格审查与质量复审均通过，已关闭所有本批审查问题。

精确提交本批源文件、4个测试文件、设计/计划/交接共8个文件；不包含原始工作树的13项
历史改动或任何生产资产。只做本地提交，不推送、不创建本批PR、不合并、不部署或启用模型。
当前实际入口仍为原应用；要在运行服务验证本次改动，需后续明确选择集成与部署范围。

后续建议：经授权发布本批draft PR；PR56已完成CI 7/7但仍需单独合并授权。另行调查共享CI
root首次超时与Windows对接子进程偶发失败；本批没有消除这两项已知不稳定性。T09与剩余
职责拆分仍未完成，不把本次局部展示修复称为整个任务书完成。
