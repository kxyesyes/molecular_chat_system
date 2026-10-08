# 分子性质入口核心收敛交接

## 本批范围

本批只收敛分子基础性质的科研计算入口，不改变 ADMET 的可用性语义、HTTP 响应结构或 Agent 公共输出字段。

- `src/molecular_design/chemistry.py` 负责唯一的 RDKit 描述符计算，并保留规范化结果与未截断的原始数值。
- `src/web/routes/molecule_properties_routes.py` 只负责 HTTP 输入、兼容字段映射和 `Unknown/not_calculated` 的 ADMET 展示。
- `src/agent/tools/property_calculator.py` 只负责 Agent 输入/输出适配，复用同一科研核心。
- 旧 Web/Agent 调用方的字段名、精度、无效结构错误 envelope 和 QED 边界校验保持不变。

## 验证证据

- 先新增两条“必须调用科研核心”的回归，修复前均失败，分别证明 Web 路由和 Agent 工具存在重复计算。
- 修复后聚焦回归：`123 passed`。
- 分子设计、性质和路由联合回归：`138 passed, 1 skipped, 1 warning`。
- 路由/兼容/运行时边界回归：`133 passed, 7 warnings`。
- `python -m compileall -q src scripts`：通过。

## 未在本批完成

这不是整个架构整理或科学验收的完成标志。以下事项仍需独立批次：

1. 其他科研模块（ADMET、活性、对接、靶点搜索、分子设计）继续统一核心结果与 Web/Agent 适配边界。
2. 剩余 `_support` 历史直接调用点、Planner/工具契约和旧交接文档的逐项清理。
3. 首页正式入口、真实模型/权重链路、真实数据库资产与部署环境验收。
4. 全量 Python、Node、健康检查和最终跨层扫描完成后再形成最终发布结论。
