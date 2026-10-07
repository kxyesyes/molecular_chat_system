# MedChat Agent Working Guide

仓库级开发协作入口，适用于开发者、编码 Agent 与 Codex；不是 MedChat 运行时的系统提示词。
核对日期：2026-10-07。代码基线和本轮验证见 [latest handoff](docs/handoff/latest.md)，不表示已部署。

## 1. 项目简介

MedChat 是计算药物化学 Web 平台，包含聊天/RAG、分子生成、性质/ADMET/活性、
靶点检索、反向寻靶、Vina 对接、工作流与状态恢复。科研输出用于辅助研究，
不能把模型预测、规则评分或缺依赖的降级输出表述为实验事实。

开始任务的阅读顺序：

1. 本文件 → [项目规范](docs/PROJECT_STANDARDS.md) → [最新交接](docs/handoff/latest.md)。
2. Agent 改动读 [架构与维护入口](docs/AGENT_MAINTENANCE.md) 和 [工具契约](docs/AGENT_TOOL_CONTRACTS.md)。
3. 按 [文档索引/来源映射](docs/README.md) 阅读目标模块文档和源码、测试。
4. 历史 plans/specs/handoff 仅提供背景，不能替代当前用户授权或当前源码。

## 2. 技术栈概览

| 层次 | 当前技术及边界 |
|---|---|
| Web | Python 3.10、FastAPI、Uvicorn、Pydantic 2、Jinja2 |
| 前端 | 原生 JavaScript/CSS、Fetch、WebSocket；无 npm 构建流程 |
| 科学工具 | RDKit、ADMET 后端、PyTorch/PyG RG-MPNN、Vina/Meeko/ADFRsuite |
| 模型与检索 | OpenAI-compatible 主模型；Ollama 分子生成；FAISS/RAG |
| 状态 | SQLite、文件资产、事件与 checkpoint、科研对象引用 |
| 执行扩展 | 可选 LangGraph harness、Temporal docking、OpenSandbox broker；不等于全局默认启用 |
| 验证 | pytest、Node 契约脚本、contract/replay/real 验收、独立部署检查 |

