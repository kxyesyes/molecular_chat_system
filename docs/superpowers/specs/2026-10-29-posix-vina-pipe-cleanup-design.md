# POSIX Vina 输出管道收尾修复设计

## 范围

修复 Linux/POSIX 环境下 AutoDock Vina 主进程结束后，命令适配器因 stdout/stderr reader 线程未在固定窗口内退出而将已结束的对接判定为失败的问题。

本次不修改 Vina 参数、受体/配体准备流程、科学结果校验规则、Windows Job Object 路径或托管命令的所有权安全边界。

## 已确认现象

- 服务器日志显示环境检查、受体准备和配体准备均成功。
- Vina 阶段抛出 `Command output readers did not stop`。
- 该异常来自非托管 POSIX 命令路径的 `_take_captured_output()`，不是 Vina 返回的科学计算错误。
- 本地 MedChat 环境使用 Windows 分支，已有真实 Vina 成功记录，不能直接证明 POSIX 分支没有问题。

## 方案

在非托管 POSIX 路径中，将管道读取从阻塞 reader 线程改为监督循环内的非阻塞读取：

1. `Popen` 仍使用独立进程组和 stdout/stderr 管道。
2. 将两个父端管道设置为非阻塞，在监督循环中通过 `selectors` 或等价的非阻塞读取持续排空。
3. 继续累计 stdout/stderr，并在达到现有 8 MiB 总上限时终止进程组并抛出 `CommandOutputLimitError`。
4. 主进程退出且进程组不再活动后，读取当前可用字节、关闭父端管道并返回 `CompletedProcess`；不再等待外部残留进程通过继承的管道发送 EOF。
5. 仅覆盖没有 `CommandOwnershipScope` 的路径。带 receipt 的托管路径继续要求 reader、stream 和进程树均可证明收尾，否则保持 `CommandOwnershipUncertainError`。

这样可以避免 Vina 已退出但外部进程仍持有输出管道时的假失败，同时不会把进程所有权不确定的托管任务标记为成功。

## 错误处理与兼容性

- 读取错误、输出超限、超时、取消和无法确认的进程树仍保持现有异常语义。
- 返回的 `CompletedProcess.stdout/stderr` 保持字符串类型和 UTF-8 replacement 解码行为。
- Windows 路径不变。
- 不新增配置项，不修改 API、事件、数据库 schema 或任务状态契约。

## 测试

- 新增 POSIX 行为测试：主进程退出后，另一个进程仍短暂持有继承的 stdout/stderr 管道，命令适配器仍能在进程组结束后完成收尾并保留已读取输出。
- 保留并运行现有命令取消、超时、输出超限和 reader 故障测试。
- 在 Linux/POSIX 环境运行相关 Python 测试；在 Windows 本地至少运行可执行的适配器测试、`compileall` 和现有前端回归。

## 验收标准

服务器上再次执行同一对接时，不再因 `Command output readers did not stop` 直接失败；只有 Vina 非零退出、输出缺失、结果解析失败或安全所有权无法确认时才报告失败。
