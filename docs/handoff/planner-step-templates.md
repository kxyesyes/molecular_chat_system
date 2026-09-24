# T10-B Planner 纯步骤模板提取交接

## 范围与状态

用户已确认书面设计。本批基于 main `75d6a3abc6f6d79e96bf38a019ce2980576c100e`，
分支 `codex/planner-step-templates-pr`；设计提交 `1e973dd`，实现提交 `6d4f3f6`。
只提取五类纯步骤模板，不改解析/编译/执行/科学校验，不调用真实模型或服务。
独立规格/质量审查及本地联合回归已通过。远端 PR/CI 状态以 PR 描述和检查页为准；未通过远端门禁且未获明确授权，不视为可合并。

## 文件与行为

- `src/agent/planning/step_templates.py`：ADMET、综合评价、靶点设计、分子生成、先导优化五个纯函数；每次新建步骤和自有 metadata，不修改传入的生成 request。
- `src/agent/planning/task_planner.py`：只将五个步骤构造位置改为委托；从 547 行缩减到 354 行。WorkflowPlan 定义/导入身份、参数解析、错误分类、靶点澄清及其他分支保留。
- `tests/agent/test_planner_step_templates.py`：71 项全字段特征、数量与分支、辅助方法覆写、模板委托、可变对象隔离测试。
- `tests/agent/test_planner_template_execution.py`：8 项真实执行器/编译器/绑定/校验器测试；工具体受控且数据明确标为合成。验证上游输入消费、可选失败继续、必需失败停止、错误/warnings/events 和科学安全门。
- `docs/AGENT_MAINTENANCE.md`：更新职责定位、测试命令及未完成事项。
- 本主题设计、实施计划及本交接：记录边界、TDD 与验证，不替代统一维护指南。

改前：五类步骤与选择/参数解析同处 TaskPlanner。
改后：步骤描述在纯模板模块；相同输入的 WorkflowPlan/异常保持一致，不建立第二套执行器。
生成候选→性质/ADMET/活性→排序、反向寻靶→结构检索、baseline→生成等路径仍由现有编译器和执行器控制。

## 实际验证记录

Python 使用本机 MedChat Conda 环境；除静态编译外，复用
`docs/superpowers/plans/2026-09-24-rag-service-extraction.md` 的隔离包装，
将 repo 指向本工作树。清除非必要环境、临时配置/数据库/cwd、关闭 real/canary，
正常 pytest 子进程，不读取密钥或私人运行资产，不减少断言或新增 skip。

| 阶段 | 结果 | 说明 |
|---|---|---|
| 迁移前既有五文件 | 421 passed，14.66s，exit 0 | planner/compiler/bindings/executor/decision-loop |
| 新特征测试首轮 | 12 failed、30 passed，0.66s，exit 1 | 测试对英文靶点语句及错误分类预期不符，生产尚未修改 |
| 校正现状预期 | 43 passed，0.69s，exit 0 | 保留英文澄清现状为单独用例；数量错误按既有类型精确断言 |
| 结构性 RED | 18 failed、43 passed，0.86s，exit 1 | 18 项均因纯模板模块不存在而明确断言失败 |
| 首轮 GREEN + 五文件 | 482 passed，13.55s，exit 0 | 新模板字段、委托及旧基线全部通过 |
| 扩充边界后的单元文件 | 71 passed，0.61s，exit 0 | 加缺省数量、Top N、歧义、别名与搜索提升 |
| 执行测试旧 Planner 首轮 | 1 failed、7 passed，0.28s | 新测试误用 skipped-step 字段 step，实际为 step_id |
| 执行测试旧 Planner 校正后 | 8 passed，0.26s，exit 0 | 以 git show 基线源码在内存加载，无覆盖工作树 |
| 执行测试迁移后 | 8 passed，0.48s，exit 0 | 相同用例不变 |
| 最终七文件聚焦 | 500 passed，13.91s，exit 0 | 两个新文件 + 既有五文件 |
| 临时等价性探针 | 2 passed，0.64s，exit 0 | AST 检查未动方法及构造表达式；2,016 组 plan/异常前后对照一致 |
| 全 Agent + 相关联合 23 路径 | 5248 passed、9 skipped、7 warnings、9 subtests passed，213.28s，exit 0 | 无失败；未把 skipped 算作成功 |
| 断网 contract | 34/34 passed，pass_rate 1.0，exit 0 | socket connect/connect_ex/create_connection 禁用；不是真实科研验收 |
| 静态验证 | 306 个 src/scripts Python 内存 compile、12 个 Node 脚本、diff-check 通过 | 未产生 pyc；无 JS 修改；凭据模式扫描 0 命中 |
| 独立规格审查 | SPEC APPROVED；独立新测试 79 passed，exit 0 | AST 核对五类构造和未迁移代码，未发现规格偏离 |
| 独立质量审查 | QUALITY APPROVED；独立七文件 500 passed，15.10s，exit 0 | 未发现待修项，未独立重跑整个联合范围 |

