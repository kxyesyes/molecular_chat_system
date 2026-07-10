# 最新任务交接

## 2026-07-10 工作树整理与 Agent 升级交接

- 当前分支：`codex/agent-runtime-real-validation`，未直接修改或合并 `main`。
- 原有混杂改动已按领域整理为五个可独立回滚的提交：
  - `bc08d34 feat(science): harden molecular prediction and docking services`
  - `46afb1c feat(agent): add persistent multi-agent scientific runtime`
  - `ba1cdfa fix(web): secure agent operations and frontend rendering`
  - `54fc3b7 chore: ignore local sqlite3 runtime files`
  - `docs: record agent platform standards and rollout plans`（当前文档提交）
- `data/agent_state.sqlite3` 保留在本地但由 `.gitignore` 排除，未提交运行数据库。
- 整理前扫描 155 个候选文件：真实运行时密钥命中 0，通用长密钥模式命中 0，超过 1 MB 的候选文件 0。
- 最近验证：Agent pytest 178 passed；聚焦安全/路由测试 19 passed；健康检查 22/22；Node 静态安全测试通过；contract、real repeat=3 与 replay 验收均通过。
- 真实报告位于本地 `outputs/agent_evaluation/`，该目录受忽略规则保护，不提交运行产物。
- 整理完成后 `git status --short` 为空，工作区和暂存区均无待处理 diff。

## 2026-06-21 Agent 唯一入口与真实验收

### 本轮目标

- 消除 WebSocket 聊天链路仍由 `ReActMolecularAgent` 接管的双主入口。
- 确保所有原子和复合技能统一经过 WorkflowExecutor、Orchestrator、ToolResult 适配和领域 Validator。
- 使用外部 `gemini-3.1-pro` 主模型与本地 Ollama `gmm-llama:latest` 完成真实案例验收。

### 本轮修改

- `src/web/app.py`
- `src/agent/supervisor.py`
- `src/agent/planning/task_planner.py`
- `tests/agent/test_app_supervisor_entrypoint.py`
- `tests/agent/test_supervisor_agent.py`
- `tests/agent/test_task_planner.py`
- `outputs/agent_evaluation/agent_contract_cases_20260621.json`
- `outputs/agent_evaluation/agent_real_cases_20260621.json`
- `outputs/agent_evaluation/agent_validation_summary_20260621.md`

### 最终状态

- ChatHandler 的 `agent_system` 现在是 `SupervisorAgent`。
- 原子技能使用声明式单步计划，不再绕过统一执行与 Validator。
- Chat Supervisor、Orchestrator 与 EventBus 复用 `data/agent_state.sqlite3` 状态存储。
- 外部主模型与本地分子生成模型保持隔离。
- pytest 221/221、Agent 128/128、健康检查 22/22、JS 45/45、Node 3/3 均通过。
- Contract 34/34 通过。
- REAL-001 至 REAL-016：14 Passed、2 预期 Partial、0 Failed、0 Skipped。
- 外部模型、Ollama、RG-MPNN、靶点检索和 Vina 均完成真实执行。
- 凭据未写入仓库或报告，验收结束后环境变量已清除。

### 后续建议

- 动态 LLM Planner 只作为受约束的候选计划生成器引入；必须保留工具白名单、schema 校验、权限预检和可回放测试。
- FastAPI `on_event` 和 PyTorch/PyG 弃用警告可在独立维护任务中处理，不应混入本次架构修复。

## 2026-06-19 Agent 架构真实测试与修复

当前分支：`codex/agent-runtime-real-validation`

本轮依据以下用户文档完成架构修复与真实测试：

- `molecular_chat_system_agent_issues_solutions_detailed.docx`
- `molecular_chat_system_agent_real_test_plan.docx`

### 完成内容

- 新增统一 `WorkflowExecutor`，执行前强制计划与工具权限预检。
- `TaskPlanner` 成为 workflow 步骤唯一来源，workflow skill 仅声明意图、权限和 `is_workflow`。
- 新增 `WorkflowState`，落实 output binding、required/optional、timeout、错误与附件汇总。
- 完整透传 `ToolResult` 的 warnings/evidence/artifacts/quality/error。
- 新增 docking/activity/ADMET/target evidence 领域 Validator。
- ChatHandler 支持执行中实时事件，并默认直出 workflow 权威结果。
- 修复 ADMET 与 hit-to-lead、ADMET 与 comprehensive 的真实中文路由冲突。
- Vina 结果增加 pose 文件路径，验收要求真实 pose 文件存在。
- 新增 REAL-001 至 REAL-016 版本化数据集与合同/真实案例执行器。

