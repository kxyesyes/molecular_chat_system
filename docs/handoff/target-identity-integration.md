> 历史来源报告：以下记录不是本次 P04 候选的验收结果。
> 当前只整理 T08，不含 P05 会话归属；本轮身份与成绩见协调工作树 docs/handoff/t08-patch-review.md。

# T08 靶点识别补丁集成（2026-09-23）

## 边界

在独立工作树 `codex/delegated-session-baseline` 的 T02/T04/T07 组合基线上，
逐文件迁入 `codex/target-identity-alignment` 的 T08 实现与测试。
原始 `D:/MedChat/molecular_chat_system` 的用户改动未改写；没有提交、推送、
合并、部署、真实模型调用或密钥读取。两个原分支仍各自存在，不能将本集成
视为已在 `main` 生效。

## 修复与依据

测试先迁入 `tests/agent/test_target_identity_alignment.py`，在组合基线上
运行得到 **26 failed / 3 passed**：BuChE 同义词走通用分子设计；多靶点、
未知靶点、否定/切换请求可能被静默选择；强制指定 skill 会绕过澄清。
随后迁入有界靶点别名表、保守的请求分析、Router/Planner/工具/聊天入口
对齐。迁入后新增测试 **29 passed**，与 T07 和现有路由/家族契约的聚焦
联合测试 **497 passed**。这只识别请求，不代表本地结构、模型权重、
靶点库条目实际可用；无证据时仍由既有科学前置条件拦截。

本批新增 `src/target_identifiers.py`、
`src/agent/contracts/target_request.py`、
`tests/agent/test_target_identity_alignment.py`。
修改 `src/activity/family_contract.py`、
`src/agent/planning/task_planner.py`、`src/agent/routing/hybrid.py`、
`src/agent/runtime/workflow_executor.py`、`src/agent/supervisor.py`、
`src/agent/tools/target_database_tool.py`、`src/web/chat_handler.py`。
另外更新 `tests/agent/test_entrypoint_outcome_parity.py` 的一处旧 fixture：
它强制指定 `target_driven_design`，但原 prompt `design 2 molecules` 不含靶点。
新澄清边界使该 fixture 的 `run`/`execute` 两种参数均返回 failed；
将 prompt 改为明确的 PDE5A 目标后，仍验证候选覆盖率不足会产生 partial，
其余断言未放宽。该测试文件单独运行 **68 passed**。
`src/agent/supervisor.py` 上保留 T07 的委派 Session 迁移，
仅增加靶点澄清的 preflight 通道，没有覆盖其执行改动。

## 验证与限制

- `python -B -m pytest tests/agent/test_target_identity_alignment.py -q -p no:cacheprovider --tb=short`：29 passed。
- T08/T07/路由/Planner/家族契约聚焦联合：497 passed。
- `python -m compileall -q src scripts` 与 `git diff --check`：通过。
- `python -B scripts/run_agent_acceptance.py --mode contract --output outputs/agent_evaluation/t07_t08_integration_contract.json`：passed；无效 SMILES 的 RDKit 解析提示是负例预期。
- 首次完整组合回归：3798 passed / 2 failed / 4 skipped；两项失败是上述
  同一个旧 fixture 的 `run`/`execute` 参数化分支。
- 修正 fixture 后运行：
  `python -B -m pytest tests/agent tests/test_activity_family_contract.py
  tests/test_target_search_fallback.py tests/test_phase2_phase3_routes.py
  tests/test_agent_anti_hallucination_fallbacks.py
  tests/test_agent_platform_health_check.py -q -p no:cacheprovider --tb=short -rs`
  → **3800 passed / 4 skipped / 7 warnings**，158.38 秒，退出码 0。
  四项 skip 分别是两项 Windows 符号链接不可用、性能测试未开启、
  真实权威靶点在线检索未启用。弃用警告来自 SWIG 与 FastAPI `on_event`。

独立子审查未完成，不应声称已审查通过。T09 在任务书中明确要求
单独授权，本批不实施。真实数据库/模型验收与集成分支的提交/PR
也未进行。

## 后续集成审查补测

审查发现普通闲聊短路发生在靶点分析之前：`你好，搜索 EGFR 蛋白结构`
和 BuChE 变体都被当作纯闲聊。先新增两项失败用例（2 failed），
再让 `_is_general_chat` 尊重共享的已识别靶点模式；单纯问候仍保持
无科学工具路由。新增用例、T08 和现有 Router 矩阵联合 **168 passed**。
这不改变未知靶点的通用自然语言理解边界。
更新后再次运行完整组合回归：**3802 passed / 4 skipped / 7 warnings**，
178.17 秒，退出码 0；跳过原因与上次相同。`--mode contract` 再次 passed，
`compileall` 与 `git diff --check` 通过。完整组合测试和 contract 都未调用
真实外部主模型，也不证明在线靶点库/科学工具实际可用。
