# 项目文档索引

按任务选择入口；日期化计划与验收记录用于追溯，不代表当前功能已上线。

## 开始使用与协作

| 需要做什么 | 入口 |
|---|---|
| 了解项目、本机启动、保存主聊天模型配置 | [项目 README](../README.md) |
| 开始开发或交给 Agent 修改 | [AGENTS.md](../AGENTS.md) |
| 查目录、编码规范、测试命令和安全要求 | [项目规范](PROJECT_STANDARDS.md) |
| 首次部署 Ubuntu 单机服务 | [部署教程](DEPLOYMENT_TUTORIAL.md) |
| 查部署模板、生产门禁和 Temporal worker 边界 | [部署环境说明](../deployment/README.md) |
| 确认数据来源、版本和迁移资产 | [数据登记](../data/REGISTRY.md) |

## 领域与运行手册

- Agent：[架构与维护入口](AGENT_MAINTENANCE.md)、[工具契约](AGENT_TOOL_CONTRACTS.md)。
- 科学工具：[对接工具安装](../deployment/docking_tools.md)、[靶点数据库](target_search_local_db.md)、[反向寻靶](../src/reverse_target/README.md)、[ADMET-AI](admet_ai_140.md)。
- 活性训练：[数据准备](activity_data_preparation.md) → [训练执行](activity_prepared_training.md) → [模型 registry](activity_model_registry.md)。
- 家族活性模型：[数据准备](activity_family_data.md)、[bundle](activity_family_bundles.md)、[推理](activity_family_inference.md)、[API](activity_family_api.md)。
- 长任务：[Temporal canary 运行手册](runbooks/temporal_docking_canary.md)、[OpenSandbox](../deployment/opensandbox/README.md)。
- 安全：[数据留存与清理策略](security_data_retention_policy.md)。

## 交接与历史证据

- [交接汇总](handoff/latest.md)：按日期、commit 和 PR 确认适用范围；其中旧记录不是当前状态保证。
- [专项交接](handoff/)：改动范围、审查和验证证据。
- [设计](superpowers/specs/)、[执行计划](superpowers/plans/)、[验收记录](superpowers/validation/)与[测试报告](reports/)：保留当时的条件、失败和限制。
- [早期问题清单](issues_and_improvement_plan.md)、[Agent 化实施记录](agentization_phase2_phase3_implementation.md)：仅用于历史背景，当前代码入口优先查维护文档。

## 文档维护边界

快速启动和主模型设置维护在根 README；协作与提交约束维护在 AGENTS；测试命令和编码细则维护在项目规范。部署教程保留完整操作步骤，部署环境说明只补充生产边界和专用运行约束。

重复说明改为链接，不重复粘贴整段命令。新增专题后更新本索引；历史记录保留原始证据，不因篇幅长而删除。详细规则见 [项目规范第 17 节](PROJECT_STANDARDS.md#17-文档维护规则)。
