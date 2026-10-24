# MedChat 项目规范

整理日期：2026-10-16（去重与入口校准，不表示全部功能重新验收）
适用仓库：`molecular_chat_system`
规范状态：基于当前仓库结构整理；标记为“待确认”的内容尚未形成团队最终决策。

## 1. 项目定位

项目能力与使用方式见 [README](../README.md)。本文维护工程细则；协作、Git/PR、科学真实性和禁止提交边界以 [AGENTS.md](../AGENTS.md) 为准，不另维护一套重复规则。

## 2. 技术栈说明

### 2.1 后端

- Python 3.10+；
- FastAPI 和 Uvicorn；
- Pydantic 2；
- Jinja2；
- HTTPX/Requests；
- WebSocket。

主要入口：

- `main.py`：推荐启动入口，加载 `.env`、检查环境、选择配置并启动 Uvicorn；
- `src/web/app.py`：FastAPI 应用工厂与模块级 ASGI `app`；
- `src/web/routes/`：页面、API、系统和 Agent 路由；
- `src/target_search/routes.py`、`src/task_runtime/routes.py`：领域独立路由。

### 2.2 前端

- 服务端 Jinja2 模板；
- 原生 JavaScript/CSS；
- Fetch API 和原生 WebSocket；
- 3Dmol.js、ECharts、SmilesDrawer；
- Ketcher 作为已构建静态资产放在 `src/web/static/ketcher/`。

前端没有 npm 构建步骤；`runtime/pi/` 的 npm manifest/锁文件仅服务后端 Pi Agent 运行时，不是前端构建链。第三方 CDN 和 vendored 资产版本变更必须单独审查。

### 2.3 科学计算和模型

- RDKit、NumPy、Pandas、SciPy、scikit-learn；
- PyTorch、PyTorch Geometric、RG-MPNN；
- FAISS；
- Ollama `gmm-llama:latest` 分子生成；
- ModelScope 和 OpenAI-compatible 主模型接口；
- AutoDock Vina、ADFRsuite、Meeko。

### 2.4 数据与持久化

- 靶点数据库：SQLite + CSV 种子 + 本地结构缓存；
- Agent 状态：SQLite WAL；
- 后台任务：SQLite WAL；
- RAG：CSV + FAISS 索引；
- 反向寻靶：TSV、NumPy 指纹、可选 ChEMBL SQLite；
- 模型：磁盘权重文件和 JSON 元数据。

### 2.5 依赖版本

- `requirements.txt`：Python 3.10 的 CI / CPU 开发环境，不包含 CUDA 版 PyTorch/PyG；
- `deployment/requirements.txt`：Linux、CUDA 12.1、RTX3090 生产部署环境，包含 GPU 深度学习栈。

两套 profile 不可互换，版本也不要求机械同步。CI 使用根依赖验证不需要真实模型/密钥的离线质量门；GPU 服务器使用 deployment profile，并在部署后额外运行健康检查和真实工具验收。修改依赖时必须说明目标 profile，并验证另一 profile 是否需要兼容调整。

两套 Python profile 的网页通用 Agent 均依赖 `runtime/pi/` 的官方 Pi SDK **1.4.0**。
Node.js 最低 **22.19.0**，推荐 **24.15.0**；在仓库根目录用
`npm ci --prefix runtime/pi --ignore-scripts --no-audit --no-fund` 按锁文件安装，不依赖全局 Pi。
`requirements-agent-harness.txt` 及 CI 中对应的 include 已删除；部署方也须移除旧 include，不再安装旧 Python harness 钉住版本；RAG 依赖仍保留。
默认 `MEDCHAT_CHAT_PROFILE=pi`，`semantic_v1` 在同一 Pi 上增加语义准入，`decision_a2` 仅为临时别名；
`legacy` 被拒绝，不提供旧运行时 fallback。确定性工作流保留 Supervisor → 直接执行器路径。
旧 checkpoint 只保留历史读取、拒绝执行续接；完整 Git 发布回滚须包含匹配的 Python/Node 依赖与锁文件安装。
详见 [Pi 运行手册](PI_RUNTIME.md)，不得清库或自动转换旧 checkpoint。

