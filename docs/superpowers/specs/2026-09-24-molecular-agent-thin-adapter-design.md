# T11-B：旧 MolecularAgent 薄适配设计

日期：2026-09-24。基线：main `5f56053`，独立分支 `codex/molecular-agent-thin-adapter`。
用户已确认包含拒绝/取消状态修订的书面设计，现按独立实施计划进入 TDD。

## 目标与边界

保留 `src.agent.agent_executor.MolecularAgent` 的导入路径、类名、公开参数与返回字段，
取消该类逐工具 `should_use` 加直接 `execute` 的第二套科学执行规则。
科学路由、计划、执行和校验委托已有 `SupervisorAgent` → WorkflowExecutor/Harness/Session。
不新增框架、DSL、模型决策循环或另一套错误归类器。

本批不修改 ReActMolecularAgent、不切换 Web 入口、不做 T09 跨轮对象引用，
不修改阈值/训练权重/科学校验，不启用真实模型、不部署。英文筛选修复单独提交。

## 已核查的问题

现有 agent_executor 对非生成工具直接调用 `tool.execute()` 后 `.get()`：
canonical ToolResult 会触发属性错误；普通失败字典也被过滤掉。
旧 `execute()` 因而可能只返回通用失败文字，或者在一项成功、一项失败时只保留成功项。
partial 的对象与字典走不同路径，外层没有一致的部分成功标识。

只读合成探针通过实际公共 execute 入口复现以上差异；它不是科研模型验收。
不能只补 `.get()` 兼容并保留第二套执行链，或用删除失败测试来声称完成支持面收缩。

## 方案比较与选择

1. 选用薄适配：复用规范入口，边界只做参数与返回形状转换，减少双轨维护。
2. 只修旧循环：改动较小，但继续绕过统一执行和科学校验，未满足本批目标。
3. 删除旧类：结构最简单，但存在明确导入与测试消费者，破坏兼容，不采用。

## 公共接口与行为

保持 `__init__(llm=None)`、`get_all_tools()`、`get_tool_descriptions()`、
`should_use_tools(query, *, preflight=True)`、
`execute_tools(query, temperature=0.7, mol_count=None)` 和
`execute(query, temperature=0.7, mol_count=None)` 的签名。

工具列表与描述仍可查询，但不再用每个工具的 should_use 决定执行集合。
should_use_tools 采用规范 Router/policy 判定，不执行科学工具，不为探测而加载可选工具。
普通聊天不计算；缺必需工具或未知/歧义请求不得偷偷退回旧循环。
这是批准的行为调整：兼容接口不等于保留错误路由和“任意一项成功即完整成功”。

## 参数、工具与执行生命周期

- 保留已有生成预校验以及 `mol_count=None` 的旧“未指定”含义：委托时省略该参数，
  不把 None 传成规范入口的显式无效值。
- 非 None 的 mol_count 必须先用既有契约验证；禁止用真假值判断省略零或 False。
  显式数量覆盖文本数量的既有语义保留；非生成任务不能因有数量参数被强制生成。
- temperature 原样交给规范执行入口，不由适配层生成 SMILES 或科学数值。
- 复用已初始化的核心工具与延迟可选工具。只按规范计划所需名称解析可选工具，
  使用既有注册/别名机制；不另建关键词白名单或自动加载全部重工具。
- 每次调用固定工具注册表快照，避免修改共享工具集合；一次公开执行至多调用一次规范执行。
  不同时走 execute_tools 和 execute 两次执行。适配层不建立第二套持久化数据库或 Session。
- 主模型配置不能覆盖分子生成器。已有工具资源的所有权不转移；适配器不得关闭借入工具，
  不绕过现有会话清理、deadline、异常与取消传播。

若仅能通过修改 Supervisor 的科学策略或增加另一套 Planner 才达成，停止并重新设计，
不在本批扩大改动。允许最小依赖接线，不修改规范执行器的策略语义。

## 输出映射与状态

保留 execute_tools 的 `success/results/used_tools/error/message` 及 execute 的
`success/message/response/used_tools/tool_results/error` 字段；无工具时仍返回明确非成功。
添加 `partial/status/trace_id/agent_events/warnings` 与现有规范执行证据字段，不引入新事件格式。

- results/tool_results 使用规范 `tool_result_sequence` 的有序结果，保留重复工具调用及失败项，
  不能从按工具名聚合的字典重建序列。
- 每条结果完整保留 data、formatted、error、warnings、artifacts、evidence、quality 及扩展来源。
  复用 canonical 序列化，不把类型 schema 当投影丢弃数据。
