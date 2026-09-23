# TaskStore offload 测试拆分验证

## 范围、授权与结论边界

- 工作分支：`codex/task-store-offload-test`；基线/HEAD：`14b2185e631383dc2115c28181ce0e3cf825cb9a`。
- 仅修改 `tests/task_runtime/test_local_backend.py` 和本交接文档；helper 留在原测试文件，不新增 helper 文件。
- 不修改生产代码、CI、pytest 全局配置；不操作来源工作树，不暂存、提交、发布或创建 PR，不调用真实 API。
- 将原共享 CI 的墙钟门禁移为独立显式 opt-in 性能检查，是用户已经明确批准的**测试语义变化**，不是生产性能修复。
- **原 CI 失败原因尚未定位。本次不保证实际 event-loop gap 小于 50ms，也不证明原 CI 性能问题已修复。** 默认功能测试仅验证指定存储调用确实离开 event-loop 线程，且存储调用受控挂起时 loop 能继续推进。
- 状态：实现及本地验证完成，已停止写入，待 SPEC / QUALITY；本文不是已通过独立审查的声明。

## 诊断证据的来源

父任务移交的证据（本次未重新读取远端 CI，也未将其当作已定位根因）：

- PR #37，CI run `35876507686`，job `107233690215`：原测试有两个 `gap >= 0.05`，约 `0.5575s`、`0.16165s`；汇总 `1 failed, 1549 passed, 3 skipped`。
- 父任务确认相关测试和 `LocalTaskBackend` 相对其 main 比较未变，`TaskStore` 存在 owner 变更。不能据此直接归因于 owner、SQLite、机器负载或 offload。
- 父任务本地当前版本 `1 passed in 1.63s`，其基线 `1 passed in 1.75s`。
- 父任务无文件 profile：11 次 `SlowStore._slow` 均不在 event-loop 线程，max gap 约 `0.016s`。
- 向 loop 注入两次外部 `time.sleep(0.08)` 回调可以触发原断言，仅证明墙钟断言混合归因，不能反推 CI 原因。

本次工作树检查：初始干净，分支/HEAD 与指定值一致；本地 `origin/main` 也在 `14b2185`，本地 `main` 则是较旧引用，未将旧引用的大 diff 当作 PR #37 的差异证据。

## 已批准设计与执行顺序

1. 原测试修改前本地复跑，保留基线结果。
2. 原测试名保留为默认功能测试，按 `create/get/list/claim_running/request_cancel` 参数化。
3. 在真实 `LocalTaskBackend`、真实临时文件 SQLite 的存储入口设置线程身份检查和同步门；成功路径调用原绑定的真实 `TaskStore` 方法。
4. 先运行不捕获异常的内存 inline 变异取得 RED，再以精确异常匹配保存防退化测试，运行正常 offload 的 GREEN。
5. 原墙钟测试体原样移动到独立性能测试；仅该测试使用专用 opt-in skip。
6. 直接变异默认功能测试再次保存 RED 证据，运行完整文件及 task_runtime 回归，核对文件边界与既有测试不变性。

## 功能测试如何证明执行与进展

- 默认功能测试不替换 `asyncio.to_thread`，只包装被观察的真实 store 实例方法；释放门后调用原绑定方法。返回记录、handler 已启动以及通过新 SQLite reader 读出的真实持久化状态均被断言。
- `create`、`claim_running` 在 submit 前布置门；`get/list/request_cancel` 在真实任务已 RUNNING、handler 挂起后布置门。这样 `create` 内部的嵌套 `get` 不能冒充 public `backend.get` 的覆盖。
- worker 通过 `call_soon_threadsafe` 报告线程 ID；**先检查线程身份，再执行阻塞式 Event.wait**。inline 回归立即失败，不会让 loop 等待由自己释放的门。
- loop 收到进入通知后启动独立协程，显式 yield，再断言门尚未释放、存储调用尚未返回；只有该协程推进成功后才释放 worker。
- `10s` 仅是握手/故障上限，不是性能 SLA，也没有放宽或替代原 `0.05s` 阈值。
- `finally` 无论哪个断言失败，都恢复被包装方法、释放 worker 和 handler，gather 排空 public operation，再 close backend 排空 runner；检查后台任务数为零。被 shield 的 operation 不因观察超时直接取消而遗留 to_thread worker。
- 持久化检查：create/claim/get/list 路径读出 RUNNING；cancel 路径读出 CANCEL_REQUESTED。handler 由测试控制释放，不依赖调度睡眠猜测状态。

