# PDE / BuChE 真实 RG-MPNN 基线训练：2026-09-08

## 结论

`baseline-20260908-v1` 四个真实模型训练、单次测试评估和两个配对模型组注册均完成，
总耗时 928.109 秒。未激活模型、未重启服务、未改动原始项目或合并 main。
**训练完成不等于预测质量达标**：PDE 分类在固定概率阈值下区分无活性分子的能力较弱，
两组回归的测试 R² 均只有约 0.2。不能把这些基线描述为高精度活性预测。

## 冻结协议与溯源

- 训练代码提交：`81ca08b1545ed271fddf82b92df608123ced448c`。
- 设备：NVIDIA GeForce RTX 5060 Laptop GPU；PyTorch `2.8.0+cu128`。
- 参数：epochs=50、patience=12、batch_size=32、lr=0.001、num_layers=5、
  hidden_size=256、dropout=0.2、weight_decay=0.0001、Cosine 调度、random_seed=42。
- 分类使用 BCEWithLogitsLoss；回归使用 MSE。pIC50 < 5 为无活性标签，
  分类概率决策阈值固定为 0.5；没有根据测试集调整阈值。
- 使用已校验的 family-v1 配对数据和原固定 scaffold 拆分；分类/回归分子划分一致。
  PDE train/validation/test 为 1850/396/396；BuChE 为 1599/343/343。
- 分类按 validation PR-AUC 最大、回归按 validation RMSE 最小选模，恢复最佳权重后
  每个模型仅评估 test 一次。没有调参搜索、失败重试或看到测试结果后重训。
- 四模型注册记录均为 `demo_mode=false`、`fallback_used=false`；权重和模型卡哈希验证通过。
  模型卡保留来源、许可、拆分、代码和文件哈希；不在本文复制原始分子行。

## 测试集指标

常数基线只从训练标签拟合：分类使用训练集阳性比例，回归使用训练标签均值。
所有数字来自本轮结构化报告，不是合成测试、外部 LLM 或实验测量。

| 模型 | 训练轮数 / 最佳轮 | ROC-AUC | PR-AUC | 平衡准确率 |
|---|---:|---:|---:|---:|
| PDE 分类 | 50 / 50 | 0.712055 | 0.854513 | 0.516094 |
| PDE 常数分类基线 | — | 0.500000 | 0.651515 | 0.500000 |
| BuChE 分类 | 47 / 35 | 0.818198 | 0.829669 | 0.790939 |
| BuChE 常数分类基线 | — | 0.500000 | 0.574344 | 0.500000 |

| 模型 | 训练轮数 / 最佳轮 | RMSE（pIC50） | MAE（pIC50） | R² |
|---|---:|---:|---:|---:|
| PDE 回归 | 50 / 43 | 1.703752 | 1.285144 | 0.203562 |
| PDE 常数回归基线 | — | 2.292231 | 1.753938 | -0.441637 |
| BuChE 回归 | 36 / 24 | 0.926840 | 0.734183 | 0.229635 |
| BuChE 常数回归基线 | — | 1.056085 | 0.874627 | -0.000195 |

PDE 分类混淆矩阵：TN=13、FP=125、FN=16、TP=242；138 个无活性样本只识别出 13 个。
BuChE 分类混淆矩阵：TN=102、FP=44、FN=23、TP=174。
BuChE 两个模型按原 patience 正常早停，不是异常终止。

四模型各项指标均严格优于对应常数基线，但比较没有显著性检验或置信区间，
不能由 `improves_all_metrics` 推导部署就绪。PDE 回归 validation RMSE=1.071556，
test RMSE=1.703752，存在明显泛化差距；原因尚不能仅凭这一次拆分确定。

训练器未保留逐分子的测试预测，因此本轮**未评估分类/回归联合一致性**；
不从边际指标推算、不重跑测试推理补造结果。也未进行真实权重的 Web/Agent 在线联调。

## 本地资产（不提交 Git）

以本工作树根目录为基准：

- 报告：`outputs/activity_training/baseline-20260908-v1/report.json`。
- 模型、模型卡、注册表：`data/activity/models/baseline-20260908-v1/`。
- PDE 组：`baseline-20260908-v1-pde-family`。
  分类 `bbfefb9a-cbe0-46f2-b952-a0920a423f69`；回归 `84c0835b-010f-43bf-8ba5-919aa8453f74`。
- BuChE 组：`baseline-20260908-v1-buche-family`。
  分类 `f3cea44e-7875-447c-9d3c-8bed16e4b6b9`；回归 `2a737539-2711-4e3e-9ac8-9f5b73be4463`。

四份权重各 8,256,635 字节，均通过注册表读取和权重解析验证。
隔离注册表的 `active_model_id` 为 null，`active_models_by_endpoint` 和
`active_family_bundles` 均为空。注册产物不影响现有服务的模型选择。

## 实际验证

以下 `python` 使用 MedChat Conda 环境，不是缺 RDKit 的系统默认解释器。

```powershell
python -B -m pytest tests/test_activity_prepared_training_data.py tests/test_activity_prepared_training_loop.py tests/test_family_training_run.py -q -rs -p no:cacheprovider --tb=short
python -u -B scripts/train_family_activity_models.py --run-id baseline-20260908-v1
python -m compileall -q src scripts
git diff --check
```

- 聚焦测试：96 passed、3 skipped、2 warnings；3 项跳过是本机 Windows 符号链接权限，
  warnings 是已有 PyG 弃用和张量构造性能提示。
- CUDA 实际 RGNN 前向/反向/Adam 烟雾检查通过；仅使用公开简单分子，不作性能依据。
- 真实训练命令 exit 0、4 completed、0 failed、2 bundles、无 bundle_errors。
- 完成后注册表和四模型权重/模型卡验证通过，demo/fallback 均为 false，未再调用测试推理。
- compileall 通过；本阶段没有复跑全量测试，也没有使用外部模型或 API key。
- 训练前独立代码审查通过；bundle 注册异常继续下个家族、显式基线质量比较已补回归测试。

## 下一步

先在隔离环境进行真实权重的 API/Agent 双模型接入验收，检查靶点路由、分类/回归来源、
负例和错误输入，不把在线冒烟案例当作新的测试集性能证据；通过后再明确批准启用。
PDE 分类改进应在新协议下使用训练/验证数据研究类别不平衡和校准，不能用已查看的
测试结果挑阈值或反复选模。后续性能主张需要新的独立评估，保留本轮原始基线不覆盖。
