# PDE/BuChE RG-MPNN 多种子质量搜索

日期：2026-10-11。状态：真实本机训练与独立测试评估完成；部署门禁未通过，未激活任何新 family bundle。

## 协议

`scripts/search_family_activity_models.py` 编排四个 family/task 的独立训练运行。候选阶段只消费验证集指标，保存未注册 checkpoint；候选选定后才对每个候选 seed 在固定 scaffold test split 上各评估一次。模型发布、family bundle 注册和 smoke chain 仅写入本次搜索的隔离目录，搜索本身不会修改生产根注册表或激活 bundle。

同时生成 `deterministic_random_molecule_v1` 的随机拆分诊断，用于识别分子内插值与 scaffold 外推的差异；该诊断不参与部署门禁。随机拆分仍按 RG-MPNN 实际无立体化学通道的 molecule identity 分组，不能把立体异构体伪装成独立 held-out 分子。

## 可复核命令

```powershell
python scripts/search_family_activity_models.py --search-id <fresh-search-id> --package-version v2
python -m pytest tests/test_family_training_search.py tests/test_activity_feature_split_integrity.py tests/test_activity_prepared_training_loop.py tests/test_family_training_run.py -q -p no:cacheprovider
```

真实运行资产、权重和输出只保存在本机被忽略的 `data/activity` 与 `outputs/activity_training` 下，不提交到 Git。搜索前应确认 PDE/BuChE prepared package、PyTorch/PyG、RG-MPNN featurizer 与 CUDA/CPU 运行环境可用；缺少这些资产时应失败或标记不可用，不能退回 demo/fallback。

## 2026-10-11 本机结果

- 搜索报告：`outputs/activity_training/pde-buche-quality-search-20261011-r6/search_report.json`。
- 四个 scaffold 外推模型均完成两种子候选的验证选择、固定 test 评估、模型卡/权重摘要校验和未激活的隔离 family bundle 构建。
- PDE 分类：两种子 test balanced accuracy 的最差值约 `0.698`，标准差约 `0.051`，超过当前 `0.05` 稳定性阈值。
- PDE 回归：跨种子 test 指标稳定，且相对常数基线和当前候选不劣，但这只证明该 family/task 的本次数据划分证据。
- BuChE 分类：相对常数基线有改善，但相对当前 `pde-buche-20261006-r2` 候选的比较门禁未通过。
- BuChE 回归：相对常数基线有改善，但跨种子 test 不稳定，且相对当前候选比较门禁未通过。
- 独立随机拆分诊断的四个 task 均优于常数基线；该结果不替代 scaffold 外推证据，也不构成临床或实验验证。
- 新搜索的 `classification_regression_chain` 使用独立 `CCO`/`CCN` smoke inputs，两个 family 的 provenance identity 均通过；链路通过不等于部署资格。
- 最终 `deployment_gate.eligible=false`、`activated=false`。因此不能将该搜索结果注册为生产 root active PDE/BuChE bundle，也不能把本机真实模型输出称为生产就绪或科学性能已充分验证。

## 限制与后续

当前结果仍未完成目标服务器的 HTTPS/WSS、反向代理、多 worker/重启、真实外部模型端点和生产根注册表激活验收。模型搜索只能改善证据收集和防止 test 泄漏，不能替代这些部署门禁；若要激活，必须由独立审查依据完整 gate 结果选择模型并再次运行真实入口验收。
