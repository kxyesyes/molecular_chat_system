# 普通聊天剩余延迟优化

本轮承接 [上一轮共享权限修复](2026-10-28-chat-docking-latency.md)，保留原有模型、
RAG、科学工具、权限策略、持久化和任务/API 契约。基准 HEAD 为 `aca4dd59`；
上一轮未提交的修复继续保留。本轮不修改算法、模型参数、JavaScript 或页面布局。

## 耗时证据与修改

上一轮约 12 秒的聊天剖析中，每次构造 Pi bridge 都重新扫描 PATH，约 464 次候选路径检查，
一次耗时约 2.94 秒；12 次会话数据库连接的权限处理合计约 4.0–4.3 秒。
模型调用本身约 1.4–2.1 秒。临时 profiler 只保存函数名、次数和耗时，不保存实参或聊天正文。

| 文件 | 变化与边界 |
|---|---|
| `src/agent/pi_runtime/bridge.py` | 在进程内复用成功发现的 Node 绝对路径；PATH、PATHEXT 或当前目录变化时重新查找。每次使用仍检查文件及执行权限，缺失结果不缓存。显式 Node 参数继续优先。 |
| `src/web/app.py` | 启动阶段在线程中完成第一次 Node 查找。只查找程序，不启动 Pi 子进程、模型或科学工具。Node 缺失仍可启动，后续聊天按原错误路径拒绝执行。 |
| `src/task_runtime/staging.py` | 复用已初始化的 WinDLL 句柄与函数签名。安全描述符、DACL 内容、读取缓冲区和验证结果均不缓存。 |
| `src/task_runtime/private_permissions.py` | 一次 stat 同时获得存在性和类型。若本次实时 DACL 检查通过且不发生写入，直接返回；修复时仍要求写入成功和独立回读成功。保留可选 sidecar 缺失、无效路径及访问错误语义。 |

缓存选定程序后，不会仅因同一 PATH 中更早的目录新增另一个 Node 就替换正在使用的程序；
服务重启或搜索环境改变会重新选择。选定程序删除或不可执行会立即重新查找。
每轮仍创建独立 Pi 进程，保留受限子进程环境、协议校验、取消排空和物理资源归属。
数据库连接、事务提交、事件和 checkpoint 持久化均未省略或改为异步延后。

## 真实聊天计时

使用同一普通问候、同一模型和参数，关闭 RAG、保留工具入口，经正常 WebSocket 链路记录
从发出请求到 `complete` 的耗时。每组在同一连接中连续发送 3 次，不打印回答正文或凭据。
最终组在关闭 profiler、相关测试结束后，重新启动正常服务运行；均为 `completed`，
每次只调用模型一次。

| 样例 | 本轮修改前正常服务 | 最终正常服务 | 最终模型调用 |
|---|---:|---:|---:|
| 首次聊天 | 10.106 s | 9.477 s | 1.674 s |
| 第二次 | 17.682 s | 7.955 s | 1.468 s |
| 第三次 | 13.289 s | 8.303 s | 1.773 s |
| 三次中位数 | 13.289 s | 8.303 s | — |

这组中位数降低约 37.5%。仅 3 次样例，不代表稳定服务分位数或性能保证。
此前开启 profiler 的阶段测量为 7.565、5.284、6.556 秒；最终正常模式没有稳定达到该范围，
因此不以阶段最好结果作为最终承诺。Windows 本地 I/O、独立运行时启动及模型等待仍有开销。
服务最终按 `python main.py --host 127.0.0.1 --port 6001` 运行，首页 HTTP 200；
本次正常进程 PID 41552（仅验收时有效）。

## 测试与审查

新增/扩展测试：

- `tests/agent/test_pi_node_resolution.py`：重复与并发查找、环境/工作目录变化、程序删除、
  执行权限丢失、缺失后恢复及显式覆盖。
- `tests/test_pi_startup_warmup.py`：执行实际 startup 方法，验证查找发生在启动且不创建子进程，
  Node 缺失不阻止启动。
- `tests/test_windows_permission_latency.py`：系统库复用、并发读取、每次实时 DACL 检查、
  修改权限后修复，以及写失败/回读失败时拒绝访问。
