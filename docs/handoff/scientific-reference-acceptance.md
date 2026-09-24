# T09 第三增量：故障恢复与浏览器验收

## 范围

分支 `codex/scientific-reference-continuity`，开始于 `212e454`，遵循已确认的科研引用设计。
不修改main、不推送/创建PR/合并/部署、不启用真实主模型或生成模型。原始13项混杂改动不动。
使用TDD、系统化调试及独立SPEC/QUALITY；通过既有测试的行为不人为制造失败。

## 修改文件与发现

- `tests/agent/test_scientific_reference_resilience.py`：13个真实模块/SQLite故障、取消、重放和失效用例。
- `tests/scientific_reference_browser_lab.py`：显式loopback测试应用，复用真实首页、会话、ChatHandler、Supervisor、引用API、SQLite；合成5个候选，仅性质和类药性使用真实RDKit；无生产配置写接口。
- `tests/agent/test_scientific_reference_browser_lab.py`：真实HTTP→WebSocket→ACK→恢复及新实例/异会话隔离测试。
  通过公共ASGI消息观察器与有界队列接收，避免依赖Starlette私有实现；提前complete/error立即失败，静默连接受单一总截止时间约束，退出后验证服务端已排空。
- `src/web/static/js/home/main.js`：浏览器实测发现，刷新时成功恢复的候选被插入隐藏聊天容器，欢迎屏仍显示；最小修复是在有效恢复回调中调用既有 `enterChatMode()`，不在空指针/失败恢复时切换。
- `src/web/templates/index.html`：更新main.js缓存版本，避免旧脚本缓存持续隐藏恢复结果。
- `tests/home_scientific_references_test.js`：执行实际 `connection_ready` 分支，断言渲染前可见且无有效恢复仍保留欢迎页；验证新缓存版本。
- `tests/home_workflow_completion_behavior_test.js`：仅跟随模板更新精确资产版本断言，不移除旧完成行为断言。
- `docs/AGENT_MAINTENANCE.md`、本交接、latest及同日实施计划：定位更新、矩阵和真实失败记录。

## 覆盖矩阵

| 场景 | 实际验证层与结论 |
|---|---|
| 同一引用在新实例重放 | 实际ChatHandler/Supervisor/SQLite；成功性质checkpoint复用，CCN不重复计算；失败的独立步骤允许按原机制重试，仍partial |
| 取消与资源排空 | 两次取消等待方，控制线程事件释放；无gate/有gate两种；工具至多一次，实际线程结束、async任务与读租约排空，源run/checkpoint不变 |
| 读/连接/回滚/不确定提交失败 | 实际引用HTTP路由注入SQLite故障；统一404、无confirmed/科研成功；连接关闭；不确定commit不谎称回滚，恢复后幂等确认 |
| 真SQLite写锁 | 独立连接持有BEGIN IMMEDIATE，测试连接busy_timeout=0用于确定性注入；确认失败、WAL读取仍可用；释放后恢复原视图，生产超时不变 |
| 失败/取消/损坏/恰好到期来源 | 实际chat只澄清、无工具调用、无新科研run；不删除历史 |
| partial恢复 | 原状态、警告、完整事件和expiry经新SQLite实例及HTTP读取保持 |
| 真实浏览器确认与序号 | 5张卡片实际可见、确认后请求第二个，服务器工具输入确认为CCN，页面MW45.08/LogP−0.035/QED0.406来自RDKit |
| 刷新可见性 | 首次复现隐藏卡片错误；Node先失败，最小修复及资产缓存更新后，真实页面显示恢复提示和同一5张卡片 |
| 真实进程重启 | 空闲时停止本轮专属Uvicorn进程，使用同一临时目录重新启动；原浏览器cookie/标签页指针恢复同一候选，无重新生成或性质调用 |
| 重启后checkpoint重放 | 重复相同第二个分子请求，显示原CCN结果，新进程三种工具调用计数均0 |
| 显式卡片选择 | 点击第三张，输入“计算属性”，property/drug-likeness实际收到CCC，不沿用第二个结构 |
| 标签页与清除 | 新建同站标签页无候选引用；原页清除后刷新不恢复，再问第二个明确澄清、工具计数不变 |

浏览器层由协调者在Codex内置浏览器执行；独立审查代理的模块测试不能替代上述人工步骤，也不宣称其重新执行了浏览器。
两会话越权、修订篡改、未知字段、显示去重、并发claim等继续由前两增量测试保护。

## 实际运行记录（保留失败）

1. 浏览器夹具测试首次 **2 failed，0.82s**：模块尚不存在，预期RED。
2. 实现后 **1 failed、1 passed，1.92s**：TestClient相对WS URL使用testserver，Origin是127.0.0.1；属于测试夹具地址不一致，被既有安全中间件正确拒绝。改为同源绝对WS URL后 **2 passed，4.33s**，未放宽安全策略。
3. 后端韧性测试首轮 **12 passed、1 failed**：测试在主线程检查工作线程SQLite连接，触发跨线程错误；将关闭检查留在原线程后通过。未改业务代码或弱化关闭断言。
4. 后端组合引用/生命周期 **266 passed、7 warnings，43.46s**；最终加强的韧性文件 **13 passed，5.42s，无skip**。
5. 浏览器发现隐藏恢复；Node实际回调新增断言RED。加一行进入聊天模式后GREEN。真实reload仍读旧main.js缓存；服务器文件已含新逻辑，DOM仍chat隐藏。新增缓存版本断言RED，更新模板/原版本断言后两Node脚本GREEN；浏览器reload后实际可见。
6. 独立SPEC **231 passed、0 skipped、7 warnings**，五Node、JS语法、diff通过；两个内存负对照去掉可见性修复/回退缓存版本均失败。APPROVED，不等于完整发布验收。