## 3. 目录结构说明

| 路径 | 职责 |
|---|---|
| `main.py` | 本地和 systemd 启动入口 |
| `config/` | YAML 配置，支持环境变量占位符 |
| `deployment/` | 服务器依赖、nginx、systemd、对接工具说明 |
| `docs/` | 项目规范、架构、部署和交接文档 |
| `scripts/` | 健康检查、验收、数据校验 |
| `data/REGISTRY.md` | 数据资产登记 |
| `data/samples/` | 可提交的轻量科学样例 |
| `src/web/` | FastAPI、Jinja 模板和静态前端 |
| `src/agent/` | Agent 路由、规划、执行、工具、持久化和评测 |
| `src/agent/pi_runtime/`、`src/agent/scientific_session/` | Pi 桥接/续接校验与 Python 领域授权、科学执行边界 |
| `runtime/pi/` | 官方 Pi SDK 的 Node 运行时、npm 锁文件和离线契约测试 |
| `src/docking/` | 对接服务、适配器和结果处理 |
| `src/molecular_design/` | 分子设计领域逻辑 |
| `src/reverse_target/` | 反向寻靶领域逻辑 |
| `src/activity/` | 活性预测和训练 |
| `src/target_search/` | 靶点数据库、检索、结构下载和缓存 |
| `src/task_runtime/` | 后台任务模型、SQLite 和路由 |
| `tests/agent/` | Agent 单元/集成/恢复/评测测试 |
| `tests/` | Web、领域、部署和 Node 静态测试 |
| `archive/` | 归档代码，不得被运行时代码重新引用 |
| `outputs/`、`scratch/`、`temp_docking/` | 本地运行产物；提交限制及资产例外见 [AGENTS 第 9 节](../AGENTS.md#9-禁止提交的内容) |

## 4. 前端开发规范

1. 保持 Jinja2 + 原生 JavaScript/CSS 架构，未经单独设计评审不得引入 React/Vue 或构建链。
2. 页面模板放在 `src/web/templates/`；页面专属资源放在对应的静态子目录。
3. 新页面推荐采用：

```text
src/web/templates/<feature>.html
src/web/static/css/<feature>.css
src/web/static/js/<feature>/config.js
src/web/static/js/<feature>/api_client.js
src/web/static/js/<feature>/main.js
```

4. JS 模块通过显式全局命名空间或当前目录既有模式协作，不得隐式依赖未声明变量。
5. API 地址集中在 `config.js` 或 `api_client.js`，不要散落硬编码。
6. 事件监听在初始化函数或 `DOMContentLoaded` 中注册；重复初始化必须可防护。
7. WebSocket 消息必须兼容现有消息类型，Agent 事件仍采用结构化 `agent_event`。
8. 前端不得显示伪造科学结果；失败、部分结果和 fallback 状态必须可见。
9. 新增外部 CDN 时记录版本、用途和离线影响；优先使用仓库已有 vendored 资产。
10. 修改 JS 后运行 `node --check`；修改静态契约时增加或更新 `tests/*_test.js`。
11. 大型遗留文件允许渐进拆分，但不得在无关任务中整体重写。
12. 可访问性、移动端和加载失败状态应与现有 `mobile_baseline.css`、子页头部组件保持一致。

## 5. 后端开发规范

1. 使用 Python 3.10 兼容语法；是否将 3.11 纳入正式支持为“待确认”。
2. 模块按领域放置，不将业务逻辑堆入 `src/web/routes/api_routes.py`。
3. Route 负责参数解析和 HTTP 映射；Service 负责业务流程；Repository/Database 负责持久化。
4. 新路由优先使用 `setup_<feature>_routes(app, ...)` 或现有注册模式。
5. 依赖昂贵的模型和数据库应延迟加载、缓存实例并提供健康检查。
6. 同步/异步边界必须清晰；异步 Route 中避免直接执行长时间 CPU 或外部进程任务。
7. 长任务使用 `src/task_runtime/` 或 Agent 工作流，不阻塞 WebSocket/HTTP 请求。
8. 路径使用 `pathlib.Path`，运行资产优先保存相对项目路径。
9. 不使用随机值填充科学结果；服务不可用时返回明确错误或标记清晰的降级信息。
10. Pydantic schema、dataclass 或现有领域契约应作为跨模块接口，避免任意字典扩散。
11. 外部工具调用必须设置超时，并将标准错误转换为结构化错误。
12. 新增公共行为必须有聚焦测试和全量回归。

### 5.1 Agent 科学执行契约

- Agent plans must compile before execution. Multi-step plans must bind downstream inputs to versioned upstream outputs. Scientific claims require accepted evidence, and generated molecular candidates must pass RDKit validation and canonical deduplication before they are returned to downstream tools or the frontend.
- 工具观察必须保留显式状态、provenance、warnings、evidence 和 artifacts；`partial`、`unavailable`、`failed` 不得转换为完成态。
- 编译器只接受受限绑定语法，运行时不得执行任意表达式；靶点驱动设计和 hit-to-lead 必须验证关键上游结果确实进入下游步骤。
- demo、fallback 或缺少真实模型/工具证据的结果不得支撑科学 claim。

## 6. API 设计规范

### 6.1 URL

- 页面：短横线形式，如 `/target-search`；
- API：以 `/api/` 开头；
- 领域资源：`/api/<domain>/<resource>`；
- 任务状态：使用资源 ID，如 `/api/tasks/{task_id}`；
- WebSocket：保持 `/ws`。

### 6.2 响应格式

新 API 优先使用 `src/web/api_response.py`：

```json
{
  "success": true,
  "code": "OK",
  "message": "success",
  "data": {},
  "request_id": "uuid"
}
```

错误格式：

```json
{
  "success": false,
  "code": "DOMAIN_ERROR_CODE",
  "message": "可读错误信息",
  "details": {},
  "request_id": "uuid"
}
```

现有 `api_routes.py` 大量接口仍直接抛出 `HTTPException`，属于兼容现状。统一迁移策略为“待确认”，不得在无关 PR 中批量改写。

### 6.3 状态码

- `200`：成功查询或兼容的成功响应；
- `201/202`：新建资源或异步任务已接受；
- `400`：无效业务输入；
- `404`：资源不存在；
- `409`：重复或状态冲突；
- `422`：缺失/无法验证的参数；
- `500`：未预期内部错误；
- `503`：模型、数据库或科学工具不可用；
- `504`：外部计算超时。

### 6.4 输入和输出

- SMILES、文件路径、分页、数量、阈值必须在边界层验证；
- 文件上传限制需与 nginx `client_max_body_size` 保持一致；
- 科学结果返回模型/工具、版本、状态和必要 provenance；
- 不在错误消息中返回密钥、完整绝对路径或原始堆栈；
- 下载接口必须验证路径归属和文件存在性。

## 7. 数据库/配置文件规范

### 7.1 SQLite

- 使用参数化 SQL；
- 连接及时关闭或使用上下文管理器；
- Agent/任务运行库采用 WAL；
- Schema 变更必须可重复执行并有测试；
- 不把二进制模型、PDB/SDF 大文件直接塞入 SQLite；
- 存储相对路径、SHA256、媒体类型和验证状态；
- 运行库、journal、WAL 的提交限制及运行时 registry 例外见 [AGENTS 第 9 节](../AGENTS.md#9-禁止提交的内容)。

当前数据库：

- `data/target_db/target_database.sqlite`：靶点元数据；
- `data/agent_state.sqlite3`：Agent 状态，按环境变量配置；
- `scratch/tasks.sqlite`：后台任务默认库。

### 7.2 数据资产

- 可重建数据需保留来源、版本、命令和校验方式；
- 轻量种子 CSV 可以提交；
- 模型权重、FAISS、指纹矩阵和结构缓存按 [data/REGISTRY.md](../data/REGISTRY.md) 登记和部署；提交范围遵守 AGENTS 第 9 节，不再一律禁止模型权重、运行时 registry 和真实训练资产；
- 新增数据资产必须同步更新 `data/REGISTRY.md`。

### 7.3 配置

- 默认值放 YAML 或 `.env.example`；
- 端口、科学资产等运行参数放本地 `.env` 或部署环境变量；主聊天模型的用户级保存与迁移规则见 [README](../README.md#主模型接入与一次保存)，不能用旧主模型环境变量配置替代；
- 测试/验收允许使用真实外部 API key，但只能通过运行时环境变量或本机密钥管理器注入；
- 环境变量优先于默认配置；
- `.env.example` 只能包含空值或无敏感性的示例值；
- 配置路径不得写死个人目录；
- 外部 LLM 与本地分子生成模型配置必须隔离。

## 8. 命名规范

### Python

- 文件/模块：`snake_case.py`；
- 函数、变量：`snake_case`；
- 类：`PascalCase`；
- 常量：`UPPER_SNAKE_CASE`；
- 私有实现：前导下划线；
- 测试：`test_<behavior>()`，文件 `test_<area>.py`。

### JavaScript

- 变量/函数：`camelCase`；
- 全局命名空间/模块对象：`PascalCase`；
- 常量：沿用模块当前风格，新增共享常量优先 `UPPER_SNAKE_CASE`；
- 文件：当前以 `snake_case.js` 为主，新增文件遵循同目录既有风格。

### API/数据

- URL 使用小写短横线；
- JSON 字段使用 `snake_case`，与现有 Python API 保持一致；
- 数据库表/列使用 `snake_case`；
- 错误码使用 `UPPER_SNAKE_CASE`；
- Git 分支使用 `<type>/<short-topic>`。

## 9. 错误处理规范

1. 在最接近失败边界的位置捕获已知异常。
2. 不使用裸 `except:`。
3. 不把失败转换为伪成功。
4. 对可恢复外部错误使用有限重试；仅对幂等操作自动重试。
5. 参数错误返回 4xx；依赖不可用返回 503；超时返回 504。
6. Agent 工具错误使用结构化错误码和 `ToolResult`/`AgentExecutionError`。
7. 工作流可选步骤失败可产生 `partial`，必需步骤失败必须停止或请求输入。
8. 用户消息简洁明确；内部日志保留 `exc_info=True`，但不得返回给客户端。
9. 对接、活性、ADMET、反向寻靶等科学模块必须区分：

```text
真实成功 / 部分成功 / 输入无效 / 工具不可用 / 模型缺失 / 计算失败
```

## 10. 日志规范

- 每个模块使用 `logging.getLogger(__name__)`；
- `debug`：诊断细节，禁止秘密和完整用户数据；
- `info`：启动、完成、选用的模型/工具、耗时；
- `warning`：可恢复降级、可选依赖缺失；
- `error`：请求或任务失败；
- 未预期异常使用 `logger.exception()` 或 `exc_info=True`；
- Agent/任务日志应带 `trace_id`、`task_id` 或 `job_id`；
- 不记录 API Key 原文、Authorization、Cookie、完整环境变量；测试报告只允许记录 `API key present: true` 或 `sk-***...***` 等脱敏状态；
- 不在循环中高频输出大对象；
- `logs/` 为运行目录，不提交 Git。

当前日志由 `main.py` 和各模块标准 logging 共同产生。统一 JSON 日志和轮转方案为“待确认”。

## 11. 测试规范

### 11.1 测试分层

- 单元测试：纯函数、schema、路由评分、适配器；
- 集成测试：FastAPI TestClient、SQLite、工作流和模型装载；
- 恢复测试：checkpoint、幂等、重启；
- 契约测试：API、事件、工具结果和前端静态契约；
- 真实验收：Ollama、RG-MPNN、靶点库和 Vina；
- 部署检查：`scripts/health_check.py --strict`。

### 11.2 命令

以下命令从仓库根目录执行；涉及 RDKit/PyTorch 的测试须使用具备相应依赖的环境。

```powershell
# Python 全量、Agent 与靶点聚焦测试（来源：tests/）
python -m pytest tests -q
python -m pytest tests/agent -q
python -m pytest tests/test_target_search.py -q
python -m compileall -q src scripts

# 官方 Pi SDK 离线契约（先按第 2.5 节安装锁定依赖）
npm test --prefix runtime/pi

# 环境检查（来源：scripts/health_check.py）
python scripts/health_check.py
python scripts/health_check.py --strict

# Agent 验收（来源：scripts/run_agent_acceptance.py）
python scripts/run_agent_acceptance.py --mode contract
python scripts/run_agent_acceptance.py --mode replay
python scripts/run_agent_acceptance.py --mode real
python scripts/run_agent_acceptance.py --mode all
```

`real`/`all` 会调用真实服务，运行前确认凭据、模型和工具可用；测试密钥规则见 [AGENTS 第 9 节](../AGENTS.md#9-禁止提交的内容)。contract/replay 不能替代真实科学验收。

Pi 替换已尝试云端与本地真实模型验收，但两组均未通过，成功的模型/科学工具端到端验收仍为 **未验证**。
具体失败、离线验证与部署限制见 [Pi 验收记录](superpowers/validation/2026-10-23-pi-harness-replacement.md)。
上述命令是验证入口，不是已通过记录；离线契约和健康检查不代表替换已完整验证。

靶点数据准备与检查（[详细说明](target_search_local_db.md)；seed 会写入数据，应确认目标目录）：

```powershell
python -m src.target_search.seed
python scripts/target_db/validate_target_db.py --require-cache --strict
```

前端无 npm 测试框架，直接运行受影响的 `tests/*_test.js`。常用示例：

```powershell
node tests/home_agent_task_panel_test.js
node tests/reverse_target_broad_recall_test.js
node tests/reverse_target_pagination_test.js

Get-ChildItem src/web/static/js -Recurse -Filter *.js -File |
  Where-Object { $_.FullName -notmatch 'ketcher|backup' } |
  ForEach-Object { node --check $_.FullName }
```

当前未配置统一 Python/JS formatter 或 lint；不要将 `node --check` 和 `compileall` 称为完整 lint。

### 11.3 要求

- Bug 修复先增加失败测试；
- 测试名称说明行为，不只说明函数名；
- 优先测试真实代码，非必要不 mock；
- 时间、随机数和外部服务必须可控；
- 测试不得写入正式数据目录；
- 真实测试必须明确依赖、超时和是否跳过；
- 不能用“测试通过”替代源码编译、健康检查和安全扫描。

`.github/workflows/quality.yml` 在 Python 3.10 上运行完整 Python 测试、源码编译、全部受支持的 Node 契约测试和跟踪文件密钥扫描。该离线 CI 不读取真实 API key，也不把缺失 Ollama、RG-MPNN 权重或 Vina 包装成真实通过。覆盖率阈值和分支必需检查仍为“待确认”。

## 12. Git 提交规范

分支、显式暂存、commit、共享历史和强推规则统一见 [AGENTS 第 7–8 节](../AGENTS.md#8-git-分支与-pr-规则)；禁止提交内容及训练资产例外见 [第 9 节](../AGENTS.md#9-禁止提交的内容)。

## 13. Pull Request 规范

PR 目标、draft 默认值、描述字段、科学/数据变更证据和合并授权统一见 [AGENTS 第 8 节](../AGENTS.md#8-git-分支与-pr-规则)。不要在本文件另维护一套审批要求。

## 14. 安全与隐私规范

- 密钥只来自运行时环境变量、本机密钥管理器或受控运行时配置；
- 测试允许使用真实外部 API key；禁止输出、保存、提交、写入日志或报告，报告只允许记录 `API key present: true` 或 `sk-***...***` 等脱敏状态；
- `.env`、token、账号、Cookie、证书私钥禁止提交；
- 持久化前执行敏感字段递归脱敏；
- SQL 使用参数化语句；
- 上传和下载路径必须规范化并限制在允许目录；
- 用户上传文件不得永久保留，除非有明确数据保留策略；
- 日志、评测报告和 handoff 不得包含真实凭据原文；
- 外部模型、HTTP API、MCP 和命令行工具视为不可信边界；
- 子进程只接收完成任务所需的最小环境白名单，不得继承 API Key、会话路径或完整运行时环境；
- 大型数据与模型需要来源、许可证和完整性校验；
- 发现凭据泄露时立即停止 push/PR，并轮换凭据。

用户数据留存与清理边界见
[`docs/security_data_retention_policy.md`](security_data_retention_policy.md)。其中 TaskStore
终态记录、对接报告和已发布 artifact 的统一自动过期作业仍明确标为工程待办，生产部署不得将本地
开发服务器行为当作完整留存治理。

## 15. 新功能开发流程

1. 明确目标、非目标、输入输出和成功标准。
2. 阅读相关 Route、Service、数据、前端和测试。
3. 确认是否需要新的 schema、数据库表、配置或外部依赖。
4. 从最新 `main` 创建独立分支。
5. 先写契约或失败测试。
6. 以最小改动实现领域逻辑。
7. 接入 API/Agent/前端，不跨层复制业务逻辑。
8. 添加错误处理、日志、健康检查和安全边界。
9. 运行聚焦测试、全量测试、compileall 和相关静态检查。
10. 更新 README、项目规范、数据登记或部署文档。
11. 显式暂存本任务文件，提交并创建 draft PR。
12. 按 AGENTS 第 8 节完成审查与明确授权合并，不自行合并。

## 16. Bug 修复流程

1. 记录可复现输入、环境和完整错误。
2. 检查近期 diff、调用链和组件边界。
3. 确认根因，不先猜测修改。
4. 添加最小失败测试并确认按预期失败。
5. 只修根因，避免顺手重构。
6. 运行聚焦测试和受影响模块回归。
7. 对 Agent/科学工具额外验证：

```text
没有伪造结果
没有重复执行昂贵步骤
失败状态持久化正确
事件顺序兼容
凭据未进入日志或数据库
```

8. PR 描述写明根因、修复方式和回归风险。

## 17. 文档维护规则

- 文档必须基于仓库当前事实，禁止复制通用模板后不校验；
- 命令旁标注来源文件或适用环境；
- 不确定内容统一写“待确认”；
- 路径、配置项、端口、模型和依赖变更时同步更新相关文档；
- 文档入口与职责见 [文档索引](README.md)：README 管快速使用，AGENTS 管协作/提交，本文件管工程细则，部署教程管逐步操作，deployment README 管生产补充约束；
- 相同规则或操作步骤只维护一份正文，其他页面链接引用；保留安全提示和适用条件，不复制整套安装/配置命令；
- 修改标题时检查入站锚点链接；历史计划、handoff 和验收报告保留日期、commit、失败与限制，不能当作当前状态保证；
- `AGENTS.md` 保持短而可执行；新增专题须更新文档索引，链接必须指向真实存在的文件；
- 每轮重要任务更新 `docs/handoff/latest.md`；
- 数据资产变化更新 `data/REGISTRY.md`；
- 部署变化更新 `deployment/README.md`；
- 不在文档中写真实密钥原文、个人隐私或本机绝对凭据路径；如需记录测试凭据状态，只能使用脱敏形式；
- 文档 PR 同样遵守独立分支、审查和显式授权合并规则。

## 18. Temporal docking canary 运行规范

Temporal 是 docking 长任务的可选执行后端，不是全局默认，也不替代现有科学工具、Validator 或同步 API。开发和未放量环境保持 `MEDCHAT_TASK_BACKEND=local`、`MEDCHAT_TEMPORAL_CANARY_PERCENT=0`；非法配置必须 fail closed 到该默认状态。

### 18.1 生产边界与安装

- 生产证据仅接受 native Linux + systemd + cgroup v2；Windows、SDK dev server、contract fake 和 replay 不能冒充生产证据。
- Docker/Compose 基础设施由 operator 管理并先确认健康；systemd Web/worker unit 不启动、重启或修改 Docker。worker 独立于 Web，docking 并发固定为 `1`，内部端口和 metrics 只允许 loopback 或私有网络。
- secrets 只能由运行时 secret file/环境注入，不得进入 Git、argv、日志或报告；专用 worker env 必须是 `root:root 0600` 的无秘密 allowlist。
- worker 资产由 `install-temporal-worker.sh` stage 为不可变 generation，再由 `activate-temporal-worker-generation.sh` 事务 activate、rollback 或 migrate。禁止手工修改 `current`、固定 unit/helper、digest/manifest 或 legacy tmpfiles；prepare service 只能使用已激活 generation。
- 只有受控运行目录属于 `medchat:medchat 0700`；源码、Conda 和部署资产保持 `root:root`、`go-w`。

完整安装、迁移、激活、健康检查和回滚步骤以 [Temporal canary 运行手册](runbooks/temporal_docking_canary.md) 为唯一操作来源；部署总览只保留生产边界。可选依赖仍使用 `requirements-agent-temporal.txt`，本地开发 server/worker 也只能用于开发和验收。

### 18.2 API、数据与科学结果边界

- 异步 `POST /api/docking/tasks`、事件查询和取消接口需要管理鉴权；同步 `/api/docking/submit` 保持原响应契约，不通过 Temporal。
- Temporal workflow/history 只保存任务控制信息、相对 artifact 标识、hash、状态码和脱敏 provenance，不保存 receptor、ligand、SMILES、manifest 内容、凭据或绝对路径；SQLite 是查询投影，Temporal 是已接收任务的执行权威。
- 成功必须同时满足一次真实 Vina 执行、一个终态事件、数值能量、有效 pose、artifact SHA-256 一致，以及 `demo_mode=false`、`fallback_used=false` 的真实 provenance。

### 18.3 验收、晋级与回滚

验收命令、报告位置和 host capability 检查见运行手册第 [6](runbooks/temporal_docking_canary.md#6-validate-the-deployment)–[8](runbooks/temporal_docking_canary.md#8-run-production-preflight-at-0) 节。`contract` 只验证生命周期并必须标记 `scientific_execution=false`；`real` 才能验证真实 Vina、pose、hash、provenance、终态和执行次数。缺失、不可用或 skipped 的 required evidence 必须保持 `partial`，不能记为 passed。

生产流量只能按 `0% -> 5% -> 10% -> 25%` 晋级，每档观察真实 Temporal 终态任务；任一硬门禁失败立即用 `manage_temporal_canary.py rollback` 将新流量降为 0%，但不删除 history、TaskStore、staging、artifact 或报告，也不在 local 重放已接收任务。PostgreSQL 备份/恢复使用受信版本化 bin，密码只进入 child env，恢复目标固定为 `temporal_verify` 且不 drop source。

### 18.4 证据真实性

contract/replay、fake runner、Windows 静态验证和缺少宿主工具的检查只证明契约或静态条件，不能冒充真实 Vina、Temporal、PostgreSQL、systemd 或 cgroup 证据。报告必须逐项保留 `passed`、`failed`、`skipped`，总体状态按最严格结果收敛。

## 待确认事项汇总

1. Python 3.11 是否纳入正式支持范围。
2. `requirements.txt` 与 `deployment/requirements.txt` 的跨 profile 版本兼容窗口和升级节奏。
3. 是否引入 Ruff/Black、ESLint/Prettier 和统一配置。
4. API 是否逐步统一到 `api_success/api_error`。
5. FastAPI `on_event` 何时迁移到 lifespan。
6. CI 覆盖率阈值、必需检查名称和是否增加 GPU/真实工具的受控工作流。
7. GitHub 分支保护、审批人数和 CODEOWNERS。
8. README 声明 MIT，但根目录未见独立 LICENSE 文件，许可证状态需确认。
9. TaskStore 终态记录、对接报告和已发布 artifact 的统一自动留存/删除作业及其审计实现。
10. 生产数据库是否从 SQLite 迁移到 PostgreSQL，以及对象存储/Redis 的采用时点。
