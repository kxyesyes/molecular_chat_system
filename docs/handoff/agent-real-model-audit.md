# Agent 真实模型验收与有界修复交接

日期：2026-09-08。

- 分支：`codex/agent-real-model-audit`，起点 `af55114`。
- 实施目录：独立 `scientific-assets` worktree，不是原项目运行目录。
- 完成目标：隔离真实权重 API/Agent/WebSocket/DOM 验收；六类复现 Bug 修复；确认重复逻辑的有界清理。
- [完整审查、命令及边界](../agent_real_model_audit_20260908.md)。
- [实施计划](../superpowers/plans/2026-09-08-agent-real-model-audit.md)。

## 文件范围

生产代码：

- `src/agent/contracts/domain.py`
- `src/agent/orchestrators/base.py`
- `src/agent/orchestrators/workflow.py`
- `src/agent/runtime/run_session.py`
- `src/agent/supervisor.py`
- `src/agent/tools/activity_input.py`
- `src/web/chat_handler.py`

测试：

- `tests/agent/test_family_activity_tool.py`
- `tests/agent/test_supervisor_delegation.py`
- `tests/agent/test_workflow_resume.py`
- `tests/agent/test_agent_audit_regressions.py`（新增）
- `tests/test_activity_family_real_acceptance.py`（新增，显式资产 opt-in）

文档：本交接、完整审查报告、实施计划，以及 `activity-data-readiness.md` 阶段指引。

## 验证状态

最终全量 7074 passed / 240 skipped / 9 warnings / 171 subtests passed。
真实权重两项单独启用验收通过；contract 34 项通过；四个 Node 脚本、compileall、diff 检查通过。独立复审无剩余阻断。

原始基线模型注册表保持未启用；未重启服务、未调用外部主模型、未写入密钥、未改原项目混杂工作树。权重、运行报告与私有数据不提交。

## 待决事项

模型上线仍须单独授权与质量判断；Linux 集成跳过项仍须对应环境验证。分支继承较长历史，未 push、未创建大型 PR、未合并 main。不能把当前 worktree 已修复表述为原运行实例已经更新。
