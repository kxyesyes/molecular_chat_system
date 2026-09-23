# T06-A：旧聊天私有入口精确集成

日期：2026-09-23。本记录区分独立来源历史与当前组合版本；未部署或调用真实模型。

## 基线、范围与来源

- 目的分支：`codex/legacy-chat-entry-cleanup-integration-pr`。
- 基线：`24838f2edff93222718fe50ba377f8f7c6e56478`，含P05、T01、T03，起始干净。
- 来源：`codex/legacy-chat-entry-cleanup @ d89a48b` 的未提交四路径，仅只读核验；不复制旧latest.md。
- 目的只改三路径：`src/web/app.py`、`tests/agent/test_legacy_chat_entry_cleanup.py`、本记录。
- T05-B位于另一同基线独立工作树，未以本批测试冒充二者联合回归。

## 删除核对

在当前组合基线执行：

```powershell
git grep -n -E '_handle_websocket|_reject_websocket_without_chat_handler|_create_supervisor_agent|setup_websocket_routes|setup_page_routes' -- src tests scripts config deployment
rg -n 'conversation_history|_format_rag_context|_build_prompt_with_agent|_build_prompt' src/web/app.py
rg -n 'format_rag_context|rag_info_molecule|WebSocketDisconnect|generate_for_chat|\bList\b' src/web/app.py
```

- 删除四个未注册、无外部生产调用的私有方法：`_handle_websocket`、`_format_rag_context`、`_build_prompt`、`_build_prompt_with_agent`；后三者仅服务前者。
- 删除仅供旧实现使用的应用级`conversation_history`。
- 删除未再使用的`WebSocketDisconnect`和T01引入的两个`rag_presentation`导入；共享RAG模块和正式ChatHandler中的使用不变。
- 保留`_create_supervisor_agent`、`_reject_websocket_without_chat_handler`，以及公开包导出的`setup_page_routes`、`setup_websocket_routes`；不能把无内部调用等同可删除公共兼容接口。
- `List`及`generate_for_chat`仍有有效消费者，不删除。

实际生产diff：1 insertion / 241 deletions。AST整模块比对只归一化上述四方法、旧字段和两条导入后完全相等；所有保留方法（含RAG共享检索、会话、注册、生命周期）未改动。并非把已有工作流或科学校验删除。

## 测试设计与边界

来源旧测试直接导入全局app且WebSocket未取得P05 cookie，不能整文件照搬。目的新测试在独立短生命周期进程、白名单环境、临时工作目录/配置/任务与会话库中导入实际app；在导入前替换模型和科学工具工厂，不读取真实凭据、权重或数据。HTTPX外部传输失败关闭，ASGI请求与真实Jinja页面不替换，禁止启动科学lifespan。

四个用例验证：

1. 旧私有方法和字段不存在，ChatHandler的有效同名方法保留。
2. 实际`/ws`仅一个，经过真实首页取得服务端cookie后交给现代处理器，并携带非cookie原文的服务端session ID。
3. 处理器缺失时实际`/ws`保留错误消息和1011关闭，不恢复旧fallback。
4. 真实首页200、工作流plan空输入422，Supervisor工厂与公开兼容导出仍可用。

测试关闭自己拥有的客户端、app、模型；ExitStack保证断言失败仍运行清理。独立进程不导入或关闭父进程已有app；临时目录退出后检查移除。设计/靶点路由注册不是本批对象，导入前隔离其科学资产加载；没有mock首页、session或工作流plan输入校验。

## 实测记录

解释器：`C:/Users/xkx52/.conda/envs/MedChat/python.exe`；cwd为本目的工作树。pytest使用`-B -m pytest -q -p no:cacheprovider --tb=short -rs`。

```powershell
python -B -m pytest tests/agent/test_legacy_chat_entry_cleanup.py -q -p no:cacheprovider --tb=short -rs
```

- RED：**1 failed / 3 passed，10.53秒，exit 1**。精确失败`AssertionError: _handle_websocket`，不是session、模板或导入失败；其余真实路由删除前已通过。

```powershell
python -B -m pytest tests/agent/test_legacy_chat_entry_cleanup.py tests/agent/test_app_supervisor_entrypoint.py tests/test_phase2_phase3_routes.py -q -p no:cacheprovider --tb=short -rs
```

- GREEN：**7 passed / 7 warnings，14.86秒，exit 0**。警告为既有SWIG/FastAPI弃用。
- `git diff --check`及内存compile/AST保留实现等价性：通过。

完整Agent及相关入口联合回归前设置`MEDCHAT_RUN_FAMILY_REAL_ACCEPTANCE=0`、`RUN_REAL_TARGET_SEARCH=0`、`PYTHONDONTWRITEBYTECODE=1`、`PYTHONIOENCODING=utf-8`，并将`AGENT_STATE_DB`和`MEDCHAT_TASK_DB_PATH`指向本轮临时路径；pytest原conftest继续隔离用户配置/session。

