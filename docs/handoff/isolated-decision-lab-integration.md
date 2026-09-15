# 隔离验收页面与 CLI 集成交接

日期：2026-09-15。基线 main `40fcd2a`（PR #27）。
分支：`codex/agent-isolated-lab-integration`。状态：实现和独立双审完成，等待 PR/CI 与具体合并授权；未合并、未启用生产。

## 本批内容

- `src/web/decision_lab.py`：显式 FastAPI factory，独立于生产应用；只允许 loopback peer、精确 Host/Origin 和服务器 HttpOnly SameSite 会话。
- `src/web/static/decision_lab/`：保留历史布局的独立观察页面；四个预设验收案例，安全文本显示状态、事件、trace、工具结果、warnings/evidence/artifacts。
- `scripts/run_decision_browser_lab.py`：临时 SQLite、127.0.0.1 监听、无代理头/访问日志的显式启动器。
- `scripts/run_decision_chat_acceptance.py`：聊天、CCO+CCN 性质、澄清后 CCN、无效 SMILES 四案例的隔离验收 CLI。
- `src/web/decision_chat.py`：共享 I/O deadline 的最小兼容修复，避免 Python3.10 下收发恰好完成时吞掉调用方取消。没有改生产路由或首页。
- 新增 `tests/agent/test_decision_lab.py`、`tests/agent/test_decision_lab_lifecycle.py`、`tests/agent/test_decision_lab_cli.py`、`tests/agent/test_decision_chat_acceptance.py` 与 `tests/decision_lab_ui_test.js`。

预设案例定义验收义务及工具权限，并不替代逐轮模型决策。聊天不授权科研工具；性质任务只授权 property_calculator。页面不是自由聊天入口；澄清案例固定以 `SMILES: CCN` 验证恢复，输入其他结构不能满足该预设义务。

## 生命周期与可靠性

- 会话过期/重置时先撤销并等待旧执行清理，再释放容量；shutdown 跟踪所有自有会话任务，包括等待会话锁的阶段，重复取消也不提前释放 registry。关闭/撤销后不会派发新任务。
- 一个会话只运行一个请求；新命令不会排队或重放；续接只在所属会话内可用，终结帧在续接登记后发送。
- WebSocket accept、正文、错误、最终 flush、close 均有30秒发送上限；清理仍需等待实际子任务结束，不把超时视为物理计算已停止。
- 页面建立会话的 headers/body 都有30秒上限；超时恢复显式重试，不把迟到响应当成功，不自动重连重放。事件面板最多保留200条。
- 展示只使用 textContent，不从自然语言猜测 SMILES 或科学属性；截断/脱敏提示可见。

## 验证与失败记录

本轮仅离线模型替身和真实 RDKit；没有读取真实 key/.env/数据集/权重，也没有调用供应商 API。

- 历史 lab 测试：缺模块29 failed → 移植后29 passed。
- 新生命周期测试先暴露重置未等待、final/error/accept/close 无发送上限。初次修复还出现取消 ASGI 外层任务和 Python3.10 取消被吞的问题；均保留 RED 证据并修复为只拥有内部会话任务、共享有界发送。
- 最初桥接+lab 联合72 passed；补过期/重复取消关闭及 active busy-error 清理后，生命周期9 passed、桥接+lab+CLI 聚焦117 passed/1 skipped。新增过期测试曾因一秒cookie的墙钟过期出现429；改为30秒cookie并用注入时钟跨越31秒验证过期，复测通过，不放宽服务端规则。
- CLI：缺脚本10 failed，随后完成安全路径/诊断/输入拒绝回归；42 passed/1 skipped（本机无 symlink 权限，reparse-point 用例通过）。
- 页面：27缺资产失败→27 passed，说明标签1 failed→28 passed；会话headers/body超时2 failed→最终30 passed。
- 10个 Node 脚本全部通过；`node --check src/web/static/decision_lab/app.js`、`compileall -q src scripts`、contract 均通过。
- 首轮整体 Agent/model/fallback 3279 passed/2 skipped/7 warnings（118.73秒），最后覆盖用例加入前的阶段证据。最终整体 **3285 passed/2 skipped/7 warnings（114.46秒）**。7条现有警告来自 SWIG 和 FastAPI on_event 弃用；未在本批无关迁移。
- 独立 SPEC 初审要求补 active busy-error 发送超时测试；补齐后复审27 passed（9生命周期+18桥接传输），无剩余规格阻断。
- 独立 QUALITY 首审复现接收端吞取消、shutdown等待会话锁时取消会绕过清理。新增对应 RED 回归并修复；另补关闭期间接收完成不能派发的回归，修复后桥接+lab78 passed（12生命周期+29lab+37桥接）。此前全量3282 passed/2 skipped（114.79秒）不能覆盖这两个缺陷，保留为阶段证据。
- 独立 QUALITY 复审通过：120 passed/1 skipped、Node30项及语法通过；接收取消/关闭竞态各重复3/3、零模型调用；等锁关闭探针重复取消5次仍等待清理后关闭registry、reset返回503且无遗留lab任务。无剩余重要审查问题；CI 和具体 PR 合并授权待完成。

