# T03 提示输入预算：新基线精确集成

日期：2026-09-23。仅本地实施；QUALITY 指出的 P2 历史倒序回归修复见末节，待原 QUALITY 复审。
前半部分保留首次集成的真实快照，不把该快照或来源批准当作最新 QUALITY 批准。
P05 依赖 CI 未通过的发布约束保持不变；本次不评价、不修复该依赖，也不发布累计分支。

## 工作树、来源和写集

- 目的：`D:/MedChat/molecular_chat_system_worktrees/chat-input-budget-integration-pr`。
- 分支：`codex/chat-input-budget-integration-pr`。
- 开始/结束 HEAD：`dc937b62456fed58f7bf58ccb368bef641cdd88b`，起始工作树干净。
- 来源（全程只读）：`D:/MedChat/molecular_chat_system_worktrees/chat-input-budget`。
- 来源 HEAD：`d89a48b803fb0dcfdff95539513de725ca36756f`，加下列五路径未提交补丁。
- 已读两端 AGENTS、项目规范、来源 handoff、预算模块/测试/ChatHandler diff，以及目的
  ChatHandler、共享 RAG presentation、RAG manifest 和事件回归、隔离 fixture。
- 来源历史 108 passed / Agent 3301 passed、2 skipped 不计入本次结果；
  新基线历史 Agent 3700 passed、2 skipped 同样不冒充本次实测。

精确写集：

1. 修改 `src/web/chat_handler.py`：早期预算校验、提示组装、工具解读保守退出、RAG 预算适配。
2. 新增 `src/web/prompt_budget.py`：迁入已批准来源模块，未另造 tokenizer/模型窗口推断。
3. 新增 `tests/agent/test_chat_input_budget.py`：原 32 项测试，加 9 项目的基线集成回归及8项 P2 历史顺序回归。
4. 新增 `docs/handoff/chat-input-budget-integration.md`：本记录。

全部编辑使用 apply_patch；ChatHandler 仅按局部 hunk/方法迁入，未整文件覆盖。
没有修改任何额外 fixture；新测试直接复用目的 `tests/test_rag_index_manifest.py` 的
`retrieval_service` fixture，其 CSV、FAISS index、manifest 都是 tmp_path 下的合成资产。
没有复制或修改 `docs/handoff/latest.md`，没有暂存、commit、push、PR、merge、部署或模型调用。

## 执行计划与兼容处理

- [x] 只读确认双端状态，记录来源 SHA256；目的先跑事件/RAG 基线。
- [x] 仅迁入测试并增加共享 presentation / 检索 / 早期拒绝断言，实际运行 RED。
- [x] 迁入预算模块和 ChatHandler 最小 hunk，实际运行聚焦 GREEN。
- [x] 回归 session/owner 与 T01；静态比较受保护函数及模型调用段。
- [x] 完整 Agent 回归结束后补录结果、最终状态与来源哈希复验。
- [ ] 父任务独立 SPEC / QUALITY（本轮不自行标记批准）。

### 预算行为沿用已批准 T03

系统提示、科学真实性约束及当前问题完整保留，移除末尾 `prompt[:3000]`。
`_process_message` 在 generation preflight、分子输入分析、技能路由和检索之前校验；
超限返回 `complete` / `input_budget_exceeded`，不调用工具或模型，不把超长问题存入历史。
provider_name 只选择本地/外部字符预算，不改变主模型、分子生成模型或输出 token budget。

`inference` 配置（Python 字符数，不是 token 或模型窗口）：

| 字段 | 默认值 |
|---|---:|
| `input_max_chars` | 12000 |
| `external_input_max_chars` | 24000 |
| `history_input_max_chars` | 2000 |
| `rag_input_max_chars` | 3000 |
| `tool_input_max_chars` | 5000 |

总预算还预留 2048 字符必要工具状态及省略提示。配置必须为非负整数；布尔值、负数、
字符串、None 无效。可选工具叙述、RAG、最近两轮历史整块/整条/整轮纳入或省略，
不截断 SMILES、JSON 或 provenance。沿用来源的深度 8、节点 1024 有界序列化。

