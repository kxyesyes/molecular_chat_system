# 家族双模型：注册与 Python 推理

本入口补充现有单模型/端点入口，不自动替换 Web、Agent 或 legacy 全局模型。
模型完整性检查通过不等于科研性能认证。当前阶段没有训练或激活真实 PDE/BuChE 模型。

## 模型准备与注册

先通过上一阶段的 `family_dataset.json` 准备分类、回归两个子数据集。
后续用现有 prepared 训练器分别训练，两模型完成各自验证后进入同一个 ActivityModelRegistry。
模型组注册核对真实模型卡与 metadata、权重摘要、所属家族、任务/单位、数据 manifest 摘要
及 prepared 摘要。不能拿另一个家族或不同拆分的模型凑成一对。

```python
from src.activity.model_registry import ActivityModelRegistry

registry = ActivityModelRegistry("data/activity/models")
bundle = registry.register_family_bundle(
    bundle_id="pde-pair-v1",
    family_dataset_path="data/activity/prepared/pde-family-v1/family_dataset.json",
    classification_model_id="registered-classifier-id",
    regression_model_id="registered-regressor-id",
)
# 显式激活：注册本身不改变任何活动选择。
registry.select_family_bundle("pde-pair-v1")
```

上述 model ID 是占位符，不能替代训练产物。模型缺失、损坏、demo/fallback、来源不匹配
时应明确失败；不生成随机权重补位。注册库和权重仅保存在 Git 忽略的本地资产目录。
删除模型会在同一注册事务内移除依赖模型组及相应活动选择，不切换另一家族。

注册表升级为 v3：读取 v1/v2 时保留旧全局/端点选择，新建空的家族映射，
不会擅自把历史模型凑成模型组。已有 bundle ID 不覆盖，失败激活保留原活动包。
注册时完整验证数据包，随后固定来源、分配与两个子 manifest 的证据；
在线读取不重开私有训练 CSV，仍逐次核验模型卡、权重和固定证据。
完整预检在全局注册锁外执行，进入事务后核验最新模型及重复 ID，再单次写入。
注册库保留描述 JSON 的原始文本（非 CSV），读取时独立重算原文摘要并核对解析结果，
避免仅复制待验证摘要进行自比较。
摘要用于完整性和绑定检查，不是防恶意本机管理员篡改的数字签名或实验真实性认证。

`dataset_split_seed` 必须与数据包一致；训练随机种子 `random_seed` 可以独立选择，
两个模型也不必使用相同训练随机种子。相同 seed 本身不能证明使用同一分子拆分。

## 推理

```python
from src.activity.family_predictor import FamilyActivityPredictor

predictor = FamilyActivityPredictor(registry)
rows = predictor.predict(["CCO", "CC(C)(("], target="PDE5A")
```

`target` 是受控家族/亚型标识，不是任意自然语言 prompt。
未知靶点、AChE、同时含 PDE 和 BuChE 的未消歧标识不会默认选择某个模型。
只对可唯一确定母体的有效输入计算；失败行保留原位置，不返回虚构数值或可视化结构。

固定顺序：校验输入 → 取得完整模型组 → 分类 → 对分类成功的全部分子回归。
分类阴性也回归，不用概率推导 pIC50、不用固定 0/4.99 替代回归，也不裁剪数值。

| 状态 | 返回内容 |
|---|---|
| `passed` / `success=true` | 两个阶段成功，输出活性概率、类别及预测 pIC50 |
| `partial` / `success=false` | 分类成功、回归失败；保留分类，pIC50 为 null |
| `failed` / `success=false` | 输入、模型组或分类失败；不声称完成两阶段预测 |

训练标签阈值为 pIC50 **5**；推理概率阈值为 **0.5**。二者不可混淆。
分类阴性但回归 >=5（或反向矛盾）时，设置
`classification_regression_consistent=false` 并保留两项原始输出。
模型原始输出在 sigmoid 前检查有限值，防止无限值伪装成正常的 0/1 概率。

## 安全与生命周期

- 一次调用固定完整模型组，不在两阶段之间重新选择。
- 权重从受校验字节快照加载，强制 `weights_only=True`，不回退不安全 pickle。
- 按家族缓存模型组；身份或模型证据变化时成组失效，每次调用仍复核文件完整性。
- 同一 predictor 实例用锁串行保护模型组加载与推理；这不是 GPU 调度或多租户队列。
- 结果保留来源、两模型身份/摘要、阶段错误及 warnings；溯源采用字段白名单，
  不回传本机私有数据路径或任意模型 metadata 扩展。
- 合成权重测试只证明注册/前向/错误边界，不能引用其指标说明真实预测效果。

Web 路由、Agent 工具适配、用户结果展示与正式四模型训练属于后续阶段。
