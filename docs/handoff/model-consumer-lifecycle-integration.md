# T05-B 主模型生命周期：已审查补丁精确集成

## 状态与边界

- 当前日期：2026-09-24。原 QUALITY NOT APPROVED 的两个资源收尾缺口已按 TDD 修复；当前离线验证为 4145 passed / 8 skipped，等待原 SPEC / QUALITY 重审，不代表 APPROVED。首轮 2026-09-23 的记录保留为历史。
- 目的：`model-consumer-lifecycle-integration-pr` 工作树，分支 `codex/model-consumer-lifecycle-integration-pr`。
- 起止 HEAD：`24838f2edff93222718fe50ba377f8f7c6e56478`；开始时工作树干净。
- 来源：只读 `model-consumer-lifecycle` 工作树，HEAD `d89a48b803fb0dcfdff95539513de725ca36756f`。
- 先读两端 AGENTS / PROJECT_STANDARDS、来源 handoff 与实际 diff；两端规范文件对比一致。
- 全部文件编辑经 apply_patch；不整文件覆盖旧基线，不 stage / commit / push / PR / merge / deploy。
- 不修改并行的 legacy-chat-entry-cleanup-integration-pr 工作树；旧方法清理留给父任务独立合并。
- 无外部模型调用、网络模型验收、真实权重加载或科学结果声明；新增测试显式构造应用前替换主模型工厂及本地生成客户端，使用本地 ASGI transport。

## 精确写集（累计 12 路径）

- `src/molecular_design/service.py`
- `src/task_runtime/manager.py`
- `src/web/app.py`
- `src/web/chat_handler.py`
- `src/web/routes/agent_workflow_routes.py`
- `src/web/routes/design_routes.py`
- `src/web/model_lifecycle.py`
- `tests/test_design_model_switch.py`
- `tests/test_model_request_lifecycle.py`
- `docs/handoff/model-consumer-lifecycle-integration.md`
- `src/web/models/ollama_model.py`（2026-09-24 新批准，仅资源收尾）
- `src/agent/openai_compatible_model.py`（2026-09-24 新批准，仅资源收尾）

未修改旧 `docs/handoff/latest.md`、`src/web/prompt_budget.py`、现有 tests/conftest.py 或其他 fixtures / 路径。

## 迁入的逻辑

1. 设计路由显式 model_provider，每次请求进入后只解析一次；保留直接 model 注入及 standalone 构造兼容。
2. 聊天保存请求级 model，流式、非流式、流失败重试、输出预算和 completion metadata 使用同一对象；helper 的新增 model 参数可省略。
3. ModelRequestGate 保持正常读者并发；等待切换的 writer 暂停新准入，排空已存在的消费者再替换配置。
4. TaskManager.wait_for_completion 等待真正 executor Future；后台 lease 从提交前持有至 worker 结束。预先固定 task_id，入队后回执读取失败也必须等 worker。
5. executor 线程、关停清理与临时连接测试使用取消保护；工作完成 / 资源清理后传播取消。
6. 连接测试临时模型 finally 清理；切换关闭退休主模型；关停排空后关闭当前主模型与独立生成模型，保留已有工具清理。

来源新增 model_lifecycle.py、service.py、design_routes.py 与目的对应文件 SHA-256 一致；其余代码按 hunk 集成，保留目的新基线。

## 保留的基线及新增集成断言

