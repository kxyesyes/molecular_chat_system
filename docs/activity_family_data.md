# PDE / BuChE 家族配对数据包

本阶段只准备训练数据，不训练、激活模型，也不改变正在运行的 Web/Agent。
设计见 `superpowers/specs/2026-09-07-family-activity-two-stage-design.md`。

## 固定契约

- `family_contract.resolve_activity_family` 解析受控家族标识；PDE 亚型归入
  `pde-family`，BuChE/BChE 归入 `buche-family`。不接受 AChE、未知或双家族标识，
  不是自然语言意图解析器，调用方应先消解否定和多靶点意图。
- 输入列为 `Smiles`、`pIC50`；pIC50 已是负对数摩尔浓度，不再转换。
- 同一家族的同一 canonical parent 先按连续标签取中位数，再生成分类标签：
  `< 5` 为 0（无活性），`>= 5` 为 1（有活性）。保留所有原始记录证据。
- 分类、回归使用相同来源和同一组 scaffold 拆分。每个拆分至少两分子且包含两类。
- 这是混合记录聚合，不声称同一实验的重复测量。数据来源范围、授权、
  关系未知状态保存在机器元数据中；不虚构实验来源或模型性能。
- 后续推理设计为先分类、再对所有有效分子回归，包括分类为无活性的分子。
  本阶段没有实现这段在线推理。

## 来源适配与核验

`family_dataset.py` 读取一次原始字节，保留原始源文件 SHA-256；数字以文本读取，
避免 `4.9999999999999994` 被 CSV 类型推断提前舍入到阈值。
只有缺列时才补充 `units=pIC50` 和 `relation==`，不覆盖已有列。
其中 `=` 是对已提供点标签的适配假设，不证明原实验没有截尾。
适配内容使用独立 SHA-256，不能冒充原文件摘要。

`load_family_dataset` 校验两个子包，并从原始快照重新计算适配、标签、质量统计、
配对拆分与元描述，拒绝仅改描述或重新哈希后的不一致内容。
保留验证失败，不回退到旧 CSV 或默认模型。

骨架提取前在母体副本上补全双键邻接方向，保留 E/Z 差异并确保 canonical 往返一致。
元描述记录 RDKit 版本与 `murcko-isomeric-bond-directions-v1` 策略。
本修复可能改变旧包的骨架/拆分摘要；若旧包复验失败，应从原始数据以新 ID 重建，
不得手改旧包摘要。不同 RDKit 版本的兼容性需重新验证，不自动放行。

## 操作

在具备 RDKit 的 MedChat Conda 环境、仓库根目录运行；下列输入是占位路径。

```powershell
python scripts/prepare_family_activity_dataset.py --input <private-input.csv> --family PDE --package-id pde-family-v1 --output-dir data/activity/prepared --validate-only
python scripts/prepare_family_activity_dataset.py --input <private-input.csv> --family PDE --package-id pde-family-v1 --output-dir data/activity/prepared --prepare
```

BuChE 使用 `--family BuChE --package-id buche-family-v1`。也支持 UTF-8 TSV。
两个模式互斥且必须明确选择；validate-only 不创建输出目录。
已存在包拒绝覆盖，重新处理需新 package ID，不能直接改旧包里的 CSV。
stdout 是单个脱敏 JSON；成功退出码 0，契约拒绝 2，运行故障 1。
失败报告不含原始行、SMILES、私有列内容或机器路径。

## 本地文件布局

```text
data/activity/prepared/<package-id>/
  source.csv                         # 原始私有字节快照（TSV 时为 source.tsv）
  family_dataset.json                # 家族、阈值、配对拆分及来源摘要
  <package-id>-classification/
    dataset_manifest.json
    train.csv / validation.csv / test.csv / quality_report.json
  <package-id>-regression/
    dataset_manifest.json
    train.csv / validation.csv / test.csv / quality_report.json
```

原始快照可能包含全部私有列。整个数据包只留在 Git 忽略的本地目录，
不得上传、提交或假定获得再分发许可。原子发布失败不得留下半包或覆盖已有包。
数据就绪不是科学预测性能通过；后续仍需真实训练、独立留出评估、模型卡与成组注册。

## 2026-09-07 真实预检

用户授权的源文件保持原样；未使用外部主模型或 LLM 判定数据是否通过。

| 项目 | PDE | BuChE |
|---|---:|---:|
| 原始记录 | 4,012 | 2,361 |
| 聚合后唯一分子 | 2,642 | 2,285 |
| 拒绝记录 | 0 | 26（多片段母体不明确） |
| 合并重复记录 | 1,370 | 50 |
| 骨架数 | 1,189 | 796 |
| 无活性 / 有活性 | 522 / 2,120 | 976 / 1,309 |
| train / validation / test | 1,850 / 396 / 396 | 1,599 / 343 / 343 |
| 重复组 / 跨阈值组 | 642 / 102 | 43 / 2 |

按固定 seed 42、比例 0.7/0.15/0.15 的骨架分配，分类和回归共享映射。
只检查 test 的标签和拆分质量，未据此选模型、调参或计算科研性能指标。
首次 BuChE 预检曾因骨架往返校验失败被拒绝；修复方向标记问题后重新预检通过，
没有绕过校验，也没有把失败的那次运行计为通过。