工具必要信息包括状态、warnings/errors、来源、evidence/artifacts、tool_results、
逐次/逐步结果等；若不能完整装入保留区，不让 LLM 总结，保留原始工具答复并追加
“未进行模型总结”提示，返回 `interpretation_budget_exceeded`。
既有 terminal scientific failure、错误脱敏、事件语义以及默认工作流直接返回路径不变。

### T01 与预算 formatter 共存

来源 T03 的 RAG 上下文直接输出有界 JSON；目的已采用共享 presentation。
此次唯一必要适配是在 `_format_rag_context` 中先用 `bounded_record` 检查完整记录，
仅把有界 JSON-compatible 内容交给原 `format_rag_context`，并把最终展示字符开销
和省略提示计入上限。单条装不下则整条跳过，后面的较小记录仍可纳入。

共享 formatter 继续负责标题、序号、SMILES、相似度及属性展示；预算 wrapper 不重写
行映射/检索/来源逻辑。可容纳的两条正常记录与共享 formatter 输出完全一致。
原始 retrieved_molecules 不变；两个 `rag_info` 路径仍调用 `rag_info_molecule`，
source_index / provenance 保留在顶层，而非退回 properties 字符串。

新增离线整合矩阵覆盖 legacy async、canonical `rag_search`、alias `rag_database_search`
三入口 × RAG cap 0/1000。走真实 RAGSystem、manifest 校验和工具适配器；只替换 embedding，
逐次计数为恰好一次 async 或 sync，没有第二次检索。验证 vector_label=1 → source_index=2，
SMILES=CCC，以及真实临时 manifest 的 source/index SHA256；预算为0时卡片来源仍完整。

P05 owner、T07 session、T08 target、T01 retrieval/row mapping 实现均未修改。
AST 对照：已有 ChatHandler 仅 `_process_message`、`_format_rag_context`、`_build_prompt`、
`_build_prompt_with_agent` 四方法改变，另新增 `_input_limit`；其余 **24 方法 AST 相同**。
`_process_message` 从“AI正在生成回答”开始到结尾的模型调用、stream、输出完成状态和
历史保存语句 AST 全部与目的 HEAD 相同。

## 实际命令与结果

本节至“审查与未执行项”为首次集成记录；P2 修复后的最终快照见末节。

以下命令 cwd 均为目的树，使用 `C:/Users/xkx52/.conda/envs/MedChat/python.exe`。
每轮 pytest 都在本命令进程设置下列前缀；PHASE 依次为 baseline/red/green/agent/compat。
既有 tests/conftest.py 在 collection 前和各测试 fixture 中隔离用户配置、env 文件、锁与 session DB。

```powershell
$env:MEDCHAT_RUN_FAMILY_REAL_ACCEPTANCE='0'
$env:RUN_REAL_TARGET_SEARCH='0'
$env:PYTHONDONTWRITEBYTECODE='1'
$env:AGENT_STATE_DB=Join-Path $env:TEMP ('medchat-t03-PHASE-' + [guid]::NewGuid().ToString() + '.sqlite')
```

green/agent/compat 另设置 `$env:PYTHONIOENCODING='utf-8'`。
agent 和 compat 另设置独立任务库（前缀分别为 medchat-t03-tasks / medchat-t03-compat-tasks）：

```powershell
$env:MEDCHAT_TASK_DB_PATH=Join-Path $env:TEMP ('medchat-t03-tasks-' + [guid]::NewGuid().ToString() + '.sqlite')
```

### 基线：目的实现尚未修改

```powershell
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -B -m pytest tests/agent/test_chat_handler_agent_events.py tests/test_rag_index_manifest.py -q -p no:cacheprovider --tb=short -rs
```

exit 0：**129 passed、4 warnings，9.46s**。

### RED：仅新测试已迁入，生产源码仍为目的 HEAD

```powershell
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -B -m pytest tests/agent/test_chat_input_budget.py -q -p no:cacheprovider --tb=short -rs
```

exit 1：**32 failed、9 passed、4 warnings，4.90s**。
原 32 项里 30 failed、2 passed；新增9项里2项失败、7项既有兼容保护断言通过。
没有 collection error，也没有靠修改旧 fixture 消除失败。

实际失败原因：

