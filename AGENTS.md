# MedChat Agent Working Guide

本文件是开发者与自动化 Agent 的协作入口。项目介绍和本机启动见 [README](README.md)，详细工程规范见 [PROJECT_STANDARDS](docs/PROJECT_STANDARDS.md)，专题入口见 [文档索引](docs/README.md)。

## 1. 项目简介

MedChat 是面向药物设计与计算药物化学的 Web 平台，整合聊天/RAG、分子生成、性质/ADMET/活性预测、靶点检索、反向寻靶、Vina 对接和后台任务。

科学结果必须来自真实工具，并保留来源、模型状态和失败信息；不得伪造分数、结合能、pIC50、文献或实验结论，也不得将预测表述为已验证实验事实。

## 2. 技术栈概览

保持 FastAPI + Jinja2 + 原生 JavaScript/CSS 的现有架构，未经明确批准不迁移。科学计算使用 RDKit、PyTorch/PyG 等本地依赖；推荐 Python 3.10，3.11 正式支持待确认。

完整技术栈、依赖 profile 及目录职责见 [项目规范第 2–3 节](docs/PROJECT_STANDARDS.md#2-技术栈说明)。仓库没有前端打包流程或已配置的 Ruff/Black/ESLint；不得假定这些工具已启用，引入需独立 PR。

## 3. 重要目录说明

优先阅读任务涉及的领域模块及测试：

- Web：`src/web/routes/`、`src/web/templates/`、`src/web/static/`。
- 科学领域：`src/activity/`、`src/docking/`、`src/reverse_target/`、`src/target_search/`、`src/molecular_design/`。
- Agent 和后台任务：`src/agent/`、`src/task_runtime/`；维护入口见 [AGENT_MAINTENANCE](docs/AGENT_MAINTENANCE.md)。
- 数据来源与部署清单：[data/REGISTRY.md](data/REGISTRY.md)；提交边界见第 9 节。

## 4. 本地启动命令

在具备科学依赖的环境中，从仓库根目录执行（来源：[requirements.txt](requirements.txt)、[main.py](main.py)）：

```powershell
python -m pip install -r requirements.txt
python main.py --port 6001
```

本机浏览器访问 `http://127.0.0.1:6001`。省略 `--port` 时后端默认监听 `127.0.0.1:6000`；6000 是浏览器受限端口，生产使用 nginx HTTPS，不关闭浏览器保护。

需要分子生成时，先运行 `.\start_ollama.ps1` 或 `ollama serve`，并确认已有 `gmm-llama:latest`。主聊天模型配置不能覆盖分子生成工具模型。

启动选项见 `python main.py --help`；主模型设置见 [README](README.md#主模型接入与一次保存)。Linux、直接 ASGI 与多 worker 的适用边界见 [部署文档](deployment/README.md)。

## 5. 构建、测试、lint 命令

完整可执行命令集中在 [项目规范第 11 节](docs/PROJECT_STANDARDS.md#11-测试规范)，不要在多份文档维护不同版本。

- 修改功能/Bug：先建立失败测试，再做最小修复；运行聚焦测试、受影响回归和 `compileall`。
- 修改 JS：运行 `node --check` 和相关 Node 契约测试。
- 修改部署/数据：运行 `scripts/health_check.py --strict`，或说明无法运行原因。
- `real`/`all` 验收会调用真实服务，运行前确认凭据、模型和科学工具可用；密钥规则见第 9 节。
- 未运行的检查和跳过项必须明确报告，不能宣称已通过。

## 6. 代码修改边界

- 页面、静态资源和路由沿用现有模块化目录；路由使用已有领域模块或 `setup_*_routes()`。
- 大型结构、模型、索引保存在磁盘；SQLite 优先保存元数据和相对路径。
- 不做无关重构，不覆盖或回退用户已有改动。
- 公共事件、API 响应、数据库 schema、Agent 工具契约变更必须补回归测试，并说明兼容性。
- 前后端、科学执行、错误处理和安全的详细规则见 [项目规范](docs/PROJECT_STANDARDS.md)。

## 7. AI/Codex 工作规则

1. 开始前读取本文件、相关模块和测试，检查最近 Git 状态。
2. 范围不清、涉及破坏性选择或扩大任务边界时，先询问用户。
3. 诊断任务只分析；除非用户明确要求，不自动实施修复。
4. 禁止 `git reset --hard`、`git clean` 或覆盖式 checkout 回退用户改动。
5. 完成时报告准确文件、验证命令及结果、分支、commit 和 PR；未提交或未建 PR 也应说明。

## 8. Git 分支与 PR 规则

- `main` 只作稳定基线，不直接修改或提交；一个任务一个独立分支、一个 PR。
- 工作树混杂时显式暂存本任务文件，禁止 `git add -A`。
- 分支建议 `feat/`、`fix/`、`docs/`、`chore/`；commit 使用简洁 Conventional Commit，如 `docs: clarify setup guide`。
- 不混入无关格式化、依赖升级或其他主题；不擅自 amend/rebase 共享历史或强推。
- PR 目标为 `main`，默认 draft；说明目标、文件范围、影响、验证、待确认事项、风险/兼容性/回滚和审查重点。
- 科学算法变更附输入、输出和 provenance；数据变更附来源、版本与校验；UI 截图不得含隐私。
- 默认不合并。只有维护者明确指定 PR、合并方式并授权，且质量门禁满足后，才可执行合并。

分支保护、必需审查人数、CI 必需检查和 CODEOWNERS 尚待确认。

## 9. 禁止提交的内容

- `.env`、API Key、token、密码、Cookie、证书私钥、数据库凭据及含真实密钥的脚本/报告/输出。
- 用户输入、聊天原文、个人信息和未脱敏日志。
- `outputs/`、`scratch/`、`temp_docking/`、`logs/`；SQLite/DB 运行文件、FAISS 索引和训练缓存。
- 大型视频、未经批准的图片或二进制文件；本地 IDE、虚拟环境、缓存和 `__pycache__`。
- 真实生产结构缓存、许可不明数据集、临时 probe 库、测试产物和绝对机器路径配置。

模型权重、运行时 registry 和真实训练资产不因其资产类型被一律禁止提交；上述运行文件、缓存、输出目录和二进制类别限制不作为禁止这三类资产提交的依据。密钥、凭据、隐私、未脱敏日志和许可不明数据的禁令仍适用。

提交前核对 `.gitignore` 和 [data/REGISTRY.md](data/REGISTRY.md)；如仍限制上述三类资产，在明确提交范围后同步调整，不默认强制添加。发现敏感内容已进入历史时，立即停止发布并通知维护者。

测试可使用真实外部 API key，但只能来自运行时环境变量或本机密钥管理器，例如 `OPENAI_COMPATIBLE_API_KEY`；禁止输出、保存、提交或写入日志/报告。报告仅记录 `API key present: true` 或 `sk-***...***` 等脱敏状态。

## 10. 完成任务前的自检清单

- [ ] 不在 main；只暂存本任务文件，未覆盖用户已有改动。
- [ ] 第 9 节的数据与密钥边界已检查。
- [ ] 新行为有测试；聚焦/相关回归、compileall 及适用的 JS/部署检查已执行，未执行项已说明。
- [ ] API、事件、schema、路径兼容，或已记录破坏性变更。
- [ ] 文档命令有真实文件依据，不确定内容标记“待确认”。
- [ ] Commit 范围单一；PR 指向 main，未经明确授权未合并。
