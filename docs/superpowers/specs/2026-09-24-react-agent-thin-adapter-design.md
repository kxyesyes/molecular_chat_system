# T11-B：ReAct 公共兼容入口收敛

日期：2026-09-24。用户已确认薄适配方向；本文待书面审阅，尚未实施。
独立分支：`codex/react-agent-thin-adapter`，基线 `50bf8a7`（PR #55 待合并）。
不得把该依赖的 19 文件当作本批新增；本批后续发布前须对齐已合并基线。

## 1. 目标与方案

保留 `ReActMolecularAgent` 公共兼容入口，但不再维护单工具 ReAct 循环、关键词回退、
直接工具执行及独立状态汇总。科学执行统一委托已有 Supervisor → Harness/WorkflowExecutor
→ WorkflowRunSession → Tool Adapter/Validator。旧类名表示兼容路径，不承诺继续运行旧推理算法。

已比较并由用户选择：

- 薄适配：消除旁路、复用校验与状态，选用。
- 仅补失败字典/类型转换：改动少，但保留第二套执行规则，不满足最终一致性目标。
- 删除公共类：破坏包导出和已知测试消费者，不采用。

不新增 Agent 框架、提示驱动自由执行器或 DSL。不切换正式 Web 入口，不启用真实模型、
权重或沙盒作业，不做 T09 跨轮引用、数据库迁移及领域路由拆分。

## 2. 缺陷与证据

已通过真实 constructor/execute 复现六项失败：有/无合成主模型两条路径中，旧失败字典
丢失警告/错误；canonical ToolResult 被 `.get` 属性错误替代；有主模型时外层可能仍为
success=true；明确标记 demo 的合成活性输出两条路径均被接受。

探针只替换领域工具工厂和模型，阻断网络，不是生产网页或真实科研模型验收。
既有六文件 189 passed 没有覆盖这些缺陷。迁移必须新增实际公开入口回归，不能只删除旧测试。

## 3. 保留的公共支持面

- 包级和模块级 `ReActMolecularAgent` 导入保持。
- 构造参数 `llm=None, molecular_generator_llm=None` 保持。
- `execute(query, temperature=0.7, mol_count=_MOL_COUNT_UNSET, active_skill=None, event_callback=None)` 保持。
- `set_llm(llm)`、`should_use_tools(query)`、`format_result(result)` 保持。
- 模块级 `ReActStep` 及其字段保留，以兼容外部构造和旧结果格式化；不再生成模型思维链。
- `tools` 仍使用既有工厂得到的同一批工具；`llm`、独立生成模型及 `skill_router` 的已知支持面保留。
  不为了本批清理另做工具加载架构重写。

旧 `max_iterations` 可保留为无执行作用的兼容属性；不再影响规范执行预算。
`_execute_tool`、`_generate_react_step`、`_build_react_prompt`、旧回退与内部格式解析循环
不是继续执行的兼容承诺；先迁移它们承载的科学断言，再删除无调用的私有实现。
不得为保住私有单测而重新保留执行旁路。共享 prompts 常量须核对其他消费者，不能连带删除。

## 4. 路由、策略和执行

1. 使用请求内的主模型和工具映射快照创建轻量 Supervisor；不创建第二个状态库或线程池。
2. `should_use_tools` 采用完整规范 Router decision，不运行科研工具；requires_confirmation
   不得因有 selected_skill 而被丢掉。未知、歧义和普通聊天不得回退到逐工具 should_use。
3. `execute` 未指定策略时同样保留完整路由拒绝原因。显式 active_skill 按规范 catalog
   的名称解析；对象中的自定义 allowed_tools 不覆盖服务端策略，未知名称明确拒绝。
   显式策略不能跳过输入、编译、授权和科学校验。不要额外增加一套允许工具白名单。
4. 正常执行最多调用一次 Supervisor.execute；有无主模型均走同一科学执行链。
   模型文本不再被该兼容类解析为任意工具动作，也不再以主模型最终回答代替校验后的证据。
5. event_callback 原样交给规范执行链；不重复回放实时事件、不伪造 tool_started 或完成事件。
   预执行拒绝与规范入口一致，不虚构一次已执行的工具调用。

如发现必须改变共享 Router/Supervisor 的策略才能实现上述保护，应先用实际入口复现并提出
最小共享修复，不在 ReAct 边界复制一个新的安全策略或直接调工具兜底。

## 5. 参数和模型边界

- mol_count 哨兵表示省略，委托时省略该参数；显式 None、False、0、非整数及越界不能
  用真假值判断变成默认数量。沿用现有 generation_request 契约及显式数量优先规则。
