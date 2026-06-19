# MedChat Agent Working Guide

本文档是仓库级协作入口，适用于开发者、自动化 Agent 与 Codex。更完整的工程规范见
[`docs/PROJECT_STANDARDS.md`](docs/PROJECT_STANDARDS.md)。

## 1. 项目简介

MedChat（Molecular Chat System）是面向药物设计与计算药物化学场景的 Web 平台。当前仓库整合了：

- 自然语言聊天、RAG 检索与 Agent 工作流；
- 本地 Ollama 分子生成；
- 分子性质、ADMET 与活性预测；
- 靶点搜索、反向寻靶和结构缓存；
- AutoDock Vina 分子对接；
- 分子设计、任务持久化、长期记忆与评测。

项目仍处于持续开发阶段。任何输出均应保留科学计算来源、模型状态和失败信息，禁止用模拟结果冒充真实预测。

## 2. 技术栈概览

| 层次 | 当前实际技术 |
|---|---|
| 后端 | Python 3.10+、FastAPI、Uvicorn、Pydantic 2 |
| 前端 | Jinja2 模板、原生 JavaScript、CSS、Fetch API、WebSocket |
| 科学计算 | RDKit、NumPy、Pandas、SciPy、scikit-learn |
| 图神经网络 | PyTorch、PyTorch Geometric、RG-MPNN |
| 检索 | FAISS、本地 CSV/索引 |
| 数据持久化 | SQLite、CSV、本地文件缓存 |
| 模型接口 | Ollama、ModelScope、OpenAI-compatible API |
| 对接工具 | AutoDock Vina、ADFRsuite、Meeko |
| 测试 | pytest、pytest-asyncio、Node.js 静态测试脚本 |
| 部署 | systemd、nginx、Conda、Linux/Windows 本地开发 |

仓库没有 `package.json`、前端打包流程或已配置的 Ruff/Black/ESLint。不要假设这些工具已经存在。

## 3. 重要目录说明

```text
.
├── main.py                    # 推荐的本地启动入口
├── config/                    # Ollama、ModelScope、部署配置
├── data/                      # 轻量数据、样例及本地运行资产
├── deployment/                # Linux 依赖、systemd、nginx、对接工具文档
├── docs/                      # 项目、部署、设计和交接文档
├── scripts/                   # 健康检查、Agent 验收、数据库校验脚本
├── src/
│   ├── web/                   # FastAPI 应用、路由、Jinja 模板、静态资源
│   ├── agent/                 # 路由、规划、Supervisor、工具、持久化、评测
│   ├── molecular_design/      # 分子设计服务
│   ├── docking/               # 对接服务和外部工具适配
│   ├── reverse_target/        # 反向寻靶
│   ├── activity/              # RG-MPNN 活性预测与训练
│   ├── target_search/         # 靶点 SQLite、结构缓存、下载和检索
│   ├── task_runtime/          # SQLite 后台任务运行时
│   └── system/                # 数据版本信息
└── tests/                     # Python 测试与 3 个 Node.js 静态测试
```

大模型权重、FAISS 索引、SQLite 运行库、结构缓存和计算输出通常是本地资产，不应因“项目能运行”就提交到 Git。

## 4. 本地启动命令

### 4.1 安装通用开发依赖

来源：`README.md`、`requirements.txt`。

```powershell
python -m pip install -r requirements.txt
```

RDKit、FAISS、PyTorch/PyG 在 Windows 或不同 Python 版本下可能需要 Conda。推荐 Python 3.10；Python 3.11 是否作为正式支持版本为“待确认”。

### 4.2 启动本地 Ollama

来源：`start_ollama.ps1`、`config/ollama_config.yaml`。

```powershell
.\start_ollama.ps1
```

或：

```powershell
ollama serve
```

分子生成所需模型为 `gmm-llama:latest`。不要让主聊天模型配置覆盖分子生成工具模型。

### 4.3 推荐启动方式

来源：`README.md`、`main.py`。

```powershell
python main.py
```

默认访问地址：

```text
http://127.0.0.1:6001
```

常用变体：

```powershell
python main.py --reload
python main.py --debug
python main.py --host 127.0.0.1 --port 6001
python main.py --no-reload
python main.py --modelscope
python main.py --config config/modelscope_config.yaml
```

### 4.4 直接 ASGI 启动

来源：`main.py` 中的 Uvicorn 调用、`src/web/app.py` 导出的 `app`。

```powershell
python -m uvicorn src.web.app:app --host 127.0.0.1 --port 6001
```

这是可行的底层启动方式；日常开发优先使用 `python main.py`，因为它负责 `.env`、目录检查和配置选择。

### 4.5 Linux 服务启动

来源：`deployment/medchat.service`、`deployment/README.md`。

```bash
python main.py --no-reload
```

生产部署由 systemd 调用上述命令，并由 nginx 代理 HTTP 与 `/ws` WebSocket。

## 5. 构建、测试、lint 命令

### Python 测试

来源：`tests/`、`requirements.txt`。

```powershell
python -m pytest tests -q
python -m pytest tests/agent -q
python -m pytest tests/test_target_search.py -q
```

涉及 RDKit/PyTorch 的测试必须在具备对应依赖的 Conda 环境运行。

### 源码编译检查

```powershell
python -m compileall -q src scripts
```

### 健康检查

来源：`scripts/health_check.py`、`deployment/README.md`。

```powershell
python scripts/health_check.py
python scripts/health_check.py --strict
```

### Agent 验收

来源：`scripts/run_agent_acceptance.py`。