内存变异测试仅将 backend 模块的 `asyncio` 引用换成局部 proxy，目标方法的 `to_thread` 改为直接调用；其余属性和调用继续使用真实 asyncio。每次参数化测试结束恢复引用；不修改磁盘生产代码。永久负向用例复用与默认功能测试完全相同的 helper，并且只接受对应方法的线程身份 AssertionError，不接受 TimeoutError 或任意失败。

## 性能检查与 skip 边界

新性能测试名：`test_async_store_calls_and_run_claim_event_loop_latency_performance`。

仅 `MEDCHAT_RUN_TASK_STORE_OFFLOAD_PERF=1` 启用；未设置、`0` 或其他值均跳过，skip reason 明示环境变量、受控 runner 建议以及 shared-CI gap 无法归因于 offload。未添加全局 marker、全局 skip、CI 条件或重试。

原 SlowStore 的 `sleep(0.06)`、ticker 的 `sleep(0.005)`、调用序列与以下断言全部保留原值：

```python
assert len(ticks) >= 20
blocking_gaps = [gap for gap in gaps if gap >= 0.05]
assert gaps and len(blocking_gaps) <= 1
```

AST 检查确认原性能函数体与新性能函数体相同；另 95 个既有顶层函数（含 helper、fixture、参数化测试函数）的 AST 均未改变。没有用新 skip 隐藏其他既有测试。

## 验证环境与命令

本地 Windows，Conda `MedChat` Python 3.10.20。所有 pytest 调用均使用 `python -B`、`PYTHONDONTWRITEBYTECODE=1`、`-p no:cacheprovider`。本节变量方式定位同一个实际执行的解释器，不需要新增机器路径配置：

```powershell
$python = Join-Path $env:USERPROFILE '.conda/envs/MedChat/python.exe'
$env:PYTHONDONTWRITEBYTECODE='1'
$env:MEDCHAT_RUN_FAMILY_REAL_ACCEPTANCE='0'
$env:MEDCHAT_RUN_REAL_EXTERNAL_TESTS='0'
$env:MEDCHAT_RUN_REAL_TESTS='0'
$env:MEDCHAT_RUN_OPENSANDBOX_ACCEPTANCE='0'
$env:RUN_REAL_TARGET_SEARCH='0'
$env:MEDCHAT_RUN_TASK_STORE_OFFLOAD_PERF='0'

& $python -B -m pytest -p no:cacheprovider tests/task_runtime/test_local_backend.py -k 'async_store_calls or offload_contract' -q -rs
& $python -B -m pytest -p no:cacheprovider tests/task_runtime/test_local_backend.py -q -rs
& $python -B -m pytest -p no:cacheprovider tests/task_runtime -q -rs

$env:MEDCHAT_RUN_TASK_STORE_OFFLOAD_PERF='1'
& $python -B -m pytest -p no:cacheprovider tests/task_runtime/test_local_backend.py::test_async_store_calls_and_run_claim_event_loop_latency_performance -q
$env:MEDCHAT_RUN_TASK_STORE_OFFLOAD_PERF='0'
```

### RED / GREEN 实测

| 验证 | 结果 |
| --- | --- |
| 修改前原测试，指定原 node ID，`-q` | `1 passed in 2.11s` |
| 初次变异 RED，尚未加入 `pytest.raises`，`-k offload_contract_rejects_inline -q --tb=short` | `5 failed, 111 deselected in 2.10s`，五个方法均为精确线程身份失败 |
| 加入精确预期异常后的聚焦 GREEN | `10 passed, 1 skipped, 105 deselected in 1.55s` |
| 对默认功能测试直接施加内存变异（下节脚本） | `5 failed in 2.16s`；exit 1，五个线程身份断言失败，无 timeout |
| opt-in 原性能测试 | `1 passed in 1.79s`；仅代表此次本地运行 |
| 完整 `test_local_backend.py` | `115 passed, 1 skipped in 10.12s`；唯一 skip 为新性能测试 |
| 完整 `tests/task_runtime` | `1539 passed, 22 skipped in 131.60s (0:02:11)`；无失败 |
| 测试源码内存 compile、AST 不变性、`git diff --check` | PASS；不产生字节码缓存 |