7. QUALITY首审独立 **231 passed、0 skipped、7 warnings，40.94s**，但发现测试辅助函数只限制消息数量、缺失候选事件时可无限等候，暂不批准。追加提前complete/error和静默连接三个回归，**3 failed、2 passed，3.25s**；补总截止时间与终止事件判定后 **5 passed，2.87s**。不修改生产超时、不依赖接收后台线程。

8. QUALITY窄复审 **APPROVED**，原P2已解决，无新增发现；独立隔离 **5 passed，2.97s，exit 0**，diff通过。仅复核测试接收/清理边界，不冒充重新完成浏览器演练。

9. 冻结本批7个生产/测试路径，运行前后逐文件SHA-256一致；最终隔离联合回归 **5609 passed、7 skipped、7 warnings，402.86s，exit 0**。范围为全部 `tests/agent` 与下列五个Web测试模块，不是全仓测试，也不是真实模型验收。

7个skip分别是目录符号链接不可用1项、显式关闭性能测试1项、POSIX目录权限2项、需要两个独立任务store2项、需要配置runtime1项。7个warnings为既有SWIG弃用3项和FastAPI `on_event` 弃用4项；未修改跳过条件或警告规则。五个Node脚本、三个JS语法、编译及diff检查通过。本任务常见凭据模式扫描候选0，不等于完整安全审计。

本批精确暂存11个文件、本地提交；提交标识见本分支 `git log -1` 和本轮回复，避免文档自引用哈希。分支仍为 `codex/scientific-reference-continuity`。未推送、创建PR、合并或部署。原始工作树13项既有混杂改动保持原样。独立审查代理和测试进程已收齐。

## 可复核命令

Python为MedChat Conda环境；既有隔离runner从 `docs/superpowers/plans/2026-09-24-rag-service-extraction.md` 读取，仅替换repo；非必要环境清除，配置/DB使用临时路径，real/canary关闭。

```powershell
$runner | & $python -B -c "import sys; exec(sys.stdin.read())" tests/agent/test_scientific_reference_resilience.py tests/agent/test_scientific_reference_browser_lab.py tests/agent/test_scientific_reference_contracts.py tests/agent/test_scientific_reference_store.py tests/agent/test_scientific_reference_web.py tests/agent/test_scientific_reference_execution.py
$runner | & $python -B -c "import sys; exec(sys.stdin.read())" tests/agent tests/test_agent_session.py tests/test_agent_session_entrypoints.py tests/test_agent_task_ownership.py tests/test_phase2_phase3_routes.py tests/test_web_app_lifecycle.py
python -B -m tests.scientific_reference_browser_lab --state-dir <独立临时目录> --port 6017
node tests/home_scientific_references_test.js
node tests/home_structured_molecule_render_test.js
node tests/home_agent_task_panel_test.js
node tests/home_workflow_completion_behavior_test.js
node tests/frontend_safe_render_test.js
node --check src/web/static/js/home/main.js
node --check tests/home_scientific_references_test.js
node --check tests/home_workflow_completion_behavior_test.js
git diff --check
```

五个Node脚本、三个JS语法检查通过；实际编译使用Conda Python的
`-X pycache_prefix=<本任务临时目录> -m compileall -q src scripts`
并包含三个新增Python测试/夹具文件，exit 0；该字节码临时目录已成功清理。
不修改仓库字节码或使用生产配置。未改部署/运行资产，未运行会探测真实服务的health check或real验收。

## 清理与限制

- 两个自建浏览器标签页已关闭；核对命令行归属后停止本轮专属进程，6017端口无监听。重启演练是空闲时强制终止，旧进程exit1是主动停止结果，不冒充自然优雅退出。
- 本任务临时目录删除命令被工具安全策略拒绝，未绕过。目录仍为 `%TEMP%/medchat-reference-browser-8c4f2dcc59a2401d8afffe66ad5e77b8`，只含本轮合成测试SQLite/会话数据；不能宣称全部临时文件已清理。未将其加入Git。
- 未测试运行中任意指令点硬崩溃后的exactly-once，也未调用Ollama/真实主模型/活性权重/Vina；本次验证科研引用链路，不是整个科研能力或生产负载认证。
- 实测发现既有 `src/agent/tools/drug_likeness_assessment.py:336` 将Lipinski符合解读为“预测具有良好的口服生物利用度”，与属性工具的证据边界冲突。这是未修复的独立科学文案问题，建议下一独立批次TDD处理；不把它混进本批引用修复或隐瞒。
- PR/CI/具体合并授权及生产验收尚未执行；本地通过不等于已上线或整个任务书完成。
