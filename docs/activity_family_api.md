# 家族活性预测服务与 API

模型组与隔离预测器见 [activity_family_inference.md](activity_family_inference.md)。
本批把同一预测器接入既有 HTTP 预测接口，不训练/激活模型，也不改变 Agent 和前端默认调用。

## HTTP 接口

| 接口 | 原有字段 | 可选字段 |
|---|---|---|
| `POST /api/activity/predict` | 表单 `smiles` | 表单 `target`，如 `PDE5A`、`BuChE` |
| `POST /api/activity/batch_predict` | 上传 `file` | 表单 `target` |

未提供 target 的调用保留旧版单模型路径。非空显式 target 只能进入家族预测器；未知/歧义
靶点、未选用模型组或组文件损坏不会退回全局模型。该字段接受受控标识，不是自由文本规划。
批量输入沿用现有解析方式与上传上限，保留有效解析行的顺序和重复 SMILES。

## 结果与状态

响应保留 `results`，增加 `status` 和汇总 `warnings`。外层 `success` 表示科学步骤完成情况，
不再只表示 HTTP 调用结束；HTTP 200 不等于有可用科学预测。

- 非空且每行完整成功：`passed / success=true`。
- 至少一行完整成功或有部分观察，但未全部完整成功：`partial / success=false`。
- 空结果或全部失败：`failed / success=false`。

行内分类概率、类别、pIC50、provenance、warnings 和错误原样保留。回归失败的 null 不补 0，
概率 0 不当成缺失。汇总警告只接受列表中的字符串并去重；畸形可选 warnings 字段仍留在原行，
不将字符串拆成字符、不因 null 警告让已有观察丢失。

内部异常返回固定错误消息，不回显堆栈/路径；显式 HTTPException 的状态码与 headers 保留。
原有上传限制和模型管理接口不在本批变更范围。

## 服务生命周期

`src/activity/prediction_service.py` 统一分发与汇总；以已有 `ACTIVITY_MODEL_DIR` 解析后的
注册目录为缓存键，最多缓存两个家族预测器。冷启动构建串行，预测计算不占用工厂锁。
每个家族预测器仍在自己的锁内固定模型组，并逐次验证模型证据；缓存不豁免校验。
获取预测器、建注册表、校验和计算均在现有 route 线程池内，不阻塞事件循环。

缓存是进程内机制，不是 GPU 队列、跨进程锁或部署批准。生产模型、并发配额和公网权限
必须另行验收。本批没有恢复或改变此前移除的管理员令牌机制。

## 测试与限制

`tests/test_activity_family_api.py` 覆盖分发、汇总、线程池、并发构建、错误脱敏和 legacy 兼容。
`tests/test_activity_family_inference_integration.py` 保留 Python 入口，并扩展真实 FastAPI
单分子/批量入口，在临时目录使用未训练合成 RGNN 权重前向、禁止重开源数据、检查暖缓存卡片损坏。
这些测试不是 PDE/BuChE 真实模型性能证据，不会注册或启用正在服务的模型。