- 不再保存 current_temperature/current_mol_count/_active_skill 等跨请求执行状态。
  temperature 和数量通过规范 context/input.metadata 传递，保留已修复的摘要与检查点语义。
- 分子生成始终使用专用 molecular_generator_llm；set_llm 保留其他既有消费者更新行为，
  排除生成工具（包含已有规范别名映射），不得把主模型误接到生成器。
- 不承诺本批解决整个应用模型切换并发；不得关闭借入工具/模型，生命周期和取消清理仍归
  现有执行基础设施。异常、超时不能被转成成功文本。

## 6. 返回形状和状态

保留 query、success、steps、final_answer、reasoning_trace、tools_used、active_skill。
steps 保持列表但新执行返回空列表；reasoning_trace 保持列表，可记录规范工作流标识，
不杜撰主模型推理过程。format_result 支持新结果和既有 ReActStep 列表，不调用模型或工具。

以 AgentResult.to_legacy_dict 为序列化真源，保留 tool_results 名称映射兼容视图，同时
暴露 tool_result_sequence、tool_results_by_step、agent_result、trace_id、agent_events、
workflow_plan、status、partial、error、warnings、artifacts、evidence、metadata。
重复调用的完整来源只以有序序列为准；不能从名称映射反推序列或丢弃失败项。

- success 仅表示完整成功；partial 为 success=false、partial=true。
- completed/partial/failed/rejected/cancelled 及混合观察优先级直接沿用 AgentResult，
  不照抄 Supervisor 为历史聊天兼容设置的 success-or-partial。
- 每个 ToolResult 的数据、formatted、quality、错误和来源保留，不做 schema 投影式删字段。
- tools_used 来自实际 tool_started 事件，顺序及重复保留；输入绑定失败不能伪装成已调用。
- final_answer 来自规范结果；不追加主模型润色，不把 demo、失败、不可用说成完成计算。
- 无匹配及预校验失败若没有 agent_result，也必须返回一致非成功边界和真实错误，
  不捏造工具观察；复用既有错误类型，不另写失败分类器。

## 7. TDD 与迁移验证

先将六项诊断转成 tracked 公共入口测试，保留 RED 输出并确认非夹具问题，再实现。
除 shape 单测外，必须使用真实 Router、Supervisor、Session、Adapter 与 Validator：

1. legacy/canonical 的成功、partial、unavailable、invalid_input、rejected、cancelled；
   两种主模型存在状态下错误、warnings、证据、产物不丢失。
2. demo pIC50、无效 SMILES、缺 docking 输入、无来源靶点等继续拒绝；不出现伪数值。
3. 同工具重复执行保持序列；成功后下游失败保持 partial；实际工具调用一次及有序事件。
4. 显式策略、别名、未知策略、禁止工具、路由澄清和普通聊天不旁路授权。
5. 省略/显式计数、文本冲突、非法值、真实生成器参数传递与主/生成模型隔离。
6. 不共享请求执行参数，异常/取消/超时不关闭借入资源，沿用规范清理回归。
7. 兼容导入、构造/方法签名、ReActStep 与新旧 format_result；未知外部消费者风险写入交接。

迁移六份现有测试：test_agent_llm_wiring.py，以及 tests/agent 下的
test_react_agent_workflow_routing.py、test_workflow_skills.py、test_comprehensive_workflow.py、
test_family_activity_tool.py、test_target_driven_design_workflow.py。
私有 `_execute_tool` 的禁止调用断言改到真实 public execute/规范授权入口；生成优先断言
改为实际生成工具收到输入、数量和温度。允许更新错误文案，不删除科学判定或伪造成功夹具。

验证顺序：聚焦 RED/GREEN → 六文件回归 → Agent/Web 联合回归 → 离线 contract →
源码内存编译和 diff 检查 → 独立规格/质量审查。沿用已登记隔离 runner、临时库/配置、
清除继承凭据、无真实服务。测试报告明确 skipped 与失败原因，不包装成真实科研验收。

## 8. 交付与完成条件

没有 ReAct 私有科学执行循环或直接 tool.execute；全部关键科学断言已在真实共享入口验证；
旧公共接口可用、状态和来源不丢、工具禁用/输入拒绝仍有效。记录实际删除的重复职责，
不以文件行数或测试通过总数代替行为证明。

仅在独立分支精确提交本批文件；原始混杂工作树不动。PR #54 英文修复与 #55 MolecularAgent
已独立发布，不将它们混作本批。ReAct 本批待书面设计审阅后写实施计划和进入 TDD；
发布与具体合并继续遵守仓库门禁。此设计不代表整个任务书完成或生产入口已迁移。
