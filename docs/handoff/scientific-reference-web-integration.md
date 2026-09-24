# T09 第二增量：Web 科研引用接线

## 范围与当前状态

基于第一增量 `632af8b`，在独立分支 `codex/scientific-reference-continuity` 实施。
已批准设计与同日实施计划分别见 `docs/superpowers/specs/2026-09-24-scientific-reference-continuity-design.md`
和 `docs/superpowers/plans/2026-09-24-scientific-reference-web-integration.md`。
本批不启用真实模型、不部署、不修改 main；原始混杂工作树的13项改动未触碰。

实现代理中途因额度限制退出；协调者继续接手，保留代码和失败记录，不把中断当完成。
以下列出本轮实际收齐的验证；中断代理未交付的早期 RED 命令不作为证据。
最终质量审查及冻结回归结果见本文件末尾；未记录前不能视为发布就绪。

## 改动与前后行为

- 原来候选显示后不能权威续接；现在服务端从归属绑定的最新 checkpoint 匹配观察，
  按前端去重/整事件容量规则生成展示清单。前端严格协议无法确认的情况保留旧展示，但不发布可用引用。
- 原来只有候选事件；现在附加可选 reference 元信息，旧无 reference 事件仍可显示。
  必须实际挂载卡片且顺序一致才 ACK，成功后只保存标签页级 trace/presentation/revision。
- 新增受已有会话中间件保护的 confirm / restore 接口。身份仅来自服务端 scope，
  16KiB 请求上限，存储在线程池执行；越权、不存在、过期、损坏统一不可用。
- 刷新/重连重新验证源版本、归属、固定24小时期限。恢复不续期、不重新生成，不默认搜索用户最后一个 run。
  多集合必须明确选择；渲染、ACK、存储失败均不宣称引用可用；过时异步响应不覆盖新操作。
- 已解析的候选结构通过专用服务端对象、Supervisor、AgentContext、Planner 传入实际工具。
  测试捕获了 property/drug-likeness 的 `CCN` 输入及 activity 的新靶点输入；不是只验证 prompt 或 metadata。
- 新 SMILES 优先于旧选择；无效新输入仍走既有校验；普通聊天、关闭工具、独立靶点搜索不受旧选择劫持。
  改靶点只转移结构，不沿用旧活性/能量；缺 receptor/box 仍不能 docking。
- 引用请求复用已有原子 claim / checkpoint 重放，幂等身份包含归属、视图修订、候选、请求与执行选项。
  不把来源 trace 当新执行 trace；源科研记录和24小时期限保持原样。
- 工具实际分发和已完成 checkpoint 复用前再次验证引用，规划期间来源失效时返回结构化 INVALID_INPUT，
  不执行工具。校验不跨长任务持有数据库锁，也不声称能取消已经开始的工具调用。
- 英文范围/列表和多义选择明确澄清，不默取第一个；选择集合 A 不再干扰已挂载集合 B 的异步确认。
- 未经确认的 target 明确为 null，不从自由文本推断为已验证靶点，也不自动复用历史靶点结果。

## 文件职责

- `src/agent/contracts/resolved_molecule.py`、`context.py`：服务端已解析结构与上下文传递。
- `src/agent/persistence/base.py`、`sqlite_store.py`、`scientific_references.py`：受归属保护的来源读取，复用既有事务/科学校验；不加表。
- `src/agent/supervisor.py`、`planning/task_planner.py`：再次验证、幂等 claim 和实际输入绑定。
- `src/agent/runtime/run_session.py`：checkpoint 复用及真实工具分发前的引用失效防护。
- `src/web/scientific_references.py`：展示投影、指针、恢复及有界选择解析。
- `src/web/routes/scientific_reference_routes.py`：有界 ACK/恢复 HTTP 协议。
- `src/web/chat_handler.py`、`app.py`：使用同一个应用 store/service 的薄接线。
- `src/web/static/js/home/scientific_references.js`：确认、选择、清除、标签页指针与恢复控制器。
- `src/web/static/js/home/molecule_candidates.js`、`main.js`、`config.js`、`src/web/templates/index.html`：严格元信息校验、真实挂载、协议地址及加载。
- `tests/agent/test_scientific_reference_web.py`、`test_scientific_reference_execution.py`、`tests/home_scientific_references_test.js`：SQLite/ASGI/应用工厂/两轮聊天/实际工具输入/Node控制器与挂载回归。

## 实际验证记录（保留失败）

采用第一增量同样的临时环境 runner：MedChat Conda Python、清除非必要环境、隔离配置/任务/会话/Agent库，
关闭 real/canary。所有候选/工具替身仅用于离线契约，不能称为真实模型或科学预测验收。

1. 接手首轮四个引用模块：**1 failed、186 passed、7 warnings，43.01s**。
   失败为 Supervisor 先验证旧引用，挡住显式新 SMILES。最小优先级修复后执行链 **21 passed，4.98s**。
2. 新增独立任务回归：**2 failed、25 passed，5.62s**。含“对接”的靶点搜索被旧选择拦截/污染。
   复用无LLM的既有本地路由排除独立任务后 **27 passed，6.27s**。
3. SPEC 初审发现前端拒绝事件干扰后续显示序号、独立失败生成步骤使全部来源不可用；已有新增回归通过。
4. 警告共享预算、源状态不一致、长警告恢复：实际 **3 failed、14 passed，5.07s**；
   前两者在投影入口拒绝，恢复复用初次显示的有界警告投影且不修改存储原文；**17 passed，4.98s**。
