# 反向寻靶 CLI 测试编码与输出验收

## 边界与基线

用户已确认：仅修复 CLI 测试的 UTF-8 编码和帮助输出断言，保留 GBK 兼容测试，不改业务代码，按 TDD 实施。
本设计落实该边界，待书面设计复核后实施。独立分支 `codex/reverse-target-cli-test-encoding`，基线 main `3567096`（PR #58）。不部署、不启用真实模型、不下载 ChEMBL 数据、不访问真实凭据。

## 已验证的缺陷

`tests/test_reverse_target_health.py::ReverseTargetHealthTest::test_documented_fetch_cli_runs_directly_from_project_root` 调用实际 `fetch_chembl_api.py --help`，但 `subprocess.run(text=True)` 未指定解码编码。隔离测试环境的子进程输出 UTF-8，Windows 父进程默认 GBK，读取线程因此出现 UnicodeDecodeError。原断言仅检查返回码和 stderr，不检查 stdout，可能在警告策略下漏检丢失的帮助输出。

在与基线 tree 一致的已审工作树，采用现有隔离 runner、仅追加 `-W error::pytest.PytestUnhandledThreadExceptionWarning`，该节点实际 **1 failed / 1.95s / exit 1**，GBK 解码错误位置 204。此前同一源码的临时 UTF-8 父进程对照通过；这不是生产检索算法失败的证据。

## 方案选择

采用测试局部编码契约：此处父进程 `encoding="utf-8"`，仅此 subprocess 的子环境覆盖 `PYTHONIOENCODING="utf-8"`，不改父环境。保留原 CLI、参数、cwd、返回码和 stderr 检查，补充 stdout 为字符串且包含 `--limit`、`获取记录数` 的检查。

不采用全局开启 UTF-8：会掩盖其他编码兼容缺陷，并扩大测试环境影响。不采用 `errors="ignore"`/`replace` 解码：乱码或信息丢失必须使断言失败，而非悄悄通过。

## 文件与实现约束

- 修改现有 `tests/test_reverse_target_health.py` 的上述测试；独立的 `test_fingerprint_cli_completes_under_windows_gbk_console` 保持原样。
- 必要的局部编码回归优先放同一测试模块，调用实际测试方法/CLI，而非复制命令成为平行实现。避免导入 TestCase 别名引发重复收集。
- 文档仅本设计、实施计划与简短交接。禁止修改 `src/`、共享 conftest、CI、依赖、科学断言、数据资产和超时。
- 实际 `--help` 在参数解析阶段退出，发生于 ChEMBLAPIFetcher 构造与网络检索之前。测试不得改成真实下载模式。

## TDD 与验收

1. 先增加 stdout 内容断言，在未修编码时通过实际节点确认 RED；记录读取线程错误及空 stdout，不以孤立正则探针代替模块测试。
2. 再仅添加父子 UTF-8 配置，确认同节点 GREEN，严格读取线程警告检查继续生效。
3. 用受控外层编码配置验证子进程覆盖是局部的，实际中文帮助仍正确，父环境恢复/不变；不改变全仓默认编码。
4. 运行完整 `tests/test_reverse_target_health.py`，包含既有真实 GBK 指纹 CLI 回归；运行 PR #58 的三个测试模块，核验互不污染。分别保留 passed、skipped、warnings 与退出码。
5. 检查业务代码零 diff、原断言和 GBK 测试保留、任务文件编译、`git diff --check`。再独立审查，按已有 PR 规则发布；新 PR 的具体合并仍需授权。

## 完成与非目标

完成仅代表帮助输出测试不再依赖父进程默认编码且能检出丢失/损坏文本。不得宣称生产 ChEMBL 下载、真实科研模型或整个任务书完成。T09、剩余 T11 和最终整体审计继续单列。
