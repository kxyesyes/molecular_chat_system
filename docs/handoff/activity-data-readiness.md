# 活性数据就绪层实施检查点：2026-09-07

分支：`codex/activity-data-readiness`。在独立 `scientific-assets` 工作树实施。
原始项目混杂工作树未改动，未合并 main。

## 当前检查点：任务 5 完成

注册状态升级为版本 2，增加 `active_models_by_endpoint`，保留旧全局选择。
不同端点只能选择身份匹配的就绪模型；不存在或权重/模型卡内容损坏时不回退到全局模型。
版本 1 原子迁移不自动推断端点，删除时在原事务中清除所有关联选择。

共享纯校验器约束端点身份、输出单位、拆分、指标与 provenance；
注册、选择、读取另验证实际权重与模型卡的哈希和内容一致性。
模型卡和权重统一进行严格文件名、解析后路径和文件归属检查，
封堵 Windows 尾点、尾空格、短路径别名引起的跨模型文件冲突。
训练 metadata builder 支持可选 `prepared_metadata` 扩展，旧调用保持兼容；
该 builder 不注册、不激活模型，也不改变现有训练循环。

新增/修改：

- `src/activity/model_registry.py`
- `src/activity/trainer.py`
- `tests/activity_test_support.py`
- `tests/test_activity_model_registry.py`
- `tests/test_activity_endpoint_selection.py`
- `tests/test_activity_trainer_endpoint_metadata.py`
- `docs/activity_model_registry.md`

任务 5 规范与代码质量审查均 Approved。
在 MedChat Conda Python 环境的最终验证：

- 注册表、端点选择、训练元数据三项测试：**162 passed**。
- 加上 activity prediction、dataset contract、prepare CLI、RG-MPNN legacy、
  phase2/phase3 routes 的八项扩展测试：**497 passed、2 skipped**。
- 两个跳过项来自此前数据发布模块的本机符号链接限制；未新增跳过项。
- `python -m compileall -q src scripts` 与 `git diff --check` 通过。
- 本阶段未重复运行全量 `tests`；下面保留的是任务 1–4 的历史全量结果。
- 测试仅使用临时合成产物验证工程契约，未训练或安装真实模型。

聚焦命令：

```powershell
python -m pytest tests/test_activity_model_registry.py tests/test_activity_endpoint_selection.py tests/test_activity_trainer_endpoint_metadata.py -q -p no:cacheprovider --tb=short
python -m pytest tests/test_activity_model_registry.py tests/test_activity_endpoint_selection.py tests/test_activity_trainer_endpoint_metadata.py tests/test_activity_prediction_contract.py tests/test_activity_dataset_contract.py tests/test_activity_prepare_cli.py tests/test_rg_mpnn_legacy_modules.py tests/test_phase2_phase3_routes.py -q -p no:cacheprovider --tb=short
```

## 已完成

- 任务 1–2：manifest、终点身份、单位转换、无效输入与重复测量校验。
- 任务 3：重新计算分子与骨架身份，三路确定性拆分，来源文件与内容哈希分别绑定，
  CSV 类型与浮点往返，完整重复测量证据，临时目录校验后原子发布并拒绝覆盖。
- 拆分算法标识升级为 `deterministic_scaffold_greedy_v2`；避免平方级候选遍历，
  有 1 万骨架规模的工作量与内存回归测试。
- 任务 4：CSV/TSV 数据准备 CLI、只校验模式、UTF-8 JSON 输出、退出码与隐私约束，
  Git 忽略规则及操作说明。
- 任务 3、4 均经独立规范和代码质量审查通过。

本轮关键提交：

- `68437c3`：数据发布、算法版本、性能和重复证据加固。
- `d0288fe`：CLI、子进程测试、忽略规则和使用说明。
- `8c8cf74`：没有可选证据列时也保留完整重复测量。

## 关键文件

- `src/activity/dataset_contract.py`
- `tests/test_activity_dataset_contract.py`
- `scripts/prepare_activity_dataset.py`
- `tests/test_activity_prepare_cli.py`
- `.gitignore`
- `docs/activity_data_preparation.md`
- `docs/superpowers/specs/2026-09-06-activity-data-readiness-design.md`
- `docs/superpowers/plans/2026-09-06-activity-data-readiness.md`

## 验证

在已安装 RDKit 的 MedChat Conda Python 环境运行：

| 命令 | 结果 |
|---|---|
| `python -m pytest tests/test_activity_dataset_contract.py tests/test_activity_prepare_cli.py -q -p no:cacheprovider --tb=short` | 最终修改后 313 passed、2 skipped |
| `python -m pytest tests/test_activity_prepare_cli.py -q -p no:cacheprovider --tb=short` | 21 passed，包括 UTF-8/GBK 子进程 |
| `python -m pytest tests -q -p no:cacheprovider --tb=short` | 6507 passed、235 skipped、1 failed，另 171 subtests passed |
| `python -m pytest tests/sandbox_broker/test_service.py::test_successful_destroy_is_invoked_once_without_replacement -q -p no:cacheprovider --tb=short` | 单独复跑 1 passed |
| `python -m pytest tests/sandbox_broker/test_service.py -q -p no:cacheprovider --tb=short` | 模块复跑 172 passed |
| `python -m compileall -q src scripts` | 通过 |
| `git diff --check` | 通过 |

全量测试在最后一项重复证据补丁前启动；该补丁随后用完整数据契约与 CLI 测试验证。
不得把本轮全量测试表述为全部通过。
唯一全量失败是沙盒清理测试
`test_successful_destroy_is_invoked_once_without_replacement` 的任务终态断言，
单独及整个模块复跑通过；具体导致波动的原因待确认，未改动沙盒业务代码。
数据契约的两个跳过项涉及本机无法建立的符号链接场景，不能宣称已在 Windows 上验证这些场景。

## 后续

继续任务 6：衔接 prepared dataset 训练、独立测试集指标和模型卡生成。
随后任务 7 迁移端点推理与 Agent 调用，
任务 8–9 完成健康检查、最终文档及 PR。

真实 PDE/BuChE 数据尚待用户提供。本轮只使用临时合成数据验证工程契约，
没有训练真实模型，也没有评估真实活性预测性能。
