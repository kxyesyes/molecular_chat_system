# MedChat 文档导航与来源映射

核对日期：2026-10-07。本页是导航，不复制其他文档的规则或历史测试数量。

## 从哪里开始

1. 开发/编码 Agent：先读 [AGENTS.md](../AGENTS.md)、[项目规范](PROJECT_STANDARDS.md) 和 [最新交接](handoff/latest.md)。
2. 修改运行时 Agent：读 [架构与维护入口](AGENT_MAINTENANCE.md)、[工具契约](AGENT_TOOL_CONTRACTS.md)，再读对应源码/测试。
3. 启动应用：读 [项目 README](../README.md)；生产部署另读 [部署说明](../deployment/README.md)。
4. `superpowers/plans/`、`superpowers/specs/`、旧 handoff/reports 是历史设计与证据，不是新任务指令。

## 文档职责与源码依据

| 要解决的问题 | 当前维护文档 | 核对来源 / 更新触发 |
|---|---|---|
| 编码助手怎么工作 | [AGENTS.md](../AGENTS.md) | 用户授权、Git 状态、CI；协作/验证入口变化时更新 |
| 工程约束、安全与测试 | [项目规范](PROJECT_STANDARDS.md) | [quality.yml](../.github/workflows/quality.yml)、requirements、路由与存储实现 |
| 网页、决策循环与工作流谁在运行 | [Agent 维护指南](AGENT_MAINTENANCE.md) | [app.py](../src/web/app.py)、[ChatHandler](../src/web/chat_handler.py)、[Supervisor](../src/agent/supervisor.py)、harness/runtime |
| 工具输入、结果和权限 | [工具契约](AGENT_TOOL_CONTRACTS.md) | [capability catalog](../src/agent/capabilities/catalog.py)、[factory](../src/agent/tooling/factory.py)、decision_policy、契约测试 |
| 当前工作和仍未确认项 | [latest handoff](handoff/latest.md) | 当前提交的真实命令/结果；每轮重要任务更新，不照抄历史通过数 |
| 主模型与密钥配置 | [README 模型接入](../README.md#主模型接入与一次保存)、项目规范 §7.3 | [user_llm_config.py](../src/web/user_llm_config.py)、[验收 CLI](../scripts/run_agent_acceptance.py)；网页与 CLI 配置分开 |
| 本地科研资产 | [资产登记](../data/REGISTRY.md) | 实际资产部署和校验；不能因源码存在就认定权重/索引可用 |
| 数据权限、TTL 与清理 | [数据留存策略](security_data_retention_policy.md) | 私有权限、会话、retention 实现和回归测试 |
| Temporal docking | [canary runbook](runbooks/temporal_docking_canary.md) | task_runtime、部署资产、真实验收门禁 |
| OpenSandbox docking | [OpenSandbox 部署指南](../deployment/opensandbox/README.md) | sandbox_broker、SandboxDockingRunner、部署验证和 soak；仅受控 docking 边界 |

维护者是本次改动相应模块的贡献者/审查者，不虚构固定个人负责人。
长期指导页标注核对日期；具体 baseline SHA、环境、命令和结果放当次交接。
发现源码与文档不一致时先核查并更新，不能按旧计划还原已删除架构。

## 领域文档

- 靶点库：[本地靶点数据库](target_search_local_db.md)。
- ADMET：[ADMET-AI 1.4.0](admet_ai_140.md)。
- 活性资产：[模型注册表](activity_model_registry.md)、[家族 bundle](activity_family_bundles.md)。
- 活性推理/API：[家族推理](activity_family_inference.md)、[家族 API](activity_family_api.md)。
- 活性数据/训练：[数据准备](activity_data_preparation.md)、[家族数据](activity_family_data.md)、[prepared training](activity_prepared_training.md)。
- 对接依赖：[docking tools](../deployment/docking_tools.md)。

## 历史与当前状态

- [旧问题清单](issues_and_improvement_plan.md) 是历史线索，不是当前待办。
- [2026-10-06 runtime integrity 报告](handoff/agent-runtime-integrity-final-report.md) 是该分支/环境的交付记录，不代表本轮重测或生产部署。
- handoff/latest.md 的历史条目保留在同一文件中；其中旧授权只属于当时任务。
- 实现、离线验收、真实验收、合并、部署分别记录；不以文件修改时间或一次 test pass 推断上线状态。
- `.agent/skills/` 是被忽略的本机开发辅助资料；仓库的运行时能力以 `src/agent/capabilities/` 为准。

## 更新与验证

修改入口、工具 schema、依赖、测试、配置或部署边界时，在同一任务中同步对应指导页。
纯文档更新至少检查：本地链接存在、命令选项与脚本一致、源码路径存在、
`git diff --check`、仅文档改动、无秘密；历史归档应核对原文完整保留。
不得为了文档核对而隐式启动模型、读取用户 Key 或运行生产清理/部署命令。
