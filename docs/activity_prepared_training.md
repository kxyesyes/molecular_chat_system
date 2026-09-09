# 已校验数据集的 RG-MPNN 训练

状态：内部 Python 接口已实现；Web 的服务端 dataset ID 解析、端点推理迁移留在后续阶段，尚未上线。不要把客户端提供的任意磁盘路径直接传入此接口。

## 使用边界

先按照 [数据准备说明](activity_data_preparation.md) 生成 `dataset_manifest.json`、三份 CSV 和质量报告。训练读取同一份已校验字节快照，重新检查文件哈希、汇总哈希、标签转换、分子/骨架身份、拆分泄漏及数量。哈希只能证明内容完整性，不能证明实验来源真实；来源、许可和测量语义仍需数据提供者确认。

三份 CSV 在解析前复用数据准备模块的严格记录校验，拒绝 NUL 字节、表头重复和列数错位。
即使文件哈希已重新计算，也不能把解析器静默丢失的字段当作有效训练输入。
若记录或重复测量证据保留了 `target_id` / `endpoint`，它们必须与声明一致；
修改 manifest 并重算哈希不能将保留了其他靶点身份的数据重新标记。

本批仅运行临时合成数据工程测试，不读取已提供的真实 PDE/BuChE 数据，也不启用生产模型。

## 内部调用

在仓库根目录、已安装 RDKit/PyTorch/PyG 的环境使用：

```python
from src.activity.trainer import submit_training_job, get_job_status

job_id = submit_training_job(
    prepared_manifest_path="data/activity/prepared/my-dataset/dataset_manifest.json",
    epochs=50,
    patience=12,
    batch_size=32,
    num_layers=5,
    random_seed=42,
)
status = get_job_status(job_id)
```

此例的 dataset 路径为占位示例，必须换成准备命令实际返回的文件。`submit_training_job` 在后台线程运行，进程必须持续存活；状态目前是进程内字典，不具备训练任务重启恢复能力。用 Web/API 管理训练需等待下一阶段。

- prepared 路径一旦显式传入，失败不会回退到旧 CSV 流程。
- task、终点、单位和标签来自已校验 manifest，不由旧 `target_column/task_type` 参数覆盖。
- 三路拆分不可重新随机分配；`random_seed` 是训练种子，数据拆分种子另外记录。
- 现有 RGNN 需要 `num_layers >= 2`；小于 2 会在特征计算前明确拒绝。
- 任何分子特征计算失败都终止训练，不静默删行改变拆分。
- 在优化前，依据实际盐处理/中和后的特征分子再次检查三组的分子与骨架重叠。
  当前图特征未编码的立体差异不能充当独立测试骨架；发现重叠明确失败，
  不删行、不重拆测试集。原始 canonical SMILES 不重复不足以证明实际输入独立。
- prepared 文件在成功、失败后均保留。

## 选模、评估和发布

1. 仅 train 数据用于梯度更新，validation 用于调度、早停和最佳权重选择。
2. 回归按 validation RMSE 最小选择；分类按 validation PR-AUC 最大选择。
3. 恢复最佳权重后仅评估 test 一次。分类各集合必须含 0/1 两类；回归评估集至少两条记录。
4. 模型卡分别保存 validation/test 指标、测试预测摘要、来源/许可、数据质量、代码哈希与 Git revision、模型参数、权重哈希和限制。
5. 模型卡原子发布、不覆盖已有文件；注册失败清除本次新建权重与卡，不影响已注册模型。
6. 注册不等于激活；仍需按 endpoint 显式选择。`endpoint_ready` 表示满足数据/产物契约，不代表已达到临床或科研性能阈值。

代码哈希是本次训练相关源文件的实际摘要，比未提交工作树下的 Git revision 更精确。未找到 Git 时 revision 为 `unknown`，源码哈希仍保留；训练期间源码发生变动会拒绝注册。

本进程内 prepared 训练串行保护 PyTorch RNG，并恢复调用前 RNG 状态；已测试 CPU 顺序运行的非零 dropout 可复现。不保证与无关 Torch 调用并发、跨硬件或 GPU 运算的逐位一致性，不提供分布式训练调度。

旧 raw CSV 入口继续兼容，但明确标记 `legacy_unvalidated`，不能自动取得端点就绪状态。旧路径自身的历史清理/标签行为不在本阶段重构范围内。

## 验证

干净 CPU CI 使用 `requirements-ci.txt`：Torch 2.4.0、PyG 2.6.1 及匹配
该 Torch ABI 的 `torch-scatter` / `torch-sparse`。扩展 wheel 来自
[PyG 官方 CPU 索引](https://data.pyg.org/whl/torch-2.4.0%2Bcpu.html)，
对应关系见 [官方安装说明](https://pytorch-geometric.readthedocs.io/en/latest/install/installation.html)。
本批未改动现有 Conda 环境或 CUDA 部署依赖。
原子显式价态特征兼容仓库固定的 RDKit 2023.9.2 与新版 RDKit：新版使用
`GetValence(EXPLICIT)`，旧版使用等价的 `GetExplicitValence()`，不改变特征编码。

```powershell
python -m pytest tests/test_activity_prepared_training_data.py tests/test_activity_prepared_training_loop.py tests/test_activity_trainer_endpoint_metadata.py -q -p no:cacheprovider
python -m pytest tests/test_activity_prediction_contract.py tests/test_activity_model_registry.py tests/test_activity_endpoint_selection.py -q -p no:cacheprovider
python -m pytest tests/test_activity_prepared_input_integrity.py tests/test_activity_training_integration_guards.py -q -p no:cacheprovider
python -m pytest tests/test_activity_training_ci_dependencies.py -q -p no:cacheprovider
python -m pytest tests/test_activity_feature_split_integrity.py -q -p no:cacheprovider
python -m pytest tests/test_activity_rdkit_valence_compat.py -q -p no:cacheprovider
python -m compileall -q src scripts
```

真实网络烟雾测试使用临时目录中的合成标签，只验证网络、优化器、三路评估和注册接口。不得引用这些指标作为 PDE/BuChE 预测性能；正式指标及适用域需另行开展真实数据评估。
