# 靶点测试隔离与 CLI 编码最小修复

## 范围与基线

基线 main `4ff859e`；独立分支 `codex/target-test-isolation`。用户已同意最小方案；本书面设计经确认后进入计划与 TDD。不混入 Web partial PR #57，不改生产代码、CI 超时、科学断言、预期计数、权限或环境变量优先级。

任务文件为 `tests/test_target_search.py`、`tests/test_target_db_validation.py`，以及本批设计、计划与交接。必要的回归辅助仅放 tests 内，优先复用现有 pytest/unittest 机制，不新增通用运行框架或全仓 autouse 网络策略。

## 已复现的原因

全仓追加测试报告为 10 failed、12157 passed、252 skipped；不是全仓通过。

- 原隔离运行器给所有测试设置同一绝对 `TARGET_DB_PATH` 和 `TARGET_CACHE_DIR`。生产代码按设计优先使用这些变量；两组 unittest 虽创建自己的 `self.root`，实际仍共享 SQLite，且测试写入的假缓存不在实际读取目录。造成数据累积、计数变化和意外下载。
- CLI 测试用 `text=True` 未指定编码。运行器使子进程输出 UTF-8，而 Windows 父进程采用 GBK 解码；reader thread 抛 `UnicodeDecodeError`，结果 `stdout=None`。
- 阻断真实 HTTP 的同代码对照：共享路径 10 failed/32 passed；移除共享覆盖并把默认根目录放在临时目录后 1 failed/41 passed；再以 `-X utf8` 启动父进程后 42 passed。诊断未修改业务或测试断言。

## 方案选择

1. **采用：测试边界自行隔离。** 修复两个测试文件对启动环境的隐含依赖，普通 pytest、CI 和隔离运行器都能得到相同语义。
2. 仅修文档运行命令：改动更少，但普通开发者继承相同变量或 Windows 默认编码后仍可能失败。
3. 改生产路径优先级或全仓环境/网络策略：会改变已承诺配置行为或干扰真实服务与本地 HTTP 测试，超出本批。

## 最小设计

### 每例独立路径与清理

两组测试的 setUp 在调用种子、服务或验证函数前，以测试补丁暂时移除 `TARGET_DB_PATH`、`TARGET_CACHE_DIR` 两个外部覆盖值；只隔离这两个变量，不清空其他测试配置。补丁通过 unittest cleanup 可靠恢复，断言失败也必须恢复外层环境。临时目录同样注册 cleanup。

这样已有 `project_root=self.root` 指向每例自己的数据库与缓存。显式配置覆盖的用例仍可在隔离范围内用嵌套 patch 设置绝对或相对路径，并继续验证原优先级。不得修改 `src/target_search/database.py`。

### CLI 父子编码

仅该 CLI 测试的 subprocess 明确 `encoding="utf-8"`，并通过子进程 env 指定 `PYTHONIOENCODING=utf-8`（从当前已隔离测试环境复制，不加载用户配置）。保留 `capture_output`、返回码、JSON 字段和严格缺失 PDE 断言。不用忽略/替换解码错误，不用吞异常或降低 JSON 内容检查。

### 缓存命中禁止意外联网

对声明使用已存在本地缓存的测试，在调用准备/送对接函数时 patch 真实下载 HTTP 接口，使任何网络调用立即失败，并断言未调用。保留文件内容、路径、缓存状态、中文消息等原断言；真正测试网络失败或下载协议的用例继续使用自身 mock，不全局禁用这些分支。

## TDD 与验收

1. 保留诊断 RED，另补可自动回归的测试边界检查：外层传入两个绝对路径时，每例数据库与缓存仍在自身 root；清理后外层值原样恢复；两例互不累积。显式覆盖测试不跳过。
2. CLI 用包含中文的 JSON 验证 UTF-8 往返；检查实际子进程调用，而不是只比源码字符串。不得依赖父 shell 预先开启 UTF-8。
3. 缓存命中在 HTTP guard 下成功；缓存文件未命中时 guard 能暴露路径错误，不用请求真实 RCSB。
4. 用原共享路径运行环境、父进程非 UTF-8 模式重复两文件测试；目标为全部通过，无 reader thread 解码警告。补反向顺序执行验证，无共享 seed 串扰。
5. 回归相关 target/cache、Agent 靶点契约以及既有公共配置覆盖测试；区分平台 skip，不启用真实模型/下载。
6. `git diff --check` 和测试文件内存编译，确认 src、生产数据、权重、缓存、CI 配置均无 diff。独立规格/质量审查后再交付。

## 交付边界

记录每轮真实命令、RED/GREEN、跳过原因和清理证据。局部通过不能拼接成全仓通过；若后续全仓运行，单独记录其结果。此次确认不包含新 PR 的合并、部署或启用真实服务权限。测试失败不通过增加超时、删除断言、更改预期数量解决。