- T05-A：应用主模型工厂构造一次的断言通过；未重复删除初始化，也未让主模型覆盖独立的本地生成模型。
- P05 / T07：workflow/run 从 request.scope 读取 agent_session_id；清理 metadata 身份字段；supervisor.run(session_id=...) 与 submit(owner_session_id=...) 均保留。
- 新后台测试使用真实 AgentSessionMiddleware / AgentSessionStore，HTTP localhost 请求自然创建会话；未 mock 首页、删除鉴权或伪造 scope。
- 后台测试通过 TaskStore.get_agent_owner 检查实际持久化 owner，断言 Supervisor 收到同一服务端身份；请求中伪造的 session_id / user_id / owner_session_id 不被采纳。
- 后台取消测试将实际 SQLite 记录合法转到 CANCELED，同时断言 executor Future 尚未完成；writer 必须继续等待。另覆盖排队 Future 取消、排队正常执行和回执失败。
- 聊天保留 _process_message / _execute_agent 的 session_id 参数及传递；组装应用的执行、配置切换和取消测试联合断言模型对象与 inference 在完成前保持不变，取消最终向调用者传播。
- T03：流 / 非流 / 重试测试同时检查有界提示、完整历史内容及历史时间顺序，并检查请求 model 的 finish_reason，不读替换客户端的 metadata。
- T01 / T08：保留显式工具注册、共享 RAG 注入和既有靶点逻辑；未修改其实现。
- AST 对比 HEAD 与最终代码确认以下不变：RAGSystem；应用 _create_chat_agent / _create_supervisor_agent / _create_model_from_llm_config / _build_prompt / _build_prompt_with_agent；ChatHandler _input_limit / _build_prompt / _build_prompt_with_agent。

## 首轮 TDD 与验证记录（真实历史结果，非本轮快照）

| 阶段 | 结果 | 解释 |
|---|---|---|
| 先迁两份测试并适配服务端会话、T03 断言，尚未迁生产实现 | 18 failed，7 warnings，5.89 秒 | provider 参数缺失、metadata 混用、重试换客户端、切换提前完成、临时客户端未关闭、取消提前返回；新生命周期模块尚不存在的用例在导入处失败。不是 collection failure。 |
| 部分 hunk 集成后的中间验证 | 9 failed / 9 passed，7 warnings，4.52 秒 | 旧上下文与 T03 不匹配，漏迁 ChatHandler 导入 / helper；新增 owner 断言误把私有持久化字段当作公开 TaskRecord 属性。补齐原补丁 hunk，并按既有 get_agent_owner API 修正测试；未扩展生产契约。 |
| 聚焦两文件 | 18 passed，7 warnings，5.50 秒 | 上述失败已消除。之后只补强测试 finally 清理及取消传播断言，未再改生产代码。 |
| 最终同一源码快照联合回归 | **4113 passed / 8 skipped / 7 warnings，253.94 秒** | 包含下列全部 16 组；包含最后测试清理 / 取消断言修改。不引用来源历史 3371 结果。 |
| 内存 compile | 302 个 Python 文件通过 | src、scripts 与新增两份测试；没有写 pyc。首个 PowerShell→Python JSON 管道因 Windows 字符编码失败，明确 UTF-8 后通过，未改源码。 |
| git diff --check | 通过 | 包括最后交接文档后复核。 |
| 暂存检查 | 空 | git diff --cached --exit-code 通过。 |

最早的 shell 临时目录删除包装在测试启动前被工具拒绝，未产生测试结果；改用 TemporaryDirectory 管理临时 DB 的子进程启动器。没有因负载修改墙钟阈值，没有盲重跑；中间失败均保留根因和记录。

### 最终联合回归命令

解释器：`C:/Users/xkx52/.conda/envs/MedChat/python.exe`，目的工作树为工作目录。
根 tests/conftest.py 照常由 pytest 自动加载，隔离用户配置 / 会话库。
以下启动器只管理运行时临时数据库，不修改仓库文件；执行普通 `-B -m pytest`，未使用 pytest-timeout。