## PR #28 CI 兼容性修复

已创建 draft PR #28，首个提交 `eb49582`。首次 CI run34949647619：Agent 24 failed/3171 passed/1 skipped，原因均为 CI 固定旧 Starlette 不支持 `TestClient(client=...)`；其它5个执行组通过，聚合门禁失败。保留失败记录，不靠重跑掩盖。

增加模拟旧构造器的 RED 回归，改为仅测试内 ASGI peer scope 注入；未修改生产 loopback/Host/Origin 校验或升级依赖。补充合法 cookie/Host/Origin 下非本地 peer 的 HTTP/WebSocket 拒绝测试。lab+生命周期44 passed；指定 MedChat Python 最终整体 **3288 passed/2 skipped/7 warnings（115.31秒）**，独立增量复审和新 head CI 记录以 PR 为准。

独立增量复审通过：指定 MedChat Python 下 lab32 passed，新兼容性/peer3项另跑通过。审查者首次误用 Conda base 得到22 passed/10 failed，直接原因是该环境缺少langgraph（亦未安装RDKit），不是本批peer注入回归；已纠正解释器并保留失败记录。旧固定依赖的实际运行以新 head CI 为最终验证，不以本机新版本替代。

## 后续显式运行方法（本轮未连接真实模型）

本轮已运行的验证命令（科学依赖位于 MedChat Conda 环境；以下路径相对本工作树）：

```powershell
python -B -m pytest tests/agent/test_decision_chat.py tests/agent/test_decision_chat_transport.py tests/agent/test_decision_lab.py tests/agent/test_decision_lab_lifecycle.py tests/agent/test_decision_lab_cli.py tests/agent/test_decision_chat_acceptance.py -q -p no:cacheprovider --tb=short
python -B -m pytest tests/agent tests/test_agent_decision_model.py tests/test_openai_compatible_model.py tests/test_agent_anti_hallucination_fallbacks.py tests/test_agent_platform_health_check.py tests/test_admet_predictor_fallback.py -q -p no:cacheprovider --tb=short
node tests/decision_lab_ui_test.js
node --check src/web/static/decision_lab/app.js
Get-ChildItem tests -File -Filter '*test.js' | ForEach-Object { node $_.FullName; if ($LASTEXITCODE -ne 0) { throw 'Node regression failed' } }
python -m compileall -q src scripts
python -B scripts/run_agent_acceptance.py --mode contract
```

compileall 的 `PYTHONPYCACHEPREFIX` 指向系统临时目录。两个 skip 分别是显式 opt-in 性能门禁、Windows 缺少目录 symlink 权限；不是科学 case 成功。未运行真实浏览器/供应商/权重验收，当前网页交互证据来自真实页面脚本的离线 DOM/WebSocket 驱动和 FastAPI ASGI 测试。

在拥有依赖的 Python 环境中，由运行时环境提供 `OPENAI_COMPATIBLE_API_KEY`、`OPENAI_COMPATIBLE_BASE_URL`、`OPENAI_COMPATIBLE_MODEL`，不在文件里填写真实 key。Provider endpoint 必须 HTTPS；日志/报告不包含 key 或原始供应商异常。

```powershell
python scripts/run_decision_browser_lab.py --port 6012 --mode native
# 浏览器打开 http://127.0.0.1:6012/decision-lab/
python scripts/run_decision_chat_acceptance.py --mode native
python scripts/run_decision_chat_acceptance.py --mode json
```

CLI 默认输出新建的 `outputs/agent_evaluation/decision_chat_<随机ID>.json`，不覆盖已有报告；缺运行时配置输出 skipped 且退出码2。无效输入案例可以“预期拒绝验证 passed”，但同时保留实际 result_status failed/rejected、工具失败及原错误码；不是科学计算成功。

真实模型调用收费、结构化决策兼容性、真实训练权重效果都需另行验收。当前 loopback cookie 不是公网认证，不得据此公开部署。生产入口、原始混杂树、历史未合并残差均未覆盖。