- 实际模型提示缺少完整问题，旧路径对长 history/RAG/tools/input 使用 3000 字符截断。
- 超限仍触发 should_use_tools 或 generation preflight，违背路由前拒绝。
- 缺少 `_input_limit`、`prompt_budget` 模块和 `agent_result` 参数（尚未迁入的功能）。
- RAG 中间文本可超 cap，任意对象字符串化，大记录未经预检进入共享 formatter。
- 必要工具元数据超限仍调用模型；错误、partial 和来源保护不足。

### GREEN：局部实现迁入后

```powershell
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -B -m pytest tests/agent/test_chat_input_budget.py tests/agent/test_chat_handler_agent_events.py tests/test_rag_index_manifest.py -q -p no:cacheprovider --tb=short -rs
```

exit 0：**170 passed、4 warnings，9.81s**。包括全部 41 项预算/集成测试。

### Session / owner / RAG / lifecycle 兼容回归

```powershell
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -B -m pytest tests/test_agent_session.py tests/test_agent_session_entrypoints.py tests/test_agent_task_ownership.py tests/test_rag_index_manifest.py tests/test_web_app_lifecycle.py -q -p no:cacheprovider --tb=short -rs
```

exit 0：**259 passed、5 skipped、4 warnings，35.90s**。
skip 原因未隐去：session.py:113/:121 为 POSIX directory permission semantics；
task_ownership.py:177/:218 为 requires two independent task stores，:201 为 requires a configured runtime。
没有新增 skip/xfail。4 warnings 均为既有 FastAPI on_event 弃用警告。

### 完整 Agent

```powershell
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -B -m pytest tests/agent -q -p no:cacheprovider --tb=short -rs
```

exit 0：**3741 passed、2 skipped、7 warnings，202.06s**。
两项 skip：`tests/agent/test_decision_chat_acceptance.py:149` 的 directory symlinks unavailable，
`tests/agent/test_harness_shadow.py:277` 的 performance test disabled。
7 warnings 为3项既有 SWIG与4项 FastAPI 弃用警告。没有新 skip/xfail。
以上集合相互有重叠，不把不同轮次通过数相加。

### 静态检查

- `git diff --check`：exit 0。
- `node tests/home_agent_task_panel_test.js`：exit 0，Homepage agent task panel static checks passed。
- 300 个 Python 文件内存编译通过（跟踪的 src/scripts + 新预算模块 + 新测试），不写 bytecode。
  未运行会写 __pycache__ 的 compileall，遵守 -B/no-cache/精确写集要求。
- `git diff --exit-code HEAD -- src/rag src/web/rag_presentation.py src/web/app.py src/agent src/web/routes src/web/agent_session_config.py docs/handoff/latest.md`：exit 0，无输出。

内存编译实际命令的编译部分：

```powershell
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -B -c 'import pathlib, subprocess; paths=[p for p in subprocess.check_output(["git","ls-files","src","scripts"],encoding="utf-8").splitlines() if p.endswith(".py")]; paths += ["src/web/prompt_budget.py","tests/agent/test_chat_input_budget.py"]; [compile(pathlib.Path(p).read_bytes(),p,"exec") for p in paths]; print("In-memory syntax compilation:",len(paths),"files passed; no bytecode written")'
```

## 来源只读证据

开始和结束复验均执行 `git rev-parse HEAD`、`git status --porcelain=v1` 和
`Get-FileHash -Algorithm SHA256`。以下状态、HEAD、6个文件哈希全部逐项相同：

```text
 M docs/handoff/latest.md
 M src/web/chat_handler.py
?? docs/handoff/chat-input-budget.md
?? src/web/prompt_budget.py
?? tests/agent/test_chat_input_budget.py
```

| 来源路径 | SHA256（开始 = 复验） |
|---|---|
| AGENTS.md | 66B60ECC1D29F083E399FA157CA59180F7ECB9CF3F2F7C7C0B0259FB98AD53C3 |
| docs/handoff/latest.md | 89240AA3C4BC6D7BF8972163A58443D019D9F3E68FB6439FAA5A058056F2E9C1 |
| docs/handoff/chat-input-budget.md | F64817F1E67DF15622963D25515BD5E829A3E018040EE3D120D81B94262326A0 |
| src/web/chat_handler.py | EE5B9E08DA7A1EBB1804EFFA4980A08BAB8820D9502D35312E22191B396589B2 |
| src/web/prompt_budget.py | FFB672AEE000D08B34428CEB6E855E1569200F934D5B2058ADD2074C36FB43B5 |
| tests/agent/test_chat_input_budget.py | C29BAFC476EA6CB4D62509A8913CA2C190FD5F4ECD8FA32352C89427FBE8AB96 |