### 验证结果

- 合同验收：34/34。
- REAL-001 至 REAL-016：16/16 通过或符合预期 partial。
- Agent 测试：122 passed。
- 全量测试：217 passed。
- 健康检查：22/22。
- Python compileall：通过。
- JavaScript：47/47 语法通过。
- Node 静态测试：3/3。
- 密钥扫描：352 个文本文件，0 命中。

真实依赖：

- Ollama `gmm-llama:latest`：通过，生成结果有效且唯一。
- PDE5A 靶点检索：通过。
- RG-MPNN：通过，使用真实权重。
- AutoDock Vina：通过，3 个 pose，最佳能量 -3.5 kcal/mol，pose 文件存在。
- 外部 `gemini-3.1-pro`：补测通过，精确探针匹配，延迟 2393 ms。密钥只在单次子进程中临时使用，未持久化。

补测外部模型时重新执行了全链验收。外部模型、本地 Ollama、PDE5A 检索、RG-MPNN 和 Vina 均通过；REAL-005 本轮因生成模型随机波动仅得到 9/10 个有效唯一分子，被验收器正确判为失败，总状态为 partial。此前完整真实矩阵曾达到 16/16；当前 JSON 保留最新一次诚实结果，不将数量不足伪装为成功。

### 证据文件

- `outputs/agent_evaluation/agent_contract_cases_20260619.json`
- `outputs/agent_evaluation/agent_real_cases_20260619.json`
- `outputs/agent_evaluation/agent_validation_summary_20260619.md`
- `docs/superpowers/plans/2026-06-19-agent-runtime-real-validation.md`

### 继续工作规则

- 保留当前所有未提交改动，不 reset、clean、checkout 或回退。
- 不将用户 API Key 写入命令、文件、日志、测试或 Git。
- 外部模型补测已完成；后续运行仍应通过临时环境变量注入密钥。
- 当前任务尚未提交、推送或合并；禁止直接合并 `main`。

---

## 上一轮：项目规范文档

日期：2026-06-19
分支：`docs/project-standards`
目标分支：`main`
状态：文档已完成校验并发布到 draft PR #1，尚未合并。

## 1. 本轮任务目标

基于当前仓库真实结构，建立完整、实用、可长期维护的项目规范，且不修改业务代码：

- 新增仓库级 Agent/Codex 工作说明；
- 建立详细工程规范；
- 记录本轮事实来源、现状、待确认问题和后续建议；
- 使用独立分支、独立 commit 和 PR，不直接修改或合并 `main`。

## 2. 本轮新增或修改的文件

本轮只应包含以下三个文档文件：

- `AGENTS.md`
- `docs/PROJECT_STANDARDS.md`
- `docs/handoff/latest.md`

没有修改业务代码、测试代码、配置、数据库或数据资产。

工作区在本轮开始前已存在大量其他未提交改动。它们属于既有工作，不属于本轮文档 commit，禁止使用 `git add -A`。

## 3. Codex 读取的关键文件

### 项目与依赖

- `README.md`
- `main.py`
- `requirements.txt`
- `.gitignore`
- `.gitattributes`
- `.env.example`
- `start_ollama.ps1`

### 配置与部署

- `config/ollama_config.yaml`
- `config/modelscope_config.yaml`
- `config/deployment.yaml`
- `deployment/README.md`
- `deployment/requirements.txt`
- `deployment/medchat.service`
- `deployment/nginx-medchat.conf`
- `deployment/docking_tools.md`

### 后端与 API

- `src/web/app.py`
- `src/web/api_response.py`
- `src/web/chat_handler.py`
- `src/web/routes/main_routes.py`
- `src/web/routes/page_routes.py`
- `src/web/routes/api_routes.py`
- `src/web/routes/agent_workflow_routes.py`
- `src/web/routes/system_routes.py`
- `src/web/routes/websocket_routes.py`
- `src/target_search/routes.py`
- `src/target_search/service.py`
- `src/target_search/database.py`
- `src/task_runtime/database.py`
- `src/task_runtime/manager.py`
- `src/agent/persistence/sqlite_store.py`

### 前端

- `src/web/templates/*.html`
- `src/web/static/js/home/`
- `src/web/static/js/design/`
- `src/web/static/js/docking/`
- `src/web/static/js/reverse_target/`
- `src/web/static/js/target_search/`
- `src/web/static/js/activity_prediction/`
- `src/web/static/css/`

### 数据、脚本与测试

