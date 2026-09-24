# 活性预测 Adapter 类型契约交接

日期：2026-09-24。状态：实现、独立双审与本地验证完成；进入独立草稿PR发布门禁，尚未合并。

## 范围

用户已确认最小方案及书面设计。基于 main `4476568b7da9aed7338118206badbc09cc410468`，在独立分支 `codex/activity-typed-contract-pr` 实施。当前设计提交为 `a5fa2bc021e2f987a9cd4a8b0eece11bd4e28fe4`。不得修改原始混杂工作树。

生产写集仅 `src/agent/tooling/activity_contract.py` 和 `src/agent/tooling/factory.py` 的活性专用选择。保留解析器、科学校验、阈值、预测服务、工具直接调用和执行资源生命周期；不训练或启用模型、不调用外部 API，不改 docking、Planner 或旧 Agent。

## 实际复现

新增 `tests/agent/test_activity_contract_integration.py` 使用真实 Registry、Activity 专家、SpecialistDispatch、ActivityPredictorTool、输入解析和科学校验；仅模型推理边界注入明确合成输出。

首次实现前运行：**5 failed、16 passed，2.15 秒，exit 1**。
随后增加六个真实单模型工具兼容测试：**5 failed、22 passed，1.33 秒，exit 1**。

五项失败证明：

- 直接结构化 `{smiles, target}` 被旧通用 schema 因缺 query 拒绝（PDE5A、BuChE 各一项）。
- 直接结构化 `{query, smiles, target}` 被通用 schema 过滤掉顶层科学字段，只剩无结构文本（两个家族各一项）。
- 原始字符串在 Registry 边界被拒绝。

已有一层委派包装路径通过，不能把问题表述成“所有委派输入都丢失”。新增六项基线证明实际单模型工具的分类/回归成功、混合失败、全失败输出可被现有 `execute_tool_compat` 保留；新 Adapter 必须保持同样结果（耗时除外）。

## 本轮测试命令

PowerShell 从已有计划取出隔离 runner，替换唯一工作树路径；未读取秘密：

```powershell
$python='C:/Users/xkx52/.conda/envs/MedChat/python.exe'
$plan=Get-Content docs/superpowers/plans/2026-09-24-rag-service-extraction.md -Raw
$m=[regex]::Match($plan,'(?s)\$runner = @''\r?\n(.*?)\r?\n''@')
$runner=$m.Groups[1].Value.Replace(
  'D:/MedChat/molecular_chat_system_worktrees/rag-service-extraction-pr',
  'D:/MedChat/molecular_chat_system_worktrees/activity-typed-contract-pr')
$runner | & $python -B -c "import sys; exec(sys.stdin.read())" tests/agent/test_activity_contract_integration.py
```

该 runner 清空非白名单环境、使用临时 cwd/配置/数据库、关闭 real/canary；底层运行 `python -B -m pytest <absolute paths> -q -p no:cacheprovider --tb=short -rs`。上述测试没有使用真实模型或权重。原四文件迁移前基线 354 passed 记录在设计文档，不冒充修改后通过。

## 门禁与未完成事项

目前实现、GREEN、独立 SPEC/QUALITY、联合回归、离线 contract 和编译已完成；本地提交后仍须最新 CI 和具体 PR 合并授权。远端最近只读核验 main 为 `4476568...`。本交接记录不预先声称 CI 或合并成功。

### 首轮实现与审查（未通过最终门禁）

- 实现者记录：契约 RED 222 failed/5 passed；使用旧版本已支持的 query 包装单独复现输出缺陷，9 failed。首轮 GREEN 632 passed/0 skipped/7 warnings，包括新契约251、实际集成27、原基线354。
- 独立 SPEC 实际重跑：新契约与集成278 passed（2.43秒）；四文件基线354 passed/7 warnings（14.15秒）。另补13个实际 Registry 探针，**9 failed/4 passed**（1.20秒），因而结论为 **NOT APPROVED**。
- 两处 P1：家族字段可遮蔽附加的 task-aware 单模型 NaN/demo 声明；已规范化失败的 `error.details.raw_result.data` 和已知 `evidence[].prediction` 未校验科学内容。只校验已知科学证据载体，不递归猜测任意扩展元数据；实现者正在补永久 RED 并修复。
- 父任务首次21路径联合回归：**3 failed、4833 passed、8 skipped、7 warnings，255.77秒，exit1**。完整路径见实施计划及下节，不当作通过。三项失败分别为 `test_decision_merge_blockers.py::test_targetless_evidence_cannot_satisfy_later_target_obligation` 和 `test_supervisor_runtime_integration.py` 的持久化、幂等复用两项。
- 定位到旧合成桩分别返回 property 字典和只有 smiles 的列表，没有真正的活性观察字段。窄复现3 failed/1 passed（4.16秒），只补上述两文件的合成活性数据和来源，不改生产代码或原有成功/续接/事件/复用断言；整两个文件重跑 **49 passed，10.69秒，exit0**。
- 离线 contract 在原隔离包装内替换 pytest 调用为 `runpy.run_path`，显式阻断 socket connect/connect_ex/create_connection：`scripts/run_agent_acceptance.py --mode contract --output scratch/t10-activity-typed-contract.json`，**34/34 passed，pass_rate1.0，exit0**。报告 SHA256 `7df930505aaae196bb1f219d47ffebdc76ab8c17d863ae01f3b60d69007a9182`。是工程契约，不是真实科研验收；修复审查问题后须再跑。
- 当前src/scripts跟踪文件加新契约共304个Python文件内存编译通过；Node按CI列表11个`*_test.js`加`activity_family_acceptance_dom.js`共12文件exit0；diff-check通过。其时点在审查修复之前，不代替最终代码快照验证。