```powershell
$env:MEDCHAT_RUN_FAMILY_REAL_ACCEPTANCE='0'
$env:RUN_REAL_TARGET_SEARCH='0'
$env:PYTHONDONTWRITEBYTECODE='1'
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -B -c "import os, sys, tempfile, subprocess; from pathlib import Path; tmp = tempfile.TemporaryDirectory(prefix='medchat-t05b-'); root = Path(tmp.name); os.environ['AGENT_STATE_DB'] = str(root / 'agent.sqlite'); os.environ['MEDCHAT_TASK_DB_PATH'] = str(root / 'tasks.sqlite'); result = subprocess.run([sys.executable, '-B', '-m', 'pytest', *sys.argv[1:]]); tmp.cleanup(); sys.exit(result.returncode)" tests/agent tests/test_model_request_lifecycle.py tests/test_design_model_switch.py tests/test_molecular_design_architecture.py tests/test_llm_runtime_config.py tests/test_user_llm_routes.py tests/test_agent_llm_wiring.py tests/test_task_runtime.py tests/test_phase2_phase3_routes.py tests/test_agent_anti_hallucination_fallbacks.py tests/test_agent_platform_health_check.py tests/test_agent_session.py tests/test_agent_session_entrypoints.py tests/test_agent_task_ownership.py tests/test_rag_index_manifest.py tests/test_web_app_lifecycle.py -q -p no:cacheprovider --tb=short -rs
```

聚焦及 RED 使用同一启动器，只把测试选择替换为：
`tests/test_design_model_switch.py tests/test_model_request_lifecycle.py`。

最终 8 个 skip：

- tests/agent/test_decision_chat_acceptance.py:149：directory symlinks unavailable。
- tests/agent/test_harness_shadow.py:277：performance test disabled。
- tests/test_llm_runtime_config.py:33：Windows symlink 权限不足（WinError 1314）。
- tests/test_agent_session.py:113、121：POSIX directory permission semantics，共 2 个。
- tests/test_agent_task_ownership.py:177、218：requires two independent task stores，共 2 个。
- tests/test_agent_task_ownership.py:201：requires a configured runtime。

7 个 warning 为 SWIG 类型 / FastAPI on_event 的既有弃用提示。
本轮没有修改 JS、部署或数据，无新增 Node / 部署验收要求；不启动可能依赖真实服务的健康检查。

## 来源不变证据

开始与完成复核：HEAD、11 条 git status（含未跟踪文件）及以下全部 SHA-256 一致。
来源从未作为工作目录执行测试或写入命令，只运行读取 / Git 查询。

```text
 M docs/handoff/latest.md
 M src/molecular_design/service.py
 M src/task_runtime/manager.py
 M src/web/app.py
 M src/web/chat_handler.py
 M src/web/routes/agent_workflow_routes.py
 M src/web/routes/design_routes.py
?? docs/handoff/model-consumer-lifecycle.md
?? src/web/model_lifecycle.py
?? tests/test_design_model_switch.py
?? tests/test_model_request_lifecycle.py
```

| 来源路径 | SHA-256（起止一致） |
|---|---|
| `docs/handoff/latest.md` | `59CEF25949020D87EC08D84850DE1740FCE3F2AE2EAC5CFB2434016834CE0B83` |
| `src/molecular_design/service.py` | `7CE8534C5393EB4B5A536DC60CA616591223DE3FAA050E8C0E2F7EACE2CFA9DC` |
| `src/task_runtime/manager.py` | `95CAE4D8CB09F0336051B0440DA8E48706DC759C15DC66038BF664097FF6410F` |
| `src/web/app.py` | `1A5B8D8162EAF10F7B562ADFC9B62AFCD40E542459E40F3922BCC695FE97F319` |
| `src/web/chat_handler.py` | `1098189A2292331F0865A2C58843B2C58EC51A226D96258443ECA8F0B15004EF` |
| `src/web/routes/agent_workflow_routes.py` | `8465A9B2E56BB273BAF8E28B232A5747BD1B3C51D90EFE4D299BB50D37EB93A0` |
| `src/web/routes/design_routes.py` | `F06C7A0587951A7F1214F6C4DBD01FE764AA98CA218C819D683FF425634FB76B` |
| `docs/handoff/model-consumer-lifecycle.md` | `285578BD120343A5F3C180BB7A70A2815A613C6364357FACF0164FC0BF598D27` |
| `src/web/model_lifecycle.py` | `D25DBFD4BCFF8AABA8C8514C82A11E183D17352B79AD179BE333B199CBF4A666` |
| `tests/test_design_model_switch.py` | `FCAE39BD9B3C3B244D6C6F609DC05C41CBCA3A8E01EAAC12709E2E12A89B7F09` |
| `tests/test_model_request_lifecycle.py` | `9E2F3F68DDDB75AD24AFA1B7A1A4EC18077191FAB4B598CEEF30CA6D01888821` |