```powershell
python -B -m pytest tests/agent tests/test_phase2_phase3_routes.py tests/test_user_llm_routes.py tests/test_agent_anti_hallucination_fallbacks.py tests/test_agent_platform_health_check.py tests/test_agent_session_entrypoints.py tests/test_web_app_lifecycle.py tests/test_rag_index_manifest.py -q -p no:cacheprovider --tb=short -rs
```

最终结果：**3841 passed / 2 skipped / 7 warnings，227.55秒，exit 0**。两项skip为Windows directory symlinks unavailable和既有performance test disabled；没有新增skip或xfail。没有把来源3301历史结果或T03的3749结果用作本批结果。

另执行299个src/scripts Python文件内存compile（无bytecode写入）、`node tests/home_agent_task_panel_test.js`及`git diff --check`，均通过。来源四个修改/新增文件SHA256与开始时一致，未写来源。

独立SPEC审查APPROVED：另跑新文件4 passed（11.77秒），增加`--noconftest`后4 passed（10.51秒），确认测试不依赖父进程conftest也自带隔离；独立AST等价性、内存编译及diff检查通过。

独立QUALITY审查APPROVED：`--noconftest`新文件4 passed（9.85秒）；正常及强制断言失败内存探针验证client/app/model清理、临时目录移除及父app哨兵不变，无外部网络/科学工具或目录外资产写入。另探针验证缺失/伪造cookie、跨源WebSocket均1008，跨源plan POST为403；未替换真实首页、Jinja或session。探针初期误拦Windows内部操作后修正探针，没有改生产或测试代码。父任务据此精确暂存三路径形成本地提交，未发布。

## 发布边界与待办

- SPEC/QUALITY均通过；只形成独立本地提交，不发布累计历史。
- PR37以及真实首页旧Starlette兼容依赖仍未解决，不能因为本机新版测试通过宣称CI或部署通过。
- 模板兼容采用何种方案仍待用户确认；本批不修改模板调用、升级依赖或绕过真实首页。
- 原始混杂工作区、来源树与真实科学资产不动；后续与T05-B组合需重新回归。

## T05-B 组合验证追加（2026-09-24）

T06-A 本地提交 `0cf6824` 与已独立双审的 T05-B `4d22a8d` 在本分支完成无冲突组合，head `1a0cfcbf502344ad5b381db067ada4cda9365df7`，tree `2ed2dbc51991afc7306a82db191f5e287c736140`。候选 tree 与事前 merge-tree 一致；不改 main、不推送。

原 QUALITY 审查者只读集成复核 APPROVED：唯一交叉文件 app.py 为原 T06-A 删除补丁在 T05-B 上的逐字重放，其余 13 个变更文件精确来自已审快照；模型使用门、配置切换、关闭、正式 /ws 和会话边界完整保留。独立新测试加会话入口测试 10 passed，11.35 秒。

父任务实际运行完整组合矩阵（MedChat Python，正常根 conftest、临时数据库、禁用真实服务）：

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
$env:MEDCHAT_RUN_FAMILY_REAL_ACCEPTANCE='0'
$env:MEDCHAT_RUN_REAL_EXTERNAL_TESTS='0'
$env:MEDCHAT_RUN_REAL_TESTS='0'
$env:MEDCHAT_RUN_OPENSANDBOX_ACCEPTANCE='0'
$env:RUN_REAL_TARGET_SEARCH='0'
& 'C:/Users/xkx52/.conda/envs/MedChat/python.exe' -B -c "import os,sys,tempfile,subprocess; from pathlib import Path; tmp=tempfile.TemporaryDirectory(prefix='medchat-t05-t06-'); root=Path(tmp.name); os.environ['AGENT_STATE_DB']=str(root/'agent.sqlite'); os.environ['MEDCHAT_TASK_DB_PATH']=str(root/'tasks.sqlite'); result=subprocess.run([sys.executable,'-B','-m','pytest',*sys.argv[1:]]); tmp.cleanup(); sys.exit(result.returncode)" tests/agent tests/test_model_request_lifecycle.py tests/test_design_model_switch.py tests/test_molecular_design_architecture.py tests/test_llm_runtime_config.py tests/test_user_llm_routes.py tests/test_agent_llm_wiring.py tests/test_task_runtime.py tests/test_phase2_phase3_routes.py tests/test_agent_anti_hallucination_fallbacks.py tests/test_agent_platform_health_check.py tests/test_agent_session.py tests/test_agent_session_entrypoints.py tests/test_agent_task_ownership.py tests/test_rag_index_manifest.py tests/test_web_app_lifecycle.py tests/test_openai_compatible_model.py -q -p no:cacheprovider --tb=short -rs
```

结果：**4149 passed / 8 skipped / 7 warnings，256.00 秒，exit 0**。八项 skip 为两项 Windows 符号链接权限、默认关闭性能项、两项 POSIX 目录权限、两项需要独立 task store、一个需要 configured runtime 的既有测试；未新增跳过。七个 warning 为 SWIG/FastAPI 弃用提示。

303 个 Python 文件内存编译及 diff 检查通过；测试完成时工作树干净，之后仅追加本节记录。此结果不代替旧 Starlette 兼容验收或 PR37 CI，未使用真实主模型/科学模型、未部署。
