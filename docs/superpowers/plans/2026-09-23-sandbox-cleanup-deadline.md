# 沙盒清理截止时间测试分层实施计划

> **For agentic workers:** 使用 subagent-driven-development：独立时钟 helper 与主场景实现分离，之后规格/质量双审。

**Goal:** 修复混合墙钟计时导致的清理测试不稳定，不修改生产语义。
**Architecture:** 测试局部逻辑时钟驱动真实 asyncio timers；单独的真实 SQLite 场景验证状态和生命周期。共享既有测试构造器，不造新的 broker。
**Tech Stack:** Python 3.10、asyncio、pytest、现有 SQLite broker 测试。

## Task 1：可控时钟（独立实现者）

文件：`tests/sandbox_broker/controlled_clock.py`、`tests/sandbox_broker/test_controlled_clock.py`。

- [x] 先写回归并记录红测：真实 call_later / wait_for 在边界前不触发、边界后触发；ready 工作先于推进；取消 timer 不执行；退出恢复 loop.time 和分辨率；非法倒退/非有限推进被拒绝。
- [x] 最小实现下述测试 API，不 mock asyncio.wait_for：

```python
with ControlledClock(asyncio.get_running_loop()) as clock:
    task = asyncio.create_task(asyncio.wait_for(blocked(), timeout=0.02))
    await clock.drain()
    await clock.advance(0.019)
    assert not task.done()
    await clock.advance(0.001)
    with pytest.raises(asyncio.TimeoutError):
        await task
```

- [x] advance 先 drain 当前 ready/到期 timers，再依次走到目标前的下一 timer，不跳过中间回调；drain 有界，防止无穷 ready 链挂死。
- [x] 运行 `python -B -m pytest tests/sandbox_broker/test_controlled_clock.py -q -p no:cacheprovider`，记录真实结果。

## Task 2：逻辑截止时间场景（主任务）

文件：`tests/sandbox_broker/test_service.py`，只修改原失败场景附近的测试。

- [x] 保留原 CI 红测与存储延迟受控红测作为修改依据。
- [x] 将 fake SDK 抽成该组测试的小型共享 fixture，增加 run_cancelled/destroy_started 事件供阶段同步；继续保留 cancel-resistant 行为。
- [x] 在当前 loop 安装 ControlledClock，真实 service.start/submit/run 后记录起点，取消作业。
- [x] run grace 到期前不得开始 destroy；destroy 到期前终态等待仍 pending；到期后取得 FAILED / cleanup_failed；stop 到期前 pending，到期后 StopIncomplete 且 shutdown 仍 pending。
- [x] 从原起点至首次 stop 返回验证 `elapsed < 0.5` 逻辑预算；释放 run/destroy 后正常 stop，验证全部 broker 任务消失。
- [x] finally 总是释放 SDK 并排空服务/场景任务；任何中间断言失败同样退出。
- [x] 增加参数化变异场景：延长 run/destroy/stop timeout 应被该阶段断言拒绝；提前超时、任务跟踪丢失也必须失败。变异仅作用于测试实例或测试进程。

## Task 3：真实 SQLite 集成

文件：`tests/sandbox_broker/test_service.py` 原场景附近。

- [x] 常规 loop 下复用同一 SDK 和真实临时数据库，正常/延迟存储两种场景。
- [x] 等待使用有限 watchdog，业务超时参数不改；不再把 DB/调度墙钟开销称为逻辑 deadline。
- [x] 用实际返回和持有任务验证以下边界：

```python
assert terminal.status is BrokerJobStatus.FAILED
assert terminal.error_code == BrokerErrorCode.CLEANUP_FAILED.value
assert terminal.cleanup_status == "failed"
assert service._cleanup_tasks
assert service._isolated_tasks
# SDK 释放并 stop 完成后
assert not service._cleanup_tasks
assert not service._isolated_tasks
assert leaked == []
```

- [x] 断言中途注入失败时 finally 仍能释放并收尾，不能在 asyncio.run 的退出阶段挂住。

## Task 4：回归与独立审查

- [x] 聚焦运行：`python -B -m pytest tests/sandbox_broker/test_controlled_clock.py tests/sandbox_broker/test_service.py -q -p no:cacheprovider --tb=short -rs`（首轮另有一个上传失败，保留记录）。
- [x] 原失败场景至少重复 3 次，记录每次结果而非只保留最后一次。
- [x] 全沙盒核心：`python -B -m pytest tests/sandbox_broker --ignore=tests/sandbox_broker/test_api.py -q -p no:cacheprovider --tb=short -rs`；本机缺 pytest-timeout，带选项命令被拒，未改 CI。
- [x] 编译与差异：`python -m compileall -q src scripts tests/sandbox_broker`、`git diff --check`、`git diff --exit-code -- src .github`。
- [x] 规格复核后做质量复核；两次独立审查均 APPROVED，各自复跑 46 项通过；不用通过数量替代行为正确性。
- [x] 更新交接报告：原失败、本轮红绿测试、真实/逻辑时间区别、文件范围、未完成的 Linux CI 与 PR #34 合并事项；不自动合并或启用生产模型。

结果详见 `docs/handoff/sandbox-cleanup-deadline-validation.md`。完整沙盒核心 1912 passed / 65 skipped；一次上传阶段失败仍保留为独立待查项。本地完成不等于已发布或已合并。