首次联合使用相同隔离 runner，pytest参数如下（21路径）：

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
```

八项skip：Windows目录符号链接两项、POSIX目录权限两项、未启用shadow性能一项、需要独立任务库或已配置runtime的归属集成三项。七个warning为SWIG/FastAPI弃用。没有为本批新增skip或弱化断言。

### 审查修复后的验证

- 两处漏洞新增永久回归先得到 **16 failed/15 passed**；修复后六个聚焦文件加13个独立SPEC探针为 **676 passed/7 warnings**，实现者和SPEC复审各自重跑确认。
- 独立 SPEC 复审 **APPROVED**：另跑两个控制器fixture文件49 passed；42个进程内探针确认循环/深度、规范化后注入、原始数据保留、一次执行和关闭边界。无未解决阻断项。复审的契约文件SHA256：`9a3eb7e0ef075b378119b9bbad819164bf3fa658b246b2f9322281775cd96ca4`。
- 父任务按上述相同21路径完整重跑：**4867 passed、8 skipped、7 warnings，251.97秒，exit0**。这是Agent/活性/Web/RAG/生命周期组合，不是全仓、真实权重或生产验收。
- 再跑相同socket阻断的离线contract：**34/34 passed、pass_rate1.0、exit0**；新报告SHA256：`2dac118f8339a12258f5ffc10928d3bc4c8fac1d794662f5986d7fb1f11ab57e`。覆盖了前一份忽略报告，保留各次摘要而不提交产物。
- 修复后304个src/scripts文件内存编译和diff-check再次通过。未动前端源码；12个Node脚本此前通过。暂未验证最低Pydantic2.5.0，本地为2.12.5，后续CI需实际确认。
- 独立 QUALITY **APPROVED**：新契约/集成309 passed，三个fixture调整文件86 passed/7既有warnings，另27个独立边界探针passed，共422个不同用例、0skip。检查dataclass污染、嵌套model_construct、16/17层界限、循环和compat后注入，无未解决问题；没有重复声称跑过整个Agent组合。

## 最终本地写集

1. `src/agent/tooling/activity_contract.py`（新增）
2. `src/agent/tooling/factory.py`
3. `tests/agent/test_activity_tool_contract.py`（新增）
4. `tests/agent/test_activity_contract_integration.py`（新增）
5. `tests/agent/test_registration_consistency.py`（只补fixture）
6. `tests/agent/test_decision_merge_blockers.py`（只补fixture）
7. `tests/agent/test_supervisor_runtime_integration.py`（只补fixture）
8. `docs/superpowers/specs/2026-09-24-activity-typed-contract-design.md`
9. `docs/superpowers/plans/2026-09-24-activity-typed-contract.md`（新增）
10. `docs/handoff/activity-typed-contract.md`（新增）

上述路径均位于独立activity-typed-contract-pr工作树。scratch中的探针、报告和PR正文不提交；原项目13项混杂状态未改动。没有更新原预测器、家族服务、通用Adapter、模型资产或生产配置。后续CI状态应以远端实际head为准，不能沿用前一PR的绿灯。

本批不是全任务书完成。T09 跨轮科研对象、其余 T10 docking 契约与 Planner、T11 领域路由及旧 Agent 兼容整理仍未完成。

另有已登记的历史诊断，不混入本批：全仓包装的全局靶点路径覆盖导致测试污染、Windows CLI GBK/UTF-8 解码问题、旧 ReAct 单工具返回 tuple/ToolResult 不兼容。此前全仓运行并非全绿（10 failed），也不是严格离线；不要以本批聚焦结果覆盖历史失败。
