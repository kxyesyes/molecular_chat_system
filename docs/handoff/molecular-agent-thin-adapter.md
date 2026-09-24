# T11-B MolecularAgent 薄适配实施记录

基线 main `5f56053`；分支 `codex/molecular-agent-thin-adapter`。
已确认设计提交 `fd8a602`、状态契约补严 `812fceb`，实施计划 `be23d69`。
本批不混入英文筛选修复，不改 ReAct，不发布、部署或启用真实模型。

## 修改与边界

`src/agent/agent_executor.py` 保留旧类/导入/公开方法，移除逐工具 should_use 与直接执行循环。
由请求级 Supervisor 对象复用规范 Router/Planner/Session，适配层只做生成参数预校验、
按计划加载既有可选工具、旧返回形状转换。缺工具不回退模拟或旧循环。
canonical AgentResult 的 success/partial/status 原样保留，不能用聊天入口的 success-or-partial。
有序观察保留重复调用和失败，warnings/evidence/artifacts/provenance 不丢失。
主模型不传入本地分子生成器，不创建第二个持久化库、不关闭借入工具。

兼容行为调整已批准：任意工具 should_use=True 不再触发执行；规范 Router 对
`Analyze CCO` 的 abstain、对解释场景的不计算、对明确生成任务只选生成均与规范入口对齐。
旧测试仅迁移这些断言，保留无模型调用、数量预校验等原保护。

## TDD 和基线

解释器 Conda MedChat Python；所有 pytest 使用本批实施计划的隔离 runner，
清除继承环境配置/凭据，临时数据库和配置，`-B -q -p no:cacheprovider --tb=short -rs`。

- 原 `test_agent_executor.py` + `test_agent_llm_wiring.py`：**61 passed，3.00s，exit0**。
- 新 `test_molecular_agent_adapter.py`：**20 failed、11 passed，0.99s**。
  旧执行未委托 Supervisor，未返回规范结果且仍调用工具自己的 should_use。
- 初版实现与旧测试：**6 failed、83 passed，1.19s**。
  其中1条新形状测试用到规范Router不识别的英文性质请求，改用明确中文请求；
  另5项是上述批准的旧工具选择变化，未改规范路由或生成校验。
- 修正后连同 LLM 接线：**92 passed，1.64s**。
- RAG 旧别名新增回归：**1 failed、33 passed，0.84s**，发现已有 rag_database_search
  仍重复加载 RAGSearchTool；复用现有 TOOL_ALIASES 并保留显式 canonical 优先级后，
  三文件 **95 passed，1.58s**。
- 加入 artifacts/provenance 保留断言，与旧执行、LLM接线、Session、resume、科学契约
  六文件联合 **165 passed，4.69s，exit0**。

这些是受控合成契约和真实代码执行验证，不是科研模型性能或真实服务验收。
本记录尚未声明独立审查、完整回归或本批实现最终完成。

## 科学校验集成阶段

新增 `test_molecular_agent_adapter_science.py` 直接调用公开构造/execute，只有领域工具
工厂返回合成受控对象，Router/Planner/Supervisor/Session/验证器均为实际实现。
涵盖无效输入、缺对接参数、demo 活性拒绝、靶点证据断路、失败观察保留、生成结构与唯一性、
正常性质请求不生成、主模型与生成器隔离、借入工具不关闭；阻断网络。

子任务首轮夹具缺 ranker/demo 来源形状/缺工具时的编译前置预期已单独修正。
之后 **14 passed、2 failed，0.87s**；父任务重跑 **14 passed、2 failed，0.83s**，
确认无效 C1CC 仍到达性质工具、缺 receptor/box 仍到达对接工具。
原因是仅用旧 route() 获取 policy 会丢掉完整 RouteDecision 的 requires_confirmation。
适配边界改用同一 Router 的 decide()，在加载工具前保留拒绝及完整原因；不改 Router、
Supervisor、Planner 或任何科学验证规则，也不新增 SMILES/参数解析器。

调整后12条旧“非行动性示例”测试只因 message 字段变化失败（其余99项通过）。
对未选中任务保留原 message，同时用 final_answer/error 保留澄清原因，四文件最终
**111 passed，1.62s，exit0**。未弱化示例不生成或其他输入保护断言。

子任务另在内存装载旧 HEAD 实现运行新科学测试，16项失败；这是追溯基线，
不能冒称为生产变更前完成的 RED。正式先测后修记录以上述两项真实 RED 为准。
本批的 used_tools 根据规范 tool_started 事件列出尝试工具；是否成功必须读取每项 status，
不能将工具名出现解释为已成功产生科研结果。tool_results 仍完整保留有序观察，
包括未开始工具调用就发生的输入绑定失败，不能将观察数量当成真实调用数量。

