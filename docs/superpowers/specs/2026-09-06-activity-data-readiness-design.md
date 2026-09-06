# RG-MPNN 真实活性数据就绪层设计

## 1. 目标

在不替换现有 `ActivityTrainer`、RG-MPNN 网络和 `ActivityModelRegistry` 的前提下，补齐真实 PDE 与 BuChE 数据进入训练前所需的数据契约、质量检查、三路 scaffold 拆分、端点模型选择和可追溯记录。

本阶段不训练模型、不下载或生成替代数据、不创建模拟权重。没有通过质量门的真实数据不得进入训练，也不得注册为可用模型。

## 2. 设计原则

- 每个靶点与科学终点独立建模。PDE 和 BuChE 数据不会被默认合并为一个模型。
- 原始实验值、标准化值和派生标签必须可区分，禁止静默改变单位或标签语义。
- 同一 canonical SMILES 及其 scaffold 不得跨 train、validation、test 集泄漏。
- 数据不足、单位混杂、关系符无法处理或标签不合法时 fail closed。
- 训练、注册和推理都通过明确的 endpoint 标识选择模型，保留现有单一 active model 行为作为兼容路径。
- 数据集、模型权重和运行报告保存在 Git 外部目录；Git 只保存代码、schema、示例占位文件和文档。

## 3. 数据契约

### 3.1 必需字段

标准输入使用 CSV 或 TSV，至少包含：

| 字段 | 含义 |
|---|---|
| `smiles` | 原始分子 SMILES |
| `target_id` | 稳定靶点标识，如 UniProt ID 或项目内受控 ID |
| `endpoint` | 科学终点，如 `pIC50`、`IC50`、`Ki` 或二分类终点名 |
| `value` | 实验值或标签 |
| `units` | `nM`、`uM`、`M`、`pIC50`、`probability` 等明确单位 |

### 3.2 可选证据字段

支持但不强制：`relation`、`assay_id`、`source`、`reference`、`organism`、`assay_type`、`measurement_date`、`compound_id`。

`relation` 仅接受受控值 `=`、`<`、`<=`、`>`、`>=`。第一版训练只接收 `=`；其他关系符保留在拒绝或隔离报告中，不静默转换为精确值。

### 3.3 标签语义

- 回归任务必须声明目标单位和转换方式。
- 浓度终点转换为 `pActivity` 时使用显式公式并记录原值、原单位和转换结果。
- 二分类任务必须由数据清单声明阈值或直接提供合法的 `0/1` 标签；训练器不得继续使用隐式 `value > 0.5` 转换任意连续值。
- 同一训练任务只允许一个 `target_id + endpoint + units + label_transform` 组合。

## 4. 组件设计

### 4.1 数据预检与标准化

新增 `src/activity/dataset_contract.py`，负责：

1. 读取 CSV/TSV 并规范列名。
2. 使用 RDKit 解析、去盐、canonicalize SMILES。
3. 校验标签类型、有限数值、单位、关系符和 endpoint 一致性。
4. 识别无效结构、重复分子、标签冲突和潜在异常值。
5. 按明确策略聚合同分子重复测量；默认使用中位数，并在报告中记录样本数与离散程度。
6. 输出标准化表、质量报告和 dataset manifest。

预检返回结构化 `DatasetValidationResult`，包含 `accepted_rows`、`rejected_rows`、`warnings`、`statistics`、`dataset_sha256` 和 `ready_for_training`。

### 4.2 数据清单

每个数据集使用一个显式 manifest，至少记录：

- `dataset_id`、`target_id`、`target_name`
- `task_type`、`endpoint`、`units`、`label_transform`
- 输入文件 SHA-256、标准化文件 SHA-256
- 原始行数、有效行数、唯一分子数、唯一 scaffold 数
- 拒绝原因分布、重复聚合策略
- split 策略、随机种子和各集合数量
- 来源和许可说明

manifest 不包含原始实验数据，也不允许绝对机器路径。

### 4.3 三路 scaffold 拆分

扩展现有 scaffold split 为 train/validation/test 三路：

- 默认比例 `0.70 / 0.15 / 0.15`。
- 以 Bemis-Murcko scaffold 为分组单位。
- canonical SMILES 与 scaffold 在三个集合间均不得重叠。
- 拆分结果必须可由同一随机种子复现。
- scaffold 数不足以形成三个非空集合时直接失败，不自动退化为随机拆分。

随机拆分只作为用户明确选择的诊断模式，不能作为生产模型默认策略。

### 4.4 训练入口

新增 `scripts/prepare_activity_dataset.py`：

- `--input`：真实数据文件。
- `--manifest`：数据集声明文件。
- `--output-dir`：Git 外部输出目录。
- `--validate-only`：只检查并生成报告，不写训练数据。
- `--prepare`：生成标准化数据和确定性 split 文件。

