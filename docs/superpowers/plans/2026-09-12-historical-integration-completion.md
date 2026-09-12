# 历史代码集成与服务器准备 Implementation Plan

> **For agentic workers:** Use subagent-driven-development and test-driven-development. 每批先规格审查再质量审查。不得用局部测试通过宣布总体完成。

**Goal:** 完成剩余历史功能集成、处理直接相关潜在问题，并为服务器部署提供可复核的代码与验收证据。

**Architecture:** 从最新 main 做选择性移植，不整体合并滞后的历史分支；复用现有 FastAPI、
模型注册表、预测器、Agent harness、状态与工具契约。原始混杂工作树和运行资产保持不变。

**Tech Stack:** Python/FastAPI/Pydantic、PyTorch/PyG/RDKit、SQLite、原生 JS、pytest/Node、现有 CI。

## 当前基线与总体完成条件

- 已合并 PR #13，main `1a1677bb389850b84e81144654d8c18e6520cb4c`。
- 活性候选源：`codex/activity-integration-blockers` / `5432968`。
- Agent 候选源：`codex/agent-integration-blockers` / `9312bf5`；应优先于更旧 `eb93c43`。
- 用户要求先完成代码集成；服务器系统、硬件和部署方式不阻塞代码集成，部署验证暂待确认。
- [ ] 所有候选文件/功能均归类为已集成、待移植、被新实现替代、历史文档或本地资产，不能遗漏。
- [ ] 所有待移植功能在 main 中有实际实现和测试，而非只存在本地分支。
- [ ] 不回退 main 已新增的数据、模型卡、显式 SMILES、特征身份和安全校验。
- [ ] API/前端/Agent 全链路可实际调用已配置服务，缺依赖时明确失败，不伪造科学结果。
- [ ] 完整回归、Linux CI、合并结果及部署风险有证据；未验证的真实模型/宿主依赖单独记录。
- [ ] 不把合成权重测试、contract/replay 或未启用的测试宣称为科研性能/生产部署验收。

## Task 1：共享预测服务与 API

Files: `src/activity/prediction_service.py`、`src/web/routes/api_routes.py` 两个预测端点、
`tests/test_activity_family_api.py`、`tests/test_activity_family_inference_integration.py`。

- [ ] 先移植 API 测试，观察显式 target 请求和汇总契约失败。
- [x] 加入有界目录缓存、并发冷启动保护；整个获取/校验/推理在已有线程池中。
- [x] 显式 target 不走全局模型；无 target 保留 legacy；空/全失败批次不能 success。
- [x] partial 保留分类、错误、warnings、provenance、null 回归；HTTPException 保留，内部异常脱敏。
- [x] 实际 FastAPI + 临时合成 RGNN 权重前向；只证明工程链路，不启用正式模型。
- [ ] 聚焦、完整活性、Agent、Node、compileall、独立规格/质量审查及 CI。

## Task 2：Agent 活性接线与输入边界

Files: `src/agent/tools/activity_predictor_tool.py`、`activity_input.py`、
`orchestrators/workflow.py`、`validators/domain_validators.py`、`tests/agent/test_family_activity_tool.py`。

- [ ] 移植前先覆盖自然语言/结构化 PDE/BuChE、未知/冲突靶点、无效显式 SMILES 与无模型失败。
- [ ] 复用 main `tools/molecular_input.py` 的更强边界，不恢复 source 的片段截取逻辑。
- [ ] 生成候选传入实际 SMILES 且保留用户 target，验证下游输入而非仅检查工具名称。
- [ ] 标准 ToolResult 保留 partial、双模型证据和阶段错误；领域校验器拒绝无证据科学数值。
- [ ] 原有性质/类药性/路由/提示词/恢复回归不退化；独立审查后单独 PR。

## Task 3：活性结果前端

Files: `src/web/templates/activity_prediction.html`、
`src/web/static/js/activity_prediction/main.js`、`results_renderer.js`、`tests/activity_family_results_test.js`。

