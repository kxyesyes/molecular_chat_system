# T06-B 静态备份独立集成：QUALITY P2 修订交接

## 当前状态与阻断

- 本轮已将必要隔离从手工 runner 移入普通 pytest 自动执行的测试文件。
- 本机新版依赖：普通单文件 **7 passed**；有限相关回归 **12 passed**，无 skipped。
- **仍待原 SPEC / QUALITY 独立复审，不代表可以发布。**
- 父任务已报告独立模板阻断：当前 `main_routes.py` 的
  `TemplateResponse(request=...)` 在 CI Starlette 0.27 不兼容。
  本轮未修改生产文件、未 mock 页面渲染、未降级/跳过页面断言、未更改依赖。
  必须等父任务模板补丁集成后再跑 CI；本机结果不是旧版 CI 通过证明。

## 基线、工作树与精确写集

- 目的：`D:/MedChat/molecular_chat_system_worktrees/static-backup-cleanup-pr`；
  分支 `codex/static-backup-cleanup-pr`。
- HEAD 始终为指定起点
  `37aa558d65c92744613f5e37c0bd86374c26d32e`。
- 首轮开始时本地 origin/main 与 HEAD 相同；首轮末及本轮检查时 origin/main 为
  `14b2185e631383dc2115c28181ce0e3cf825cb9a`（PR36）。
  没有 fetch / merge / rebase / reset；结果仅对应指定起点加未提交补丁。
- 来源：`D:/MedChat/molecular_chat_system_worktrees/static-placeholder-cleanup`，
  分支 `codex/static-placeholder-cleanup`，HEAD
  `d89a48b803fb0dcfdff95539513de725ca36756f` 的未提交内容。
- 整体补丁仍只有三个路径：
  1. 删除 `src/web/static/js/activity_prediction_v2.legacy.backup.js`
     （57,793 字节、1,474 行；首轮完成，本轮未再修改）。
  2. 新增 `tests/test_static_placeholder_cleanup.py`。
  3. 新增本报告 `docs/handoff/static-backup-cleanup-integration.md`。
- **本轮只修改后两项**。未修改其他测试、conftest、生产模块、模板、占位 JS、
  配置或依赖；没有复制旧 latest.md，没有改写来源工作树。
- 测试原先按来源原样迁入；本轮因 P2 修改隔离/执行及收尾方式，
  不再宣称与来源测试全文相同。四项原始静态契约的断言语义保留。
- 未暂存、未提交、未推送、未创建 PR、未合并、未部署；新增 commit / PR：无。
  本轮任务不包含父任务 PR36、RAG worker 或模板兼容补丁的写集。

## 当前基线依赖结论（首轮已重新核查）

| 对象 | 证据与决定 |
|---|---|
| 被删备份 | 当前 src / tests / scripts / main.py / deployment / config / .github 无运行依赖；2026-04-22 历史计划 4 处、设计文档 1 处提及创建/保存备份，保留历史文档 |
| 静态发布 | app.py 的实际 StaticFiles 挂载整个 static；nginx 无该文件专用发布逻辑；删除有意使备份 URL 从 200 变 404 |
| 首页与活性页 | index.html 加载 home 模块；activity_prediction.html 加载 shared/safe_render 及 activity_prediction 模块，均未引用备份 |
| 动态加载 | 项目自有 JS / 模板查 createElement(script)、importScripts、loadScript、动态 import 与 .src 赋值；后者命中 home / docking 图像 URL，未发现该备份的动态加载入口 |
| script.js | main.py:178 检查存在，221–226 缺失时可能复制同名旧脚本，因此保留 |
| activity_prediction_v2.js | 当前模板未加载，旧 URL 的外部兼容需求待确认，因此保留 |
| activity_prediction_v2.css | 活性页实际使用，保留 |
| 其他备份 | test_agent_platform_health_check.py 所提 script.legacy.backup.js 不是本次删除对象 |

核查未读取密钥、模型权重、真实数据库、RAG 索引或用户运行资产。
配置目录引用只用 git grep -l 输出匹配文件名，未输出配置值。零匹配不视为运行验收。
仓库外手工链接和旧客户端是否引用备份无法由仓库检索证明，仍是维护者需接受的兼容风险。

## 首轮静态删除 TDD 记录（历史，不替代本轮验证）

1. 仅迁入测试、尚未删备份：实际应用探针为
   `exists=True HTTP=200 bytes=57793`。
2. RED：**1 failed, 3 passed**（1.39s，退出码 1），
   唯一失败为“备份不得存在”。原断言先检查文件，HTTP 200 由同轮探针另行确认。
3. apply_patch 删除备份，不改模板和生产入口。
4. 首轮手工隔离 runner GREEN：**13 passed**（3.52s），
   `exists=False HTTP=404 bytes=22`。
   这是手工 runner 环境的历史结果，不代表旧 fixture 对普通 pytest 安全。
