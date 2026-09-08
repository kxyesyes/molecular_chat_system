# 活性数据就绪层实施检查点：2026-09-08

分支：`codex/activity-data-readiness`。在独立 `scientific-assets` 工作树实施。
原始项目混杂工作树未改动，未合并 main。

## 当前阶段：四个真实基线模型已训练，尚未启用

2026-09-08 按用户批准的冻结参数，在 RTX 5060 Laptop GPU 上完成
`baseline-20260908-v1`：PDE 分类/回归、BuChE 分类/回归共四个真实 RG-MPNN。
训练脚本与协议先冻结提交 `81ca08b`，训练期间源码未修改。
总耗时 928.109 秒，四任务 completed，两个配对组注册成功，0 失败、0 自动重试。
每模型恢复 validation 最佳权重后仅评估 test 一次；未重新运行测试推理。

结果和模型 ID 详见 [真实训练报告](../activity_family_baseline_20260908.md)。
PDE 分类测试 ROC-AUC=0.712055、平衡准确率=0.516094，区分负类较弱；
BuChE 分类为 0.818198 / 0.790939。PDE / BuChE 回归 RMSE 为 1.703752 / 0.926840，
R² 为 0.203562 / 0.229635。均优于常数基线，但不能称为高精度预测或部署质量通过。

本轮增加 `scripts/train_family_activity_models.py`、`tests/test_family_training_run.py`、
冻结计划和报告，更新训练说明；未修改业务训练算法。
聚焦测试 **96 passed、3 skipped**（Windows 符号链接权限），CUDA 网络训练冒烟及
compileall 通过。独立审查通过；真实权重、模型卡和注册记录验证通过，demo/fallback=false。

本地资产位于 `outputs/activity_training/baseline-20260908-v1/` 和
`data/activity/models/baseline-20260908-v1/`，不提交 Git。
四个模型未激活，两个模型组无 active 选择；没有重启或更改原始项目。
联合分类/回归测试一致性尚不可获得（未保留逐行输出）。
后续隔离真实权重接入与 Agent 审查已完成，见 [本轮交接](agent-real-model-audit.md)；
真实模型仍未生产启用。不得拿本次已查看的 test 反复调参选模。

以下各节为历史检查点，关于“尚未真实训练”的表述仅对应当时阶段。

## 最新需求补充：PDE / BuChE 家族双模型

用户已提供两份真实 CSV，并确认 pIC50 已是负对数摩尔浓度、允许用于项目训练。
已确认 PDE 各亚型共用 PDE 模型组、BuChE 共用 BuChE 模型组；以 pIC50 5 为分类阈值，
先分类后回归，对“无活性”分子也输出独立回归预测值。前端不重复显示范围说明，
模型卡与机器溯源仍保留真实数据范围。

书面设计：`docs/superpowers/specs/2026-09-07-family-activity-two-stage-design.md`。
用户已批准设计及重复记录混合中位数策略；家族配对数据包代码和本地发布已完成，未执行真实训练。
原任务 7 需按此设计扩展为家族级分类/回归模型组，而非只完成单模型端点加载。
真实数据文件未修改、未复制进 Git。实施计划为
`docs/superpowers/plans/2026-09-07-family-activity-data-contract.md`，
操作说明为 `docs/activity_family_data.md`。

## 历史检查点：家族预测 API / Agent / 前端接入

计划：`docs/superpowers/plans/2026-09-07-family-activity-integration.md`。
用法已并入 `docs/activity_family_models.md`。仍在独立 scientific-assets 工作树，
没有训练或激活真实模型，没有修改或重启原始项目，没有推送 PR 或合并 main。

- API 单分子/批量增加可选 `target`；明确靶点只走对应家族模型组。
  不指定靶点保留单模型兼容入口。模型加载和计算继续使用已有线程池。
- 统一科学状态聚合；全部失败不再外层 success=true，部分观测保留 partial。
  HTTP 异常保持原状态码，其他服务异常不回显内部路径。
- 按注册目录有界缓存预测器，工厂锁保护并发首次创建，不持该锁做推理。
- 活性工具增加完整输入和靶点解析、标准 ToolResult、两模型溯源校验；
  工作流候选绑定保留原始靶点意图，实际候选进入活性工具。
- 页面添加 PDE/BuChE 选择，独立显示分类、概率、预测 pIC50、错误与 warnings。
  不可用值不补 0；不使用旧模型仪表解释家族结果。来源折叠详情绑定本次响应，
  不随表单选择变化。旧版 value/probability 也按实际字段显示。
- 增加临时合成随机权重的 HTTP→注册组→实际 RGNN 前向集成检查；
  数字仅测试软件链路，不能表示真实数据训练性能。

本轮真实训练、正式 test 评估、模型组激活、线上重启均未执行。
下一阶段才训练 PDE/BuChE 共四个真实模型，按已批准 validation 选模、冻结后独立 test 的流程评估。