首轮失败没有算作成功，也不声称是生产缺陷修复后的 RED。
读取路径核对中出现不存在的辅助文件路径及一次不支持的通配符搜索，随后改用真实路径；未导致文件改动。

联合的9项 skip：目录 symlink 权限、性能 opt-in 关闭、LLM 配置 symlink 权限、
两项 POSIX 目录权限、两项要求独立任务库、一项要求已配置 runtime、一项 POSIX process-group。
未新增 skip，7个警告为既有 SWIG/FastAPI on_event 弃用。

两次独立审查和本地回归对应相同生产快照 SHA-256：

- `task_planner.py`：`d5b33e8c3471d106212af468e87c90059d1de44cdbe61838838e03699f48c7d6`。
- `step_templates.py`：`2cd9b3f6e6ef26c690196dc0d7a2826923810d0e1287e95466340d8990ab21e3`。

维护指南62个本地链接、30个测试路径校验通过。文档收尾不改变已审生产快照。

### 命令与产物

通过隔离包装传入实际测试路径，包装最终执行：

```text
python -B -m pytest tests/agent/test_planner_step_templates.py tests/agent/test_planner_template_execution.py tests/agent/test_task_planner.py tests/agent/test_plan_compiler.py tests/agent/test_binding_resolver.py tests/agent/test_workflow_executor.py tests/agent/test_decision_loop.py -q -p no:cacheprovider --tb=short -rs
python scripts/run_agent_acceptance.py --mode contract --output scratch/t10-planner-step-templates.json
git diff --check
```

联合回归在同一隔离包装中传入以下23路径，再追加 `-q -p no:cacheprovider --tb=short -rs`：

```text
tests/agent
tests/test_activity_prediction_contract.py
tests/test_model_request_lifecycle.py
tests/test_design_model_switch.py
tests/test_molecular_design_architecture.py
tests/test_llm_runtime_config.py
tests/test_user_llm_routes.py
tests/test_agent_llm_wiring.py
tests/test_task_runtime.py
tests/test_phase2_phase3_routes.py
tests/test_agent_anti_hallucination_fallbacks.py
tests/test_agent_platform_health_check.py
tests/test_agent_session.py
tests/test_agent_session_entrypoints.py
tests/test_agent_task_ownership.py
tests/test_rag_index_manifest.py
tests/test_web_app_lifecycle.py
tests/test_openai_compatible_model.py
tests/test_rag_service_boundary.py
tests/test_main_routes_template_compat.py
tests/test_static_placeholder_cleanup.py
tests/test_docking_agent_architecture.py
tests/test_docking_configuration.py
```

合同报告为 ignored `scratch/t10-planner-step-templates.json`，SHA-256：
`70e647fba2756275ddeb7564d427922b8cef8ea6ede75b6f1db7392e0160911b`。
临时等价性探针 `scratch/planner_equivalence_probe.py` 不提交：只从 Git 读取旧实现做对照，不引入运行时第二实现。

## 已知限制与下一批

- 英文 `Design 2 molecules for PDE5A and select top 3` 在原实现即触发靶点澄清：`and select` 中 select 被当作额外未知标识。保持现状，不把测试换用中文描述成路由已修复；该问题可另批最小修复。
- 先导优化 metadata 的缺省表达式仍会调用数量解析，错误仍传播；保留现有语义，不借机统一为其他分支的拒绝计划。
- 本批不修复此前尚未定位的 docking 偶发测试问题；通过回归不等于该问题已定位。
- T09 跨轮科研对象引用、T11 其他职责拆分与旧 Agent 接口收缩仍未完成；不能将本批视为整个任务书完成。
- 未运行 real/all、未训练/启用模型、未部署。没有改运行资源管理或依赖，无部署健康检查的通过声明。
- 原始工作树 13 项混杂改动未纳入本分支。后续发布只精确暂存本批文件，PR 需独立审查、CI 门禁和明确合并授权。
