# Sandbox late-create 测试同步与失败清理

日期：2026-09-12。分支 `codex/sandbox-create-test-synchronization`，基线 main `9876cc4`。
状态：仅测试修复，独立规格与质量审查通过；准备独立 draft PR，CI 尚待运行。

## 已知失败与边界

PR #22 CI run34692710150，sandbox-core job103550700590：
`test_late_cancellation_resistant_create_is_owned_and_cleaned_before_stop` 等待 terminal 的 0.5 秒外层超时；
该分片 1927 passed、1 failed、4 skipped。没有修改过的生产 sandbox 模块出现这次失败。
不能仅凭失败位置断言生产存在资源泄漏，也不能把重跑通过当作根因已解决。

受控增加 650 ms 的 terminal 发布延迟后，旧测试同样超时；此时取消已被观察、迟到创建仍被服务持有，
释放 fake creator 后恰好 destroy 一次且无遗留任务。这证明测试把生命周期正确性与过短发布时限混淆；
CI 当时为何延迟仍未证实，不声称复现了它的具体调度条件。

## 最小修复

只改 `tests/sandbox_broker/test_service.py` 的一个场景：

- 保留 20 ms create hard deadline；用 create_started、create_cancelled、真实 terminal 通知作为前置同步。
- 5 秒等待仅用于测试死锁保护，不放宽生产 deadline；状态读取依然保留原 0.5 秒保护。
- 在释放前验证 FAILED/CLEANUP_FAILED、迟到任务身份和所有权、首次 stop 必须抛 StopIncomplete。
- 释放后、fallback 前验证 destroy 恰好一次、未运行 docking、live IDs 和全部相关任务清空。
- `finally` 总会释放 fake creator 并收束服务；主要断言放在其前面，避免清理代码掩盖失败。

## 验证

使用 MedChat Conda Python、`-B -m pytest -p no:cacheprovider`，仅临时数据库与 fake sandbox：

- worker：service 172 passed；client/soak 360 passed、2 skipped；受控探针及 30 次重复共 36 passed。
- 规格审查：目标测试与原探针 37 passed，独立探针 4 passed；释放前断言故障被保留，finally 能完成清理。
- 父任务 `pytest tests/sandbox_broker/test_service.py tests/sandbox_broker/test_opensandbox_client.py tests/sandbox_broker/test_stability_soak.py -q --tb=short`：532 passed、2 skipped，33.36s。
- 质量审查：service 172 passed、独立探针 6 passed；五类故障均正确失败，650ms 通知延迟可通过。
- `git diff --check` 通过。最新 CI 待 PR 创建后运行。

故障注入覆盖提前成功终态、提前成功 stop、丢失所有权、缺失 destroy；不得让 fallback 修复使主测试误报通过。
本批不改任何生产代码、不调用 QEMU/OpenSandbox 服务、不消耗真实 API、数据或模型。
旧 artifact_failed/manifest 偶发问题、PR #20 关闭测试与 PR #22 长文本矩阵时限问题分别保留，不混为一个已修复问题。
通过独立双审和 CI 后仍需用户对本 PR 的明确合并授权。
