# 活性数据就绪层实施检查点：2026-09-07

分支：`codex/activity-data-readiness`。在独立 `scientific-assets` 工作树实施。
原始项目混杂工作树未改动，未合并 main。

## 最新需求补充：PDE / BuChE 家族双模型

用户已提供两份真实 CSV，并确认 pIC50 已是负对数摩尔浓度、允许用于项目训练。
已确认 PDE 各亚型共用 PDE 模型组、BuChE 共用 BuChE 模型组；以 pIC50 5 为分类阈值，
先分类后回归，对“无活性”分子也输出独立回归预测值。前端不重复显示范围说明，
模型卡与机器溯源仍保留真实数据范围。

书面设计：`docs/superpowers/specs/2026-09-07-family-activity-two-stage-design.md`。
当前等待用户审阅，尤其是重复记录的混合中位数聚合策略；尚未编写扩展代码或执行真实训练。
原任务 7 需按此设计扩展为家族级分类/回归模型组，而非只完成单模型端点加载。
真实数据文件未修改、未复制进 Git；本次仅更新设计和交接文档。

## 当前检查点：任务 6 完成

本阶段规范与代码质量审查均已通过。独立质量审查另检查了实际 RGNN 的
batch size 2/4/6 与逐分子前向结果一致性（含尾批）；仅为合成工程检验。

prepared 数据训练内部入口已接通，显式传入 `prepared_manifest_path` 后不回退旧 CSV：
验证已发布字节快照的全部 artifact/内容/source binding 哈希、标签、身份与拆分；
训练只使用 train，validation 选模和早停，恢复最佳权重后 test 只评估一次。
模型卡保存独立指标、测试预测摘要、数据质量、来源/许可、代码版本与文件摘要。
失败不删除 prepared 数据，注册失败清理本次权重/卡；注册不自动激活。

新增真实 RGNN 的临时合成数据工程烟雾测试，发现单层配置索引越界，
已按现有网络要求提前拒绝 `num_layers < 2`，未重构网络。
同时修正训练种子仅初始化生效的问题：完整训练 RNG 作用域加进程内锁，
验证 CPU 非零 dropout 顺序重复训练的权重与指标一致、外部 RNG 恢复。
并不承诺与无关 Torch 调用并发或跨 GPU 平台的逐位确定性。

本阶段新增：

- `src/activity/model_card.py`
- `src/activity/prepared_training.py`
- `tests/test_activity_prepared_training_data.py`
- `tests/test_activity_prepared_training_loop.py`
- `docs/activity_prepared_training.md`

修改 `src/activity/trainer.py`、`tests/test_activity_trainer_endpoint_metadata.py`，
以及本交接与实施计划检查点。旧 metadata 明确为 `legacy_unvalidated`。

验证（MedChat Conda Python）：

- 本阶段聚焦三模块：115 passed、3 skipped。
- 十模块扩展回归：584 passed、5 skipped、2 warnings。
- 额外 `tests/test_docking_agent_architecture.py`：10 passed、9 subtests passed。
- 新增三个 skip 均为本机无创建符号链接权限；另两个是既有数据契约链接测试。
- warnings 为已有 PyG 弃用提示、图特征 tensor 构造性能提示。
- `python -m compileall -q src scripts` 和 `git diff --check` 最终通过。
- 未重复运行全量 `tests`，下面的全量测试记录属于任务 1–4。

扩展命令：

```powershell
python -m pytest tests/test_activity_model_registry.py tests/test_activity_endpoint_selection.py tests/test_activity_trainer_endpoint_metadata.py tests/test_activity_prepared_training_data.py tests/test_activity_prepared_training_loop.py tests/test_activity_prediction_contract.py tests/test_activity_dataset_contract.py tests/test_activity_prepare_cli.py tests/test_rg_mpnn_legacy_modules.py tests/test_phase2_phase3_routes.py -q -p no:cacheprovider --tb=short
```

没有使用真实 PDE/BuChE 数据，没有训练、安装或发布用于科研的真实活性模型。
临时合成数据下网络/优化器实际运行，仅验证工程生命周期，不能引用指标证明预测性能。
Web dataset ID 解析、按端点推理与 Agent 接入仍待任务 7；没有修改正在运行的原始项目。

## 历史检查点：任务 5 完成

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

任务 7 迁移端点推理、Web 服务端 dataset ID 解析与 Agent 调用，
任务 8–9 完成健康检查、最终文档及 PR。

真实 PDE/BuChE 数据尚待用户提供。本轮只使用临时合成数据验证工程契约，
没有训练真实模型，也没有评估真实活性预测性能。
