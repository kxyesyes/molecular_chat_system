# Windows 聊天与对接延迟修复验收

## 范围与根因

分支：`codex/fix-chat-docking-latency`。基准：`aca4dd59`。

普通聊天和独立对接页面共用的私有文件权限处理，每次访问数据库都调用
`SetNamedSecurityInfoW` 重写目录 DACL。Windows 会向现有子项传播目录权限，
因此运行目录越大，重复写入越慢，查询和任务提交也受到影响。

`src/task_runtime/staging.py` 现在先读取当前完整 DACL，已经满足要求时跳过写入；
不缓存路径的权限状态，调用方仍做后置验证。校验覆盖完整 ACE、权限掩码及继承标记，
只容忍等价 ALLOW 顺序和 Windows 的自动继承控制标记 `AI`。权限发生变化时仍修复。
API、事件、数据库结构及科学计算参数未改变。

## 同机真实请求对比

使用本机 Conda `MedChat` 环境和运行中的 HTTP/WebSocket 服务。以下为单次配对样例，
不是统计分位数或服务时延保证。记录不含凭据、会话标识和用户原始消息。

| 操作 | 修复前 | 修复后 |
|---|---:|---:|
| 普通问候聊天完成 | 23.930 s | 12.673 s |
| 聊天模型调用（均一次） | 1.966 s | 1.968 s |
| 对接任务列表 | 约 7.2 s | 0.037 s |
| 对接任务提交返回 202 | 84.937 s | 9.733 s |
| 对接从提交至观测到成功 | 121.134 s | 31.340 s |
| 对接进度查询 | 约 4.9–6.7 s | 约 0.5–1.0 s |

两次对接均使用仓库样例 `data/samples/MAGL_5zun.pdb` 和 `data/samples/5.sdf`；
中心 `(5.99, 3.01, 17.345)`，盒子 `(20, 20, 20)`，`exhaustiveness=8`、
`num_modes=10`、`energy_range=3`、`manual_center=true`。均返回 `succeeded`，
无终态错误码。本报告只验证执行和时延，不声称预测分数具有实验效力或两次分数完全相同。

临时 cProfile 测量显示修复后的聊天仍有运行时启动、可执行文件路径查找及会话持久化开销；
未通过降低安全校验、跳过持久化或改变模型来掩盖这些开销。

## 回归与边界

- 新增 `tests/test_windows_permission_latency.py`，先建立失败测试再修复。
- 测试覆盖重复调用不写 DACL、实时权限变化、公用读取授权、只读权限、额外 DENY、
  同一路径文件替换，以及目录继承标记 `CI`、`OICIIO`、`OICINP` 的检测与修复。
- 初轮权限与 staging 回归：184 passed、8 skipped。
- 最终分批回归：权限/暂存/任务存储 491 passed、15 skipped；聊天会话/Pi 运行时
  205 passed、2 skipped；本地后台任务 116 passed、1 skipped。合计 812 passed、18 skipped。
- `python -m compileall -q src scripts`、`git diff --check`：通过。
- 独立代码审查未发现阻断性问题，建议的目录继承测试已补充。
- `test_local_backend_submits_and_requests_cancel` 等待 handler 的 `started` 事件时停滞；
  恢复基准版本的两个权限函数后，单独执行同一测试仍超过 25 秒，确认基准也可复现。
  沙箱内两轮扩展检查被中止，不能算通过。改用正常本机执行环境后，聊天/Pi 和本地后台任务
  文件分别全部完成，结果如上。
- 一轮本机合并套件出现 `MoveFileExW` 目录发布失败；该用例在核心回归中通过，
  本机单独复查也为 1 passed。未确定该次失败原因，不将该轮合并套件计作通过。
- 未运行全量测试、完整 `real/all` Agent 验收及 strict 部署检查；本次未改部署或数据资产。
- 未改 JS，`node --check` 不适用。最终浏览器自动化接口不可用，不能声称完成视觉验收；
  正常启动服务后首页 HTTP 200，真实聊天与对接由同一页面使用的 API/WebSocket 验证。

最终命令（仓库根目录，Conda `MedChat`；每组使用独立的 `--basetemp=scratch/...`，
`TEMP`/`TMP` 也指向工作区的临时目录）：

```powershell
python -B -m pytest tests/test_windows_permission_latency.py tests/test_private_permissions.py tests/test_task_database_security.py tests/task_runtime/test_staging.py tests/test_private_storage_permissions.py tests/task_runtime/test_task_store.py -q -x -p no:cacheprovider
python -B -m pytest tests/test_agent_session.py tests/agent/test_pi_runtime.py tests/agent/test_pi_bridge.py -q -x -p no:cacheprovider
python -B -m pytest tests/task_runtime/test_local_backend.py -q -x -p no:cacheprovider
python -m compileall -q src scripts
git diff --check
```

第二、三组在正常本机环境运行；第一组在工作区执行沙箱内运行。重启后的正常服务进程为
PID 41724（仅本次验收时有效），监听 `127.0.0.1:6001`。

## 三方体验与兼容性

- 用户：减少聊天等待、任务提交及进度查询阻塞，保留具体失败状态。
- 开发者：保留单一权限实现，新增可重复的 Windows 权限回归测试。
- AI Agent：相同任务/API/事件契约下减少等待，保持权限、来源记录和终态真实性。

未改变页面文案、布局或对接算法。运行产物、临时 profiler、数据库及测试输出均不纳入交付文件。
回滚只需撤销本次 `staging.py` 的修改，恢复原权限写入行为；无需迁移数据。