## 独立审查与当前未完成项

SPEC 审查结论为 **NEEDS CHANGES**，未进入 QUALITY 或发布：

1. 温度只传到 Supervisor，未传到生成工具。公开 execute/execute_tools 均实测
   请求 0.23，生成工具仍收到默认 0.7。
2. lead optimization 的共享 Planner 使用 dict.get 的急切求值默认表达式；
   显式 mol_count=3 仍会解析文本中的 7.5 并抛出 GenerationRequestError。
3. 原 used_tools 由全部观察生成，会将未执行的 target_database_search 绑定失败
   误算为调用。已先用公开入口测试复现，再改为规范 tool_started 事件映射；保留失败观察。

三问题新增测试首次 **5 failed、16 passed，1.06s**；修复第三项后形状/科学两文件
**4 failed、51 passed，1.14s**。随后加强无效 SMILES 和缺 docking 参数的双入口断言：
必须 rejected、invalid_input、空观察/事件/工具调用、零可选工具加载。
四文件最新结果 **4 failed、114 passed，3.03s，exit1**，剩余正是前两问题的双入口用例。
没有跳过、删除或放宽这四条失败断言。

先前启动的 23 路径联合回归已结束：**5298 passed、9 skipped、7 warnings、
9 subtests passed，218.11s，exit0**。其测试收集早于上述新增失败用例和最终 used_tools
修改，只能作为中间证据，不代表当前代码全绿。9 个跳过为 Windows 符号链接权限2项、
POSIX 权限/进程组3项、独立存储/运行时配置3项、opt-in 性能1项。

离线 contract 与源码编译的早期通过也不替代最终回归。当前共享 Planner、Orchestrator
未修改；已请求将范围最小扩展至显式数量优先级和生成温度传递链，等待确认。
不得为绕过此边界另建旧入口专用执行器。获确认后先补共享入口回归、最小修复，
再完成 SPEC→QUALITY 和最终联合验证。本批实现未提交、未推送、未创建 PR。

## 最小共享修复：批准与 TDD

用户回复“是”批准上述范围扩展。修改共享 Planner 的 lead 显式数量分支，避免
dict.get 的默认表达式提前执行；省略数量仍按原文本解析，非法显式类型/范围仍拒绝。
Orchestrator 在规范生成输入 metadata 加入 request-local temperature，生成器读取它。
温度参与输入摘要/检查点标识；不改工具派发签名、不写共享模型属性、不建立第二条执行链。
结构化温度非数字或非有限时返回 invalid_input，不向模型提交；旧字符串输入默认保持0.7。

测试把早期仅观察假工具 kwargs 升级为实际 LLMMolecularGenerator + 记录参数的合成
LLM，检查参数确实抵达模型 generate；同时覆盖两个旧入口、Supervisor，以及直接、
线程和真实 SpecialistDispatch/注册工具适配路径。没有访问外部服务或生产资产。

- 扩展首轮 **16 failed、90 passed，1.50s**，其中2项为 dispatch 测试夹具不完整
  （lambda 没有 authorize），不是产品缺陷。替换成现有 SpecialistDispatch 后，
  温度文件真实 **9 failed、1 passed，0.77s**，四执行分支均收到0.7而非请求温度。
- 首轮修复后 **10 failed、205 passed，2.27s**：错误构造多传 field 导致分类错误；
  另3项合成生成结果预设 requested_count=1、2项旧精确结构断言未含温度。
  修正错误构造和夹具/精确期望，保留所有错误分类、数量、下游输出断言。
- 七文件聚焦最终 **215 passed，2.20s，exit0**。
- 断网 contract **34/34 passed**，报告 scratch/molecular-agent-adapter-contract-final.json；
  RDKit 的无效 SMILES 诊断属于负例预期。
- src/scripts **306** 个 Python 文件内存 compile 通过（不写 pyc）；git diff --check 通过。

完整23路径回归、独立 SPEC 复审正在进行；完成前不宣称最终通过。

SPEC 独立复审 **APPROVED**，审查者在隔离环境复测三份新增文件 **69 passed，1.06s**。
随后完整回归 **15 failed、5302 passed、9 skipped、7 warnings、9 subtests passed，
218.45s，exit1**；15个失败全部来自执行时精确 payload 期望缺 temperature 字段。
仅同步五个文件的精确期望，不删除失败断言；其中损坏 checkpoint 的输入 hash fixture
同步新 payload，继续检验损坏反序列化后重执行和 warning。五文件 **107 passed，4.92s**。
生产代码未因此进一步修改；最终全量正在重新验证，QUALITY 正在审查。