5. rejected 条目的数组/字符串/null details 和未知字段：实际 **4 failed、17 passed，7.55s**；
   严格对齐浏览器形状后 **21 passed，5.18s**。Node实际 normalizer 加入同样形状及共享警告预算用例。
6. 独立最终 SPEC：**200 passed、7 warnings**，Node/语法/diff检查通过，APPROVED。
   这是规格批准，不是质量批准或发布。
7. 探索性 Agent+五个 Web 模块联合运行：**5562 passed、7 skipped、7 warnings，392.60s**。
   测试期间进行了上述后续修复，因此不把该次视为最终冻结源码验证。
8. 五个Node脚本、四个改动JS语法检查、临时bytecode目录下 `compileall src scripts` 通过并清理产物。
9. 第二次中间联合运行 **5575 passed、7 skipped、7 warnings，378.75s**；质量修复期间源码变化，
   同样不作为最终冻结验证。
10. QUALITY 复现英文范围只取第一个、集合选择干扰兄弟集合 ACK、规划中来源撤销后仍调用工具。
    执行链新增用例首轮 **9 failed、28 passed，7.66s**；其中两个 tool_started 场景的测试夹具
    使用了错误事件键 `type`，随后改为真实 `event`（中间 **2 failed、35 passed，7.01s**）。
    业务修复及夹具纠正后 **37 passed，6.66s**。Node 兄弟集合确认断言先失败，独立 epoch /
    selectionRevision 后通过。引用及 runtime/ownership/dynamic 聚焦 **274 passed、7 warnings，30.98s**。
11. QUALITY 独立回归 Web/执行 **58 passed**、WorkflowRunSession **46 passed**，Node/语法/diff通过，
    批准该快照；后续解析增补另行复审，不将前次批准自动用于新改动。
12. SPEC 增量发现 `candidate #1 and #2`、`first molecule and second` 绕过：
    **2 failed、39 passed，9.01s** → **41 passed，7.70s**。
    再补 `first molecule, second`、`candidate 1 and second`：
    **2 failed、41 passed，12.06s** → 合并有界名词/序号/连接符保护后 **43 passed，9.38s**。
13. 最终解析 SPEC 独立 **43 passed**；QUALITY 独立范围/列表 **10 passed**、单项兼容 **6 passed**，
    两者 APPROVED、diff通过。其余先前模块审查仍有效。审查代理已关闭。
14. 第三次中间联合运行 **5585 passed、7 skipped、7 warnings，467.41s**；启动后仍发生解析修复，
    只作中间证据。最终运行另在20个源码/测试文件 SHA-256 冻结后发起，不混淆两轮结果。

## 命令

工作目录为本独立 worktree。`$runner` 取既有
`docs/superpowers/plans/2026-09-24-rag-service-extraction.md` 的隔离 heredoc，仅替换 repo 为本工作树。
`$python` 为 `C:/Users/xkx52/.conda/envs/MedChat/python.exe`；runner 只接受路径参数。

```powershell
$runner | & $python -B -c "import sys; exec(sys.stdin.read())" tests/agent/test_scientific_reference_web.py tests/agent/test_scientific_reference_execution.py tests/agent/test_scientific_reference_store.py tests/agent/test_scientific_reference_contracts.py
$runner | & $python -B -c "import sys; exec(sys.stdin.read())" tests/agent tests/test_agent_session.py tests/test_agent_session_entrypoints.py tests/test_agent_task_ownership.py tests/test_phase2_phase3_routes.py tests/test_web_app_lifecycle.py
node tests/home_scientific_references_test.js
node tests/home_structured_molecule_render_test.js
node tests/home_agent_task_panel_test.js
node tests/home_workflow_completion_behavior_test.js
node tests/frontend_safe_render_test.js
node --check src/web/static/js/home/scientific_references.js
node --check src/web/static/js/home/molecule_candidates.js
node --check src/web/static/js/home/main.js
node --check src/web/static/js/home/config.js
git diff --check
```

## 最终冻结验证与本地交付

独立 SPEC / QUALITY（含后续解析增量）均 APPROVED；无未解决阻断审查项。
固定20个生产/测试路径，运行前后逐文件 SHA-256 一致。最终隔离 Agent + 五个 Web 模块：
**5591 passed、7 skipped、7 warnings，447.74s，exit 0**。这不是全仓测试或真实科学模型验收。

7个 skipped 原因：目录符号链接不可用1项；显式关闭的性能测试1项；POSIX目录权限2项；
需要两个独立任务store2项；需要配置runtime1项。未修改跳过条件、断言或超时。
7个 warnings 为既有SWIG弃用3项与FastAPI `on_event` 弃用4项，未隐瞒或当成本批已修复。

冻结快照上的五个Node脚本、四个JS语法检查、临时字节码目录下 `compileall -q src scripts`
均通过；实际编译调用 `compileall.compile_dir(..., quiet=1, force=True)`，临时目录清理成功。
`git diff --check` 通过；仅本任务文件的常见凭据模式扫描候选0（不等于完整安全审计）。
原始工作树仍是原有13项混杂；本批只精确暂存23个路径，本地提交，不推送/创建PR/合并/部署。
提交标识见本分支 `git log -1` 和本轮最终回复，避免文档写入自引用commit哈希。

## 后续范围

T09 第三增量仍需系统性取消/故障/重启/重放矩阵、真实浏览器恢复验收与最终整合审查。
本批已经包含真实模块的两轮输入闭环及新 SQLite 实例恢复，但不是运行中站点/真实外部模型验收。
本批没有新 PR/CI/推送/合并或部署声明；全任务书剩余模块清理不在本批顺带完成。
