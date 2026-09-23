# T01 有界补丁集成（2026-09-23）

## 状态与边界

T01 已迁入目的工作树。用户随后授权仅增加
`tests/agent/test_registration_consistency.py` 测试白名单；4 项旧 fixture 已迁移。
最新五文件聚焦回归 **347 passed**，原补充 registration/session 集合
**355 passed、5 skipped、0 failed**。不是全量测试或可直接合并结论；
仍待父任务独立 SPEC / QUALITY 审查。下文保留迁移前的真实 RED 记录。

- 目的：`D:/MedChat/molecular_chat_system_worktrees/rag-parity-integration-pr`。
- 分支：`codex/rag-parity-integration-pr`。
- 开始及结束 HEAD：`7ee6a6fffec55b97872587befca635ef390e3f21`。
- 来源：`D:/MedChat/molecular_chat_system_worktrees/rag-row-mapping-parity`，
  HEAD `d89a48b803fb0dcfdff95539513de725ca36756f` 的未提交 T01。
- 开始时目的树干净。先读两树 AGENTS、项目规范、来源 handoff、diff 和相关测试。
- 所有编辑通过 apply_patch，app.py / chat_handler.py 按 hunk 迁移，没有整文件覆盖。
- 来源只读；开始/结束对其 10 个 T01 代码、测试及交接文件做 SHA256 比较，
  `Source hashes unchanged: 10/10`，来源 HEAD / status 也未变。
- 未迁移来源 `docs/handoff/latest.md`；未带入 T03/T05/T06 其他未集成补丁。
- 无暂存、commit、push、PR、merge、部署、服务启动或真实模型调用。
  未读取密钥文件或生产运行资产；离线用例使用临时 CSV、FAISS、SQLite 和 mock transport。
- 历史来源报表不计入本次证据；以下数字全部为本次目的树实际运行。

## 准确文件清单

新增：

1. `src/rag/retrieval.py`：共享 manifest 校验、查询向量检查及 vector label → source row 投影。
2. `src/web/rag_presentation.py`：来源元数据与展示 properties 分离，统一上下文格式。
3. `docs/handoff/rag-parity-integration.md`：本报告。

修改：

1. `src/agent/tools/__init__.py`：关键字参数 rag_system 贯穿可选工具及全工具工厂。
2. `src/agent/tools/rag_search_tool.py`：改为注入服务的同步适配器，移除全局 app 回查及第二套检索；保留目的 registration_health。
3. `src/web/app.py`：同步/异步查询共用核心；保存已验证索引代际哈希；统一 embedding endpoint；工厂显式注入；小型展示接线。
4. `src/web/chat_handler.py`：仅 RAG 展示和上下文接线，两个消息分支保留结构化 provenance。
5. `tests/agent/test_app_supervisor_entrypoint.py`：验证工厂实际收到同一个 rag_system 与 generator。
6. `tests/test_rag_index_manifest.py`：迁移跳行、非法 manifest/向量、无全局导入、端点、双聊天路径及资源关闭回归；补注册/session 整合用例。
7. `tests/agent/test_registration_consistency.py`：后续授权迁移原4项 canonical/legacy × ready/unready fixture，详见末节。

没有修改扩展白名单外源码或测试。后续轮次仅修改 registration 测试和本报告，
此前8个 T01 代码/测试文件 SHA256 全部未变，未新增生产实现修改。

## 兼容整合点

- **T04**：registration_health 原函数 AST 未变；_create_supervisor_agent 的审计、别名解析、
  `tools={}` 和 registry 所有权 AST 未变。旧的“工厂构造后逐工具回填 rag_system”
  三行由显式工厂注入替代，不再保留第二条接线路径。
- 来源的工厂用例曾将 core tools 替换为空列表，不适合目的树 required-tools 审计。
  迁入时改为 REQUIRED_TOOLS 名称的惰性测试对象，不放宽生产审计。