5. 首轮四条 Node 命令通过，activity family 契约 22/22。
   原按 FullName 排除 backup 的语法命令因工作树名含 backup 选中 0 文件，
   该次不计验证；改成相对路径筛选后，47 个 JS 全部通过。
6. 首轮编译和 diff 检查通过。上述 JS 检查本轮未重跑，因本轮无 JS 变更。

## QUALITY P2 根因与修复

依据 receiving-code-review / systematic-debugging 核实，再按 TDD 修复：

- 旧 fixture 先 `from src.web.app import MolecularChatApp`，
  此导入已创建模块全局应用；随后 patch `_create_chat_agent` 无法保护导入阶段。
- 旧 yield 后清理不在 finally，异常退出跳过清理；TestClient 未 close；
  只关闭 fixture 自建分子模型，不处理导入时全局模型客户端。
- 文档中的进程外隔离 runner 不参与普通 pytest，不能作为仓库测试本身的安全保证。
- 已删除旧手工 runner 的使用说明。复现和日常验证都直接用下方普通 pytest 命令。

### 保留的 RED probe 与实际结果

新增并保留 `test_worker_isolation_and_resource_cleanup`，
在独立进程里追踪真实 httpx 同步/异步客户端、保护性工具工厂调用和临时 agent DB。
最先运行的是修复前 fixture；探针的工厂替身避免真实科学工具初始化，
数据库路径和 cwd 已外部限定在该次临时目录，故复现没有访问真实资产。

```powershell
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -B -m pytest tests/test_static_placeholder_cleanup.py -k worker_isolation -q -p no:cacheprovider --tb=short
```

RED：**2 failed, 4 deselected**（7.71s，退出码 1）。

| 修复前场景 | 工具工厂调用 | 临时 agent DB | 未关闭 HTTP 客户端 |
|---|---:|---|---:|
| 正常退出 | 1 | 创建 | 3 |
| 注入请求/测试体异常退出 | 1 | 创建 | 5 |

两个测试首先失败于工厂计数应为 0，而非依赖、导入或模板错误。
同一探针继续保留在测试文件，GREEN 时必须计数归零；其 finally 安全网仅在采集证据之后
关闭意外泄漏的客户端，不把安全网补救算作被测资源管理已通过。

### 普通 pytest 的自动隔离与所有权

1. pytest 父进程不导入 src.web.app。每个案例用同一文件的 `--worker` 入口，
   通过 `sys.executable -B` 启动新子进程，45 秒超时。
2. 父进程只传递基础系统环境白名单；config / .env / user-config / locks /
   agent DB / task DB 全部指向 TemporaryDirectory，子进程 cwd 同为临时目录。
   不继承业务凭据或模型配置；不读取来源工作树资产。
3. 在首次导入应用之前屏蔽科学工具池及 SQLiteAgentStateStore 构造。
   无关分子设计/靶点路由注册在进程内隔离，避免读取片段库和靶点运行数据；
   对接对象的初始化目录落在临时 cwd，未调用对接或科学服务。
4. 使用且仅使用此新进程拥有的模块全局应用。不清理父进程或其他测试拥有的 app；
   用父进程已存在 app 的哨兵及父配置变量验证该所有权边界。
5. 保留真实 MolecularChatApp 静态挂载、main_routes、Jinja 和 StaticFiles；
   首页/活性页 HTML、脚本顺序和所有本地 script GET 的断言在子进程真实执行。
6. 不进入 TestClient 上下文，不触发 lifespan；显式追踪 initialize 调用为 0。
   HTTPX 同步/异步网络 transport 均设阻断哨兵，外发尝试必须为 0；
   ASGI TestClient 使用真实的本地 ASGI transport，不被替换。
7. ExitStack 的独立清理回调在正常和异常退出时关闭 TestClient、await app.shutdown、
   await 主模型 close、await 分子生成模型 close；之后子进程退出，
   父进程才移除临时目录。子进程失败时父进程仍通过 with 清除临时目录。
8. 清理探针检查每个被创建的 HTTP 客户端 is_closed，并验证 shutdown 与两个模型
   close 各调用一次；异常路径也必须满足相同条件。

补充探针实际 GREEN 数据：

```text
normal: tool_factory_calls=0 agent_db_created=False open_clients=0 test_clients=1
        shutdown_calls=1 model_close_calls=[1,1] startup_calls=0 network_attempts=0
        injected_error_seen=False temporary_removed=True
error:  tool_factory_calls=0 agent_db_created=False open_clients=0 test_clients=1
        shutdown_calls=1 model_close_calls=[1,1] startup_calls=0 network_attempts=0
        injected_error_seen=True temporary_removed=True
```