首次集成时目的新增 `src/web/prompt_budget.py` 的 SHA256 也是
`FFB672AEE000D08B34428CEB6E855E1569200F934D5B2058ADD2074C36FB43B5`，与来源字节一致。
首次集成的原32项预算测试函数保持不变，只有新增 fixture 导入与9项集成用例。
P2 修复仅对目的预算模块的选中历史块呈现顺序增加显式开关，故其最新哈希见末节；来源模块不变。
最终目的 git status 仅为：

```text
 M src/web/chat_handler.py
?? docs/handoff/chat-input-budget-integration.md
?? src/web/prompt_budget.py
?? tests/agent/test_chat_input_budget.py
```

`git diff --cached --name-only` 为空。分支和 HEAD 未变；新增 commit：无；PR：无。

## 审查与未执行项

待父任务独立 SPEC / QUALITY；来源已批准不等于本次集成已获批准。
审查重点：T01 formatter 的预算前置、完整来源保留、必要状态超限的保守退出，以及事件/owner/session 不退化。
这是字符级提示资源约束，不是 WebSocket 请求体/原始工具输出大小限制，也不是模型质量认证。
工具必要结果较大时会保守跳过自动解读，这属于批准的 T03 行为。
没有真实 API/模型/数据资产操作，没有部署、health_check、CI 操作、提交或发布。
全仓 pytest、P05 依赖 CI、真实科学计算和线上效果均 not_run。

## QUALITY P2：历史优先选择与正序呈现分离

### 复现与最小修复

用户转述原 QUALITY 未批准：`reversed(history[-2:])` 的最新轮优先预算选择顺序直接
用于输出，导致两轮均能容纳时模型看到 B → A → 当前问题；目的基线的正常顺序为 A → B。
本轮独立核验并用实际 `_process_message` 复现，不把 reviewer 的170 pass或20k内存探针算为本轮证据。

先只追加测试，生产代码不动：4个情景 × stream=False/True，共8项。

- 两轮均可容纳：完整 A 在完整 B 前，当前问题在末尾。
- history 区仅容纳最新一轮：完整 B 保留，A 和其 provenance 整轮省略。
- 旧轮远超 cap：旧轮及省略提示不得挤掉完整 B，不突破 history 或总预算。
- history cap 可容纳两轮但总输入剩余仅够最新轮：仍优先 B，防止“先反转再筛选”的错误修复。

所有用例均检查实际模型输入、完整 JSON/SMILES/provenance、总/分区字符边界，以及原始历史顺序不被修改。
真实 RED 中两项 both 用例失败：`assert 1600 < 1412`；6项原有预算选择保护断言通过。

最小生产修改：

- ChatHandler 相对首轮快照仅 `_build_prompt` 的 assemble 调用增加 `chronological_history=True`，
  经完整文件字符串比对确认没有其他修改。
- `prompt_budget.assemble` 增加默认关闭的 keyword-only 开关，在原优先级/总预算筛选期间
  记录已纳入历史块的位置，筛选完成后只反转这些位置上的完整块。不解析用户文本，
  不更改块内容、消耗字符数、RAG/工具顺序、metadata或省略判断。
- assemble 之前的预算模块源码与首轮逐字一致；工具解读调用不启用此开关。
- 原四路径内编辑，未修改其他 fixture 或源码，未操作只读来源。

### 本轮实际命令

cwd、MedChat Python及既有 conftest 隔离同前。每条 pytest 在独立命令进程设置以下前缀，
PHASE 实际取 red/green/agent/compat：

```powershell
$env:MEDCHAT_RUN_FAMILY_REAL_ACCEPTANCE='0'
$env:RUN_REAL_TARGET_SEARCH='0'
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONIOENCODING='utf-8'
$env:AGENT_STATE_DB=Join-Path $env:TEMP ('medchat-t03-order-PHASE-' + [guid]::NewGuid().ToString() + '.sqlite')
$env:MEDCHAT_TASK_DB_PATH=Join-Path $env:TEMP ('medchat-t03-order-PHASE-tasks-' + [guid]::NewGuid().ToString() + '.sqlite')
```