- 新增 4 个参数化工作流用例覆盖 ready/unready × canonical/legacy tool name，
  使用真实 RAGSystem + 临时 manifest/index、真实两级 app 工厂、registry、specialist、
  Supervisor.run 和 HTTP workflow route；验证别名同一对象、ready 健康状态、来源 SHA、
  未就绪的 tool_unavailable，以及服务端 owner_session_id 和用户 metadata 身份过滤。
- **P05**：setup_agent_sessions、WebSocket session_id 传递、_execute_agent 参数与实现保留；
  workflow route 的后台任务 owner_session_id / handler 闭包未改。
- **T08**：_route_clarification AST 与目的 HEAD 一致，target clarification/context 保留。
- **科学/资源**：rag_index.py 的 manifest/hash 验证、immutable snapshot、原子写入不变；
  shutdown AST 不变。查询只校验，不重建索引；同步 httpx.Client 按调用关闭，
  成功与 HTTP 503 路径均有回归。
- 普通异步 search 仍返回列表，不可用时返回 [] 并告警；Agent 工具不可用返回
  success=false，合法无命中才返回 success=true/data=[]。
- 新增 source_index/provenance 在 rag_info 分子对象顶层，既有消息类型不改。
  embedding_endpoint 的旧构造参数只能确认注入服务的相同端点，不能另设旁路。
- ChatHandler / app 的 prompt 构建方法 AST 未变，没有顺手实施其他截断/生命周期改造。

## 本次 TDD 与真实输出

工作目录始终为目的树。解释器固定为 MedChat Python，所有 pytest 均 `-B`、
`-p no:cacheprovider`，且真实验收开关均为 0。每轮单独设置临时 Agent 状态库；
既有 tests/conftest.py 在收集前隔离用户配置、env 文件、锁目录与 session DB。

每条 pytest 命令前的 PowerShell 设置（`PHASE` 实际分别为 baseline/red/green/compat）：

```powershell
$env:MEDCHAT_RUN_FAMILY_REAL_ACCEPTANCE='0'
$env:RUN_REAL_TARGET_SEARCH='0'
$env:AGENT_STATE_DB=Join-Path $env:TEMP ('medchat-t01-PHASE-' + [guid]::NewGuid().ToString() + '.sqlite')
```

### 基线：尚未迁入任何测试/实现

```powershell
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -B -m pytest tests/test_rag_index_manifest.py tests/agent/test_app_supervisor_entrypoint.py tests/agent/test_chat_handler_agent_events.py tests/test_web_app_lifecycle.py tests/agent/test_task_planner.py tests/agent/test_registration_consistency.py -q -p no:cacheprovider --tb=short
```

exit 0：`351 passed, 4 warnings in 11.05s`。

### RED：只迁入/补齐允许的两个测试文件，生产代码仍是目的 HEAD

```powershell
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -B -m pytest tests/test_rag_index_manifest.py tests/agent/test_app_supervisor_entrypoint.py -q -p no:cacheprovider --tb=short
```

首轮 exit 1：`28 failed, 27 passed, 5 warnings in 8.29s`。
新进程测试捕获输出时碰到 Windows GBK 解码告警；在同一允许测试文件显式设置
UTF-8 + errors=replace 后重跑，未改生产实现：
exit 1：`28 failed, 27 passed, 4 warnings in 8.39s`。

关键真实失败摘录：

```text
test_index_records_source_row_mapping_and_search_resolves_faiss_label
E   AssertionError: assert 'CCN' == 'CCC'
test_chat_rag_info_preserves_structured_provenance[False]
E   KeyError: 'source_index'
test_endpoint_compatibility_argument_cannot_override_service
E   AttributeError: 'RAGSystem' object has no attribute 'embedding_endpoint'
test_chat_agent_factory_returns_supervisor_with_local_generator
E   TypeError: ...tools_factory() missing 1 required keyword-only argument: 'rag_system'
```

