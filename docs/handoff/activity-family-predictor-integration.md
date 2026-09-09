# 家族预测器分批集成交接

日期：2026-09-10。状态：本地验证及两轮独立审查通过，提交 draft PR；远端 CI 与具体合并授权仍是门禁。

## 本轮基线与计划

- 用户确认“下一步”后复核 PR #12：head `c3d4819`、7/7 CI 成功、无审查意见、无冲突。
  squash 合并为 main `132a60004d3bca2ad0c92143e85b10de0fb00eb1`；fetch 后确认文件树
  与已测 PR 完全一致，没有直接在 main 提交。
- 创建 `codex/activity-family-predictor-integration` 独立 worktree，从该 main 开始。
- 仅整理既有家族模型计划 Task 2，来源 `codex/activity-integration-blockers` / `5432968`。
  不整体合并历史分支，不移植未来 API/Agent/UI 代码。

实施顺序：先移植预测器测试并验证 RED → 移植独立预测器及必要的原始非有限值防护 →
合成 RGNN 前向/活性/Agent 回归 → 独立规格审查 → 独立代码质量审查 → 精确提交 draft PR。

## 文件范围

- `src/activity/family_predictor.py`：固定模型组、受限字节快照装载、两阶段结果与缓存。
- `src/activity/predictor.py`：仅必要的 sigmoid 前非有限原始输出防护；保留 main 特征追踪。
- `tests/test_activity_family_predictor.py`、`tests/test_activity_family_inference_integration.py`。
  历史集成测试中的未来 API/Agent 分支不属于本批，仅保留 Python 入口，不以 skip 冒充覆盖。
- `docs/activity_family_inference.md`、`docs/activity_family_bundles.md`、本记录及 latest 索引。

## 已读与保护边界

已读 AGENTS、PROJECT_STANDARDS、上批交接、家族模型原计划、registry/bundle 契约、
现有 predictor 和历史家族预测器/测试。使用 TDD 与分工实施、规格/质量两轮独立审查。
main 已有模型卡/数据绑定、序列化预检、`feature_smiles` 与 legacy 发现限制必须保持。

不读取真实密钥/用户 CSV/正式权重，不修改运行注册目录、不启用模型、不重启服务。
原始项目 `f377443` 混杂工作树保留；所有测试资产仅使用临时合成数据。
原始训练 test 集未再次消费。合成权重不作为科学性能或模型晋级证据。

## 验证

- TDD RED：公共 API 1 failed（模块缺失）；加载独立预测器后原始数值防护 2 failed、27 passed，
  正/负无穷被 sigmoid 隐藏成有效概率。先观察失败，再增加转换前的四行有限值防护。
- 实施者聚焦：家族预测器/合成前向 69 passed；加既有特征追踪/预测契约 98 passed。
  模型组、批次对齐、恶意阶段输出、缓存替换/损坏、受限加载及部分失败均有测试。
- `python -B -m pytest tests/agent tests/test_agent_anti_hallucination_fallbacks.py
  tests/test_agent_platform_health_check.py -q -p no:cacheprovider --tb=short`：
  最终复跑 1688 passed、1 skipped、7 既有弃用警告，24.66 秒；跳过项为未启用性能测试。
- 8 个 `tests/*_test.js` Node 契约脚本全部通过。
- `python -B scripts/run_agent_acceptance.py --mode contract`：34/34 passed；
  报告只保存在独立 worktree 的忽略输出目录。
- `python -B -X pycache_prefix=<temporary-dir> -m compileall -q src scripts` 和 diff 检查通过。
- 凭据模式范围扫描无匹配；main 的 registry/model-card/family-models/data/prepared 源码未改变。
- 最终完整活性测试（`tests/test_activity*.py`，同样参数并加 `-rs`）：
  1099 passed、5 skipped、2 既有警告，175.90 秒。5 个跳过项均为 Windows 符号链接权限
  或 POSIX 专用测试，由 Linux CI 补验；实施中较早一次 1088 passed 不作为最终计数。
- 独立 SPEC 和 QUALITY 审查均 APPROVED；分别复跑 98 项聚焦测试，全部通过、无跳过。
  质量审查另以临时夹具验证并发请求与活动组切换，完整模型对与 provenance 保持一致。
- Python 使用 MedChat Conda。测试集合有重叠，不累加；远端 CI 状态以 PR 最新 head 为准。

没有运行部署 health check 或外部 real 验收：本批无部署改动、不启用真实模型，
通过真实 RGNN 合成权重前向验证工程边界，不能冒充真实数据训练模型的科研验收。

## 后续

本批通过并取得具体 PR 合并授权后，再独立整理预测服务/API/Agent 接线；
真实模型训练、评估与生产选用仍需独立验收，不因工程测试通过自动启用。