- success 只表示整体完整成功；部分成功必须 `success=false, partial=true, status=partial`。
  `status` 原样保留规范 RunOutcome 的 completed/partial/failed/rejected/cancelled；
  普通失败、拒绝、取消均不得变成 success，但不能将后两者压平为 failed。
  混合观察的终态优先级直接遵循 AgentResult，不在适配层再次汇总或猜测。
  这是修复旧错误成功语义，不改字段类型；从规范 AgentResult/status 取得真值，
  不照抄 Supervisor 为聊天兼容设置的 success-or-partial。
- used_tools 保留有序的实际尝试工具名（不去重），不把失败工具说成成功；是否成功看每项状态。
- error 保留规范错误结构；partial 的步骤错误仍保留在各项结果，即使总体 error 为空。
- response 来自规范 final_answer/message，不能额外调用 LLM 改写科学结果。
- 生成预校验失败继续保留现有 INVALID_INPUT 与 generation_request_error_details。
  不能将预校验错误伪装成一次已发生的工具调用。

## TDD 验证矩阵

先补实际公共入口测试复现，确认失败原因不是夹具缺字段，再实施最小转换。

1. canonical ToolResult 与 legacy dict 在成功、partial、失败下行为一致。
2. 成功性质 + 失败活性：两项都保留，整体非完整成功，错误/警告/来源不丢失。
3. 同工具多次执行保持顺序，不能覆盖；两公开执行入口各自只执行一次。
4. Router 拒绝闲聊/未知/歧义；旧 should_use=True 不能绕过规范策略。
5. 无效 SMILES、demo 活性、缺 docking 输入、靶点无证据、生成数量/唯一性仍由真实校验拒绝。
6. None 与省略、显式有效数量、0/False/非整数/越界、文本数量冲突保持既有批准语义。
7. 生成工具接收正确 temperature/count；主 LLM 不替换本地生成器。
8. optional tool 缺失、异常、重复调用、取消/超时保留规范错误和既有资源清理。
   明确覆盖 rejected/cancelled 的外层状态与有序观察，以及混合成功/失败/取消时
   与 AgentResult 一致的汇总；unavailable/invalid_input 等逐工具状态不压平。
9. 老导入/签名/查询接口及正常非生成输入兼容；更新旧路由测试时只替换已批准行为，
   保留输入、数量和科学门禁断言，不删除保护用例。

用隔离临时配置/数据库运行 tests/agent/test_agent_executor.py、生成契约、Supervisor、
Session/WorkflowExecutor、test_agent_llm_wiring 和反幻觉测试；再跑 Agent/Web 联合回归、
离线 contract、内存编译检查、独立规格/质量审查。测试不访问真实 API 或生产资产。

## 完成标准与交付

旧类不再直接执行科学工具、不维护第二套路由/循环；公开接口保留且全部新增回归通过。
报告真实 RED/GREEN、跳过原因、状态兼容变化与剩余 ReAct 风险。
独立分支、精确暂存，不修改原始混杂工作树；发布和合并按仓库审批规则办理。
本设计不宣称整个 T11 或任务书完成。书面设计确认后再写实施计划并进入 TDD。

## 书面审阅前的状态契约复核

2026-09-24 对照 `contracts/result.py` 的 `AgentResult.from_tool_results()` 和
`to_legacy_dict()`、`contracts/scientific.py` 的 RunOutcome 核查，补明拒绝/取消不能
被适配层压平为 failed；含成功观察的取消仍遵循既有 partial 汇总，不另造优先级。
这是设计修订，不是已实施的适配器修复。

使用已登记的隔离 runner，只替换当前 worktree 路径、清除继承配置并使用临时数据：
`tests/agent/test_contracts.py` 实测 **5 passed，0.33s，exit0**；
`tests/agent/test_scientific_contracts.py tests/agent/test_agent_event_stream.py`
实测 **17 passed，1.07s，exit0**。现有用例实际覆盖 repeated tools、partial、
rejected/cancelled 的序列化和混合取消；这些通过证明规范基线，不代表旧入口已修复。
本轮仅修改本文，未改生产代码或测试，未调用真实工具、模型、部署服务。

## 实施中批准的最小共享修复（2026-09-24）

用户在复现温度传递和显式数量优先级失败后明确回复“是”，授权本批扩展到共享
Planner 和生成参数链。仅修 lead 显式 requested_count 避免急切解析默认文本数量，
以及请求 temperature 经结构化生成输入传到既有生成器；非数字或非有限结构化温度
返回 invalid_input，不新增采样范围或修改科学阈值。保留主模型/生成模型边界、
规范派发、线程超时、输入摘要与检查点机制，不另建旧入口专用执行器。
此确认不授权发布、合并、部署或真实模型调用；不覆盖其余设计限制。