- `tests/test_private_permissions.py`：缺失、非目录和无效路径，以及访问错误的兼容性。
- `tests/agent/test_web_decision_runtime.py`：依赖缺失测试同时清空成功查找缓存，
  继续断言没有模型/科学工具/旧运行时回退。

按照 TDD，重复查找/重复系统库加载测试先出现 3 个预期失败；单次权限读和单次 stat
测试先出现 2 个预期失败；启动预热测试先因尚未发生查找而失败。实现后聚焦测试通过。
独立审查未发现阻断性问题；建议的执行权限失效和并发 DLL 读取测试已补充。

最终相关回归结果（不累计重复运行）：

| 范围 | 结果 |
|---|---|
| Windows 权限、私有路径、数据库权限、staging、任务存储 | 506 passed、15 skipped |
| Node 发现、启动预热、聊天会话、Pi runtime/bridge、Agent 持久化 | 226 passed、2 skipped |
| 模型生命周期（排除下述已知失败组） | 35 passed、4 deselected |
| 实际 WebSocket、普通聊天、续接/取消、本地后台任务 | 297 passed、1 skipped |
| `python -m compileall -q src scripts`、`git diff --check` | 通过 |

合计 1064 项通过、18 项跳过，另有下述基准同样失败的用例，不能宣称全部测试通过。
核心权限组在工作区临时目录运行；涉及应用配置及异步子进程的组在正常本机环境、系统临时目录运行。

验证命令（Conda `MedChat`，仓库根目录；Web 相关组不要将 TEMP/TMP 或 basetemp 放入 Git 仓库）：

```powershell
python -B -m pytest tests/test_windows_permission_latency.py tests/test_private_permissions.py tests/test_task_database_security.py tests/task_runtime/test_staging.py tests/test_private_storage_permissions.py tests/task_runtime/test_task_store.py -q -p no:cacheprovider
python -B -m pytest tests/agent/test_pi_node_resolution.py tests/test_pi_startup_warmup.py tests/test_agent_session.py tests/agent/test_pi_runtime.py tests/agent/test_pi_bridge.py tests/agent/test_agent_persistence.py tests/test_model_request_lifecycle.py -q -x -p no:cacheprovider
python -B -m pytest tests/test_model_request_lifecycle.py -q -p no:cacheprovider -k 'not assembled_agent_and_inference_remain_stable_until_switch'
python -B -m pytest tests/agent/test_web_decision_runtime.py tests/agent/test_ordinary_web_runtime.py tests/agent/test_ordinary_web_lifecycle.py tests/task_runtime/test_local_backend.py -q -p no:cacheprovider --tb=short
python -m compileall -q src scripts
git diff --check
```

已确认的测试环境与基准限制：

- Web 测试禁止将模型配置放在 Git 仓库内。初次使用 `scratch` 作为这些测试的 TEMP/基目录
  被安全检查拒绝；改用系统临时目录，不绕过该安全规则。
- `test_assembled_agent_and_inference_remain_stable_until_switch[0-False]` 在等待模型协议事件时
  超过 15 秒。进程内恢复 `aca4dd59` 的 bridge、staging、private_permissions 三个模块后，
  同一用例仍返回 `internal_error` 并超时。它不调用本轮修改的 startup 方法。
  此失败未计为通过；该组其余 3 个参数变体未继续执行，其余模型生命周期测试独立运行。
- Windows 核心回归一次出现原子目录发布失败，后续完整核心套件通过，未确定该次失败原因。
- 未运行整个仓库全量测试、完整 `real/all` 科学验收或 strict 部署检查；本次未改部署、数据或 JS。
  未进行浏览器视觉验收；通过实际 HTTP/WebSocket 请求验证服务和聊天结果。

## 三方体验及交付边界

用户的首条和连续聊天减少本地等待；开发者可用重复调用计数与真实 Windows ACL 测试防止
退化；AI Agent 继续使用相同的身份、授权、任务状态和工具契约，减少等待而不跳过科学校验。
没有新增界面说明或内部实现标签。

性能提交只包含上述源文件、测试及验收记录。运行数据库、日志、临时 benchmark/profiler
和用户配置不纳入提交。代码验收完成后，维护者授权提交并集成全部待交付变更；
`AGENTS.md` 的代码简洁性规则使用独立文档提交，与性能修复一起进入集成 PR。
集成分支为 `codex/integrate-chat-latency`。最终提交、PR 和合并状态以 Git 记录为准。
