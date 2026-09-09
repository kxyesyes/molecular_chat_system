# 活性数据准备 CLI

本工具校验私有 CSV/TSV 实验数据，生成可追溯的 train/validation/test 数据集。
它不会训练、注册或激活 RG-MPNN 模型。通过数据准备检查也不代表预测模型已具备科研可用性。

## 输入

使用 UTF-8 编码的 `.csv` 或 `.tsv` 文件，至少提供
`smiles,value,units,relation` 四列。列名可通过 manifest 中的
`smiles_column`、`value_column`、`relation_column` 配置；`units` 使用固定列名。
靶点与终点由独立 manifest 声明；数据若带有对应身份列，还会检查一致性。

支持保留 `source`、`reference`、`organism`、`assay_type`、
`measurement_date`、`compound_id`、`assay_id`、`target_id`、`endpoint` 证据列。
同分子的重复测量按明确规则聚合，全部重复测量证据以确定性结构保留。
其他任意列不会自动进入发布数据。只接受可处理的明确测量关系，
不把 `>` 或 `<` 的删失值默认为精确实验值。

manifest 必填字段为：

```text
dataset_id, target_id, target_name, task_type, endpoint, units,
label_transform, source, license
```

每个靶点、科学终点分别声明，不能把 PDE 亚型与 BuChE 默认混为同一个任务。
真实靶点身份、单位、来源和许可应依据所提供数据确认。
完整字段约束见 `src/activity/dataset_contract.py` 中的 `DatasetManifest`。
默认质量门要求至少 100 个唯一分子和 10 个骨架；这只是准备门槛，
不保证足够训练性能。测试中的低门槛仅用于合成契约数据。

## 运行

在具备 RDKit、Pandas 的项目 Python 环境中执行：

```powershell
python scripts/prepare_activity_dataset.py --input data/activity/raw/input.csv --manifest data/activity/raw/manifest.json --output-dir data/activity/prepared --validate-only
python scripts/prepare_activity_dataset.py --input data/activity/raw/input.csv --manifest data/activity/raw/manifest.json --output-dir data/activity/prepared --prepare
```

两个模式必须选择其一。`--validate-only` 校验质量门与三路拆分可行性，
不创建输出目录。`--prepare` 在校验后生成：

```text
<output-dir>/<dataset_id>/
  train.csv
  validation.csv
  test.csv
  quality_report.json
  dataset_manifest.json
```

目录通过临时目录整体验证后发布，已有目标不会被覆盖。
需要重新准备时使用新的输出目录或数据集版本。
默认拆分比例为 70/15/15，固定随机种子；同一分子或骨架不会跨集合。
大骨架组可能造成实际比例偏差，报告记录实际比例和偏差。

输入文件哈希与标准化内容哈希分别记录。重新读取发布的 CSV 时使用
`read_prepared_split()`，避免空骨架被转成 NaN 或浮点精度变化。
源文件按字符串读取，保留前导零标识和阈值附近数值的原始文本；
如果提供的 DataFrame 已经丢失这些文本信息，源快照一致性检查会拒绝发布。
CSV/TSV 中的 NUL 字节在解析前拒绝，避免解析器悄悄截断标识或数值。
每条记录的列数必须与表头一致；多列、缺列或格式错误的引号在表格解析前拒绝，
不允许解析器推断索引或自动补齐字段。带引号的分隔符、换行、显式空字段和 UTF-8 BOM 可用。
源行的 `target_id` 或 `endpoint` 与 manifest 冲突时拒绝该行，不自动改写身份。

## 结果与退出码

除 `--help` 外，stdout 只输出一份 UTF-8 JSON；GBK 终端环境仍采用 UTF-8 输出。
报告提供状态、数量、拒绝原因计数和相对于输出目录的产物目录名，
不包含原始数据行或绝对机器路径。

| 退出码 | 含义 |
|---|---|
| 0 | 数据准备质量门与拆分可行性通过 |
| 2 | 参数、科学数据契约、质量门或目标冲突被拒绝 |
| 1 | 文件访问、依赖或其他运行时失败 |

`error_code` 区分 `invalid_arguments`、`contract_rejected`、
`quality_gate_rejected`、`runtime_failure`。错误消息不会回显私有数据。
原始数据、prepared 文件、权重及运行输出由 `.gitignore` 排除；
raw/prepared 目录中的 Markdown 说明可跟踪；models 目录仍整体忽略。

## 回归检查

```powershell
python -m pytest tests/test_activity_dataset_contract.py tests/test_activity_prepare_cli.py tests/test_activity_dataset_blockers.py tests/test_activity_nul_source.py tests/test_activity_preparation_roundtrip.py -q -p no:cacheprovider
python -m pytest tests/test_activity_source_record_width.py -q -p no:cacheprovider
python -m compileall -q src scripts
```

这些测试使用临时合成数据验证工程行为，不能作为真实 PDE/BuChE 模型效果证据。
