# 英文候选筛选短语：本地修复交接

日期：2026-09-24。分支 `codex/target-selection-phrase-pr`，基线 `75d6a3a`。
用户已确认书面设计并授权 TDD；本批不推送、不部署、不启用模型。
后续确认已授权合并 PR #53，现已合并为 `5f56053`；本分支通过 `4ea8012` 对齐该基线。

## 修复前后

输入 `Design 2 molecules for PDE5A and select top 3`，原解析器把 select 当未知靶点，Router 要求澄清，Planner 返回空步骤。

新增句尾完整后缀判别后，该请求进入 `target_driven_design`，保留 `PDE5A`、生成数量2、Top N=3以及原有6步计划。数量只是用户约束，不承诺一定产生3个可排序候选；执行层原有证据/数量/SMILES门禁仍有效。

接受 and 或中英文逗号后的 `select top N`，N 为1–100的ASCII正整数，可带 candidates/molecules、一个句末标点和尾部空白。单词/连接符之间不跨行；完整短语结束后的换行空白与 Router 原 `strip()` 保持一致。新增识别不改写查询，不把 select 加入全局动作词。

未知选择、多个靶点、否定/切换/选择性、非法数字和额外尾部条件继续澄清。真实执行器遇到靶点 not_found/unavailable 仍失败，生成及后续科学工具零调用。受控服务只验证安全边界，不是真实科学验收。

## 文件

- `src/agent/contracts/target_request.py`：唯一生产修改，完整有界短语判断及既有循环接入。
- `tests/agent/test_target_selection_phrase.py`：实际解析、Router→Planner、数字/句法边界、保留未知/限定词、线性操作次数。
- `tests/agent/test_target_selection_execution.py`：真实 prepare/execute 路径的编译绑定和证据失败门禁。
- `docs/superpowers/specs/2026-09-24-target-selection-phrase-design.md`：已确认设计及末尾空白解释。
- `docs/superpowers/plans/2026-09-24-target-selection-phrase.md`：实施步骤、隔离命令及发布依赖。
- 本交接与 `docs/handoff/latest.md`：实际结果和未完成项索引。

## TDD 与已执行验证

所有 pytest 使用实施计划内的隔离 runner（清空继承配置/凭据，临时配置、数据库和缓存，`-B -q -p no:cacheprovider --tb=short -rs`）。

1. 新测试首轮155 failed/1 passed：包含测试夹具遗漏 trace_id，不能全算业务 RED。
2. 补 trace_id 后 **130 failed/26 passed，1.71s**：合法 select 被放入 unknown，负向场景保持澄清，是已确认的业务 RED。
3. 初版修复后125 failed/410 passed：124项是新测试误读嵌套 requested_count；另1项揭示 Router 会 strip 末尾换行而 Planner 保留原文。未修改既有生成契约或 Router。
4. 按真实契约修正测试，并单独建立末尾空白用例：**2 failed/156 passed，1.23s**。最小调整尾部空白模式后，四文件 **537 passed，2.80s**。
5. 执行安全测试子任务首轮2 failed/5 passed同为夹具误读字段，修正后与3项既有测试联合 **11 passed**；不冒称独立业务 RED。
6. 新增两文件加 target_identity_alignment、task_planner、routing_prompt_matrix：初轮 **549 passed，2.92s**；独立规格审查发现句尾Unicode空白仍导致Router/Planner分歧，补全角空格/NBSP/行分隔符测试实测 **3 failed，0.78s**。仅将终端空白改为 Unicode 语义，修复后 **552 passed，2.92s**。
7. `python scripts/run_agent_acceptance.py --mode contract --output scratch/target-selection-phrase-contract-final.json`（隔离 runner 额外阻断 socket 连接）：最终代码 **34/34 passed**。无效SMILES的RDKit错误输出是预期拒绝，不是模型服务调用。报告SHA256：`e8d45d766b1fb8b932adc81777a3297adbac8c37d5501fc23a783de594bd8f19`。
8. 内存 `compile()` 检查 src/scripts：**305文件通过**；未写 pyc。`git diff --check` 通过。

9. 首轮联合23路径 **5334 passed、9 skipped、7 warnings、9 subtests，221.21s，exit0**。该轮收集早于后续新边界测试与Unicode修复，作为中间证据保留，不充当最终快照验证。
10. 独立规格复审 **SPEC APPROVED**，审查者隔离运行五文件 **552 passed，2.85s**；额外58个终端空白链路和18个非终端Unicode/未知后缀拒绝探针通过。初审P2及修复证据未隐去。

11. 独立质量审查 **QUALITY APPROVED**：隔离并阻断网络运行新增两文件 **173 passed**，另196个边界探针通过；六类失败输入8K到512K未观察到新增平方回溯。此性能观察不是生产SLA或对任意输入的数学保证。