```powershell
python scripts/run_agent_acceptance.py --mode contract
python scripts/run_agent_acceptance.py --mode replay
python scripts/run_agent_acceptance.py --mode real
python scripts/run_agent_acceptance.py --mode all
```

`real`/`all` 会调用真实本地或外部服务，运行前确认凭据、模型和科学工具可用。

### 靶点数据库检查

来源：`docs/target_search_local_db.md`、`scripts/target_db/validate_target_db.py`。

```powershell
python -m src.target_search.seed
python scripts/target_db/validate_target_db.py --require-cache --strict
```

### JavaScript 检查

仓库没有 npm 测试框架。现有测试是直接运行的 Node.js 脚本：

```powershell
node tests/home_agent_task_panel_test.js
node tests/reverse_target_broad_recall_test.js
node tests/reverse_target_pagination_test.js
```

修改 JavaScript 后至少运行语法检查：

```powershell
Get-ChildItem src/web/static/js -Recurse -Filter *.js -File |
  Where-Object { $_.FullName -notmatch 'ketcher|backup' } |
  ForEach-Object { node --check $_.FullName }
```

### lint 状态

当前没有正式配置的 Python/JavaScript lint 或 formatter 命令。不要声称 `ruff`、`black`、`eslint` 已启用。引入统一工具链需单独 PR；工具选型为“待确认”。

## 6. 代码修改边界

- 保持 FastAPI + Jinja2 + 原生 JavaScript 的现有结构，除非任务明确批准迁移。
- 新增页面应沿用 `src/web/templates/`、`src/web/static/`、`src/web/routes/` 的模块化方式。
- API 路由放在已有领域路由模块或独立的 `setup_*_routes()` 模块中。
- 科学计算结果必须来自真实工具或明确标注为不可用；不得伪造分数、结合能、pIC50、文献或实验结论。
- 大型结构、模型和索引文件保存在磁盘；SQLite 优先保存元数据与相对路径。
- 不得无关重构，不得覆盖用户工作区中已有的未提交改动。
- 修改公共事件格式、API 响应、数据库 schema 或 Agent 工具契约时必须补回归测试。

## 7. AI/Codex 工作规则

1. 开始任务前读取本文件、相关模块、测试和最近 Git 状态。
2. `main` 只用于稳定基线；不得直接在 `main` 提交。
3. 一个任务一个独立分支、一个 PR。
4. 工作树混杂时必须显式暂存文件，禁止 `git add -A`。
5. 范围不清楚、存在破坏性选择或会扩大任务边界时，先询问用户。
6. 诊断任务只分析，不自动实施修复，除非用户明确要求修复。
7. 修改功能或 Bug 时先建立可复现测试，再做最小修复。
8. 不读取、输出或提交真实密钥；日志和报告必须脱敏。
9. 不使用 `git reset --hard`、`git clean` 或覆盖式 checkout 回退用户改动。
10. 完成时报告准确文件、命令、测试结果、分支、commit 和 PR。

## 8. Git 分支与 PR 规则

- 禁止直接修改或提交到 `main`。
- 分支命名建议：

```text
feat/<short-topic>
fix/<short-topic>
docs/<short-topic>
chore/<short-topic>
```

- Commit 使用简洁的 Conventional Commit 风格：

```text
feat: add target workflow
fix: reject fabricated docking scores
docs: add project standards
test: cover checkpoint recovery
```

- 每个 PR 只解决一个主题；不要混入格式化、依赖升级或无关重构。
- PR 描述必须包含：修改目标、文件范围、验证命令、待确认事项、风险和审查重点。
- 默认创建 draft PR；由维护者确认后再转为 ready。
- 禁止 Agent/Codex 自动合并 PR，尤其禁止直接合并到 `main`。

分支保护、必需审查人数和 CI 必需检查当前未在仓库文件中定义，属于“待确认”。

## 9. 禁止提交的内容

- `.env`、API Key、token、密码、数据库连接凭据；
- 用户输入、聊天原文、个人信息、未脱敏日志；
- `outputs/`、`scratch/`、`temp_docking/`、`logs/`；
- SQLite/DB 运行文件、FAISS 索引、模型权重、训练缓存；
- 大型视频、未经批准的图片或二进制文件；
- 本地 IDE、虚拟环境、缓存和 `__pycache__`；
- 真实生产结构缓存或许可不明的数据集；
- 临时 probe 数据库、测试运行产物和绝对机器路径配置。

以 `.gitignore` 和 `data/REGISTRY.md` 为最低要求；发现敏感内容已进入历史时应立即停止发布并通知维护者。

## 10. 完成任务前的自检清单

- [ ] 当前分支不是 `main`。
- [ ] `git status --short` 中只暂存本任务文件。
- [ ] 未回退或覆盖用户已有改动。
- [ ] 未写入 `.env`、密钥、token、隐私数据或机器专用凭据。
- [ ] 新行为有对应测试；文档命令有真实文件依据。
- [ ] Python 测试、相关聚焦测试和 `compileall` 已运行。
- [ ] 修改 JavaScript 时已运行 `node --check` 和相关 Node 测试。
- [ ] 修改部署/数据时已运行 `scripts/health_check.py --strict` 或说明无法运行原因。
- [ ] API、事件、数据库和文件路径保持向后兼容，或已明确记录破坏性变更。
- [ ] 文档中的不确定内容已标记“待确认”。
- [ ] Commit 仅包含本任务范围。
- [ ] PR 目标是 `main`，但未直接合并。