- [ ] 先 Node DOM 测试 passed/partial/failed、概率 0、null 数值、恶意字符串、重复渲染。
- [ ] 表单明确目标，保留 legacy 选项；展示结果自身来源而非使用当前表单值覆盖旧结果。
- [ ] 不以默认零值/旧全局仪表伪装家族结果；沿用安全 DOM 渲染。
- [ ] Node 全套、node --check、浏览器端到端可验证后单独 PR。

## Task 4：家族训练编排

Files: `scripts/train_family_activity_models.py`、`tests/test_family_training_run.py`，以及经证明必要的训练接点。

- [ ] 先审查历史训练入口是否兼容 main 更强 prepared/model-card 校验，测试失败路径再移植。
- [ ] 保持数据 hash/拆分/阈值和模型组绑定，不扫描或消耗用户真实 test/权重作为普通 CI。
- [ ] 显式运行、拒绝缺依赖/残缺产物，禁止 import 时训练/注册/激活生产模型。
- [ ] 文档不能直接沿用历史性能报告作为当前 main 的真实模型证据；单独记录证据缺口。

## Task 5：决策协议、执行、证据和持久化

Files: `src/agent/contracts/decision.py`、`task_requirements.py`、`harness/decision_*.py`、
`decision_transport.py`、`decision_privacy.py`、`evidence/ledger.py`、`persistence/*`、
`runtime/run_session.py`、`supervisor.py` 及对应 `tests/agent/test_decision_*.py`。

- [ ] 使用已修复源 `9312bf5`，先验证敏感澄清不派发/不保存、target 续接不丢失、证据不可后改。
- [ ] 保留 main 修复，逐差异块处理；不会因源代码较新就覆盖 main 的独立增强。
- [ ] 测试结构化模型决策、工具授权、失败恢复、幂等、并发、事件流、模型错误与工具来源。
- [ ] 实验协议版本变化有明确迁移/拒绝旧快照规则，不静默损坏运行数据库。
- [ ] 将基础协议/循环/恢复按依赖拆 PR，全部集成才算此任务完成。

## Task 6：聊天/隔离验收入口与历史残差闭环

Files: `src/web/decision_chat.py`、`decision_lab.py`、`src/web/static/decision_lab/*`、
`scripts/run_decision_*`、相关聊天与外部模型接点及 Python/Node 测试。

- [ ] 隔离入口不绕过科学证据、权限与脱敏；明确是否改变生产入口，禁止隐式切换。
- [ ] WebSocket/最终回答/前端渲染完整，工具完成不等于已经给出回答；失败与中断可收敛。
- [ ] 检查原始未提交改动和其余 worktree，不能遗漏未被任何快照保存的功能；原文件不覆盖。
- [ ] 每项残差给出处置和验证依据，不能简单删除旧分支来假装完成。

## Task 7：集成回归与部署准备审计

- [ ] 在最终合并树执行 `python -m pytest tests -q`、全部 Node、compileall、contract。
- [ ] 对显式请求的真实验收使用受控环境，缺模型/凭据/宿主工具如实报告，不填充结果。
- [ ] 检查 requirements/启动入口/配置/数据目录/健康检查/数据库迁移/反向代理与 WebSocket。
- [ ] 公网前检查管理接口鉴权、HTTPS、上传限制、路径权限、任务配额、日志脱敏、备份恢复。
  当前移除管理员令牌的既有行为不能无声视作公网安全，部署前必须明确防护与验证。
- [ ] 服务器参数由用户后续提供；没有目标宿主证据不能宣称已经部署或可直接公网开放。
- [ ] 按 AGENTS 的独立分支、精确暂存、CI 与具体 PR 授权规则合并，禁止直接提交 main。

任务可跨多轮持续；任何小批次完成不代表此完整目标完成。进度和残差维护在
`docs/handoff/historical-integration-status.md`。
