# Family Activity Data Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Execute with TDD and independent spec/quality review.

**Goal:** 实现已确认家族双模型设计的第一阶段：受控路由、固定阈值、同源同拆分的分类/回归数据包，并用用户授权的两份 CSV 真实验证。

**Architecture:** 复用 DatasetManifest、validate_activity_dataset、split_prepared_dataset、write_prepared_dataset 和 load_prepared_training_data。在小型 family_contract/family_dataset 模块中组合，而不重写训练器、网络或注册表。本阶段不执行正式训练、不加载活动模型。

**Tech Stack:** Python、pandas、RDKit、pytest、现有原子目录发布工具。

## 范围和后续

已审阅设计：`docs/superpowers/specs/2026-09-07-family-activity-two-stage-design.md`。
本计划只交付数据/家族契约。成组注册与端点推理、API/Agent/显示、真实训练验收是后续独立阶段，
需在本阶段文件布局和实际数据包验证后细化实施计划。用户已选用现有子代理协作流程。

## Task 1：家族解析与 manifest

Files: 新增 `src/activity/family_contract.py`、`tests/test_activity_family_contract.py`。

- [ ] 写失败测试并运行 `python -m pytest tests/test_activity_family_contract.py -q -p no:cacheprovider`。

```python
assert resolve_activity_family("PDE5A") == "pde-family"
assert resolve_activity_family("BChE") == "buche-family"
with pytest.raises(ValueError):
    resolve_activity_family("PDE 和 BuChE")
with pytest.raises(ValueError):
    resolve_activity_family("AChE")
manifest = build_family_manifest("PDE", "example-cls", "classification")
assert manifest.classification_threshold == 5.0
assert manifest.classification_direction == "greater_or_equal"
assert manifest.smiles_column == "Smiles"
assert manifest.value_column == "pIC50"
```

- [ ] 实现明确别名/标识边界、拒绝未知和双家族输入；禁止跨家族默认值。
- [ ] manifest 固定源 endpoint/units 为 pIC50，回归 identity；分类 binary_threshold，
  output_endpoint=activity、output_units=probability。来源为用户提供、授权为仅本项目训练，
  不虚构数据库和实验来源。来源细节缺失保存在数据包 scope 中。
- [ ] 运行完整契约测试到 GREEN；边界含 4.999、5、5.001，拒绝 bool/NaN/Infinity。
- [ ] 自审后精确暂存，随阶段提交，不修改正在运行的主工作树。

## Task 2：共享拆分的数据包

Files: 新增 `src/activity/family_dataset.py`、`tests/test_activity_family_dataset.py`。

公开 API：

```python
report = prepare_family_dataset(input_path, family="PDE", package_id="pde-v1",
                                output_dir=output_dir, validate_only=True)
assert report["ready_for_training"] is True
assert not output_dir.exists()
report = prepare_family_dataset(input_path, family="PDE", package_id="pde-v1",
                                output_dir=output_dir, validate_only=False)
verified = load_family_dataset(output_dir / "pde-v1" / "family_dataset.json")
assert verified["family_id"] == "pde-family"
```

- [ ] RED：合成多骨架数据覆盖两类；共享分配、聚合后分类、私有列不泄露、失败无半包、拒绝覆盖。
- [ ] 同一份 UTF-8 字节快照读取 CSV/TSV，两种 manifest 分别验证、完整保留输入摘要。
  拒绝重复列名。禁止通过改写原始 frame 伪造来源绑定。
- [ ] 一次 scaffold 分配用于两种任务；对比 canonical SMILES、scaffold 与 label=(regression>=5)，
  分类/回归行顺序可以不同但集合映射必须完全一致。使用现有训练 loader 检查两类/最少样本。
- [ ] 发布前两个 prepared 子目录都经 loader 验证；元描述文件只允许包内相对路径，
  保存两份 manifest 哈希、共同 source/assignment 哈希、scope、阈值和中位数策略。
  复用现有原子 no-replace 目录发布与身份核验，任何阶段失败不得覆盖已有包。
- [ ] `load_family_dataset` 从已验证快照重算元描述内容，不只信任其中的声明；
  分类/回归互换、篡改标签、异源、改阈值、改 scope、路径穿越、损坏摘要必须拒绝。
- [ ] validate-only 仅内存计算，验证与发布相同的质量门；返回脱敏计数和分类比例，
  不输出 SMILES、原始行、机器路径。缺失条件返回明确失败而非成功空包。
- [ ] GREEN：`python -m pytest tests/test_activity_family_dataset.py -q -p no:cacheprovider`。

## Task 3：CLI 与真实数据预检/发布

Files: 新增 `scripts/prepare_family_activity_dataset.py`、`tests/test_activity_family_prepare_cli.py`、
`docs/activity_family_data.md`；更新交接文档。

- [ ] CLI 子进程 RED：`--input --family --package-id --output-dir` 配合互斥
  `--validate-only/--prepare`。stdout 单个 UTF-8 JSON，成功 0、契约失败 2、运行异常 1，
  stderr 不泄露原始数据。复用现有 CLI 的 Parser 和输出约定。
- [ ] 实现调用 prepare_family_dataset，不写第二套读取/标准化逻辑。
- [ ] 合成样例 GREEN；重复输出目录、无效 family、缺列、单类别不能输出成功。
- [ ] 对用户提供的 PDE.csv 与 BUCHE.csv 先运行 validate-only，再发布到 Git 忽略的
  `data/activity/prepared/` 内，使用新 package_id，绝不覆盖。只记录摘要统计。
- [ ] 真实源文件 SHA-256 在运行前后相同；确认包路径被 git check-ignore 忽略。
  两份包经 load_family_dataset 复验；检查 test 只做标签/拆分质量检查，未进行模型评估。
- [ ] 使用 MedChat Conda Python 执行下面回归；记录实测结果而不是填写预计数字。

```powershell
python -m pytest tests/test_activity_family_contract.py tests/test_activity_family_dataset.py tests/test_activity_family_prepare_cli.py tests/test_activity_dataset_contract.py tests/test_activity_prepare_cli.py tests/test_activity_prepared_training_data.py tests/test_activity_prepared_training_loop.py -q -p no:cacheprovider --tb=short
python -m compileall -q src scripts
git diff --check
```

- [ ] 完成规范审查、质量审查、修正与复验，再精确提交本阶段代码/测试/文档；
  CSV、权重、prepared 包、报告不提交。分支仍为 codex/activity-data-readiness，未合并 main。