12. 最终代码联合23路径：**5342 passed、9 skipped、7 warnings、9 subtests passed，222.09s，exit0**。清理完成后隔离 runner 正常退出。

9项跳过分别为：两项Windows目录符号链接权限限制；一项未启用的独立性能测试；两项POSIX目录权限测试；两项需独立任务库、一项需配置运行时；一项POSIX进程组测试。7条warnings来自SWIG和FastAPI `on_event`弃用提示。均未伪装成功、未降低门禁。

本批是离线工程回归，不是全项目真实科研验收。未修改JS，因此没有重新运行Node用例；未修改部署或运行资产，未运行部署健康检查、真实模型或真实对接。

独立双审与最终回归使用的代码/测试SHA256：

```text
src/agent/contracts/target_request.py
5fe60002cf0c20862266b524f0ce6d202dfc8ea1f2c7b8ab4c1595ca3e717dc0
tests/agent/test_target_selection_phrase.py
a56de56a4966b8557544599f2f1ab03bf0954d96436e70012eb27f955c3b31c8
tests/agent/test_target_selection_execution.py
d516644d87d5d6780199db2c075bc703c100c53b43569c1dc7e13df94fce9597
```

联合回归使用实施计划内的同一 `$runner`，实际参数：

```powershell
$joint = @(
  'tests/agent', 'tests/test_activity_prediction_contract.py',
  'tests/test_model_request_lifecycle.py', 'tests/test_design_model_switch.py',
  'tests/test_molecular_design_architecture.py', 'tests/test_llm_runtime_config.py',
  'tests/test_user_llm_routes.py', 'tests/test_agent_llm_wiring.py',
  'tests/test_task_runtime.py', 'tests/test_phase2_phase3_routes.py',
  'tests/test_agent_anti_hallucination_fallbacks.py', 'tests/test_agent_platform_health_check.py',
  'tests/test_agent_session.py', 'tests/test_agent_session_entrypoints.py',
  'tests/test_agent_task_ownership.py', 'tests/test_rag_index_manifest.py',
  'tests/test_web_app_lifecycle.py', 'tests/test_openai_compatible_model.py',
  'tests/test_rag_service_boundary.py', 'tests/test_main_routes_template_compat.py',
  'tests/test_static_placeholder_cleanup.py', 'tests/test_docking_agent_architecture.py',
  'tests/test_docking_configuration.py'
)
$runner | & $python -B -c "import sys; exec(sys.stdin.read())" @joint
```

## 未完成与下一步

- 本分支尚未发布，无新 PR/CI；不得将本地回归称为远端 CI。
- PR #53 已获后续明确授权并 squash 合并；已对齐并更新旧 select 澄清特征测试。
- 不修改原始工作树的13项历史改动；未部署，用户正在运行的服务不会自动切换到本地修复。
- 跨轮候选引用、旧 Agent 接口等任务书余项不属于此修复，仍需独立处理。

## PR #53 合并后的集成验证

PR #53 最新 7/7 CI、无未解决审查意见核验后，授权 squash 合并为
`5f56053bb457187ea71d22671265ff63a466aeba`。合并树与已审 head `97fc57e` 一致。
本分支 merge `origin/main` 产生 `4ea8012`，未改写历史。

旧测试 `test_existing_select_top_target_clarification_is_not_changed_by_extraction`
实测 **1 failed，1.66s**：它要求空计划，与本批批准的合法筛选行为冲突。
仅将这一条更新为完整手写 target fixture 比较，替换 query，保留六步顺序、输入绑定、
证据前置条件、输出契约和 required/optional 断言；不改 Planner 模板或科学校验。

隔离 runner 实际运行四文件 target_selection_phrase、target_selection_execution、
planner_step_templates、planner_template_execution：**252 passed，1.74s**。
独立集成审查通过：fixture 非同实现自证，解析修复与既有审查版本一致，规范执行器未变。
审查者未运行测试，其结论不替代本地测试。

集成后离线 contract（阻断 socket）：**34/34 passed**，报告
`scratch/target-selection-phrase-integrated-contract.json`，SHA256
`c9f8d11e287f49e4b68d84e42032bd5a242ebcf868ec63a7767d5a22112851eb`。
src/scripts **306 文件内存编译通过**，未写 pyc。

集成后使用上文同一23路径命令重新运行：**5421 passed、9 skipped、7 warnings、
9 subtests passed，276.62s，exit0**；隔离 runner 清理后输出 `T11A_PYTEST_EXIT=0`。
新增79项来自合入的Planner模板/执行测试，跳过原因与上述分类一致。
精确范围 `git diff --check` 通过，凭据候选扫描0命中；未运行真实模型或部署验收。