## 首轮 4113 联合回归的源码 / 测试快照（历史）

首轮回归期间与结束后下列 9 路径指纹一致；当轮回归后只新增本文。2026-09-24 复审修复后的当前快照见文末，不沿用这些历史哈希。

| 目的路径 | SHA-256 |
|---|---|
| `src/molecular_design/service.py` | `7CE8534C5393EB4B5A536DC60CA616591223DE3FAA050E8C0E2F7EACE2CFA9DC` |
| `src/task_runtime/manager.py` | `FF9263382F4E89FF60476D1C12EF98CE9C6C34A4D1E26014DC112273CA474860` |
| `src/web/app.py` | `82D84551637C6A93F2281A5F8CCE81CBAD275388E8FF6E1E57AE5269F42CCD2B` |
| `src/web/chat_handler.py` | `166DB054283D5E3835E95E9D082213AF6EC05B7AE2ACBA850C056DBC064246BC` |
| `src/web/routes/agent_workflow_routes.py` | `99D81B13FCDB375E435A25353660557E7FC32B583CDE407271604A4638E57F04` |
| `src/web/routes/design_routes.py` | `F06C7A0587951A7F1214F6C4DBD01FE764AA98CA218C819D683FF425634FB76B` |
| `src/web/model_lifecycle.py` | `D25DBFD4BCFF8AABA8C8514C82A11E183D17352B79AD179BE333B199CBF4A666` |
| `tests/test_design_model_switch.py` | `CA7E4D418CB2370F7880A3FC589C38C350CD38923CD9D2A3424B9F83837133AB` |
| `tests/test_model_request_lifecycle.py` | `54A3167F5BE4A12C157BCD1CBAA4B540B60D1C557783181DB11C7CDDEE7D8F37` |

## 留给独立 SPEC / QUALITY 与父任务

- 本批是已批准来源补丁集成，不引入多模型同时服务或新的强制终止设计。
- 永久挂起的 worker 会阻塞排空；没有强关在途模型，也不把 DB 终态视为实际完成。
- 生命周期保证针对应用组装入口，standalone 外部注入对象仍由调用者负责。
- 生产模板新旧 Starlette 兼容不在写集：按协调信息，本机新版支持测试，旧 CI 兼容仍阻塞 PR37 发布；本批未修复或声称解除该阻塞。
- 依赖尚未合并；不得发布累计历史。父任务后续合并旧入口清理时须逐 hunk 保留本批生命周期修改。
- 当前无新增 commit、PR、暂存、推送或合并。完成后停写，等待独立 SPEC / QUALITY。

## 2026-09-24：QUALITY NOT APPROVED 后的有界资源收尾修复

### 审查状态与本轮范围

原 QUALITY Popper NOT APPROVED 已确认；旧 18 测试 GREEN / 4113 联合结果不能反证此次两个缺口，均保留为历史快照。本轮已按 TDD 修复并完成下述离线验证，**仍等待原 SPEC / QUALITY 独立复审，不自行标记 APPROVED**。

用户新增批准两路径：`src/web/models/ollama_model.py`、`src/agent/openai_compatible_model.py`，只允许资源收尾。累计写集为本报告顶部的 12 路径。本轮相对上一轮仅修改：

- ChatHandler 的流消费收尾；
- OpenAI adapter 的两条流分支收尾；
- Ollama adapter 的双客户端关闭；
- 原新增两份测试及本交接文档。

其他原集成路径没有再次修改。来源始终只读；没有改其他 fixtures、prompt_budget、身份、共享 RAG、科学逻辑或并行工作树。

### 缺口与最小修复

