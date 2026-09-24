# Planner Step Templates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** 提取五类纯步骤模板，保留每个计划字段、解析/失败行为及真实执行的数据绑定。

**Architecture:** TaskPlanner 保留选择、解析和 WorkflowPlan 构建；新 step_templates 只构造 WorkflowStep 列表。沿用原 compiler、bindings、executor，不增加第二执行器。

**Tech Stack:** Python 3.10、dataclasses、pytest，现有离线 Agent 契约。

## 文件边界

- 新增 `src/agent/planning/step_templates.py`：五个内部纯构造函数。
- 修改 `src/agent/planning/task_planner.py`：只委托五个构造位置及增加模块导入。
- 新增 `tests/agent/test_planner_step_templates.py`：全字段特征矩阵、委托、对象隔离及辅助方法兼容。
- 新增 `tests/agent/test_planner_template_execution.py`：通过现有 executor 验证生成/靶点/baseline 数据流与失败控制，不调用真实服务。
- 更新既有维护指南；新增本主题交接，记录所有实际测试结果和失败。

## Task 1：先锁定旧行为，再记录结构性 RED

- [x] 新测试以 `dataclasses.asdict(plan)` 对比固定字典矩阵；测试自己的 `_step` 提供全部15个字段默认值，不调用生产模板生成 expected。
- [x] 覆盖 ADMET 两分支、综合评价、靶点设计、分子生成、先导优化，明确 bindings、metadata、tuple、required、continue_on_error。
- [x] 覆盖 helper 覆写、无效数量返回/抛出区别、metadata 优先级、未迁移分支与多次 plan 的嵌套对象隔离。
- [x] 先跑特征测试，预期 PASS；记录旧行为，不称为 RED。
- [x] 加新模块与委托测试。例如：

```python
def test_template_module_exists():
    from importlib.util import find_spec
    assert find_spec('src.agent.planning.step_templates') is not None
```

- [x] 跑该文件，预期明确断言失败：缺少模板模块；再记录委托与模板调用测试失败，不改生产直到确认 RED。

## Task 2：最小提取

- [x] 新模块只导入 `Any` 与 `WorkflowStep`，五个函数签名按已确认设计执行。
- [x] 完整搬迁旧 `steps` 列表中的 WorkflowStep 构造表达式，不改各字段字面值。三个生成步骤中的 `build_generation_request(...)` 替换为入参 `generation_request`；调用仍由 Planner 在同一合法分支执行。
- [x] ADMET 模板对应原局部列表与条件追加，条件仅由调用者传入 `include_admet`。Planner 的完整替换方法为：

```python
def _admet_plan(self, query: str) -> WorkflowPlan:
    return WorkflowPlan(
        workflow_name="admet_assessment",
        steps=step_templates.admet_steps(query, include_admet=self._wants_admet(query)),
        metadata={"input_type": "molecule"},
    )
```

- [x] 其余四个 `steps=` 替换表达式如下，原 plan metadata、控制流、错误处理不动：

```python
step_templates.comprehensive_steps(query)
step_templates.target_design_steps(
    target_hint=target_hint,
    generation_request=build_generation_request(query, requested_count),
    docking_top_n=docking_top_n,
)
step_templates.molecular_design_steps(build_generation_request(query, requested_count))
step_templates.lead_optimization_steps(
    query, generation_request=build_generation_request(query, requested_count),
)
```

- [x] `from . import step_templates` 保留为内部模块导入；不改包导出或 WorkflowPlan 类型位置。
- [x] 重跑新测试及五文件基线，预期全部 PASS；若行为差异先定位，不修改预期以迎合实现。
- [x] 精确暂存生产两文件及单元测试，提交本步骤。

## Task 3：执行级验证（可独立委派）

- [x] 在独立测试文件构造受控 ToolResult 工具，通过真实 WorkflowExecutor 而非直接手调模板执行计划。
- [x] 综合评价的 reverse_target 输出必须进入 target_search；先导原始性质必须进入生成输入，候选 SMILES 必须进入性质输入。
- [x] 靶点设计先检索后生成，返回候选经属性、ADMET、活性进入排名；确认 tool 调用顺序及可选失败继续。
- [x] 必需步骤失败后不调用后继工具；保留错误、warnings 与事件。所有来源字段标为测试数据，不宣称科学通过。
- [x] 迁移前后分别运行相同测试，不增加 skip/放松时间门槛。精确暂存独立文件提交。

## Task 4：审查与交付

- [x] 独立规格审查先行，通过后独立质量审查，修复有效意见并复审同一生产快照。
- [x] 全 `tests/agent` 及相关联合范围、断网 contract、源码内存 compile、diff-check；失败如实留档。
- [x] 更新 `docs/AGENT_MAINTENANCE.md` 的 Planner 职责说明和回归命令，交接记录改前改后等价、实际统计、未完成任务。
- [ ] 自检无凭据/运行资产/用户改动进入暂存，仅提交本任务文件；创建 draft PR，附到任务。CI 全通过不自动授权合并。

## 测试启动

使用已验证的 `docs/superpowers/plans/2026-09-24-rag-service-extraction.md` 中 PowerShell `$runner` 包装，工作树定位换成本分支；保持临时配置/数据库、无凭据环境、real/canary 关闭、正常 pytest 子进程及离线 fixture 哈希校验。

聚焦参数：

```text
tests/agent/test_planner_step_templates.py tests/agent/test_planner_template_execution.py tests/agent/test_task_planner.py tests/agent/test_plan_compiler.py tests/agent/test_binding_resolver.py tests/agent/test_workflow_executor.py tests/agent/test_decision_loop.py
```

包装实际执行 `python -B -m pytest <上述绝对路径> -q -p no:cacheprovider --tb=short -rs`。
迁移前五文件基线已经完成：421 passed，14.66秒，exit 0。后续统计写入交接，不覆盖失败记录。
