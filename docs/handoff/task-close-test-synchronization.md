# 任务关闭测试同步与失败清理

日期：2026-09-12；分支 `codex/task-close-test-synchronization`，基于 main `15061b2`。
这是历史代码集成期间发现的 CI 阻断修复，不修改生产后端或前端行为。

## 原始证据与根因

PR #15 当前 `33eed11` 的 CI run `34687945728`、job `103538135269`：
task-runtime **1548 passed / 1 failed / 3 skipped**，随后进程超时退出 124。
失败测试 `test_close_waits_for_real_sync_worker_before_cancel_terminal` 在启动关闭后
固定等待 50ms，即断言持久化状态必须为 `CANCEL_REQUESTED`；真实取消写入经线程池
调度和 SQLite 提交，无法保证 50ms 内完成。断言失败又跳过 `release_worker.set()`，
遗留的同步工作线程阻止测试进程退出。

隔离探针分别延迟真实 `request_cancel`、在释放前注入断言：旧测试两种情况均
**1 failed**，受控子进程 15 秒截止后仍未退出，需要终止。探针仍使用真实 TaskStore、
LocalTaskBackend、线程和取消流程，不伪造状态。未证明生产后端取消机制本身有错，
所以本批不改生产实现。

## 最小修复

仅修改 `tests/task_runtime/test_local_backend.py` 的该测试：

- 断言真实工作线程已启动；等待取消事件并设截止时间，不再以固定睡眠推断写入完成。
- 正常和慢存储两种参数场景；200ms 延迟仅用于故障注入。
- 取消后仍检查真实持久化状态、取消事件、无终态、无 finished_at、线程未退出及后台追踪。
- `finally` 无条件释放工作线程并等待关闭；工作线程等待自身也有失败上界。
- 工作线程退出后必须恰好一个 canceled 终态、后台追踪为零。

## 验证

使用 MedChat Conda Python 3.10.20，所有 pytest 使用 `-B -q -p no:cacheprovider`。

| 验证范围 | 实际结果 |
|---|---|
| 修复后的目标测试 | 2 passed |
| 额外慢存储探针 | 2 passed，正常退出 |
| 释放前断言注入 | 2 个预期失败，正常退出；线程退出且追踪为零，不计为 passed |
| `tests/task_runtime/test_local_backend.py` | 106 passed |
| 20 次全新进程重复运行目标测试 | 40 passed |
| `tests/task_runtime` | 1530 passed / 21 skipped（Windows） |

语法检查与 diff-check、compileall、全部8个Node脚本及contract通过。探针、首轮失败
日志仅在本分支 ignored `scratch/`；不提交临时报告。

### 审查暴露的假阳性与修复

初轮独立规格审查以“第一次 close 提前成功返回、第二次 cleanup close 补救”为故障注入，
发现测试仍会通过，五次新进程共10个假阳性。父任务复现2个假阳性后，把第一次 close 的
工作线程退出、canceled状态、唯一终态与追踪为零断言移到 protected try 内、cleanup之前。
相同故障注入现在 **2个预期失败**，且清理正常退出；不是把错误后端判为通过。
最终本文件重新运行 **106 passed**。此前联合 `tests/task_runtime tests/agent` 为
**3607 passed / 22 skipped / 7 warnings，113.33秒**；该轮加载的是审查前版本，
不能替代最终补丁的复验。

最终规格复审通过：106项及三类故障各2个预期失败均正常退出。质量审查通过：106项、
10次新进程20项、慢存储2项；三类既有故障各2个预期失败，以及启动失败/信号前关闭/
缺取消信号/提前终态四类各两轮，共16个预期失败，均在20秒截止前退出且清理完成。
没有把故障注入的预期失败计入成功数量。

父任务最终重新执行 `python -B -m pytest tests/task_runtime tests/agent -q
-p no:cacheprovider --tb=short`：**3607 passed / 22 skipped / 7 warnings，107.92秒**。
此轮在首次关闭断言修复后启动；最新Linux CI仍需在PR创建后验证。

## 边界与下一步

不跳过或放宽取消安全断言，不以重跑成功擦除原 CI 失败。21 个 Windows 跳过项不能算
通过，Linux CI 尚待本 PR 验证；本批也不解决先前独立沙盒 artifact_failed 待查项。
合并需 CI 通过、无未解决审查及用户针对本 PR 的具体授权。之后再把已合并修复带入
前端 PR #15 复验，而不是把任务运行时测试变化混入前端主题。
未读取真实密钥、CSV、模型权重，未训练、激活或重启服务，原始混杂工作树保持不变。