1. 消费者异常 / 取消并不会自动等待 async generator 关闭。ChatHandler 现在保存请求拥有的 stream，在消费循环 finally 中检测可选 aclose，并通过既有 finish_on_cancel 等待收尾后才退出请求 lease。无 aclose 的异步迭代器仍兼容。
2. OpenAI 外层 stream_generate 的共享 client、临时 owned client 两分支均以标准 contextlib.aclosing 包裹 _stream_with_client；先等子流 / HTTP response 收尾，再退出临时 client 上下文。**adapter 无 src.web.model_lifecycle 反向依赖**；取消保护属于 web 请求 / 调用者所有权层。
3. OllamaModel.close 仅增加 try/finally：异步 client 关闭失败或取消时，仍尝试独立 sync_client.close。直接 close 的原 RuntimeError / CancelledError 保持传播，不转换为成功；应用原有 owner 层仍只记录固定脱敏清理警告。

没有改 API / SSE / Ollama 协议、重试策略、凭据处理或科学逻辑。AST 对比确认 OpenAI _stream_with_client / _payload / generate / close，以及 Ollama generate / generate_async / stream_generate 与 HEAD 完全相同；前轮受保护的 RAGSystem、Agent 构造 / 共享 RAG 注入、T03 提示方法同样未变。

### 实际 adapter 与资源边界测试

- 使用实际 OllamaModel.stream_generate / generate_async / close 和 OpenAICompatibleModel；仅以 httpx.MockTransport 隔离网络。
- AsyncByteStream.aclose 用 entered / release / finished 事件控制；通过有限等待和 finally 无条件 release 清理，不以机器墙钟性能判定。
- Ollama 输出合成 JSON 行；OpenAI SSE 内容 40 字符，大于 STREAM_CHUNK_CHARS，确保外层仍悬停在真实 response 生命周期中。
- 并行 request 与 writer：在底层 response 的 release 之前，request / writer 均不得完成，reader lease 仍存在，client 不得退休。release 后断言底层 finished、退休观察值、临时 client 的关闭顺序。
- 覆盖 Ollama、OpenAI shared、OpenAI owned 三种客户端；正常消费、WebSocket send_text 抛 RuntimeError、消费端 CancelledError；分别覆盖无外部取消与重复拥有者取消。
- 保留断开后的标准生成 fallback 语义；明确断言 transport 请求模式为 [stream, nonstream]，OpenAI fallback finish_reason 仍为 stop；正常 / 消费端取消不新增 retry。
- 直接 OpenAI 外层 aclose 测试经调用者 finish_on_cancel 保护；反复取消调用者后仍等待底层 finished。此测试边界按用户补充调整，不要求 adapter 自建 web 生命周期策略。
- 实际 Ollama.close 覆盖正常、异步关闭异常、异步关闭取消，并断言同步 client 确已关闭；直接错误身份不变。owner 日志测试额外确认固定脱敏提示、不含合成敏感正文。
- 另加无 aclose 迭代器兼容测试；所有测试保持正常 pytest / 根 conftest 隔离入口。

### 本轮完整验证记录

| 阶段 | 真实结果 | 说明 |
|---|---|---|
| 初始实际 adapter RED | **11 failed / 2 passed / 18 deselected，7 warnings，3.30 秒** | 6 个请求过早释放、2 个 OpenAI 外层关闭早于 response finished、3 个 Ollama 独立 sync client 被跳过；正常关闭及兼容基线通过。 |
| 按用户边界调整直接 OpenAI 测试的调用者取消保护后，重新确认该缺口 RED | **2 failed / 23 deselected，7 warnings，2.83 秒** | 仍在“外层先完成、底层清理未完成”断言失败；未放宽 finished 条件。 |
| 仅 Ollama.close 最小 finally 后 | **4 passed / 21 deselected，7 warnings，2.55 秒** | 正常 / 异常 / 取消 / owner 脱敏。 |
| ChatHandler 显式清理后 | **3 passed / 28 deselected，7 warnings，2.51 秒** | 实际 Ollama 消费端异常 / 取消及无 aclose 兼容。 |
| OpenAI 两分支 aclosing 后 | **13 passed / 18 deselected，7 warnings，2.61 秒** | 首批实际 adapter 缺口与兼容测试通过。 |
| 扩展正常 / 不取消 / 重复取消及 retry / metadata 断言后的聚焦回归 | **59 passed，7 warnings，5.37 秒** | 两新增测试文件 + test_openai_compatible_model.py + test_agent_platform_health_check.py。 |
| **最终同一源码扩展联合矩阵** | **4145 passed / 8 skipped / 7 warnings，262.29 秒** | 原 16 组完整保留，加 test_openai_compatible_model.py；与当前源码哈希对应，不沿用旧 4113。 |
| 内存编译 | **302 Python 文件通过** | src / scripts / 两新增测试；不写 pyc。 |
| git diff --check / 暂存检查 | **通过 / 空** | 最后文档更新后再次检查。 |

