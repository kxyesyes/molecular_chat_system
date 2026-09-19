# 家族预测冲突复核接线

## 范围与基线

- 用户已确认设计及实施：保留分类/回归原值，明确计算完成与结论冲突，不重训或切换生产模型。
- 基线 main：`86639344c472549fb9552a9eaae1682c72b86139`，PR #29 已合并。
- 独立分支：`codex/family-prediction-review`。
- 原始混杂工作树的 13 个已修改/未跟踪路径保持原样。
- [设计](../superpowers/specs/2026-09-19-family-prediction-review-design.md)
  与[实施计划](../superpowers/plans/2026-09-19-family-prediction-review.md)。

## 契约

家族行增加 execution_status；双阶段成功但预测冲突为 execution_status=passed、
status=partial、success=false，保留原值和两模型 provenance。一致性阈值仍为
概率 0.5 与 pIC50 5。输入/模型/阶段失败不因残留冲突标记升级为部分成功。
Validator 需重算一致性；只有合法、可追溯的冲突观察可以保留完整数值进入复核展示。
该展示不能授权成功科研 claim 或替代未满足的任务验收条件。

## 验证记录

第一轮父执行验证（独立审查修订前）：

- 指定 MedChat Python，`-B -m pytest tests/test_activity_family_predictor.py tests/test_activity_family_api.py tests/agent/test_family_activity_tool.py tests/agent/test_decision_loop.py -q -p no:cacheprovider --tb=short`：354 passed，1 个已有 PyTorch 性能 warning。
- `-B -m pytest tests/agent -q -p no:cacheprovider --tb=short`：3253 passed、2 skipped、7 个已有 deprecation warnings。
- `node tests/activity_family_results_test.js`：22/22；`node tests/activity_prediction_safe_render_test.js`：通过。
- 修改 JS 的 `node --check`、`python -m compileall -q src scripts`、`git diff --check`：通过。

第一轮独立 SPEC 审查未通过，发现复核分支跳过无关任务验收，以及失败兄弟行使合法
冲突观察不能进入最终复核回答两项问题。两项均纳入 TDD 修订，以上绿色测试不是最终批准。
修订新增测试先观察 8 个失败；父执行决策循环回归 77 passed，原 SPEC 审查者复核
370 Python + 22 Node 通过，两个独立复现转绿，Task1 SPEC PASS。
Task1 QUALITY 独立运行 425 Python、22 Node 与 8 个混合阶段/顺序探针通过，无未解决发现。

验收层 TDD：新增报告边界检查先产生 36 failed、249 passed、3 skipped，修正后
285 passed、3 skipped。直接行检查 4 failed → 4 passed；Node DOM 15/16 →
16/16、再补旧响应保护 16/17 → 17/17。两种相反方向的冲突由明确未训练的
恒定测试输出头构造，实际走 RGNN 前向及完整服务链；不作为真实权重或性能证据。
全链路检查还校验 runtime 追加的可选步骤 warning 原样保留，不吞掉错误信息。

最终集成验证、真实权重复测和 PR 结果见下方后续记录；未执行项目不得以既有绿色
测试代替。

Task2 首轮 SPEC 未批准：发现 API 汇总验证依赖生产 summarizer、未保留实际 API
状态，以及报告投影过滤畸形 errors 后可能误判为无错误。修订要求独立校验 API
状态/成功标记，并保存每次单条/批量 HTTP 的 api_outcomes；投影前先验证原始
错误字段形状和允许字段。父执行真实合成链突变测试先 1 failed，修正后与两方向
冲突合计 3 passed；后续独立复审不能由该绿色结果替代。

Task2 修订报告测试：209 failed、32 passed → 241 passed；完整报告模块
526 passed、3 skipped。SPEC 独立复审通过：108 个原始破坏探针（包括此前漏检的
24 个错误字段探针）及 14 个 API outcome 探针均拒绝，生产 summarizer 突变
回归通过，合法冲突保留原值及 partial，无剩余规格发现。

Task2/全批 QUALITY 独立批准：105 个聚焦 pytest、40 个报告破坏探针和 12 个 DOM
探针通过，语法/diff 检查通过；无未解决质量发现。审查者没有运行真实权重，
下方真实验收由父执行完成，二者不混称。

最终核心验证：`python -B -m pytest tests/agent -q -p no:cacheprovider --tb=short`
为 3269 passed、2 skipped、7 个已有 warnings，159.27 秒；两个跳过分别为性能
测试未显式启用和本机无目录 symlink 权限。
预测器/API 两文件 140 passed（1 个既有 PyTorch 性能 warning）。全部 10 个
`tests/*_test.js` 通过，另有家族验收 DOM 17/17；JS 语法和 src/scripts compileall 通过。