- `data/REGISTRY.md`
- `docs/target_search_local_db.md`
- `scripts/health_check.py`
- `scripts/run_agent_acceptance.py`
- `scripts/target_db/validate_target_db.py`
- `tests/test_deployment_assets.py`
- `tests/test_phase2_phase3_routes.py`
- `tests/agent/`
- `tests/home_agent_task_panel_test.js`
- `tests/reverse_target_broad_recall_test.js`
- `tests/reverse_target_pagination_test.js`

## 4. 发现的项目现状

### 架构

- 后端采用 FastAPI/Uvicorn，`main.py` 是推荐入口，`src/web/app.py` 导出 ASGI `app`。
- 前端采用 Jinja2 + 原生 JavaScript/CSS，没有 npm 构建链。
- 页面与前端资源已按功能拆分，但部分核心文件仍较大。
- API 路由同时存在集中式 `api_routes.py` 和按领域拆分的 `setup_*_routes()`。
- WebSocket `/ws` 承担聊天与 Agent 事件流。

### Agent

- 已存在混合路由、任务规划、Supervisor、专家代理、工具适配、SQLite 状态、长期记忆和评测。
- Agent 运行状态默认写入 `data/agent_state.sqlite3`。
- 后台任务默认写入 `scratch/tasks.sqlite`。
- 分子生成模型与主聊天模型有独立配置边界。

### 数据

- 靶点搜索使用 SQLite 元数据、CSV 种子和本地结构缓存。
- 反向寻靶依赖 ChEMBL 表和 NumPy 指纹文件。
- 活性预测依赖本地 RG-MPNN 权重。
- RAG 使用本地 CSV 和 FAISS。
- 大型运行资产已在 `.gitignore` 和 `data/REGISTRY.md` 中规定不提交。

### 运行与部署

- 开发依赖来自 `requirements.txt`。
- CUDA/Linux 部署依赖来自 `deployment/requirements.txt`。
- Linux 部署使用 systemd + nginx，应用默认端口为 `6001`。
- 本地 Ollama 默认地址为 `127.0.0.1:11434`。
- 对接需要 AutoDock Vina、ADFRsuite 和 Meeko。

### 测试

- 当前有 49 个 Python 测试文件和 3 个直接运行的 Node.js 测试脚本。
- 没有 `pytest.ini`、`pyproject.toml`、`package.json` 或正式 lint 配置。
- 项目提供 `compileall`、健康检查和 Agent 验收作为 pytest 之外的质量门。

### Git 状态

- 本轮开始时处于 `main`，工作区已有大量未提交业务改动和新增测试/Agent 文件。
- 已创建独立分支 `docs/project-standards`。
- 本轮提交必须只暂存三份规范文档。

## 5. 不确定或需要用户确认的问题

1. Python 3.11 是否正式支持；当前文档和部署更偏向 Python 3.10。
2. 通用依赖与部署依赖版本差异如何治理。
3. 是否引入 Ruff/Black、ESLint/Prettier。
4. 是否建立 GitHub Actions、覆盖率阈值和分支保护。
5. API 是否逐步统一为 `api_success/api_error`。
6. FastAPI `@app.on_event("startup")` 迁移 lifespan 的时间点。
7. README 声明 MIT，但未看到根目录 LICENSE，许可证状态需确认。
8. 用户上传文件、聊天、任务和 Agent 状态的保留期限。
9. 生产环境长期使用 SQLite，还是迁移 PostgreSQL/对象存储/Redis。
10. Ketcher、3Dmol.js、ECharts 和 CDN 依赖的升级与离线策略。

## 6. 后续建议

优先级建议：

1. 审查并确认本轮三份规范文档。
2. 单独 PR 建立 CI：pytest、compileall、Node 静态测试、密钥扫描。
3. 单独 PR 确定 Ruff/Black 和 JavaScript formatter 策略。
4. 明确 Python/依赖支持矩阵，减少两套 requirements 漂移。
5. 为 GitHub 设置 `main` 分支保护和至少一名人工审查。
6. 补充根目录 LICENSE 或修正 README 许可证声明。
7. 制定数据保留、备份、恢复和隐私策略。
8. 逐步拆分大型 API/前端文件，但不要与功能 PR 混合。

## 发布注意

- PR 应创建为 draft，base 为 `main`。
- PR 描述应强调：只修改文档、未包含既有业务改动。
- 审查重点：命令是否真实、边界是否清晰、待确认项是否准确。
- 不要由 Codex 直接合并。

发布记录：

- 远端分支：`docs/project-standards`
- Draft PR：`https://github.com/kxyesyes/molecular_chat_system/pull/1`
- PR 状态：Open、Draft、未合并