上述为逐项实施验证，并未因本机负载改阈值或盲重跑。所有真实 RED 都保留，未用来源旧 GREEN 抵消缺口。

Ollama 既有测试先通过 `rg -n 'OllamaModel|ollama_model|__anext__|__aiter__' tests` 检索：
没有独立 Ollama adapter 测试文件，已有 canonical Ollama 类接线检查在 `tests/test_agent_platform_health_check.py`（原矩阵内）；本轮新增实际资源测试均在允许的两份测试内。原 test_openai_compatible_model.py 未编辑。

最终仍是 8 个 skip：2 个 Windows symlink 权限限制、1 个默认关闭性能测试、2 个 POSIX 目录权限语义、2 个需要独立 task store 的参数实例、1 个需要 configured runtime 的参数实例；具体文件与行号与前轮列表一致。7 个 warning 仍为 SWIG / FastAPI 弃用提示。

### 最终扩展联合命令

同前轮临时数据库启动器 / 根 conftest 隔离；MEDCHAT_RUN_FAMILY_REAL_ACCEPTANCE=0、RUN_REAL_TARGET_SEARCH=0、PYTHONDONTWRITEBYTECODE=1。未安装或使用 pytest-timeout；未启用外部模型、网络请求或真实权重。

```powershell
$env:MEDCHAT_RUN_FAMILY_REAL_ACCEPTANCE='0'
$env:RUN_REAL_TARGET_SEARCH='0'
$env:PYTHONDONTWRITEBYTECODE='1'
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -B -c "import os, sys, tempfile, subprocess; from pathlib import Path; tmp = tempfile.TemporaryDirectory(prefix='medchat-t05b-'); root = Path(tmp.name); os.environ['AGENT_STATE_DB'] = str(root / 'agent.sqlite'); os.environ['MEDCHAT_TASK_DB_PATH'] = str(root / 'tasks.sqlite'); result = subprocess.run([sys.executable, '-B', '-m', 'pytest', *sys.argv[1:]]); tmp.cleanup(); sys.exit(result.returncode)" tests/agent tests/test_model_request_lifecycle.py tests/test_design_model_switch.py tests/test_molecular_design_architecture.py tests/test_llm_runtime_config.py tests/test_user_llm_routes.py tests/test_agent_llm_wiring.py tests/test_task_runtime.py tests/test_phase2_phase3_routes.py tests/test_agent_anti_hallucination_fallbacks.py tests/test_agent_platform_health_check.py tests/test_agent_session.py tests/test_agent_session_entrypoints.py tests/test_agent_task_ownership.py tests/test_rag_index_manifest.py tests/test_web_app_lifecycle.py tests/test_openai_compatible_model.py -q -p no:cacheprovider --tb=short -rs
```

其余验证使用相同启动器 / 参数，选择分别为：