RED（新增测试之后、实现修复之前）：

```powershell
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -B -m pytest tests/agent/test_chat_input_budget.py -k actual_model_history_selection_and_chronology -q -p no:cacheprovider --tb=short -rs
```

exit 1：**2 failed、6 passed、41 deselected、4 warnings，3.11s**。
失败是预期的历史顺序断言，不是环境或 fixture 错误。

GREEN（49项预算/历史/整合用例 + 事件 + T01）：

```powershell
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -B -m pytest tests/agent/test_chat_input_budget.py tests/agent/test_chat_handler_agent_events.py tests/test_rag_index_manifest.py -q -p no:cacheprovider --tb=short -rs
```

exit 0：**178 passed、4 warnings，9.48s**。
包含 T01 三入口一次检索、source_index/provenance、必要工具 metadata 超限保守退出、重复工具逐步错误保留。

owner/session/RAG/lifecycle：

```powershell
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -B -m pytest tests/test_agent_session.py tests/test_agent_session_entrypoints.py tests/test_agent_task_ownership.py tests/test_rag_index_manifest.py tests/test_web_app_lifecycle.py -q -p no:cacheprovider --tb=short -rs
```

exit 0：**259 passed、5 skipped、4 warnings，32.58s**。
5项 skip 与首次兼容回归相同：两项 POSIX directory permission semantics、
两项 requires two independent task stores、一项 requires a configured runtime。

完整 Agent 最终快照：

```powershell
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -B -m pytest tests/agent -q -p no:cacheprovider --tb=short -rs
```

exit 0：**3749 passed、2 skipped、7 warnings，196.65s**。
两项跳过仍为 `test_decision_chat_acceptance.py:149` 的 directory symlinks unavailable，
`test_harness_shadow.py:277` 的 performance test disabled；7项 warnings 仍为 SWIG/FastAPI 弃用警告。
没有新增 skip/xfail。这是 P2 最小修复后的完整 Agent 最终快照，取代前面的3741历史快照。

### 静态边界与复审核验点

- `git diff --check` exit 0；前述300个Python文件内存编译再次通过，无 bytecode。
- `git diff --exit-code HEAD -- src/rag src/web/rag_presentation.py src/web/app.py src/agent src/web/routes src/web/agent_session_config.py docs/handoff/latest.md` exit 0。
- ChatHandler 本轮只有前述单行变化，T01格式化、owner/session/target及模型输出/stream段均未改。
- 来源6项SHA256、HEAD/status结束复验与本轮开始及首次集成记录完全一致；只读来源没有修改。
- 目的仍为 `codex/chat-input-budget-integration-pr` / `dc937b62456fed58f7bf58ccb368bef641cdd88b`；
  `git status --short --untracked-files=all` 精确为原四路径，index为空，新commit/PR均无。
- 不提交、不发布；本地完成后停写，等待原 QUALITY 复审，不自行标记 APPROVED。

供原 QUALITY 定位本轮最终代码快照的 SHA256：

| 目的文件 | SHA256 |
|---|---|
| src/web/chat_handler.py | F5B03F377DE5DA62DC25BE38569AF1AD40A9B892DE5215FF606258B795234AEC |
| src/web/prompt_budget.py | 87C4096552F596E5A11342D587A1484A8940BA201079D857F6268AA431EA107A |
| tests/agent/test_chat_input_budget.py | 8CB0B12ED7CC9148F4A20BC72059C3BB088820CD6FF31268E1C271EA336A401D |

## P2 修复后的独立复审交接

上述“待复审”为实现者停止写入时点。原 SPEC 与 QUALITY 后续均已 APPROVED：

- SPEC 独立聚焦178 passed、兼容259 passed/5 skipped及120组内存预算/顺序探针通过。
- QUALITY 独立聚焦178 passed、1000组默认行为/选择结果/长度/非历史块位置探针通过。
- 两位审查者均确认审查前后四文件哈希不变，无新增阻断项；没有将实现者完整Agent结果冒充独立全量复跑。
- 父任务据此精确暂存四路径形成本地提交。PR37依赖尚未通过全部CI，故暂不推送本累计分支，不宣称已合并或部署。