task_runtime 的 22 个 skip 中，仅 1 个是本次新增的 opt-in 性能检查；其他 21 个均来自未改动测试的既有 Windows symlink 权限、open-file replacement 或 POSIX-only 条件：docking execution 2、staging 8、task store 1、temporal acceptance 2、temporal config 7、temporal prometheus 1。未新增这些 skip，也未隐藏这些测试。

父任务在本次验证期间告知另有 T01 fullAgent 占用资源。功能门不以 50ms 调度为前提；opt-in 原墙钟测试只执行一次且通过，没有调阈值，也没有失败后重跑洗绿。这不是隔离环境性能认证。

说明：直接变异默认测试的首个一次性验证脚本错误地在 setup hook 注册 finalizer，产生 `5 errors in 1.38s`，并未进入目标断言，**不作为 RED 证据**。仅修正内存脚本为 call hook 的 context manager 后得到表中的 `5 failed`。测试/生产文件不受这个脚本修正影响。

### 可复现的默认功能测试变异证据

沿用上节环境（性能开关为 0），在工作树目录运行以下无文件脚本。预期 exit 1；恢复正常进程后，默认功能测试预期全部通过。

```powershell
@'
import asyncio
import sys
from types import SimpleNamespace
import pytest
from src.task_runtime.backends.local import LocalTaskBackend

class InlineSelected:
    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_call(self, item):
        method = item.callspec.params['method_name']
        async def inline_selected(func, /, *args, **kwargs):
            if getattr(func, '__name__', None) == method:
                return func(*args, **kwargs)
            return await asyncio.to_thread(func, *args, **kwargs)
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(sys.modules[LocalTaskBackend.__module__], 'asyncio',
                          SimpleNamespace(**{**vars(asyncio), 'to_thread': inline_selected}))
            yield

raise SystemExit(pytest.main([
    '-p', 'no:cacheprovider', '-q', '--tb=short',
    'tests/task_runtime/test_local_backend.py::test_async_store_calls_and_run_claim_do_not_block_event_loop',
], plugins=[InlineSelected()]))
'@ | & $python -B -
```

五个默认功能测试的实际失败信息分别为：

```text
AssertionError: TaskStore.create ran on the event-loop thread
AssertionError: TaskStore.get ran on the event-loop thread
AssertionError: TaskStore.list ran on the event-loop thread
AssertionError: TaskStore.claim_running ran on the event-loop thread
AssertionError: TaskStore.request_cancel ran on the event-loop thread
```

## 交接审查重点

- SPEC：确认批准的语义拆分、五条真实 backend/store 路径、仅一个专用性能 skip，以及不作 CI 根因/50ms 性能承诺。
- QUALITY：检查线程身份判断发生在阻塞前；worker 未释放期间独立 loop 协程确实推进；正常/失败路径释放和排空完整；真实 SQLite 断言不由 mock 替代；永久变异测试与默认契约一致。
- 未进行跨平台共享 CI 重跑、真实性能归因或生产修复。CI 原因仍待单独调查。
- 未运行写入字节码的 compileall；测试源码使用内存 compile 检查以遵守本任务 no-cache 要求。未改 JS、部署或数据，无需相应检查。
- 保持未暂存、未提交、未发布；无新 commit / PR。等待 SPEC / QUALITY，不代表已获最终合并批准。
- 最终范围核对：tracked diff 只有指定测试文件，untracked 只有本交接文档，index diff 为空；分支及 HEAD 保持不变。

## 独立双审与发布交接追加

以上未提交/待审状态为实现者交接时点；后续独立 SPEC 与 QUALITY 均 APPROVED，无阻断项。

- SPEC 复跑完整文件115 passed/1 skipped，opt-in性能单次1 passed；独立AST确认原性能body及95个既有函数不变。
- QUALITY 精确10 passed/1 skipped/105 deselected；另做15个内存探针覆盖五方法的推进失败、存储异常、inline回归，无loop回调异常或遗留asyncio/backend任务。claim_running存储异常需等10秒握手超时后收尾，不把它当性能数据。
- 当前受控调用只进入一次；entered.set_result不支持多次通知。若未来扩展多次调用需单独处理，当前没有相应触发路径。
- 父任务据此精确暂存两文件形成本地提交并发布独立draft PR；仅全部7项CI通过及无未解决审查项后才能合并。PR #37原始失败仍保留，后续仍需整合模板兼容等依赖并重新验证，不能以本批双审代替。
