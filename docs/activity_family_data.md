# PDE / BuChE 家族配对数据包

此模块只准备、验证数据，不训练或启用模型，不改变 Web/Agent 在线入口。
本次分批迁移记录见 [交接记录](handoff/activity-family-data-integration.md)。

## 固定契约

- `src/activity/family_contract.py` 识别受控家族标识：PDE 及已列明亚型归入
  `pde-family`，BuChE/BChE 归入 `buche-family`；未知或跨家族歧义输入拒绝。
  该函数不是自然语言意图解析器，调用方须先处理否定、多靶点和上下文。
- 源列必须包含 `Smiles`、`pIC50`。pIC50 已为负对数摩尔浓度，不再次换算。
- 同一 canonical parent 的连续标签先取中位数，再分类：`< 5` 为无活性，
  `>= 5` 为有活性。保留源记录与聚合证据，不宣称是同一实验的重复测量。
- 分类、回归共用一次 scaffold 分配；每个拆分至少两分子且须包含两类。
  正式默认质量门为至少 100 个唯一分子、10 个骨架。
- 来源范围、授权与未知实验信息保留在机器元数据中；准备成功不是预测性能通过。

## 来源与完整性

`src/activity/family_dataset.py` 只读取一次原始字节快照，以文本解析标签与证据 ID，
避免阈值附近的小数提前舍入、ID 前导零丢失。拒绝 NUL、重复列与不完整记录。
仅当缺列时补 `units=pIC50`、`relation==`；已有值不被覆盖。
`=` 是点标签适配假设，不证明原始实验没有截尾。
存在的源 `target_id`/`endpoint` 必须与声明一致，不能静默重标。

数据包保留原始源 SHA-256、适配后 SHA-256、两个子包 manifest 摘要、共用拆分摘要。
`load_family_dataset` 用原始快照及两个严格子包 loader 重算标签、质量统计与配对描述，
拒绝描述/文件不一致。摘要证明内部一致性，不能独立证明外部来源真实性。
原始 CSV/TSV 均先适配为 CSV；两个子包必须声明实际适配格式 `csv`，重算摘要也不能冒用 `tsv`。
记录 RDKit 版本与骨架策略；跨版本兼容必须复验，不能手改摘要绕过失败。

发布使用已有 no-replace 原子目录操作：不得覆盖旧包，失败回滚本次创建的目录。
`validate-only` 执行同样的发布前数据门禁，但不创建输出目录。
聚合后的证据字段也须通过子包 CSV 读取限制；预检不会对发布时必然无法读取的拆分报告就绪。

## 命令与输出

来源：`scripts/prepare_family_activity_dataset.py`。在仓库根目录、具备 RDKit 的环境执行。
下列输入路径是占位符，不是随仓库发布的数据。

```powershell
python scripts/prepare_family_activity_dataset.py --input <private-input.csv> --family PDE --package-id pde-family-v1 --output-dir data/activity/prepared --validate-only
python scripts/prepare_family_activity_dataset.py --input <private-input.csv> --family PDE --package-id pde-family-v1 --output-dir data/activity/prepared --prepare
```

BuChE 使用 `--family BuChE --package-id buche-family-v1`；支持 UTF-8 CSV/TSV。
`--validate-only` / `--prepare` 互斥且必须明确指定。
执行预检/发布时 stdout 为单个 JSON：成功退出码 0，参数/契约拒绝 2，其他运行异常 1。
`--help` 沿用 argparse 的纯文本帮助与退出码 0，不是 JSON 结果。
失败不得报告 ready/published，不输出原始行、SMILES、私有列或绝对路径。

```text
data/activity/prepared/<package-id>/
  source.csv                         # TSV 输入对应 source.tsv
  family_dataset.json
  <package-id>-classification/
    dataset_manifest.json
    train.csv / validation.csv / test.csv / quality_report.json
  <package-id>-regression/
    dataset_manifest.json
    train.csv / validation.csv / test.csv / quality_report.json
```

原始快照可能包含全部私有列；整个包仅保存在受控、Git 忽略的本地目录，不提交或上传。
本批只以合成数据验证工程契约，未读取用户 PDE/BuChE 数据、训练权重或变更活动注册表。
家族双模型注册、训练和在线推理仍是后续独立批次。
