# T06-C 有界精确集成：ChatHandler 局部去重

日期：2026-09-24。仅本地修改，未暂存、提交、推送、创建 PR、合并或部署。
独立 SPEC / QUALITY 待审；来源审批和基线测试不作为本次批准。

## 工作树与边界

- 目的：`D:/MedChat/molecular_chat_system_worktrees/chat-local-cleanup-integration-pr`。
- 分支：`codex/chat-local-cleanup-integration-pr`。
- 起始 HEAD：`e855baff9d3aebabf2ff9a129ea57358a4a75407`，起始状态干净。
- 来源只读：`D:/MedChat/molecular_chat_system_worktrees/chat-handler-local-cleanup`。
- 来源 HEAD：`d89a48b803fb0dcfdff95539513de725ca36756f`。
- 写集仅为 `src/web/chat_handler.py`、新增 `tests/agent/test_chat_local_cleanup.py`、本文件。
- 已读目的 AGENTS、项目标准、实际 handler、共享 RAG presentation、预算/事件/RAG 测试和
  `tests/conftest.py`；读取来源新测试、handoff 和 handler 的精确 diff 作为迁移依据。
- 使用 TDD：先新增实际模块行为测试，观察 RED，再 apply_patch 最小生产修改。
  不整文件复制旧 handler，不修改既有测试、root conftest、latest.md 或其他生产文件。
- 未启用真实模型/外部网络/真实资产，不运行 real/all 验收。
  合成 RAG 行、模型/Agent/Socket 替身仅为外部边界，不代表科学结果或真实服务验收。
- 历史联合 **4149 passed / 8 skipped 仅为基线**；来源测试成绩也不计入本次结果。

## 实现与兼容性

1. `rag_task` 经引用核对在 handler 中仅有两次赋值、无读取，删除两处。
   只修正并行 Agent/RAG、减少 sleep、避免内存泄漏和 Agent 并行执行的误导注释。
   不把来源已过时的 3000 字符硬截断或旧最近两轮显存注释重新带入当前基线。
2. `_append_history(history, entry)` 使用 `append(entry)` 和 `del history[:-20]`，
   原位保留末 20 条；不复制记录，不统一/删减字段，不替换列表对象。
   六个调用点为无效生成参数、分子输入澄清、工作流直接返回、解读预算拒绝、常规生成收尾、
   Agent 失败收尾。流式异常回退继续与常规生成共用末尾收尾。
3. `interpretation_budget_exceeded` 仍只有 `user`、`assistant`、`agent_used` 三字段，
   只追加一次；`input_budget_exceeded` 完全不追加也不裁剪历史。
4. `_rag_info_payload` 仅包装现有 `rag_info_molecule`，不复制数值/属性转换。
   Agent 分支仍由调用方传 `retrieved_molecules[:rag_count]`，legacy 仍传全部返回列表；
   各自文案和总数不变，history 的 `molecules_retrieved` 仍为全部检索数量。
   `source_index` / `provenance` 保留顶层，未退回 properties。
5. 不改路由、科学模型、提示预算、chronological history、取消、重试、request model lease
   或 HTTP stream close。保留 T01/T03/T05/T06A 的当前实现。

源码 diff：34 insertions、41 deletions。AST 辅助核验（不替代行为测试）：

- 排除 docstring 后，仅 `_process_message`、`_finish_terminal_agent_failure` 两个既有方法体改变；
  其他 27 个既有方法体相同，新增上述两个 helper。
- 六处历史记录字典 AST 与目的 HEAD 完全一致。
- 从 `model_max_tokens` 赋值起的生成/stream/retry/close 语句块 AST 与目的 HEAD 完全一致。

## 新增行为测试

共 21 项，直接导入并调用实际 `ChatHandler`，未替换模块、路由、预算方法或共享投影。
RAG 场景以真实可识别查询进入 legacy 分支，不 monkeypatch `_should_use_legacy_rag`。

- 原六终态（成功、Agent 失败、澄清、工作流直接返回、流式异常回退、无效 count）及流式成功：
  用记录 append 次数的 list 子类断言恰好一次，保留幸存记录对象、字段、消息类型和模型调用次数。
- 两条 RAG 分支：精确文案、Agent 切片 / legacy 全列表、来源顶层字段、检索总数，
  Agent RAG 不重复调用 legacy 检索。
