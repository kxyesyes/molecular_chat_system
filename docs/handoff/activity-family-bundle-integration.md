# 家族双模型成组管理：分批集成

日期：2026-09-09。状态：本地验证与两轮独立审查通过，提交 draft PR；远端 CI 及具体合并授权仍是门禁。

## 本轮目标与边界

- 用户确认继续后，复核 PR #11 原提交 `4ed6421`、7/7 CI、空审查意见及无冲突状态，
  squash 合并为 main `c85775a11b11633a7ae03f4f3c3c1411fe2c5874`。
  fetch 后验证其文件树与已测提交一致；没有直接在 main 提交。
- 本批从该 main 创建 `codex/activity-family-bundle-integration` 独立 worktree。
- 按既有 staged integration 顺序，仅整理原家族模型计划 Task 1（成组事务及数据绑定），
  来源 `codex/activity-integration-blockers` / `5432968`；不整体移植旧 model_registry。
- 保留 main 新增的模型卡可选 provenance、字段、metrics 与自引用摘要校验，
  以及 PR #11 的格式来源与序列化预检修复。

## 实施与检查点

1. 先移植合成测试与支持文件，缺失注册 API 测试 RED：1 failed，
   明确缺少 register_family_bundle，非 collection 错误。
2. 在原注册表追加 v3 成组状态/API/迁移/删除清理；新增纯 family_models 验证模块。
   `_verify_model_card` 保留全部现有校验，仅返回已验证 metadata 供家族检查使用。
3. 历史测试若依赖旧的较弱校验边界，只调整为更早拒绝的正确预期，禁止放宽 main。
4. 运行家族、模型卡、registry/endpoint/legacy 回归，再进行范围审查与质量复审。
5. 精确暂存本批文件、提交 draft PR；最终 CI 和具体 PR 授权是后续合并门禁。

## 文件

- `src/activity/model_registry.py`、`src/activity/family_models.py`
- `tests/family_model_test_support.py`、`tests/test_activity_family_models.py`
- `tests/test_activity_model_registry.py`、`tests/test_activity_endpoint_selection.py` 中 v3 迁移预期
- `docs/activity_family_bundles.md`、`docs/activity_model_registry.md`、本记录及 latest 索引

## 验证

- 缺失 API 断言 RED：1 failed；v3 迁移断言 RED：1 failed（实际仍为 2）。
- 初次历史家族测试：61 passed、5 failed，均因 main 已增强的卡片单侧冲突
  （来源摘要、model contract、拆分 seed、来源、许可）在单模型注册阶段更早拒绝。
  调整异常断言位置并增加“注册状态为空”检查，未削弱生产校验。
- 生产实施者独立执行 registry/endpoint/model-card：220 passed；training integration guards：26 passed。
- Agent 与反幻觉/平台健康：1688 passed、1 skipped（未启用性能测试）、7 既有弃用警告。
- `python -B scripts/run_agent_acceptance.py --mode contract`：34/34 passed。
- `python -B -X pycache_prefix=<temporary-dir> -m compileall -q src scripts`、8 个 Node 契约脚本、
  `git diff --check` 通过。
- Python 测试均使用 MedChat Conda、`-B -m pytest -q -p no:cacheprovider --tb=short`，
  全部产物位于独立 worktree 的忽略路径或临时目录，合成内容不代表真实预测性能。
- 最终完整活性回归（`tests/test_activity*.py`）：1030 passed、5 skipped、2 warnings，226.23 秒。
  5 个跳过项均为 Windows 符号链接权限/POSIX 专用测试；在 Linux CI 继续验证。
- 家族聚焦复跑：`tests/test_activity_family_models.py`，66 passed，93.74 秒。
- 跳过原因复核：`tests/test_activity_dataset_contract.py tests/test_activity_prepared_training_data.py`
  加 `-rs`，361 passed、5 skipped。
- 范围审查与代码质量审查分别独立通过；质量审查者另跑家族/registry/endpoint/model-card，
  286 passed。远端 CI 状态以 PR 最新 head 为准。以上集合有重叠，不累加计数。
- 本批 10 文件凭据模式扫描无匹配；原始项目 HEAD `f377443` 及混杂状态未改变。

## 未做与后续

没有读取用户数据、秘密或真实权重，没有实际注册/选择模型、迁移线上目录或重启服务。
不包含家族 predictor、训练脚本、Web/API/UI/Agent 入口；模型输出可靠性属于后续验收。
原始项目混杂工作树和运行资产保留。版本回退涉及 v3 状态兼容，不能盲目让旧程序读取新状态。