现有训练 API 与 `ActivityTrainer` 后续只接收预检通过的 prepared dataset manifest。为保持兼容，旧 CSV 上传入口暂时保留，但标为 legacy，并继续执行现有最小检查；不得自动获得“真实科研模型”状态。

### 4.5 端点感知模型注册

扩展模型 metadata，加入：

- `target_id`、`target_name`
- `endpoint_key`，由 `target_id + endpoint + units + task_type` 规范化生成
- `label_transform`
- `prepared_dataset_sha256`
- train/validation/test 数量和 scaffold 数
- 独立测试集指标
- 数据质量摘要和训练代码版本

`ActivityModelRegistry` 增加按 `endpoint_key` 选择 active model 的能力。原 `active_model_id` 保留，用于没有指定 endpoint 的兼容调用；真实科研调用若未指定端点或没有匹配模型，必须返回结构化不可用状态，不得回退到其他靶点模型。

### 4.6 评估与模型卡

回归模型至少报告 RMSE、MAE、R²，并保存测试集预测摘要；分类模型至少报告 ROC-AUC、PR-AUC、balanced accuracy 和混淆矩阵计数。单类别测试集不允许被报告为完整通过。

训练成功后生成 JSON 模型卡，记录数据来源、endpoint、单位、拆分、指标、适用域、限制、权重哈希和 `demo_mode=false`。只有权重、metadata、模型卡和质量门均有效时，模型才能注册和激活。

## 5. 数据流

```text
真实 PDE/BuChE 数据
  -> manifest 校验
  -> RDKit 结构标准化与标签质量检查
  -> 重复实验聚合
  -> 三路 scaffold split
  -> prepared dataset + quality report
  -> RG-MPNN 训练
  -> validation 选模
  -> held-out test 一次性评估
  -> model card + 权重哈希
  -> endpoint-aware registry
  -> Agent 按 target/endpoint 调用
```

任何阶段失败都保留结构化错误和报告，后续阶段不得继续。

## 6. 错误处理与安全边界

- 无效 SMILES、NaN/Infinity 标签、未知单位和混合 endpoint 进入 rejected rows。
- 数据量或 scaffold 数不足时 `ready_for_training=false`。
- 不把 rejected rows 原文写入普通日志，避免泄露未公开数据。
- 不在 Git 中保存真实数据、模型权重、训练缓存或绝对路径。
- 模型缺失、metadata 不匹配、权重哈希失败或 endpoint 不匹配时，预测返回 unavailable。
- 不产生默认 pIC50、不将分类概率描述为 pIC50、不将验证集指标描述为外部验证结果。

## 7. 测试策略

### 7.1 数据契约测试

- 必需字段缺失、未知单位、非法关系符、NaN/Infinity 标签必须失败。
- 无效 SMILES 被拒绝，合法 SMILES canonicalize 后稳定。
- 重复分子的相同标签可聚合，冲突标签产生明确 warning 或失败。
- 二分类只接受合法 `0/1`，除非 manifest 显式声明阈值转换。

### 7.2 拆分测试

- 固定 seed 可复现。
- train/validation/test 均非空。
- canonical SMILES 与 scaffold 无跨集合泄漏。
- scaffold 不足时 fail closed。

### 7.3 注册与推理测试

- PDE 模型不会被 BuChE 请求选择，反之亦然。
- endpoint 缺失或不匹配时不回退到全局模型。
- 权重、模型卡或数据哈希不一致时拒绝注册或加载。
- legacy active model 只用于明确的兼容调用。

### 7.4 真实数据到位后的验收

- 先运行 `--validate-only`，人工审查质量报告。
- 再生成 prepared dataset 并验证哈希与 split。
- 小规模 smoke training 成功后再运行正式训练。
- 正式模型必须通过 held-out test、模型加载、单分子预测和 Agent 工具 provenance 验收。

## 8. 非目标

- 本阶段不引入 MLflow、DVC、Weights & Biases 或分布式训练平台。
- 不进行超参数大规模搜索。
- 不下载公共数据替代用户将提供的 PDE/BuChE 数据。
- 不承诺模型达到特定指标；指标阈值应根据真实数据规模、噪声和任务用途在数据审查后确定。
- 不改变分子生成、ADMET、对接或反向寻靶模块。

## 9. 完成标准

- 未提供真实数据时，可运行完整的 schema、预检、拆分和注册契约测试。
- 提供数据后，可用一条预检命令得到可审查的质量报告，且失败不会启动训练。
- prepared dataset 可复现，三个集合无结构泄漏。
- PDE 与 BuChE 模型能够独立注册、激活和解析。
- 所有科学输出可追溯到数据哈希、模型权重、endpoint、单位和测试指标。