- history helper：长度 0/19/20/23，原位列表、原记录、error/额外字段和嵌套对象身份。
- RAG helper：空/非空输入，等价共享 projection、嵌套 JSON、NaN 过滤、不变更原输入。
- 两种 stream 配置下的解读预算终态：三字段形状、一次追加、Agent 一次 / 模型零次。
- 两种 stream 配置下的输入预算终态：已有 23 条历史逐对象不变，路由/Agent/RAG/模型零次。
- 显式 history、handler 默认 history、独立 handler 及实际 websocket 连接之间无历史串用。

## 实际命令和逐轮结果

cwd 始终是目的树。每轮在同一个 PowerShell 进程中先设置以下前缀；
PHASE 对应 `red`、`green`、`joint`，每轮 DB 使用新的 UUID 临时路径。
正常加载仓库 `tests/conftest.py`，由其隔离用户配置、env 文件、锁和 session DB。
未安装或调用 pytest-timeout，未使用 pytest cacheprovider。

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONIOENCODING='utf-8'
$env:MEDCHAT_RUN_FAMILY_REAL_ACCEPTANCE='0'
$env:RUN_REAL_TARGET_SEARCH='0'
$env:MEDCHAT_RUN_PERF_TESTS='0'
$env:AGENT_STATE_DB=Join-Path $env:TEMP ('medchat-t06c-PHASE-agent-' + [guid]::NewGuid().ToString() + '.sqlite')
$env:MEDCHAT_TASK_DB_PATH=Join-Path $env:TEMP ('medchat-t06c-PHASE-tasks-' + [guid]::NewGuid().ToString() + '.sqlite')
```

### RED 与聚焦 GREEN（相同命令）

```powershell
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -B -m pytest -q -p no:cacheprovider --tb=short -rs tests/agent/test_chat_local_cleanup.py tests/agent/test_chat_handler_agent_events.py tests/agent/test_chat_input_budget.py tests/test_rag_index_manifest.py
```

- RED：exit 1，**6 failed、193 passed、4 warnings，9.64s**。没有 collection error / skip。
  4 项 history helper 参数化测试因缺少 `_append_history` 失败；2 项 RAG helper 测试因缺少
  `_rag_info_payload` 失败，均为预期 AttributeError。其余行为测试通过。
  此时 `git diff --exit-code HEAD -- src/web/chat_handler.py` 为零，只有新测试未跟踪。
- GREEN：exit 0，**199 passed、4 warnings，8.35s**，无 skip。
  仅在 RED 后修改生产文件，未为了通过测试改变断言。
- 两轮 warnings 均为已有 FastAPI `on_event` 弃用提示。

### 最终同源码联合回归

```powershell
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -B -m pytest -q -p no:cacheprovider --tb=short -rs tests/agent tests/test_model_request_lifecycle.py tests/test_design_model_switch.py tests/test_molecular_design_architecture.py tests/test_llm_runtime_config.py tests/test_user_llm_routes.py tests/test_agent_llm_wiring.py tests/test_task_runtime.py tests/test_phase2_phase3_routes.py tests/test_agent_anti_hallucination_fallbacks.py tests/test_agent_platform_health_check.py tests/test_agent_session.py tests/test_agent_session_entrypoints.py tests/test_agent_task_ownership.py tests/test_rag_index_manifest.py tests/test_web_app_lifecycle.py tests/test_openai_compatible_model.py
```

exit 0：**4170 passed、8 skipped、7 warnings，184.29s（3:04）**。
这是本次同源码实测结果，不与聚焦结果相加。GREEN 后源码和测试均未再修改，
联合完成后仅补录本 handoff。7 warnings 为3项既有 SWIG 与4项 FastAPI 弃用提示。

8 个 skip 原因逐项保留；未新增 skip/xfail，也未修改既有测试绕过失败：

| 位置 | 原因 |
|---|---|
| tests/agent/test_decision_chat_acceptance.py:149 | directory symlinks unavailable |
| tests/agent/test_harness_shadow.py:277 | performance test disabled |
| tests/test_llm_runtime_config.py:33 | symlink unavailable，WinError 1314（客户端没有所需特权；临时 managed.env -> .env） |
| tests/test_agent_session.py:113 | POSIX directory permission semantics |
| tests/test_agent_session.py:121 | POSIX directory permission semantics |
| tests/test_agent_task_ownership.py:177 | requires two independent task stores |
| tests/test_agent_task_ownership.py:201 | requires a configured runtime |
| tests/test_agent_task_ownership.py:218 | requires two independent task stores |

本轮共三次 pytest 调用；唯一失败轮次为上述预期 RED，未隐去其他失败或重跑。

### 静态与边界核验

- `git diff --check`：通过。
- `node tests/home_agent_task_panel_test.js`：exit 0，Homepage agent task panel static checks passed。
- MedChat Python `-B -c` 内存编译：301 文件通过，不写 pyc。
  实际编译代码如下（PowerShell 单引号 here-string 传给 `-c $code`）：

```python
import pathlib
import subprocess
paths = [p for p in subprocess.check_output(
    ['git', 'ls-files', 'src', 'scripts'], encoding='utf-8'
).splitlines() if p.endswith('.py')]
paths.append('tests/agent/test_chat_local_cleanup.py')
for path in paths:
    compile(pathlib.Path(path).read_bytes(), path, 'exec')