Task2 最终四文件联合回归（chain/support/process/inference_integration）在审查修订后
重跑：681 passed、4 skipped、2 个既有 warnings，340.87 秒。
跳过原因：3 个本机 symlink 权限用例、1 个 POSIX 专属进程用例；不涉及真实权重
验收跳过。相同命令的修订前结果 439 passed、4 skipped 保留为历史，不替代本次结果。

## 本批文件范围

- 核心：`src/activity/family_predictor.py`、`prediction_service.py`；
  `src/agent/validators/domain_validators.py`、`tools/activity_predictor_tool.py`；
  `src/agent/harness/decision_policy.py`、`decision_loop.py`。
- 前端：`src/web/static/js/activity_prediction/results_renderer.js`。
- 测试：预测器/API/Agent/决策测试，`tests/activity_family_results_test.js`，
  `tests/activity_family_acceptance_dom.js`，既有家族验收 chain、report support 与对应测试；
  `tests/family_model_test_support.py` 仅扩展合成测试权重构造。
- 文档：家族 API/推理说明、设计、实施计划、本交接和 latest。
- ChatHandler 与生产入口未修改；通过既有 WebSocket 接线验证部分结果透传。

## 隔离真实权重复测

运行既有 `tests/test_activity_family_real_acceptance.py`，通过进程环境显式设置
`MEDCHAT_RUN_FAMILY_REAL_ACCEPTANCE=1`、已授权 source models 目录及两个固定 bundle ID：
`baseline-20260908-v1-pde-family`、`baseline-20260908-v1-buche-family`。
未读取原始训练 CSV、未重新发现或启用模型组。命令为
`python -B -m pytest tests/test_activity_family_real_acceptance.py -q -p no:cacheprovider --tb=short`。

- 1 pytest item passed，25.99 秒；内部每家族 21 项，共 42/42 工程验收通过。
- 实际 CPU RG-MPNN 双模型前向、HTTP、工具、隔离决策循环、WebSocket 和 Node DOM。
  决策模型为 scripted；不是外部主模型端到端测试，也不是人工浏览器验收。
- PDE 有效输入在七入口保持成功；BuChE 有效输入在七入口均为结果 partial。
  无效 SMILES/未知靶点仍明确拒绝，混合批次保持部分结果。
- BuChE CCO：概率 0.5704101324081421，pIC50 3.9903724193573；
  CCN：概率 0.4363490343093872，pIC50 5.295868396759033。两者计算完成，
  分类/回归判断相反，需复核；没有改值、改阈值或假称模型性能达标。
- 与前次本地报告 `family_acceptance_e116670420d54190b1305f9e3916b358.json`
  按家族/case/行序比较 100 个概率及 pIC50 字段（包括空值），全部精确一致；
  两家族源 digest 完全一致，source_check=passed，production_selection=unchanged。
- 两个子进程 ownership_released、process_cleanup_complete、cleanup_complete 均 true。
- 新报告：`outputs/agent_evaluation/family_acceptance_67bedffe29b54fe794c3733e398783ce.json`，
  仅保留本地、受 Git 忽略，不提交权重/注册库/运行报告。

工程通过表示正确执行和传递实际结果；不表示 42 次独立分子预测准确率，
也不消除 BuChE 已存在的模型分歧。生产入口、模型选择及服务均未切换。

## 不在本批范围

不读取原始训练数据或密钥，不更改权重/注册选择，不部署或重启服务，
不调用外部主模型，不以冲突展示修复宣称模型性能提升。

## 交付与额外回归

- 核心提交 `358f542`，验收接线 `dfda3dd`，文档 `ac0449f`。
- [Draft PR #30](https://github.com/kxyesyes/molecular_chat_system/pull/30)，目标 main；
  已附加当前任务，未合并、未部署。CI 以该 PR 最新 head 检查为准，创建时尚未完成。
- 额外广泛回归命令：`python -B -m pytest tests --ignore=tests/agent --ignore=tests/sandbox_broker --ignore=tests/task_runtime -q -p no:cacheprovider --tb=short`，
  显式 `MEDCHAT_RUN_FAMILY_REAL_ACCEPTANCE=0`：2851 passed、146 skipped、
  171 subtests passed、6 个已有 warnings，856.47 秒。146 个跳过不计入通过数。
  该长运行在最后的报告校验修订前已开始/收集，不能作为新测试全量结果；
  最终六个验收相关文件由修订后四模块联合 681 passed/4 skipped 单独覆盖。
- 凭据形状扫描仅打印命中文件名，本批无 tracked 非文档命中；运行报告被 Git 忽略，
  没有权重/注册库/运行输出进入提交。
- 后续仅在 CI/审查通过并得到具体 PR 合并授权后合并。模型分歧的性能研究或重新训练、
  生产入口启用与外部主模型验收仍需独立任务，不能由本 PR 自动扩大范围。