开发过程中增加外发保护时，最初全局阻断 socket.connect 也误伤了 Windows asyncio
内部 socketpair，产生一次 **7 failed, 5 passed**（20.16s）。
根据堆栈定位后改为只阻断 HTTPX 网络 transport，不修改生产代码或放宽页面断言；
以下是修正后重新执行的最终结果。network_attempts 指 HTTPX 外发尝试，不是所有内核 socket。

## 本轮实际命令与最终结果

环境：Windows；Conda MedChat Python 3.10.20，
FastAPI **0.135.3**、Starlette **1.0.0**、HTTPX **0.28.1**。
以下均在目的工作树执行，**无需手工 runner**：

```powershell
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -B -m pytest tests/test_static_placeholder_cleanup.py -q -p no:cacheprovider --tb=short

& C:/Users/xkx52/.conda/envs/MedChat/python.exe -B -m pytest tests/test_static_placeholder_cleanup.py tests/test_reverse_target_frontend_static.py tests/test_deployment_assets.py::DeploymentAssetsTest::test_deployment_files_exist tests/test_deployment_assets.py::DeploymentAssetsTest::test_main_uses_append_rotating_file_logging tests/test_deployment_assets.py::DeploymentAssetsTest::test_quality_workflow_covers_reproducible_offline_gates tests/test_deployment_assets.py::DeploymentAssetsTest::test_dependency_files_declare_distinct_supported_profiles -q -p no:cacheprovider --tb=short
```

- 单文件最终复跑：**7 passed in 19.93s**，退出码 0。
- 有限相关回归：**12 passed in 21.15s**，退出码 0。
- 两组均无 skipped。子进程 stderr 捕获，不把静默摘要称为应用没有任何日志警告；
  缺失临时 YAML 的配置回退是刻意设置，不是实际部署配置错误。
- 相关五项只检查静态代码/部署契约；本轮不重复整份 deployment tests 和
  phase2_phase3 routes tests，因为其其他案例会另行创建应用或检查运行资产。
  首轮 13 passed 不重复算入本轮普通 pytest 结果。
- 没有执行旧 Starlette CI 组合、完整 Python/Agent 科学验收、浏览器交互、
  CDN 联通性、真实模型或生产 health_check --strict。

语法/编译和 diff（通过，缓存留在临时目录）：

```powershell
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -B -c 'import runpy, sys, tempfile; temp = tempfile.TemporaryDirectory(prefix="medchat-t06b-pyc-"); sys.pycache_prefix = temp.name; sys.argv = ["compileall", "-q", "src", "scripts", "tests/test_static_placeholder_cleanup.py"]; runpy.run_module("compileall", run_name="__main__")'
git diff --check
git diff --no-index --check -- NUL tests/test_static_placeholder_cleanup.py
git diff --no-index --check -- NUL docs/handoff/static-backup-cleanup-integration.md
```

no-index 对新增内容返回差异退出码 1 不等于 whitespace 错误；
最终两份新文件均无 whitespace 错误输出。

## 保留项与发布前交接

- 两个占位文件继续保留并由自动测试验证 HTTP 200、执行内容只有 strict。
- latest.md 未改；main_routes.py 与 HEAD 一致。
  main_routes.py SHA-256：
  `84A2B7CE07993D978ADED74C7E45FDFE9960CC9BA52D8B7960C855B29692DE77`。
- 目的 script.js / activity_prediction_v2.js / latest.md 的 SHA-256 与首轮开始一致：
  `75DA07DF4CB37EAB8254B8AD6C838763C084A1CCB56CFCAD7EF88F212C130FFD` /
  `C3BC7D3A8275BE62464EAD16F69B0CF855D65A8970CBF6F02229E2E3E1D46DA5` /
  `0C4BE1A1AE323E11756FBBD6145543BF369066A884C9095688543CA96AF0BA59`。
- 来源首轮开始/结束 Git 状态、HEAD 及三份未提交文本的 hash 一致；
  本轮没有在来源执行写入，未复写其旧测试或旧报告。
- 仅历史备份 URL 的 404 是有意兼容变化；历史复制备份指令仍在旧设计文档中，
  不应为了执行旧计划而把备份重新放回公开静态目录。
- 父任务应对本轮两份文件独立 SPEC / QUALITY 复审；集成模板修复并确认最终 main
  后重新跑普通 pytest 与 CI。不能因本机新版 7/12 项通过而撤销旧 Starlette 阻断。

## 修复 delta 独立复审追加

原 SPEC、QUALITY 两位审查者分别复审上述自动隔离变更，均 APPROVED；原测试自身隔离/清理 P2 阻断解除。SPEC普通pytest有限回归12 passed，QUALITY单文件7 passed / 21.33秒。确认泄漏断言在兜底清理之前执行，不会因强制关闭客户端而掩盖真实未关闭。

旧 Starlette 模板依赖仍为发布阻断；本地新版通过不替代CI。父任务仅精确提交三个允许路径并对齐当前main，暂不推送/创建PR，等待模板兼容修复。上文未提交为原实现交接时点。