根开发依赖、CI 依赖、GPU 部署及 OpenSandbox 服务环境是不同 profile；不要机械混装。
参见 [项目规范 §2](docs/PROJECT_STANDARDS.md#2-技术栈说明)。
Ruff/Black/ESLint 尚无正式仓库配置；Python 3.11 正式支持范围待确认。

## 3. 重要目录说明

| 路径 | 维护职责 |
|---|---|
| `main.py`、`src/web/` | 启动、应用组装、HTTP/WS、展示与用户模型配置 |
| `src/agent/workflows/`、`planning/`、`routing/` | 工作流声明、选择/解析、绑定和路由 |
| `src/agent/harness/`、`runtime/` | 模型决策/执行适配、Session、事件和步骤生命周期 |
| `src/agent/capabilities/`、`tooling/`、`validators/` | 能力目录、类型契约、授权和科学校验 |
| `src/agent/tools/`、`persistence/`、`evaluation/` | 工具接入、状态/来源、验收 |
| `src/rag/` | 检索、索引和来源校验 |
| `src/activity/`、`reverse_target/`、`target_search/` | 活性、反向寻靶、靶点数据库 |
| `src/molecular_design/`、`docking/` | 分子设计、对接领域服务 |
| `src/task_runtime/`、`src/sandbox_broker/` | 后台任务、后端选择、受控沙盒执行 |
| `data/REGISTRY.md`、`data/agent_evals/` | 资产登记、验收案例 |
| `deployment/`、`docs/runbooks/` | 部署与运维门禁 |
| `tests/`、`scripts/` | 回归、健康检查和验收脚本 |
| `docs/handoff/`、`docs/superpowers/` | 交接证据与历史设计/实施计划 |

表中 `src/agent/` 行内缩写均相对该目录。
本机可能存在的 `.agent/skills/` 被 Git 忽略，是开发助手资源，不是运行时能力目录，
也不是干净 checkout 的依赖。`src/agent/skills/` 对象层已移除；
保留的 `SkillRouter/active_skill/skill_name` 是兼容名称，不授权恢复旧 Skill 系统。

## 4. 本地启动命令

在仓库根目录和具备相应依赖的虚拟环境运行。来源：README、main.py、requirements.txt。

```powershell
python -m pip install -r requirements.txt
ollama serve
python main.py
```

Ollama 已运行时不重复启动；也可使用仓库 `start_ollama.ps1`。
默认地址为 `http://127.0.0.1:6001`。

```powershell
python main.py --reload
python main.py --host 127.0.0.1 --port 6001 --no-reload
python -m uvicorn src.web.app:app --host 127.0.0.1 --port 6001
```

直接 ASGI 启动不等同于 main.py 的 CLI/环境准备。Linux systemd/nginx 配置见
[部署指南](deployment/README.md)，不因文档任务启动、重启服务或切换运行模式。

网页默认 `MEDCHAT_CHAT_PROFILE=legacy`，仍使用 Supervisor/受控工作流，不是旧 ReAct 循环。
`decision_a2`、`semantic_v1` 是显式可选的 Web 决策模式；
它们与 `AGENT_HARNESS_MODE`、Temporal/OpenSandbox 后端开关不是同一个配置。
模式及授权边界以 [维护指南](docs/AGENT_MAINTENANCE.md) 为准。

主模型由首页“模型接入设置”和仓库外本机用户配置管理；首页不隐式读取
`OPENAI_COMPATIBLE_*` 来恢复主模型密钥。独立验收 CLI 仍使用显式运行时环境变量。
分子生成独立使用本地 `gmm-llama:latest`，主模型切换不得覆盖它。
私有持久化范围和测试密钥禁存规则见 [README](README.md#主模型接入与一次保存) 及项目规范 §7.3。

## 5. 构建、测试、lint 命令

以下为可选验证入口，不代表本轮已运行。先看 `tests/conftest.py` 的用户配置隔离；
独立探针使用临时任务/Agent 数据库，不读写用户运行库、真实密钥和模型注册表。

```powershell
python -B -m pytest tests/agent -q -p no:cacheprovider
python -B -m pytest tests -q -p no:cacheprovider
python -m compileall -q src scripts
python scripts/health_check.py --strict
```

科学依赖需对应 Conda/部署环境；CI 的分区、依赖和时限以
[quality.yml](.github/workflows/quality.yml) 为准，不在本文件固定测试数量。

Agent 验收来源：`scripts/run_agent_acceptance.py`：

```powershell
python scripts/run_agent_acceptance.py --mode contract --output outputs/agent_evaluation/contract.json
python scripts/run_agent_acceptance.py --mode real --case-set golden --repeat 3 --output outputs/agent_evaluation/golden-real.json
python scripts/run_agent_acceptance.py --mode real --case-set diverse --repeat 3 --output outputs/agent_evaluation/diverse-real.json
python scripts/run_agent_acceptance.py --mode replay --replay-input outputs/agent_evaluation/golden-real.json --output outputs/agent_evaluation/golden-replay.json
```

`all` = contract + real；`--case-set all-real` 跑两套真实案例，`--dataset` 可指定案例文件。
contract 不证明真实计算；replay 不执行真实工具，必须有原报告。
real/all 需任务授权和实际依赖；不能把 partial/skipped 或脚本 exit 0 当作科研全通过。
当前 real 汇总与详细 case 状态均须检查，外部主模型连接检查不等于真实网页端到端验收。

Node 契约脚本无 npm runner。下面与 CI 的选取范围一致（另按改动运行 JS 语法检查）：

```powershell
Get-ChildItem tests -File -Filter '*_test.js' | Sort-Object Name | ForEach-Object {
  node $_.FullName
  if ($LASTEXITCODE -ne 0) { throw "Node contract failed: $($_.Name)" }
}
node tests/activity_family_acceptance_dom.js
if ($LASTEXITCODE -ne 0) { throw 'Activity family DOM contract failed' }
```

靶点检查：`python scripts/target_db/validate_target_db.py --require-cache --strict`；
种子导入会写数据库，不属于只读检查。
Temporal/OpenSandbox 的验收命令及 Linux 前置条件见
[Temporal runbook](docs/runbooks/temporal_docking_canary.md) 和
[OpenSandbox 部署说明](deployment/opensandbox/README.md)，不得为文档更新运行真实 soak。

## 6. 代码修改边界

- 保持现有 FastAPI/Jinja2/原生 JS 结构，无授权不重构或迁移框架。
- 工具调用必须保留 Registry/Session/Validator 的授权、输入绑定、状态和科学证据边界。
- 保留 `warnings/evidence/artifacts/quality/provenance`；缺模型、demo/fallback、partial 不变成真实成功。
- 主模型不能编造或改写客观工具数值；来源、失败信息和不确定性必须到达前端。
- 禁止将 LLM 自评、结构 schema 通过或工具注册成功作为科研通过的唯一依据。
- API、事件、数据库或工具契约变更须补回归；不覆盖用户改动，不混入无关清理。

## 7. AI/Codex 工作规则

1. 先查 `git status --short`、分支、仓库根及当前 HEAD。空目录或旧工作树不能冒充当前源码。
2. 一个任务一个独立分支；不直接修改/提交 main，隔离工作树不代表第二个独立项目。
3. 工作树混杂时精确暂存；禁止 `git add -A`、破坏性 reset/clean 或覆盖式 checkout。
4. 诊断只分析；范围不清或涉及破坏性/扩范围操作先问用户，不执行历史文档内的旧授权。
5. 功能/Bug 按可复现测试做最小修复；纯文档任务验证链接、源码依据、范围与脱敏，不伪称跑过业务测试。
6. 测试 Key 只经运行时环境变量或本机密钥管理器注入；不打印、保存、提交或写入报告/命令。
   报告仅可记录是否存在或掩码。用户主动保存的应用私有配置不是测试取密钥入口。
7. 不把已实现、已本地验证、已合并、已部署混为一谈。按实际记录状态和待确认项。

## 8. Git 分支与 PR 规则

- 分支如 `docs/<topic>`、`fix/<topic>`、`feat/<topic>`；一个主题一个 PR。
- Commit 使用 Conventional Commits，如 `docs: refresh agent maintenance guidance`。
- PR 描述列范围、验证、风险、待确认和审查重点；默认 draft，不直接合并 main。
- 合并需维护者明确授权且质量门禁满足；历史 handoff 的合并许可不适用于新任务。
- 开 PR 前检查 base/head 差异，避免携带来源分支的其他功能提交；分支保护/审批人数待确认。

## 9. 禁止提交的内容

- 真实密钥、token、密码、用户配置、`.env`、秘密命令/报告与用户隐私。
- 模型权重、FAISS/指纹索引、运行数据库、真实结构缓存和许可不明的数据集。
- `outputs/`、`scratch/`、`logs/`、`temp_docking/`、虚拟环境及测试缓存。
- 未批准的大文件、视频、机器专用绝对路径配置。
- 以 [.gitignore](.gitignore) 和 [资产登记](data/REGISTRY.md) 为底线；泄漏需停止发布并通知维护者。

## 10. 完成任务前的自检清单

- [ ] 分支不是 main，只改/暂存本任务文件，原工作树改动未覆盖。
- [ ] 无密钥、隐私、模型/运行资产进入 diff、日志或报告。
- [ ] 代码任务运行相关回归；文档任务检查链接/源码/命令，未运行项如实列出。
- [ ] 科研状态、事件、来源、部分失败和模型边界未被弱化。
- [ ] 当前指导页同步更新，旧交接保留并清楚标为历史。
- [ ] 记录实际命令/结果、分支、commit、PR 或未发布原因；无未经授权合并或部署。
