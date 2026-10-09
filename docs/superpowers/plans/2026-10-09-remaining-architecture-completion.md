# 完成 MedChat 剩余架构项实施计划

> 执行要求：按 TDD 实施；每一步先写可失败测试，再做最小生产修改。当前分支为 `codex/complete-remaining-architecture`，保留未跟踪的 `data/molecular_faiss_index.index.manifest.json`，禁止使用 `git add -A`、回退或覆盖它。

## 0. 基线与边界确认

- [x] 读取 `AGENTS.md`、`docs/PROJECT_STANDARDS.md`、`docs/handoff/latest.md`、相关 Agent/Web/Task Runtime 模块。
- [x] 确认分支不是 `main`，基于 `origin/main` 建立本任务分支。
- [x] 确认工作树只有用户已有的 manifest 未跟踪文件。
- [x] 记录基线测试：Web decision runtime、相关路由、Agent 契约、compileall。

## 1. 服务端等待续接（P0）

### 1.1 RED：恢复行为测试

- 在 `tests/agent/test_web_decision_runtime_lifecycle.py` 增加测试：
  - 第一个 socket 获得 `waiting_for_input` 后断开；
  - 同一 session 建立第二个 socket，使用原 trace/continuation resume；
  - 继续请求后只产生一条最终结果，且 trace 不变；
  - 新 session、错误 continuation、过期 continuation 均返回 `continuation_unavailable`；
  - 已执行 turn 断线不被自动重放。
- 运行新增测试，确认在当前实现上因 socket-local waiting 而失败。

### 1.2 生产实现

- 在 `src/web/decision_runtime.py` 提取有限的 Web 请求快照构造和安全校验函数。
- 在 waiting 结果写入后保存 `web_request` 与 TTL；不保存 secrets、不改 continuation CAS 内容。
- 在 `resume` 分支，当内存 `sender.waiting` 为空时，从 `SQLiteAgentStateStore.get_run` 恢复 `_Waiting`。
- 仅 `a1_closed` 允许完整恢复；`semantic_v1` 缺少安全 intent journal 时返回 fail-closed 错误。
- 保持原有 `_validate_resume`、CAS、owner 和一次性消费逻辑。

### 1.3 GREEN/回归

- 运行新增测试和 `tests/agent/test_web_decision_runtime*.py`。
- 检查既有 socket-local、shutdown、nonce、fingerprint、取消测试仍通过；必要时只更新与新契约冲突的断言。

## 2. 运行快照与事件只读 API（P1）

### 2.1 RED

- 新增 `tests/agent/test_agent_run_recovery_routes.py`：
  - 同 session GET run/events 返回脱敏数据；
  - 不同 session 返回不可区分的 404；
  - 接口不改变 run 状态；
  - metadata、query、事件内容不包含 API key、token、绝对 secrets。

### 2.2 实现

- 新增窄依赖的 `src/web/routes/agent_run_routes.py`，只依赖 state store 和 session scope。
- 在 `src/web/app.py` 注册路由。
- 在 store/route 边界复用已有 redaction 与事件读取，不让 API 暴露任意 checkpoint payload。

### 2.3 验证

- 运行路由聚焦测试、Agent persistence 测试和 FastAPI compile/import 检查。

## 3. 验收入口收敛（P1）

- 在现有 `scripts/run_agent_acceptance.py` 对报告增加 runtime recovery contract 结果，不能引入另一套 Agent 执行框架。
- 当前 acceptance runner 已保留 `contract`/`real`/`replay` 分界；本轮不新增平行执行体系。
- 运行中恢复由 `tests/agent/test_web_decision_runtime_recovery.py` 和现有 lifecycle 契约覆盖；
  docking 的 `assess_seed_stability` 已接入 repeated real report；缺失真实 seed/pose/artifact/manifest
  时标为 `partial`，不生成伪数据。
- 补充测试覆盖报告字段、依赖缺失和 redaction。

## 4. 交接与部署边界（P1）

- 更新 `docs/handoff/latest.md`：使用实际 `git rev-parse origin/main`，标出本分支已完成项。
- 新增/更新 deployment acceptance 文档，明确 HTTPS/WSS、真实模型客户端、资源清理必须在目标部署环境运行；本机不能替代。
- 不把真实 API key、模型权重、RAG/FAISS/SQLite 运行文件或 docking 输出写入 Git。

## 5. 完整验证与交付

- [x] Web decision runtime、生命周期、恢复和普通 Web 生命周期联合回归（241 passed）与前端决策测试（39 passed）。
- [x] `python -m pytest tests/agent -q -p no:cacheprovider`（12926 passed, 3 skipped, 7 warnings）。
- [x] 反幻觉/平台健康/真实验收聚焦集（54 passed）。
- [x] `python -m compileall -q src scripts`、`node --check`。
- [x] `python scripts/run_agent_acceptance.py --mode contract`（passed）。
- [x] `python scripts/run_agent_acceptance.py --mode replay` 使用真实报告离线重放（源报告失败项如实保留）。
- [x] `python scripts/health_check.py --strict`（23/23）。
- [x] `python scripts/run_agent_acceptance.py --mode real --case-set all-real --repeat 3`（partial，原因写入报告）。
- [x] 对接/部署脚本按环境可用性运行（OpenSandbox skipped；Temporal Windows 报告写入失败）。
- [x] `git diff --check`、精确检查 `git status --short`，确认未触碰 manifest。
- [x] 只暂存本任务文件，提交一个 Conventional Commit；不直接改 main。

## 6. 交付报告

最终报告必须包含：完成项、未完成的外部环境项、修改文件、测试命令和结果、分支/commit/PR 状态、未提交用户文件保持不变的证据。若真实依赖缺失，明确列出 skipped/partial 原因。