其他失败涵盖非法 manifest 仍返回记录、缺失/变化来源旁路、非法向量、
无注入时导入全局 Web app、同步客户端接口缺失、workflow 结果没有来源 SHA。

### GREEN：最小实现 hunks 迁入后

```powershell
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -B -m pytest tests/test_rag_index_manifest.py tests/agent/test_app_supervisor_entrypoint.py tests/agent/test_chat_handler_agent_events.py tests/test_web_app_lifecycle.py tests/agent/test_task_planner.py -q -p no:cacheprovider --tb=short
```

exit 0：`347 passed, 4 warnings in 10.16s`。
包含新增 4 个整合用例；未把来源历史通过数计入。

### 首轮补充 registration/session 回归（历史 RED，后续已解决）

```powershell
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -B -m pytest tests/agent/test_registration_consistency.py tests/test_agent_session.py tests/test_agent_session_entrypoints.py tests/test_agent_task_ownership.py tests/agent/test_browser_session_integration.py tests/agent/test_run_session_ownership.py -q -p no:cacheprovider --tb=short
```

exit 1：`4 failed, 351 passed, 5 skipped, 7 warnings in 41.05s`。
四项均为 `test_default_app_factory_rag_workflow_api` 参数化用例，失败在
`tests/agent/test_registration_consistency.py:42`：

```text
E ImportError: import error in src.agent.tools.rag_search_tool.requests:
  No module named 'src.agent.tools.rag_search_tool.requests';
  'src.agent.tools.rag_search_tool' is not a package
```

该 mock 绑定已移除的旧工具 HTTP 实现。该测试还使用不接受 rag_system 关键字的
get_all_tools lambda，以及没有共享同步检索接口/manifest 的旧 SimpleNamespace fixture；
仅重新导入 requests 不能完成契约迁移。不为迎合测试恢复弱检索路径。

为准确收集 skip 原因，保持文件集合不变重跑：

```powershell
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -B -m pytest tests/agent/test_registration_consistency.py tests/test_agent_session.py tests/test_agent_session_entrypoints.py tests/test_agent_task_ownership.py tests/agent/test_browser_session_integration.py tests/agent/test_run_session_ownership.py -q -p no:cacheprovider --tb=line -rs
```

exit 1：`4 failed, 351 passed, 5 skipped, 7 warnings in 37.95s`。
两轮不累加计数。实际 skip 输出：

```text
SKIPPED [1] tests/test_agent_session.py:113: POSIX directory permission semantics
SKIPPED [1] tests/test_agent_session.py:121: POSIX directory permission semantics
SKIPPED [1] tests/test_agent_task_ownership.py:177: requires two independent task stores
SKIPPED [1] tests/test_agent_task_ownership.py:201: requires a configured runtime
SKIPPED [1] tests/test_agent_task_ownership.py:218: requires two independent task stores
```

## 静态检查与未决

- `git diff --check`：exit 0，无输出。
- 对 8 个改动 Python 文件内存 compile：`In-memory syntax compilation: 8/8 passed`。
- 对跟踪的 src/scripts Python 源码、2 个新模块和2个改动测试内存 compile：
  `In-memory syntax compilation: 300 Python files passed; no bytecode written`，exit 0。
  命令如下；未运行会写 __pycache__ 的 compileall，以遵守有界写入。

```powershell
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -B -c 'import pathlib, subprocess; paths = [p for p in subprocess.check_output(["git", "ls-files", "src", "scripts"], encoding="utf-8").splitlines() if p.endswith(".py")]; paths += ["src/rag/retrieval.py", "src/web/rag_presentation.py", "tests/agent/test_app_supervisor_entrypoint.py", "tests/test_rag_index_manifest.py"]; [compile(pathlib.Path(p).read_bytes(), p, "exec") for p in paths]; print(f"In-memory syntax compilation: {len(paths)} Python files passed; no bytecode written")'
```

