# Agent 历史审计与恢复修复集成

日期：2026-09-12。分支 `codex/agent-recovery-audit-integration`。
PR #16 获得用户具体授权后合并为 main `becb6ab`；本分支已重新基于该提交，
与重接前的恢复代码树一致。未直接提交 main，未改动原始混杂工作树。

## 本批范围

总体计划 Task 5 的旧执行链审计子批次，来源 `9312bf5`，不是新的模型决策循环。

- abstain 时只路由一次；聊天返回保留结构化 status/error/warnings。
- 通用失败标题不能覆盖具体且经过脱敏的错误原因。
- checkpoint 复用同时核对工具名称、工作流、输入、工具/adapter/模型版本。
- 委派执行读取和写入同一 supervisor-owned store，不错误读取外部 orchestrator 的库。
- checkpoint 产物恢复为验证过形状的独立对象；损坏时重跑并保留警告，不静默丢弃。
- 必需步骤失败停止；可选步骤默认继续，但显式禁止继续时停止；两种执行入口复用同一策略。

未引入 dynamic session、decision loop、实验聊天入口或权限变更。未移植旧版分子输入处理。
旧 workflow 私有持久化辅助方法目前保留，是否删除另按实际调用核查，未混入本批修复。

## TDD 证据

移植 3 份历史测试后，在 main `370898a` 复现 **18 failed / 29 passed**。
最小修复后 **2 failed / 45 passed**；剩余两项依赖 PR #16 的 typed invalid-input 契约。
接入 PR #16 后聚焦 **47 passed**，全 Agent **1957 passed / 1 skipped / 7 warnings**，23.49 秒。
随后 PR #16 合并，重接分支的整树 diff 为空。代码提交 `34d95ad` 的独立规格审查
（222 项）与独立质量审查（236 项）均通过，无本批阻断项；PR 的 CI 和用户具体合并授权仍需另行确认。

重接后另运行 `tests/agent` + 反幻觉/平台健康：**1975 passed / 1 skipped / 7 warnings**，
30.11 秒。compileall（系统临时 pycache）、全部 8 个 Node、contract 通过。

测试均使用合成工具/临时数据库；不证明实际模型性能或目标服务器部署成功。
未读取真实密钥/CSV/生产权重，未启动训练、模型激活或生产服务。
旧沙盒一次 artifact_failed 的待查记录继续保留，不因本批通过而撤销。

## 非阻断审查记录

- `supervisor.py` 的旧 run 元数据仍写工作流版本 `"1"`；自定义 orchestrator 版本时，
  run 记录和 checkpoint 版本显示不一致。实际 checkpoint 复用核验使用正确版本，
  本批未扩大修改旧 run 元数据；后续应统一并增加回归。
- 产物嵌套 metadata 的深拷贝隔离已由审查者独立探针验证，但现有提交测试只检查恢复值；
  后续补充“双次恢复后修改其中一个，不能污染 checkpoint/另一次结果”的固定回归。