规格审查与质量审查最终通过。审查发现并补过 RED→GREEN 的问题包括：
HTTP 错误误转 500、并发首次重复构建、前端来源缺失、明确 SMILES 被截短、
背景靶点掩盖真实目标、无证据类别声明、全空结果冒充 passed/partial。

最终验证（MedChat Conda Python，非系统默认 Python）：

- Agent + 任务感知旧契约 + 家族 API + 实际 RGNN 前向集成：**1643 passed、1 skipped、8 warnings**。
  skip 是 `test_harness_shadow.py` 的未启用性能测试；warnings 为已有 SWIG/FastAPI 弃用
  和 RG-MPNN tensor 构造性能提示，不是失败转成功。
- 家族模型组/推理/注册表/端点/API 扩展回归：**265 passed、1 warning**。
  该组先于最后仅涉及领域校验器的空行修复；最终 1643 组已复跑该边界与 Agent 前向。
- 反幻觉、平台健康契约、对接架构测试：**28 passed、9 subtests passed**。
- 四个 Node DOM/静态测试、两个 JS 语法检查、`compileall`、`git diff --check` 通过。
- 独立审查者最初使用默认 Python 时因缺 RDKit 未能完成 API 测试；不计为通过，
  主流程上述结果均来自具备依赖的 MedChat 环境。

实际主验证命令（以下 `python` 均指 MedChat Conda 解释器）：

```powershell
python -B -m pytest tests/agent tests/test_activity_prediction_contract.py tests/test_activity_family_api.py tests/test_activity_family_inference_integration.py -q -rs -p no:cacheprovider --tb=short
python -B -m pytest tests/test_activity_family_models.py tests/test_activity_family_predictor.py tests/test_activity_family_api.py tests/test_activity_family_inference_integration.py tests/test_activity_model_registry.py tests/test_activity_endpoint_selection.py tests/test_activity_prediction_contract.py -q -p no:cacheprovider --tb=short
python -B -m pytest tests/test_agent_anti_hallucination_fallbacks.py tests/test_agent_platform_health_check.py tests/test_docking_agent_architecture.py -q -p no:cacheprovider --tb=short
node tests/activity_family_results_test.js
node tests/activity_prediction_safe_render_test.js
node tests/frontend_safe_render_test.js
node tests/home_agent_task_panel_test.js
node --check src/web/static/js/activity_prediction/main.js
node --check src/web/static/js/activity_prediction/results_renderer.js
python -m compileall -q src scripts
git diff --check
```

本轮修改范围：共享 prediction_service、活性工具/输入解析/领域校验、工作流活性绑定、
两个活性 API、活性页模板及 main/results_renderer、三份新增测试、已有前向集成测试、
阶段计划/操作文档/本交接。未变更其他业务模块，不包含真实 CSV、权重或密钥。
未执行全仓库测试、真实模型 real acceptance 或全平台在线 strict health；这些不属于本阶段已完成证据。

## 历史阶段：家族模型组与两阶段后端推理

本阶段代码位于同一独立工作树；详细计划为
`docs/superpowers/plans/2026-09-07-family-model-bundles.md`，用法见
`docs/activity_family_models.md`。没有改动原始项目或桌面源 CSV，未训练真实模型。

- 注册库 v3 保留 v1/v2 全局及端点选择，新增原子家族 bundle 注册/激活。
  两阶段必须绑定同一已验证数据包的对应子 manifest、prepared 摘要与真实模型卡。
- 源与许可、标签契约和拆分种子分别核验；训练随机种子不必等于拆分种子。
- 注册时完整验证私有数据，在线读取只验证固定数据证据和模型产物，不扫描原始 CSV。
- 耗时预检在事务外执行，入锁后读取最新状态并验证模型；通过事件屏障验证健康读取不阻塞、
  并发同 ID 单赢家、预检期间删除模型导致注册失败。描述 JSON 原文快照独立重算摘要，
  不复制待验证摘要进行自比较，不把原始 CSV 放入注册库。
- 新增 `FamilyActivityPredictor`，固定一次请求的完整模型组、安全权重字节加载、有界缓存，
  所有分类成功分子均回归，失败/partial 不造数，矛盾保留原值。
- 修复共享 predictor 在 sigmoid 前漏检原始无限值的问题，避免 inf 被变成假正常概率。
- 新增临时合成权重的注册→重开注册表→真实 RGNN 前向测试；没有使用真实 test 调参，
  不将这些结果表述为真实 PDE/BuChE 性能。

本阶段交接不意味着 Web/Agent 已接通：后续须迁移领域工具、API 与显示，
随后才正式训练两个家族共四个模型、独立评估并显式激活。

最终验收：独立规格审查、质量审查及增量复核通过。
MedChat Conda 环境八模块 **334 passed、3 skipped、1 warning**；
三个 skip 为已有本机符号链接权限限制，warning 为既有 RG-MPNN 特征 tensor 构造提示。
`compileall` 和 `git diff --check` 通过，未重复运行整个仓库测试。
本阶段不修改部署/真实数据，未运行依赖在线服务的全平台 strict health check。