- AST 对照 HEAD：7 个 registration/supervisor/shutdown/prompt/session 函数一致，
  另验 T08 `_route_clarification` 一致。首次一次性探针写错函数名 `_run_agent_async`
  导致探针断言失败；按实际 `_execute_agent` 重跑通过，无源码变更。
- `git diff --exit-code HEAD -- src/web/rag_index.py src/web/routes/agent_workflow_routes.py src/web/agent_session_config.py src/agent/tooling docs/handoff/latest.md`：exit 0，无输出。
- 首轮受白名单限制未改4个旧 registration 用例；随后用户明确授权并完成迁移，
  见末节。未新增 skip/xfail，也未用新测试替代或删除原矩阵。
- 全量 pytest、前置 PR CI/合并、真实模型/生产资产验收、部署/health_check、性能基准均 not_run。
  无 JS 改动，未跑 Node 测试；4 个 FastAPI 弃用警告与基线一致，补充回归另有3个 SWIG 警告。
- 独立 SPEC / QUALITY 审查未执行，由父任务后续完成；来源历史双审不代表本次审查。
- 新 commit：无；PR：无；所有补丁留在目的独立分支的未暂存工作区。

## 后续授权：修复4个过时 registration fixture

仍在上述目的树 / 分支 / HEAD 上操作。仅编辑
`tests/agent/test_registration_consistency.py` 与本报告；来源树未操作。

### fixture 与防退化断言

- 保留原 `legacy_name=[False, True]` × `ready=[True, False]` 四象限，测试名称不变。
- 三行临时 CSV：CCC / CCN / CCO；真实 FAISS IndexFlatIP 仅两条向量，
  合法 manifest 为 `row_mapping=[0,2]`。atomic_save_index_pair 计算真实索引哈希，
  RAGSystem._load_or_create_index 从临时磁盘加载并经过实际 validator。
- HTTP 仅替换 `httpx.HTTPTransport.handle_request` 为 MockTransport；未替换
  HTTP client、get_embedding_sync、共享检索、FAISS search 或 manifest validator。
  验证 POST 端点、embedding model 与 query；ready=false 时包括加载、注册和 API
  全路径 calls=[]，若触发 transport 会立刻断言失败。
- 替换过时 lambda 为接受关键字 rag_system 的 tools_factory，断言服务及 generator
  对象身份，再调用真实 get_all_tools / get_optional_tool；仅限制可选列表为 RAG。
- 保留真实 app._create_chat_agent / _create_supervisor_agent、HTTP route、
  Supervisor/registry/tool 执行路径；后台 Manager 仍仅作为同步调度替身。
- 断言 factory 只调用一次、注册报告无错误、canonical/alias 同一 adapter、
  tool/服务为原对象、availability 等于 ready；保留 unready 的 unavailable_tools
  与 tool_unavailable 断言及旧 fixture-db 来源断言。
- 从实际 API JSON tool_result_sequence 断言结果 CCO / CCC、source_index=[2,0]、
  vector_label=[1,0]、首条 similarity≈1，以及完整 provenance：真实 source_path、
  source SHA256、index SHA256、embedding_model、manifest schema、builder version。
  若退化为 iloc(vector_label)，首条会错成 CCN，本断言必然失败。
- 未恢复 `rag_search_tool.requests` 或全局 app_instance 旁路；未遇到需要修改生产代码的业务缺陷。

### 后续 RED → GREEN 命令与输出

解释器及隔离设置与前述相同。每轮实际使用以下前缀（PHASE 分别为
red / green / compat / focus），保留两项真实验收开关为0：

```powershell
$env:MEDCHAT_RUN_FAMILY_REAL_ACCEPTANCE='0'
$env:RUN_REAL_TARGET_SEARCH='0'
$env:AGENT_STATE_DB=Join-Path $env:TEMP ('medchat-t01-fixture-PHASE-' + [guid]::NewGuid().ToString() + '.sqlite')
```

