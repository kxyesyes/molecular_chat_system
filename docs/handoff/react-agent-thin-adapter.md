# ReAct 兼容入口迁移检查点

日期：2026-09-24。分支 `codex/react-agent-thin-adapter`，基线 `50bf8a7`（PR #55）。
设计 `e3db6c9` / `9e1ce18`，实施计划 `001ad4f`。历史失败过程保留，最新结果见末节。

用户已明确同意书面设计并进入 TDD。当前修改只在独立工作树，不覆盖原始项目的历史改动。
不启用真实模型、不读取生产密钥/权重/数据、不部署。

## 实际复现

`tests/agent/test_react_agent_adapter.py` 通过实际 constructor/execute，只替换领域工具工厂
与合成模型，网络阻断。首次10failed中2项 demo 测试误用旧实现不支持的字符串 active_skill；
改为真实 WorkflowPolicy 后重跑 **10 failed，0.97s，exit1**，确认真实行为违反：

- canonical ToolResult 被旧 `.get()` 路径转成属性错误；失败字典丢 warnings/error；
- 有主模型时失败仍 success=true；明确 demo 的合成活性也会成功；
- 实际 Router 要求澄清时，Supervisor/ReAct 对缺 receptor/box 请求仍调用 docking 工具；
- 无效 SMILES 的输入错误可能被工具缺失错误覆盖。

扩展规范投影/状态/请求参数测试后 **23 failed，1.04s**。随后实现薄适配，状态与来源
相关用例转绿，**17 passed / 6 failed，0.93s**。失败未删除或包装为成功。

## 共享缺口的进一步定位

1. Supervisor._resolve_policy 使用 route()，完整 decision.requires_confirmation 丢失。
   已请求最小共享 execute 拒绝边界修复，未收到确认前不改共享代码或在 ReAct 复制门禁。
2. ActivityResultValidator 只将 pic50/activity_score 当作旧活性声明，漏识别 pIC50。
   同一个 demo fixture 改字段名，在 ReAct/Supervisor × 有无合成模型中实测
   **4 failed / 8 passed，0.94s**：仅 pIC50 四项失败。说明迁移并不能修掉这一共享缺口。
   已请求补齐共享字段识别；不改算法、阈值或权重，未确认前保留失败证据。

诊断曾将 `-k demo` 传给只接受路径的隔离 wrapper，退出4且没有运行测试；已改用完整
pytest node id 正确复现，不将错误命令当作测试通过。

## 当前实现

`src/agent/react_agent.py` 保留公共构造/execute/set_llm/should_use_tools/format_result、
包导出和 ReActStep。保留工厂及借入工具，去除私有 action 文本循环、关键词回退与直接
tool.execute。每请求创建轻量 Supervisor，数量哨兵及温度/事件回调原样传递。

输出以 AgentResult.to_legacy_dict 为真源，旧名称映射与有序重复序列并存；完整成功、
partial、rejected/cancelled 不压平，tools_used 只记录真正开始的调用。主模型不替换
专用分子生成模型，format_result 不执行模型。无请求参数留存在 self.current_*。

新用例追加真实 LLMMolecularGenerator + 合成记录模型，验证 0.23/0.81 温度、输入摘要
区分与事件回调不重复；非法计数、旧格式、异常不重试、不关闭借入资源亦验证。
当前新文件整体 **35 passed / 8 failed，1.14s，exit1**；八项均为上述共享缺口。

## 测试迁移与完成门禁

六份既有科学测试正在迁移到真实公开构造/执行，保留计数/引用否定/原始靶点/模型隔离。
尚未完成迁移复核、全量联合回归、离线contract、最终独立双审，不能声称本批完成。
命令使用 docs/superpowers/plans/2026-09-24-react-agent-thin-adapter.md 的隔离 runner；
本检查点分别传新文件全路径或 `tests/agent/test_react_agent_adapter.py::test_demo_claim_is_rejected`。

PR #54/#55 为此前独立交付，不包含此处未提交的 ReAct 变更；不能混为本批 CI。