```powershell
python -m pytest tests/test_activity_family_models.py tests/test_activity_family_predictor.py tests/test_activity_family_inference_integration.py tests/test_activity_model_registry.py tests/test_activity_endpoint_selection.py tests/test_activity_prediction_contract.py tests/test_activity_prepared_training_data.py tests/test_activity_prepared_training_loop.py -q -p no:cacheprovider --tb=short
python -m compileall -q src scripts
git diff --check
```

首次综合回归的两项失败为旧版 `version==2` 断言，已更新为 v3 并核对新映射为空，
最终上述完整复跑通过。审查发现的无限 logits、摘要自比较和长时间持锁问题
均补过失败回归并修复；没有通过删除科学校验放行。

新增：`src/activity/family_models.py`、`src/activity/family_predictor.py`、
`tests/family_model_test_support.py`、三份 family 模型/推理测试和用法文档。
修改：注册表、共享 predictor 的原始输出有限性检查、两份旧迁移测试及相关文档。
只提交代码、合成测试和文档；无密钥读取、外部模型调用、真实训练、部署重启或 main 合并。

## 历史检查点：家族配对数据包

- 新增 `family_contract.py`：PDE/BuChE 受控标识、固定 5 阈值与双任务 manifest。
- 新增 `family_dataset.py`：原始字节和适配字节分别绑定，同源中位数聚合、
  同骨架拆分、配对标签核验、原子发布与原始来源重验。
- 新增 `scripts/prepare_family_activity_dataset.py`、三份 family 测试和操作文档。
- 最小修改 `dataset_contract.py`：保存文本标签的阈值边界精度；骨架提取前
  在母体副本补全双键邻接方向，修复原始与 canonical 往返身份不一致。
- CSV 行宽异常在 pandas 解析前明确拒绝，不静默丢列或推断私有索引。
- 独立规格审查及质量审查通过。七模块扩展回归：**524 passed、5 skipped、2 warnings**；
  五个 skip 为本机符号链接权限限制，两个 warning 为既有 PyG 弃用/张量构造提示。
  `compileall -q src scripts`、`git diff --check` 通过；没有重跑全量测试。

真实数据（不是合成验收数字）：

| 家族 | 原始行 | 聚合后分子 | 拒绝 | train / validation / test |
|---|---:|---:|---:|---|
| PDE | 4,012 | 2,642 | 0 | 1,850 / 396 / 396 |
| BuChE | 2,361 | 2,285 | 26 多片段 | 1,599 / 343 / 343 |

两份原始 CSV 均先执行 `--validate-only`，再执行 `--prepare`，退出码 0。
首次 BuChE 预检的骨架一致性失败已诚实记录，修复后重新验证通过，未绕过质量门。
新包为 `data/activity/prepared/pde-family-v1/` 和 `data/activity/prepared/buche-family-v1/`，
两个包及源快照均保持 Git 忽略；不是已训练权重，未改变活动模型或正在运行的原始项目。
发布后独立 loader 复验结果见下方最终验收记录。

最终验收记录：两个已发布 `family_dataset.json` 分别再次通过
`load_family_dataset`，退出码 0、`verified=true`、`source_unchanged=true`。
原始 SHA-256 与预检及包内源快照一致；`git check-ignore` 确认两个描述文件和
源快照均被忽略，发布目录没有遗留 staging 半包。

复现命令（使用实际私有输入替换占位路径；在 MedChat Conda 环境运行）：

```powershell
python scripts/prepare_family_activity_dataset.py --input <PDE.csv> --family PDE --package-id pde-family-v1 --output-dir data/activity/prepared --validate-only
python scripts/prepare_family_activity_dataset.py --input <PDE.csv> --family PDE --package-id pde-family-v1 --output-dir data/activity/prepared --prepare
python scripts/prepare_family_activity_dataset.py --input <BUCHE.csv> --family BuChE --package-id buche-family-v1 --output-dir data/activity/prepared --validate-only
python scripts/prepare_family_activity_dataset.py --input <BUCHE.csv> --family BuChE --package-id buche-family-v1 --output-dir data/activity/prepared --prepare
python -m pytest tests/test_activity_family_contract.py tests/test_activity_family_dataset.py tests/test_activity_family_prepare_cli.py tests/test_activity_dataset_contract.py tests/test_activity_prepare_cli.py tests/test_activity_prepared_training_data.py tests/test_activity_prepared_training_loop.py -q -p no:cacheprovider --tb=short
```

已存在的包不可再次覆盖；仅需检查时使用 validate-only 或 loader。

后续：实现家族分类/回归模型组的成组注册、就绪检查和端点推理；再接 API/Agent，
最后训练两个家族共四个真实模型并独立评估。不能将本轮标签分布当作预测性能。
数据发布的多重化学复验有较高常数开销，本次未作无关性能重构；后续可做计时分析，
但不能为了提速删去来源/摘要/标签/拆分核验。

## 历史检查点：任务 6 完成

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

任务 1–4 当时真实 PDE/BuChE 数据尚待用户提供。该历史阶段只使用临时合成数据验证工程契约，
没有训练真实模型，也没有评估真实活性预测性能。
