# T10-B：Planner 纯步骤模板提取设计

日期：2026-09-24。基线：`75d6a3abc6f6d79e96bf38a019ce2980576c100e`。
状态：用户已批准最小方案；本书面设计待确认，尚未实施。
分支：`codex/planner-step-templates-pr`。

## 1. 目标与范围

依据《MedChat 代码清理与智能体一致性修复任务书》T10-B，将
`src/agent/planning/task_planner.py` 中五类步骤构造提取到同目录
`step_templates.py`。让选择/解析与步骤描述分开，不改变计划行为。

本批不是重建 Agent，也不删除现有工作流。现有动态决策、PlanCompiler、
BindingResolver、WorkflowExecutor、科学校验与资源清理均保持不变。
不调用真实模型，不训练、切换生产入口或部署；不修改原始混杂工作树。

## 2. 方案取舍

- **采用：一个纯模板模块、五个明确函数。** 与现有 WorkflowStep 对齐，迁移范围有限，能够逐字段证明等价。
- 不采用通用模板 DSL、注册框架或配置驱动加载：会增加解析与兼容层，本批没有此需求。
- 不同时拆出 WorkflowPlan 或重写参数解析：前者有多处导入及编译器依赖，后者会混入行为变化。

## 3. 职责与接口

`TaskPlanner.plan()`、所有现有辅助方法及类级数量常量保留。
`WorkflowPlan` 仍在 `task_planner.py` 定义；`planning` 包导出保持同一对象。
Planner 继续负责分支选择、靶点澄清、数量/Top N 解析、错误分类、
generation request 构建及 plan metadata；模板只接收已准备的数据。

新增内部函数，统一返回 `list[WorkflowStep]`：

| 函数 | 输入 | 步骤 |
|---|---|---|
| `admet_steps` | `query: str`, keyword-only `include_admet: bool` | properties → drug_likeness → 按原条件追加 admet |
| `comprehensive_steps` | `query: str` | properties → drug_likeness → admet → activity → reverse_target → target_structures |
| `target_design_steps` | keyword-only `target_hint: str`, `generation_request: dict[str, Any]`, `docking_top_n: int` | target_search → molecule_generation → properties → admet → activity → candidate_ranking |
| `molecular_design_steps` | `generation_request: dict[str, Any]` | molecular_design |
| `lead_optimization_steps` | `query: str`, keyword-only `generation_request: dict[str, Any]` | baseline_properties → baseline_admet → baseline_activity → molecule_generation → candidate_properties |

模板不导入 TaskPlanner/WorkflowPlan、不解析自然语言、不访问环境变量、
数据库、磁盘、网络、工具注册表或模型；不增加初始化副作用。
不创建通用的“任意步骤构造器”，不保留迁移前后两套步骤定义。

每次调用新建步骤列表、WorkflowStep 和模板自有 metadata；不得共享可变默认值。
传入的 generation_request 不被模板修改，作为 input_data 原样引用；
Planner 每次按既有函数创建新的 request，不额外深拷贝或承诺调用者复用同一 request 的隔离。
完整 Planner 调用之间必须保持可变内容隔离。

## 4. 必须保持的行为

1. WorkflowPlan 名称、metadata、步骤全部 dataclass 字段和顺序逐项等价，包含默认 None、tuple 与字典结构，不能只比较工具名。
2. ADMET 是否追加仍调用 `self._wants_admet()`；所有现有辅助方法调用和可覆写路径保留。
3. 分子设计/靶点设计的非法数量仍返回原有无步骤拒绝计划；先导优化原有异常传播、metadata 优先级及参数求值顺序不借机修正。
4. 靶点澄清、单工具、结构化 docking、未授权工具拒绝、普通无匹配分支完全不搬迁、不改条件优先级。
5. 综合评价的 `targets` 继续绑定到 `$.outputs.targets`；靶点设计候选继续经 `$.outputs.molecules`、`smiles_text` 进入 properties/ADMET/activity。
6. 靶点设计生成步骤继续要求 `target_evidence`；排序保留 workflow output/optional output/metadata 白名单和 Top N。
7. 先导优化继续传入原始 baseline 属性，候选性质继续消费 `$.outputs.candidates`；input_template 不变。
8. required、continue_on_error、capability、output_contract、output_key 和 preconditions 不变；不能绕过现有编译/绑定/科学验证。
9. 不声称本次整理修复旧接口缺陷、跨轮引用或此前未定位的 docking 测试偶发失败；这些留在各自任务中。

## 5. 验证设计

先增加迁移前特征测试，再提取模板；行为特征测试预期在迁移前后都通过，不能把它们写成“修复前失败”。
另加模板接口、Planner 实际委托及隔离测试，在新增模块前记录真实 RED，再以最小提取达到 GREEN。

- 五类计划使用固定预期结构逐字段比较；预期不得调用新模板生成，也不得复制整个旧 Planner 作为第二实现。
- 覆盖 ADMET 开关、缺省/显式/越界/非法数量、metadata 覆盖、Top N、靶点歧义、生成/搜索分支，以及原辅助方法覆写。
- 保留旧导入身份；重复构建并修改嵌套 metadata/request，确认独立 Planner 调用没有污染。
- 经真实 PlanCompiler/BindingResolver 和受控工具运行 WorkflowExecutor，验证上游候选/靶点/baseline 实际进入下游；验证 required 失败停止和 optional 失败继续。受控工具数据只用于离线契约，不作为科研结果。
- 重跑五个核心测试文件、整个 `tests/agent`、相关联合回归、断网 `--mode contract`、源码编译及 `git diff --check`。
- 不减少既有断言、不改超时门槛、不添加 skip 掩盖失败；出现非等价行为先报告，不能借本批授权扩展修复范围。

### 已运行的迁移前基线

在上述基线的独立工作树，使用 MedChat Conda Python，复用
`docs/superpowers/plans/2026-09-24-rag-service-extraction.md` 内隔离包装，
仅替换工作树定位。清除非必要进程环境、临时配置/数据库/cwd，关闭 real/canary，
使用正常 pytest 子进程；三份离线 JSONL fixture 保持哈希一致。

实际 pytest 参数（包装将下列相对路径转换为该工作树绝对路径）：

```text
python -B -m pytest tests/agent/test_task_planner.py tests/agent/test_plan_compiler.py tests/agent/test_binding_resolver.py tests/agent/test_workflow_executor.py tests/agent/test_decision_loop.py -q -p no:cacheprovider --tb=short -rs
```

结果：**421 passed，14.66 秒，exit 0**。这是迁移前离线回归，不是迁移完成或真实模型验收。

## 6. 预计修改范围与交付门槛

生产代码仅新增 `src/agent/planning/step_templates.py`、调整 `task_planner.py` 的五个方法。
测试新增 `tests/agent/test_planner_step_templates.py`；必要的行为验证只扩展已有相关测试。
文档限本设计、对应实施计划/交接及既有 `docs/AGENT_MAINTENANCE.md` 的事实更新。
不移动公共类型，不修改编译器、工具 Adapter、Web API、数据库 schema 或依赖。

书面设计确认后执行 TDD；通过独立规格/质量审查和回归后才创建本主题 draft PR。
具体 PR 合并仍需明确授权及全部门禁通过。设计提交不表示业务代码已迁移。
