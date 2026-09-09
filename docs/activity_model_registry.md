# 按靶点与终点选择活性模型

`src/activity/model_registry.py` 的注册状态现采用版本 3，兼容迁移 v1/v2。
旧的全局 `active_model_id` 保留，新增 `active_models_by_endpoint` 映射，
每个端点分别选择模型。prepared 训练已有内部 Python 接口，网页和 Agent
端点推理入口迁移仍是后续任务；导入模块不会自动训练或激活新模型。

家族成组状态新增 `family_bundles` / `active_family_bundles`，不替代端点与全局选择。
成组注册和显式选择见 [activity_family_bundles.md](activity_family_bundles.md)。

## 端点身份

`endpoint_key` 沿用 `DatasetManifest.endpoint_key`，由规范化后的
`target_id:输出终点:输出单位:task_type` 组成。
例如项目内受控靶点 `target-a` 的 pIC50 回归任务对应
`target-a:pic50:pic50:regression`。
不要根据显示名称猜测靶点 ID；实际数据必须使用确认过的稳定标识。

浓度转 pActivity 后，模型的 `endpoint`、`units` 描述输出，
`label_transform` 保留转换方式。不同靶点或终点的模型不能相互代替。
二分类任务还需合法的分类指标和独立测试集混淆矩阵；单类别测试集不能通过。

## Python 接口

```python
from src.activity.model_registry import ActivityModelRegistry

registry = ActivityModelRegistry("data/activity/models")
registry.select_for_endpoint(endpoint_key, model_id)
model = registry.get_active_for_endpoint(endpoint_key)
if model is None:
    # 明确处理该端点没有可用模型的情况。
    ...
```

调用方应使用规范化的 endpoint key。没有选择、模型权重/卡缺失或损坏时，
`get_active_for_endpoint()` 返回 `None`，不回退到其他端点或全局模型。
注册状态文件本身损坏、映射悬空或身份不匹配时会抛出校验错误，不能视为可用状态。

`select()` / `get_active()` 仍是旧的全局接口。
选择某个端点不会改变全局选择，反之亦然。
旧全局入口未选择模型时，只允许自动发现 `legacy_unvalidated` 历史模型；
不能仅因列表排序或历史权重文件名而自动使用已注册的 prepared 模型。
删除模型会在同一注册事务中移除其全部选择映射，再尝试清理权重、sidecar 和模型卡。

## 注册门槛

旧 metadata 可继续通过兼容接口使用，但不会自动获得 `endpoint_ready`。
任何端点就绪声明必须完整提供：

```text
target_id, target_name, endpoint_key, label_transform,
prepared_dataset_sha256, split_counts, split_scaffold_counts,
test_metrics, model_card_file, model_card_sha256, scientific_readiness
```

`scientific_readiness` 必须为 `endpoint_ready`；拆分必须为 scaffold。
三个集合的行数和骨架数必须为正数且相互合理，指标必须有限且满足对应任务约束。
原有的权重哈希、数据哈希、网络配置、随机种子等字段仍然必需。

模型卡保存在模型目录内，用文件名引用并检查 SHA-256。
卡中必须明确 `demo_mode=false` 和 `fallback_used=false`，并与注册记录的
身份、权重、数据摘要、拆分数量、测试指标、随机种子和网络配置一致。
模型卡自身不必包含其文件名或哈希，避免哈希自引用。
路径穿越、共享或冲突的产物文件会被拒绝。

若声明了来源、许可、训练代码、质量摘要、拆分种子等扩展证据，模型卡与
注册记录必须双向一致，不能一侧变更或丢失。`validation_metrics` / `best_metrics`
必须与注册的验证 `metrics` 一致且数值合法；重算卡哈希不能使矛盾证据变为有效。
保留既有最小 v2 卡兼容，允许 builder 在卡内补充一致的验证指标及限制说明。

注册门槛验证元数据和本地文件的完整性，不反序列化 PyTorch 权重，
也不证明模型达到了实际科研性能要求。真实训练、独立测试集评估和适用域判定
仍须在后续训练链路中完成。

## 迁移与训练接入

版本 1/2 状态在持有现有进程/线程锁时原子升级，保留已注册模型和全局选择。
版本 1 的端点选择映射初始化为空；版本 2 的已有端点选择保留。
两种旧版本的家族映射初始化为空，不根据旧模型名字自动推断靶点或选用家族包。
旧程序不支持 v3，部署前需备份并单独验证状态迁移；不要让旧程序直接管理升级后的目录。

`trainer._build_model_metadata()` 新增可选 `prepared_metadata` 参数，
仅允许端点扩展字段，不能覆盖实际计算的权重/数据哈希或基础模型身份。
显式传入空或不完整扩展会失败；不传该参数保持旧行为。
模型构造、注册和激活是不同动作，该 helper 不执行注册或激活。

## 回归测试

```powershell
python -m pytest tests/test_activity_model_registry.py tests/test_activity_endpoint_selection.py tests/test_activity_trainer_endpoint_metadata.py -q -p no:cacheprovider
python -m pytest tests/test_activity_training_integration_guards.py -q -p no:cacheprovider
python -m pytest tests/test_activity_model_card_consistency.py -q -p no:cacheprovider
python -m compileall -q src scripts
```

测试产物全部在临时目录，合成文件仅用于工程契约测试，不代表真实科学模型。