修改 fixture 前与修改后各运行一次同一命令：

```powershell
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -B -m pytest tests/agent/test_registration_consistency.py::test_default_app_factory_rag_workflow_api -q -p no:cacheprovider --tb=short
```

- RED exit 1：`4 failed, 7 warnings in 3.20s`，均为旧 requests mock ImportError，
  与首轮4项失败完全相同；是在 fixture 修改前重新取得，不覆盖历史证据。
- GREEN exit 0：`4 passed, 7 warnings in 4.12s`，没有 skip/xfail。

原补充集合完整重跑：

```powershell
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -B -m pytest tests/agent/test_registration_consistency.py tests/test_agent_session.py tests/test_agent_session_entrypoints.py tests/test_agent_task_ownership.py tests/agent/test_browser_session_integration.py tests/agent/test_run_session_ownership.py -q -p no:cacheprovider --tb=short -rs
```

exit 0：`355 passed, 5 skipped, 7 warnings in 41.11s`。
五项 skip 的位置及原因与首轮 -rs 完全一致；不是新增跳过，也不计为通过。

原五文件聚焦完整重跑：

```powershell
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -B -m pytest tests/test_rag_index_manifest.py tests/agent/test_app_supervisor_entrypoint.py tests/agent/test_chat_handler_agent_events.py tests/test_web_app_lifecycle.py tests/agent/test_task_planner.py -q -p no:cacheprovider --tb=short -rs
```

exit 0：`347 passed, 4 warnings in 12.51s`。

后续静态检查：`git diff --check` exit 0；registration 测试内存 compile exit 0，
输出 `registration test syntax passed; no bytecode written`。前轮8个已有 T01 文件
开始/结束 SHA256 比较 `Existing T01 source/tests unchanged: 8/8 SHA256 checks passed.`
最终扩展白名单共10个变更路径，无暂存；HEAD 未变，仍无 commit / push / PR / 部署 / 真实模型调用。

## 独立集成复核（2026-09-23 追加）

上文未执行审查的状态属于实现者交接时点。父任务随后完成两阶段独立复核：

- SPEC：APPROVED，实际模块/入口组合 702 passed、5 项既有 skipped，另跑 T07/T08 154 passed。
- QUALITY：APPROVED，精确回归40 passed；额外离线并发探针8 async和8 sync结果一致，同步HTTP客户端全部关闭；legacy alias执行只检索一次。
- 无本批阻断项。极端有限向量归一化失真、原始数据属性非有限值、长期async embedding客户端关闭仍为既有残差，审查未声称本批修复。
- 审查基线均为 `7ee6a6f` 加上述10文件。PR #37 的独立task-runtime/模板CI失败不属于本批审查通过范围，依赖解决前不向main发布此累计分支。

父任务运行同候选完整 Agent 离线回归，使用上述 MedChat Python：

```powershell
$env:MEDCHAT_RUN_FAMILY_REAL_ACCEPTANCE='0'
$env:RUN_REAL_TARGET_SEARCH='0'
$env:AGENT_STATE_DB=Join-Path $env:TEMP ('medchat-t01-agent-' + [guid]::NewGuid().ToString() + '.sqlite')
$env:MEDCHAT_TASK_DB_PATH=Join-Path $env:TEMP ('medchat-t01-tasks-' + [guid]::NewGuid().ToString() + '.sqlite')
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -B -m pytest tests/agent -q -p no:cacheprovider --tb=short -rs
```

退出0：**3700 passed、2 skipped、7 warnings，212.73秒**。两项跳过为本机目录符号链接不可用和既有默认关闭性能检查；7项警告为SWIG/FastAPI弃用。不是全仓、Linux CI或真实科学验收。

双审与最终回归后由父任务精确暂存上述10文件形成本地提交，未推送或创建PR；来源工作树保持不动。上文“未提交”记录保留其历史时点含义。
