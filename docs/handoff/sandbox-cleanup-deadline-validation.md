# 沙盒清理截止时间测试分层验证

日期：2026-09-23。分支：`codex/sandbox-cleanup-deadline`；基线：`e2cb6f7`。

## 范围与行为差异

用户已明确同意将原 0.5 秒机器墙钟断言改为逻辑预算，同时独立保留真实 SQLite 集成验证。本批只修改测试及本说明、设计、计划文件；不改生产代码、CI 配置或超时，不启用外部模型，不部署，不自动合并 PR #34。

- 修复前：取消、真实 SQLite 读写、调度和首次 stop 全部计入 `<0.5` 墙钟断言，因此存储开销也能触发失败。
- 修复后：测试局部时钟驱动真正的 asyncio 计时器，分别检查 run grace=0.02、destroy=0.03、stop=0.05 的边界前后行为，保留总逻辑时间 `<0.5`。提前和延后截止时间、清除任务登记的变异均应被拒绝。
- 独立真实存储测试使用普通事件循环与真实临时 SQLite，覆盖正常和每连接增加 0.04 秒延迟。验证持久化的 FAILED / cleanup_failed、超时后继续持有抵抗取消的任务、释放 SDK 后全部排空。
- 无论中间断言成功或失败，finally 都释放 SDK 并排空任务。新增故障注入验证此路径。
- Fake SDK 仅用于可控抵抗取消；这不是 OpenSandbox、Vina 或真实科研结果验收。

## 不得抹去的失败证据

1. PR #34 CI run `35868447205` 的 sandbox-core：`1 failed / 1927 passed / 4 skipped`；原场景墙钟 `0.5184005800000193` 未满足 `<0.5`。尚未证明该次 Linux CI 的具体慢点。
2. 本轮 RED：在原测试上注入真实 SQLite 连接延迟 0.04 秒，`1 failed / 1 passed / 172 deselected`，失败值 `0.5939999999827705 < 0.5`。证明这种开销能够触发断言，不等于定位了原 CI 根因。
3. 辅助时钟实现者记录：未实现 stub 时 `22 failed`；补使用边界保护前 `9 failed / 24 passed`；最终 `33 passed`。
4. 首次完整 helper + service 回归：`1 failed / 216 passed`。未修改用例 `test_command_failure_is_single_shot_then_cleans_and_terminalizes` 实际 `upload_failed`，期望 `command_failed`；该用例单独复跑 `1 passed`。根因未定，不宣称已经修复。
5. 本机运行带 `--timeout=60` 的 sandbox-core 命令被 pytest 拒绝：本地缺少 pytest-timeout 插件。CI 的该选项保持原样；后续本地命令不带此选项，不等价于验证了 Linux CI 门禁。

## 命令与结果

以下 `python` 均指本机具备项目依赖的 MedChat Conda Python，使用 `-B`、`-p no:cacheprovider`，禁用真实验收环境开关。不打印或读取密钥。

| 命令 | 实际结果 |
| --- | --- |
| `python -B -m pytest tests/sandbox_broker/test_service.py -k 'hung_shutdown_retains_real_sqlite_state_with_delayed_storage' -q -p no:cacheprovider --tb=short`（修复前） | 1 failed，1 passed，172 deselected |
| `python -B -m pytest tests/sandbox_broker/test_service.py -k 'hung_shutdown_retains_real or hung_run_and_destroy' -q -p no:cacheprovider --tb=short`（修复后） | 3 passed，171 deselected |
| `python -B -m pytest tests/sandbox_broker/test_controlled_clock.py tests/sandbox_broker/test_service.py -k 'hung_shutdown or hung_run_and_destroy or controlled_clock' -q -p no:cacheprovider --tb=short` | 46 passed，171 deselected |
| `python -B -m pytest tests/sandbox_broker/test_controlled_clock.py tests/sandbox_broker/test_service.py -q -p no:cacheprovider --tb=short -rs`（首次） | 1 failed，216 passed；见上方失败记录 |
| `python -B -m pytest tests/sandbox_broker/test_service.py::test_command_failure_is_single_shot_then_cleans_and_terminalizes -q -p no:cacheprovider --tb=short` | 1 passed |
| `python -B -m pytest tests/sandbox_broker --ignore=tests/sandbox_broker/test_api.py -q -p no:cacheprovider --tb=short -rs` | 1912 passed，65 skipped，92.64 秒 |
| `python -B -m pytest tests/sandbox_broker/test_service.py::test_hung_run_and_destroy_keep_shutdown_pending_after_hard_deadlines -q -p no:cacheprovider --tb=short`，连续三次 | 每次 1 passed；pytest 总耗时分别 1.07 / 1.11 / 2.36 秒，不是逻辑预算测量值 |
| Windows Selector policy + warnings-as-errors 下运行上述 46 项聚焦检查 | 46 passed，171 deselected，3.39 秒 |
| `python -m compileall -q src scripts tests/sandbox_broker` | exit 0 |
| `git diff --check`、`git diff --exit-code -- src .github` | exit 0；生产和 CI 零差异 |

65 个 skip 均来自既有条件：POSIX 防火墙/安装器/systemd/所有权、Unix socket/Linux subreaper、Windows 无符号链接权限，以及未开启的 MICROMAMBA/OpenSandbox 真实验收。新增 46 项没有 skip；没有修改既有跳过条件。sandbox-core 命令不包含 sandbox-api，也不是全仓测试。

Selector 补充验证使用 `python -B -W error -c`，在测试进程内设置 `asyncio.WindowsSelectorEventLoopPolicy()` 后调用 `pytest.main()`，其参数与 46 项聚焦命令相同。默认 Windows Proactor 聚焦结果见前表。不据此替代 Linux CI。

独立 SPEC 与 QUALITY 审查均为 APPROVED，无阻断意见；两位审查者分别复跑 46 项聚焦检查通过。QUALITY 审查再次确认生产/CI 零差异、使用边界和历史失败均已如实记录。该结论不替代 Linux CI。

## 文件范围

- 修改 `tests/sandbox_broker/test_service.py`：原场景拆分、真实存储延迟、截止时间/任务所有权变异和异常收尾覆盖。
- 新增 `tests/sandbox_broker/controlled_clock.py`：测试局部可控时钟。
- 新增 `tests/sandbox_broker/test_controlled_clock.py`：时钟契约与恢复保护测试。
- 新增 `docs/superpowers/specs/2026-09-23-sandbox-cleanup-deadline-design.md`：获批设计与语义边界。
- 新增 `docs/superpowers/plans/2026-09-23-sandbox-cleanup-deadline.md`：执行清单。
- 新增本报告 `docs/handoff/sandbox-cleanup-deadline-validation.md`：包含失败在内的验证记录与后续事项。

## 维护边界与后续

- 时钟 helper 依赖 CPython asyncio 私有 ready/timer 队列，仅供单一驱动协程的隔离测试使用；不支持通过逻辑推进判断外部线程/网络 I/O 已完成。退出前必须完成或取消测试拥有的任务及计时器。
- 真实 SQLite/整体服务能否稳定满足 0.5 秒墙钟性能要求仍未验证，本次不提供该保证。
- 新发现的上传失败需另行定位；不能用单次复跑通过抹去失败。
- 独立审查、本地完整验证后，后续发布与 Linux CI 验证单独进行；PR #34 仍须重新满足全部门禁才可申请合并。