## 迁移测试完成后的聚焦检查点

六份测试迁移者：189passed基线；父适配器到位后的迁移RED为25failed/164passed；
迁移后206passed、0skipped。父代理核对diff：私有调用改公开构造；生成夹具返回对应
数量有效唯一SMILES，原本只提供CCO的下游结果显式断言partial及缺失候选，不再
将它当作整体成功。原始靶点、模型隔离、否定引用与别名授权断言保留。

额外增加无匹配trace_id回归，先1failed/1passed，补空证据边界后2passed0.85s。
直接ActivityResultValidator三种字段×demo模式实测1failed/5passed4.43s，精确定位
pIC50大写缺口。最新七文件联合 **248 passed / 9 failed，8.66s，exit1**；九项均
属于待批准共享修复，尚不提交实现、不宣称通过。独立迁移规格审查与扩展回归进行中。

## 扩展回归与离线验证检查点

23路径联合回归已结束：**5376 passed、9 failed、9 skipped、7 warnings、9 subtests
passed，310.23s，exit1**。九项失败准确对应：大小写 pIC50 的四个公开入口用例、
一个直接 validator 用例、四个 Router 确认边界用例，没有新增其他失败。
缺 docking 输入仍实际调用合成工具；无效 SMILES 返回 tool_unavailable 而不是
invalid_input。不能用旧 contract 通过抵消这些新增失败。

9项跳过：目录符号链接权限两项、未启用独立性能测试一项、POSIX目录权限两项、
未配置双任务存储/运行时三项、POSIX进程组一项。7项警告为 SWIG/FastAPI on_event
弃用警告。全部为隔离模块测试，未调用真实外部模型或科研服务。

同一隔离环境执行 `scripts/run_agent_acceptance.py --mode contract --output
scratch/react-agent-contract.json`：结构化报告 contract passed、case_count=34、
top1_accuracy=1，exit0。src/scripts 的306份Python源码内存 compile 通过，未导入
应用或写pyc；`git diff --check` 通过。

Task3独立规格审查 APPROVED，并独立复测六份迁移文件 **206 passed、0 skipped**。
审查范围仅测试迁移，不是全批验收；独立质量复核亦 APPROVED（Task3 only），
未发现六份测试迁移引入的阻断问题。
共享 Supervisor/Validator 修复仍待单独范围确认，因此不提交或发布本批实现，
不切换生产入口。之前的两份PR均不包含此处改动。

质量审查另发现非阻断的既有测试覆盖缺口：test_target_driven_design_workflow.py
的三个正向测试确认 candidate_ranker 调用与输入，但未确认排序结果。只在隔离进程
内存注入四次排序失败后，三个测试仍通过（3passed、7.87s）。首次探针注入次数0，
不计为有效证据；修正为实际收集模块的FakeTool后断言注入次数4。
基线001ad4f源码亦未断言排序观察，且旧success含partial；基线未运行同一探针，
因此“已有缺口”依据源码对照，而非基线故障注入实测。本轮不将它包装为已修复，
后续应明确检查排序观察状态、错误和top_candidates；这不改变九项共享失败的定位。

## 用户批准共享修复后的 TDD

用户“同意”明确批准两处最小共享修复。本轮修改前复测新增文件 **42passed/9failed，
1.55s**；增加决策仅调用一次/无selected_skill仍保留澄清原因、零值pIC50缺来源用例，
**42passed/14failed，1.48s**，确认RED。

- Supervisor.execute 自动路由使用一次完整 decide，确认标记在创建执行上下文和调用
  工具前拒绝，返回invalid_input、原因、trace_id、空观察/计划/事件。未改变公共route
  返回类型；显式工作流和仅实现route的旧注入路由器保持原合同，不声称本批为plan/run
  或显式策略新增通用输入预检。
- ActivityResultValidator只补充pIC50字段识别，包含0.0；沿用model_path及
  demo_mode=false检查，不改数值算法、阈值或家族双模型证据协议。