## 本批文件与复现命令

工作树：`D:/MedChat/molecular_chat_system_worktrees/molecular-agent-thin-adapter`。

生产文件：

- `src/agent/agent_executor.py`
- `src/agent/planning/task_planner.py`
- `src/agent/orchestrators/workflow.py`
- `src/agent/tools/llm_molecular_generator.py`

新增测试：`tests/agent/test_molecular_agent_adapter.py`、
`tests/agent/test_molecular_agent_adapter_science.py`、
`tests/agent/test_generation_temperature_transport.py`。
修改测试：`test_agent_executor.py`、`test_planner_step_templates.py`、
`test_planner_template_execution.py`、`test_supervisor_agent.py`、
`test_supervisor_delegation.py`、`test_target_driven_design_workflow.py`、
`test_workflow_orchestrator.py`、`test_workflow_resume.py`，均在 `tests/agent/`。
文档：`docs/AGENT_MAINTENANCE.md`、本记录及同批 design/plan。

运行方式为实施计划所列隔离 runner，使用 Conda MedChat Python。下面为传入 runner
的实际测试路径（内部统一使用 `python -B -m pytest ... -q -p no:cacheprovider --tb=short -rs`）：

```text
tests/agent/test_generation_temperature_transport.py tests/agent/test_molecular_agent_adapter_science.py tests/agent/test_planner_step_templates.py tests/agent/test_molecular_agent_adapter.py tests/agent/test_agent_executor.py tests/test_agent_llm_wiring.py tests/agent/test_workflow_orchestrator.py

tests/agent/test_planner_template_execution.py tests/agent/test_supervisor_agent.py tests/agent/test_supervisor_delegation.py tests/agent/test_target_driven_design_workflow.py tests/agent/test_workflow_resume.py

tests/agent tests/test_activity_prediction_contract.py tests/test_model_request_lifecycle.py tests/test_design_model_switch.py tests/test_molecular_design_architecture.py tests/test_llm_runtime_config.py tests/test_user_llm_routes.py tests/test_agent_llm_wiring.py tests/test_task_runtime.py tests/test_phase2_phase3_routes.py tests/test_agent_anti_hallucination_fallbacks.py tests/test_agent_platform_health_check.py tests/test_agent_session.py tests/test_agent_session_entrypoints.py tests/test_agent_task_ownership.py tests/test_rag_index_manifest.py tests/test_web_app_lifecycle.py tests/test_openai_compatible_model.py tests/test_rag_service_boundary.py tests/test_main_routes_template_compat.py tests/test_static_placeholder_cleanup.py tests/test_docking_agent_architecture.py tests/test_docking_configuration.py
```

contract 在同一隔离 runner 中使用 runpy 执行
`scripts/run_agent_acceptance.py --mode contract --output scratch/molecular-agent-adapter-contract-final.json`，
阻断 socket 网络；编译检查调用 Python 内存 compile 遍历 src/scripts，未导入应用或写 pyc。
未改 JavaScript，不运行 Node；未改部署和生产资产，不运行真实健康检查或 real/all 验收。

## 最终交付检查点

- 最终23路径：**5317 passed、9 skipped、7 warnings、9 subtests passed，219.57s，exit0**。
  9项跳过原因与上文一致，未包装成通过；7项为 SWIG/FastAPI on_event 弃用警告。
- 独立 SPEC 和 QUALITY 均 **APPROVED**；QUALITY 确认五份期望迁移仅加入默认温度，
  损坏 checkpoint 的输入 hash fixture 仍实际进入反序列化失败后重执行路径。
- 最终生产代码与断网 contract 34/34、306文件内存编译验证版本一致；其后只同步测试
  精确期望和文档。全量运行完成，审查子代理已关闭，没有未收齐的测试进程。
- 精确任务范围19文件凭据候选模式扫描零命中，diff check 通过；真实密钥、输出文件、
  scratch 报告与本地资产不加入提交。原始工作树仍 f377443、13项历史改动保持不动。

本批完成旧 MolecularAgent 适配收敛和已批准的两项共享参数修复；不代表全部任务书完成。
下一批建议独立处理 ReAct 的单工具/回退兼容入口，先建立错误状态和返回类型回归；
英文筛选修复发布、T09及生产入口切换仍按各自授权边界处理。本分支仅作本地提交，
不推送、不创建或合并 PR，不部署、不启用真实模型。
