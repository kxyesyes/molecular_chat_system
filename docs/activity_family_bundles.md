# 家族双模型成组注册

本模块组合已有端点模型与家族数据包，不训练模型、不反序列化权重、不接入在线推理。
数据准备见 [activity_family_data.md](activity_family_data.md)。

## 契约与入口

`ActivityModelRegistry` 新增 Python 接口：

```python
record = registry.register_family_bundle(
    bundle_id="pde-v1",
    family_dataset_path=family_manifest,
    classification_model_id="pde-classifier",
    regression_model_id="pde-regressor",
)
assert registry.get_active_family_bundle("PDE5A") is None  # 首次注册不自动选用
# 经部署验收、取得授权后才可显式选择；本批没有在实际运行目录执行。
registry.select_family_bundle("pde-v1")
```

以上 ID 与路径变量仅为示例，不表示随仓库附带可用模型。
PDE 亚型归入 `pde-family`，BuChE/BChE 归入 `buche-family`，未知/歧义家族拒绝。

- 两个模型必须不同，均为 endpoint-ready，分别匹配分类与回归。
- 元数据及实际模型卡必须匹配源数据摘要、对应子包摘要、拆分、靶点、任务、单位、
  model contract、来源授权。保留 main 的完整模型卡/指标一致性校验。
- 模型文件按当前摘要核验；demo/fallback/legacy 不能组成有效家族包。
- 绑定固定标签阈值 5、概率阈值 0.5；注册完整性不是科学性能或部署批准。
- 数据预检在注册事务外执行，进入事务后重新读取当前模型与重复 ID，单次原子写入。
- 选择时重新校验整个包，不允许半切换；返回 detached 副本，外部修改不影响注册状态。
- 删除单个模型时，同一事务移除依赖它的 bundle 和活动家族映射，不影响其他家族。

## 持久化与兼容性

注册表状态升级为 v3，新增 `family_bundles` 与 `active_family_bundles`。
读取 v1/v2 时通过原有事务机制迁移，保留全局/端点选择；不创建第二份状态权威。
旧版本不认识 v3；已迁移目录不能直接由旧程序继续管理。部署前应单独备份并验证迁移，
本轮没有打开或迁移正在服务的模型目录。

注册时完整验证训练数据，保存 descriptor 原始 JSON 文本、独立摘要及解析后的证据。
不保存 CSV 原文或机器路径。选择/查询不再依赖私有训练文件仍然存在，但会验证已固定
descriptor 与模型卡/权重的一致性。模型或证据损坏时抛出错误，不回退旧模型。
本地注册状态属于受信元数据；摘要用于一致性校验，并非外部来源的数字签名。

## 范围

测试仅使用临时合成数据和明确不可用于推理的占位权重字节；没有训练或启用真实模型。
隔离 Python 预测入口见 [activity_family_inference.md](activity_family_inference.md)。
API/UI、Agent 调用与真实模型晋级保留在后续独立批次。
运行记录及迁移来源见 [本批交接](handoff/activity-family-bundle-integration.md)。
