# 已校验数据集的 RG-MPNN 训练

状态：内部 Python 训练接口及家族预测 API/Agent 接入代码已实现于独立工作树；
2026-09-08 已完成四个真实家族基线模型，但未激活或部署到原始项目。
结果见 [真实基线报告](activity_family_baseline_20260908.md)，家族预测用法见
[家族模型说明](activity_family_models.md)。不要把客户端提供的任意磁盘路径直接传入此接口。

## 使用边界

先按照 [数据准备说明](activity_data_preparation.md) 生成 `dataset_manifest.json`、三份 CSV 和质量报告。训练读取同一份已校验字节快照，重新检查文件哈希、汇总哈希、标签转换、分子/骨架身份、拆分泄漏及数量。哈希只能证明内容完整性，不能证明实验来源真实；来源、许可和测量语义仍需数据提供者确认。

用户提供的 PDE/BuChE 数据已按冻结协议训练；临时合成数据测试仍只验证工程契约，
不能用其指标替代真实测试集报告。

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

此例的 dataset 路径为占位示例，必须换成准备命令实际返回的文件。`submit_training_job` 在后台线程运行，进程必须持续存活；状态目前是进程内字典，不具备训练任务重启恢复能力。
家族基线使用 `scripts/train_family_activity_models.py --run-id <new-id>` 串行驱动同一训练接口，
持久化进度报告但不支持断点续训；已有 run ID 拒绝覆盖，也不自动重试。再次训练需另行确认协议。

- prepared 路径一旦显式传入，失败不会回退到旧 CSV 流程。
- task、终点、单位和标签来自已校验 manifest，不由旧 `target_column/task_type` 参数覆盖。
- 三路拆分不可重新随机分配；`random_seed` 是训练种子，数据拆分种子另外记录。
- 现有 RGNN 需要 `num_layers >= 2`；小于 2 会在特征计算前明确拒绝。
- 任何分子特征计算失败都终止训练，不静默删行改变拆分。
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

```powershell
python -m pytest tests/test_activity_prepared_training_data.py tests/test_activity_prepared_training_loop.py tests/test_activity_trainer_endpoint_metadata.py -q -p no:cacheprovider
python -m pytest tests/test_activity_prediction_contract.py tests/test_activity_model_registry.py tests/test_activity_endpoint_selection.py -q -p no:cacheprovider
python -m compileall -q src scripts
```

真实网络烟雾测试使用临时目录中的合成标签，只验证网络、优化器、三路评估和注册接口。
不得引用这些指标作为 PDE/BuChE 预测性能；真实测试指标单独记录在基线报告，
训练/注册成功不代表预测精度达标。
