# 家族双模型隔离推理

预测器本身只提供 Python 入口；HTTP 接线见 [activity_family_api.md](activity_family_api.md)。
默认单模型选择与 Agent 调用不由该预测器隐式修改。
模型组的注册、数据绑定和 v3 状态迁移见 [activity_family_bundles.md](activity_family_bundles.md)。

## 调用与前置条件

```python
from src.activity.family_predictor import FamilyActivityPredictor

# registry 由调用方提供；必须已有经部署验收并显式选用的模型组。
# 不会隐式注册、训练、激活模型或扫描旧版默认权重目录。
predictor = FamilyActivityPredictor(registry)
rows = predictor.predict(["CCO", "CC(C)(("], target="PDE5A")
```

`target` 是受控家族/亚型标识，不是任意自然语言请求。PDE 亚型使用 `pde-family`，
BuChE/BChE 使用 `buche-family`；未知、歧义或未配置模型组明确失败，不跨家族回退。
沿用数据层母体规范化规则：无效 SMILES 或无法唯一确定母体的多片段输入不进入模型。
返回顺序和重复输入保留；无效行不返回模拟性质。

## 两阶段契约

有效输入规范化 → 固定一个已验证模型组 → 分类 → 分类成功行全部进入回归。
分类阴性也进行回归，不从概率推算 pIC50，不补 0/4.99，不裁剪矛盾数值。

| 行状态 | 含义 |
|---|---|
| `passed`，`success=true` | 两阶段均通过输出校验，返回概率、类别和回归 pIC50 |
| `partial`，`success=false` | 分类成功、回归失败；保留分类，pIC50 为 null |
| `failed`，`success=false` | 输入、模型组或分类失败；不得声称两阶段完成 |

训练标签阈值为 pIC50 5，推理分类概率阈值为 0.5，两者不能混淆。
分类与回归在阈值两侧矛盾时，保留原值并设置
`classification_regression_consistent=false` 和 warning，不掩盖矛盾。
每阶段校验批次数量、SMILES 对齐、任务/单位、有限数值及 demo/fallback 状态。
原始网络输出在 sigmoid 前检查，避免无限值被转换为正常的 0/1 概率。

## 安全、缓存与溯源

- 使用现有 RGNN 图特征和前向逻辑；main 的 `feature_smiles` 特征追踪不回退。
- 权重由注册表定位，安全读取字节快照并核对摘要，再用 `weights_only=True` 装载。
  不支持受限加载时明确失败，不退回不安全 pickle。
- 一次请求固定整组模型；中途更换活动组不会拼接两个组的阶段结果。
- 每个 predictor 实例按受控家族缓存，组身份/证据变化时替换；每次请求仍验证当前文件，
  损坏或缺失时不使用旧缓存。实例锁保护装载和推理，不是跨进程调度器。
- 各行保留请求靶点、家族/组 ID、两模型身份/摘要、来源范围、warnings 和阶段错误。
  模型 provenance 使用字段白名单，执行异常不直接回显库堆栈或私有路径。

## 验证边界

单元测试的阶段返回值是受控契约夹具；集成测试在临时目录创建**未训练合成权重**，
真实执行 RGNN 前向，只验证工程链路，不代表 PDE/BuChE 预测性能。
本批不读取用户真实数据、不训练或激活生产模型、不重启服务。
真实模型性能、Web/API/Agent 接线与部署晋级属于后续独立验收。

测试和迁移记录见 [本批交接](handoff/activity-family-predictor-integration.md)。