首次实现漏投影trace_id，聚焦 **108passed/4failed，2.71s**；补回trace_id后九文件
**318passed，3.38s，exit0**。保留首次错误证据，不修改测试绕过。
修复后离线contract报告passed；扩展联合回归与完整批次独立规格审查进行中。

新增/修改范围：src/agent/react_agent.py、supervisor.py、validators/domain_validators.py；
tests/agent/test_react_agent_adapter.py；前述六份迁移测试；docs/AGENT_MAINTENANCE.md、
本交接、同日ReAct设计/实施计划。原始混杂工作树保持不动。

## 修复后联合结果

使用实施计划登记的隔离runner，运行上批交接列明的完整23路径：
**5390 passed、9 skipped、7 warnings、9 subtests passed，299.75s，exit0**。
所有新增14个失败用例已转绿；跳过原因与前次相同，并非隐藏失败或启用真实依赖。
整批独立规格审查 APPROVED，独立选取9文件复测341passed；质量审查仍待收齐。

本轮聚焦命令为隔离runner传入以下9路径（内部pytest参数见计划）：

```text
tests/agent/test_react_agent_adapter.py tests/agent/test_supervisor_agent.py tests/agent/test_agent_audit_regressions.py tests/test_agent_llm_wiring.py tests/agent/test_react_agent_workflow_routing.py tests/agent/test_workflow_skills.py tests/agent/test_comprehensive_workflow.py tests/agent/test_family_activity_tool.py tests/agent/test_target_driven_design_workflow.py
```

该命令318passed与独立审查341passed使用的选集不同，不能相加作为唯一用例总数。
新鲜contract结构化报告34cases/passed/top1_accuracy=1；306文件源码内存compile及
git diff --check通过。任务文件凭据候选扫描零命中，报告仅留ignored scratch，不提交。
没有真实模型/生产数据/沙盒服务验收，没有性能改善结论，也没有部署或合并。

## 质量审查反馈修复

整批质量审查发现新增自动decide分支绕过原route方法的路由统计。只读基线/当前
模块探针各执行一次相同路由，当前route_attempts从1变0，属于本批新引入的兼容回归。
已按TDD新增真实Router和临时SkillMetrics测试，覆盖正常活性请求、无效SMILES、
普通问候，并断言decide仅一次：RED **2failed/1passed，2.94s**。
最小补回已识别策略的record_route_attempt，不再调用route或重复decide；未命中不
计命中，要求澄清的已识别策略仍计路由命中但不执行工具。显式策略/旧注入路径不变。
新九文件聚焦 **321passed，4.46s**；质量复审与最后23路径回归进行中。
修复后再次断网contract通过。未将审查发现隐藏或将前次5390结果冒充最终改动结果。

整批质量复审已 APPROVED，独立隔离九文件321passed；路由统计回归已关闭。
整批规格审查与质量审查均完成，无未解决的新引入审查问题。仍保留既有排序断言缺口，
不能据此声称所有科学场景均已覆盖。最后联合回归收齐后才提交本批实现。

## 最终本地交付

最终23路径 **5393 passed、9 skipped、7 warnings、9 subtests passed，288.61s，exit0**。
最后一次生产改动为补回路由统计，随后联合回归、contract34/34、306文件内存编译及
diff检查均已完成；之后只更新交接与计划。两项独立整批审查APPROVED，统计问题关闭。
没有仍在运行的本批测试或审查进程，跳过原因已逐项记录，不将其算作通过。

本地分支codex/react-agent-thin-adapter精确提交14个任务文件，未推送或创建ReAct PR，
未修改main，未改变原始项目13项历史改动，未切换生产入口或启用真实模型。
PR55仍是本批依赖；发布时应先核对依赖合并状态，避免把父批重新混进大PR。

后续建议：按门禁单独发布ReAct批次；再为既有candidate_ranker失败覆盖缺口补专项测试。
T09、其他领域职责拆分、正式模型决策入口切换均不是本批完成项。未知外部调用者若依赖
已删除的私有方法或旧自由推理steps内容，需要迁移到保留的公开接口/结构化观察。