- 初始 RED / 首批 GREEN：`tests/test_model_request_lifecycle.py tests/test_design_model_switch.py -k 'actual_http_stream or actual_openai_nested or actual_ollama_close or without_aclose'`。
- OpenAI 调用者边界复核 RED：`tests/test_model_request_lifecycle.py -k actual_openai_nested`。
- Ollama.close：`tests/test_model_request_lifecycle.py -k actual_ollama_close`。
- ChatHandler：`tests/test_model_request_lifecycle.py tests/test_design_model_switch.py -k '(actual_http_stream and ollama) or without_aclose'`。
- 59 项聚焦：`tests/test_model_request_lifecycle.py tests/test_design_model_switch.py tests/test_openai_compatible_model.py tests/test_agent_platform_health_check.py`。

### 本轮最终源码快照与来源审计

最终联合回归启动前 / 完成后，下列 11 个源码与测试路径 SHA-256 一致。回归结束后只更新本文，没有再写源码或测试。

| 目的路径 | SHA-256 |
|---|---|
| `src/agent/openai_compatible_model.py` | `C25601B85C073EF78103718B1EEA9DC29324092D4AB10871BE7FF852767A0FDE` |
| `src/molecular_design/service.py` | `7CE8534C5393EB4B5A536DC60CA616591223DE3FAA050E8C0E2F7EACE2CFA9DC` |
| `src/task_runtime/manager.py` | `FF9263382F4E89FF60476D1C12EF98CE9C6C34A4D1E26014DC112273CA474860` |
| `src/web/app.py` | `82D84551637C6A93F2281A5F8CCE81CBAD275388E8FF6E1E57AE5269F42CCD2B` |
| `src/web/chat_handler.py` | `4B67190E50ABFAD84EA88CFC09A6B5F9A18A119142908BE188B1EB83207C43F2` |
| `src/web/models/ollama_model.py` | `5F92A4E16B38E39C9340631575C9965CB9B23E985B13AE9128F411ECD7ECEEFD` |
| `src/web/routes/agent_workflow_routes.py` | `99D81B13FCDB375E435A25353660557E7FC32B583CDE407271604A4638E57F04` |
| `src/web/routes/design_routes.py` | `F06C7A0587951A7F1214F6C4DBD01FE764AA98CA218C819D683FF425634FB76B` |
| `src/web/model_lifecycle.py` | `D25DBFD4BCFF8AABA8C8514C82A11E183D17352B79AD179BE333B199CBF4A666` |
| `tests/test_design_model_switch.py` | `E25B8A0AB6EB6DC311C56DA10F75C01372F200F61A2AB8ABBE8E8806C1D7A54D` |
| `tests/test_model_request_lifecycle.py` | `03B9064A0F4F2422B7DF286D2C33ACA7D6C20A0BDA8D48FF36D6A7ACCB1FD917` |

本轮开始 / 完成再次核验：来源 HEAD `d89a48b803fb0dcfdff95539513de725ca36756f`、11 条 status 及上文全部来源 SHA-256 仍不变。目的 HEAD 仍为 `24838f2edff93222718fe50ba377f8f7c6e56478`，分支未变，累计严格 12 路径，暂存区为空。

至此停写，等待原 SPEC / QUALITY 复审。没有 stage / commit / push / PR / merge；旧 Starlette CI 兼容、PR37 发布及依赖未合并边界仍未解除。

### 原审查者最终增量复审（父任务追加）

上述停写/待审为实现者交接时点。原 SPEC 与原 QUALITY 随后均 APPROVED；原 NOT APPROVED 和 RED 记录保留。

- SPEC 独立四文件聚焦 59 passed、P05/T01/T03 小回归 60 passed，302 文件内存编译通过；确认请求排空、owner、RAG、预算和历史顺序契约没有扩大改动。
- QUALITY 独立四文件聚焦 59 passed，另外复验原实际 adapter 故障探针：六个流清理组合与一个 Ollama 双客户端关闭检查全部通过；302 文件内存编译通过。
- 两审均核对 11 个源码/测试 SHA-256 与最终联合回归快照一致，无新增阻断项。未重跑全量 4k、未调用真实网络或模型。
- 父任务对严格 12 路径进行凭据模式扫描，无匹配；diff 检查通过。后续仅精确本地提交，不将本地复审视为 PR37 或旧 Starlette CI 已解决，也不部署或启用真实模型。