print('In-memory compilation:', len(paths), 'files passed; no bytecode written')
```

- `git diff --exit-code HEAD -- . ':(exclude)src/web/chat_handler.py'`：exit 0，
  其他跟踪文件（含 latest.md 和既有测试）未改变。
- 一次只读 `rg` 检索将 Windows 路径 glob 直接作参数，出现 os error 123；
  后改为 `rg ... docs/handoff -g '*integration*.md'` 正常读取。没有文件副作用或测试失败。

## 来源 SHA-256 与最终写集

起始来源状态恰为下面四路径；联合回归结束后再次运行 `Get-FileHash -Algorithm SHA256`、
`git rev-parse HEAD` 和 `git status --short`。四个哈希逐项自动比较为一致，HEAD 与状态也未改变。

| 来源路径 | SHA-256（开始 = 结束） |
|---|---|
| docs/handoff/latest.md | 317E0F764906E41D3C3037B159121DA8B2922EAD686E516F7164A9A63D80F1A4 |
| src/web/chat_handler.py | 91DB3367849CD96373585C9470BA0BE2B500FB7A0061815CC9B7C79B0D194DED |
| docs/handoff/chat-handler-local-cleanup.md | 54618CCD995276E3B75C715B5F3AF4DC771CC587E5CDD4F674321E68642A7D83 |
| tests/agent/test_chat_local_cleanup.py | DB833E604D4F708364B6E74599001AA4BB0AEC8197EF01B7ADEA5908952D0CDB |

目的实现哈希（聚焦 GREEN 后 = 联合回归结束后）：

| 目的路径 | SHA-256 |
|---|---|
| src/web/chat_handler.py | 14BABA406DC4B92E72EE230644878FBE1BE1E351B1B31AC413AC86C47368F0EB |
| tests/agent/test_chat_local_cleanup.py | 3D7D9AF95EF1770294CD056A5FFF1DB3ABB6E1DCF3EA62724D6E67478DE84319 |

本 handoff 的最终哈希在停写后单独报告，避免自指哈希。

最终目的 HEAD 仍为 `e855baff9d3aebabf2ff9a129ea57358a4a75407`，分支仍为
`codex/chat-local-cleanup-integration-pr`，`git diff --cached --name-only` 为空。
最终状态只包含以下三个路径；commit / PR 均无：

```text
 M src/web/chat_handler.py
?? docs/handoff/chat-local-cleanup-integration.md
?? tests/agent/test_chat_local_cleanup.py
```

## 待审边界

需父任务独立 SPEC / QUALITY 核对六处收尾、预算拒绝形状、RAG 投影和条数/文案，以及
T01/T03/T05/T06A 行为保留。当前没有可调用的独立子代理工具，未伪称完成双审。
TDD、AST 检查与作者自查不是独立审查，也不是部署或真实科学验收。
未扩大生产范围；若后续审查需要修改其他生产文件，必须先取得用户授权。

## 独立复审追加（父任务）

上述待审为实现交接时点；后续独立 SPEC 与 QUALITY 均 APPROVED，无阻断项。

- SPEC 独立聚焦 199 passed，核对六处历史记录表达式、其他既有方法 AST 与来源四项哈希，未发现 T01/T03/T05/T06A 回退。
- QUALITY 独立聚焦 222 passed，另以基线对照的内存探针 68 passed 覆盖取消、异常、流式重试及对象/字段等价；请求 lease 与底层 HTTP 清理测试通过。
- 两审未重跑 4k 联合矩阵，也未改源码；最终全量证据仍为上文同源码 4170 passed / 8 skipped / 7 warnings。
- 父任务仅精确本地提交本批三路径，不发布依赖历史；模板兼容和 PR37 未合并边界不因此解除。
