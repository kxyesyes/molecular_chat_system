# 家族训练编排集成交接

日期：2026-09-12。分支 `codex/family-training-run-integration`，基线 main `c642bae`（PR #17 已合并）。
总体计划 Task 4，历史源 `5432968`。新增训练 CLI 与测试，代码提交 `d166c42`、`07baefe`。
已完成首轮 TDD 和兼容性回归；独立规格审查发现的两个阻断项已按 TDD 修复，规格复审和质量审查均通过。
未执行真实训练、未合并，不能以现有测试通过替代审查结论。

## 已读取的代码与实际边界

- 历史 `scripts/train_family_activity_models.py`、`tests/test_family_training_run.py`。
- main `src/activity/prepared_training.py`、`family_dataset.py`、`model_card.py`、
  `model_registry.py`、`trainer.py`、`predictor.py`、现有 CI 配置。
- 历史脚本固定 CUDA，PDE/BuChE 各分类/回归共四项串行任务，固定超参数与随机种子；
  模型注册但不选择或激活。训练包仍通过 main 的完整源数据及 prepared 身份校验。
- main 训练函数已有进程内 RNG 串行保护、train/validation/test 特征身份隔离、
  训练前后源码 hash 校验、模型卡及唯一产物注册，均不得被旧版本覆盖。

## 已实现并覆盖的边界

1. import 无训练/配置修改；固定参数、train-only 常数基线、四任务状态汇总。
2. CUDA/训练包缺失、包损坏或包的 family_id 与指定家族不符时，不开始训练。
3. 单项失败保留错误类型、其余独立任务继续；bundle 注册失败不能 completed。
4. 不自动激活、不静默重跑同一 run-id、不输出原始分子、标签或内部异常文本。
5. 环境变量与日志抑制状态恢复；进程全局状态不能因同时调用 run 而串扰。
6. 完成状态必须依赖真实注册产物和 bundle 校验，而不是仅信任训练函数返回文本。

## 验收与范围

使用临时合成 fixture 建立缺失 runner 的失败用例，再选择性移植，复用 main 的训练与注册模块。
不读取用户真实 CSV、权重、密钥或生产注册表，不执行真实 GPU 训练，不改生产模型。
CUDA 基线不会在服务器参数未知时擅自改成 CPU 基线；合成测试不等于真实模型性能验收。
新增的服务器部署验证仍待目标宿主信息，不能由本脚本的单元测试替代。

实施者实际运行 Conda Python `-B -m pytest -p no:cacheprovider`：

- `tests/test_family_training_run.py::test_frozen_training_configuration`：RED，1 failed（runner 缺失）。
- `tests/test_family_training_run.py`：73 passed。
- 同时运行 family_dataset、family_data_integrity、family_models、model_registry、
  model_card_consistency、prepared_training_data、prepared_input_integrity：426 passed、3 skipped。
  跳过项因 Windows 符号链接权限；272 个源码文件内存编译通过。

本工具仅适用于独立 CLI 进程；同一模块的并发 run 会被拒绝，但不声称隔离其它调用者
或重复加载的模块实例。报告 completed 仅表示训练/注册流程完成，不表示模型性能合格或可部署。
后续仍需独立双审、CI 和每个 PR 的具体合并授权。

## 独立审查与修复记录

`84f4615` 独立规格审查：73 项 runner 测试、188 项相关测试通过，3 项符号链接跳过；
但额外探针发现以下两项规格缺口，因此 **NOT APPROVED**：

1. 延迟导入的 `sklearn.metrics` 缺失时，四项训练已开始而不是预检阶段失败。
2. 训练抛出 `SystemExit(0)` 时 CLI 返回成功退出码；携带文本的 SystemExit 可在恢复 stderr 后泄漏。

已补依赖预检和 CLI 中断边界回归，保留 `--help` 正常行为；修复后须重新规格审查。
父任务在修复前运行 Agent 与训练/数据/模型卡联合回归：2359 passed、4 skipped、7 warnings，
137.10 秒。该通过结果并未覆盖上述新发现，不撤销阻断项。

修复 TDD：依赖阻断与真实子进程 CLI 中断测试 **18 failed / 7 passed → 25 passed**；
单独依赖/预检回归 **23 passed**。完整 runner + prepared loop/data/integrity 回归：
**193 passed、3 skipped、3 deselected**，1 条 PyG 弃用警告。
三个 deselected 为合成 fixture 的真实优化循环，本批不执行训练；未将其计为通过。
PR #17 合并后重接至 `c642bae`，训练代码/测试与重接前提交 `4385385` 完全一致。

独立规格复审：**284 passed、3 skipped**，确认缺 metrics 时零 job/零训练调用，
三类训练中断均脱敏且 CLI 退出码为 1，`--help` 保持 0；无剩余规格阻断。

父任务在 `07baefe` 重接后实际运行：

```powershell
python -B -m pytest tests/agent tests/test_family_training_run.py tests/test_activity_family_dataset.py tests/test_activity_family_data_integrity.py tests/test_activity_family_models.py tests/test_activity_model_registry.py tests/test_activity_model_card_consistency.py tests/test_activity_prepared_training_data.py tests/test_activity_prepared_input_integrity.py -q -p no:cacheprovider --tb=short
python -m compileall -q src scripts
Get-ChildItem tests -Filter '*_test.js' -File | Sort-Object Name | ForEach-Object { node $_.FullName }
python -B scripts/run_agent_acceptance.py --mode contract
```

均在 MedChat Conda 环境；编译 pycache 输出在系统临时目录。联合回归
**2406 passed、4 skipped、8 warnings，177.91 秒**；编译、全部 8 个 Node、contract 通过。
这些测试不包括生产训练和完整全仓/目标服务器验收；跳过项不计为通过。

独立质量审查 **APPROVED**：runner 96 passed；家族注册/prepared lifecycle/集成保护
107 passed、3 deselected；无阻断质量问题。审查再次确认总 completed 需要四项任务完成且
两组 bundle 均通过实际注册校验。单项训练完成不等同对应 bundle 已接纳，CLI 同模块并发
保证不扩展为 Web 线程或多个独立模块实例的全局隔离。下一关为 PR CI 与具体合并授权。
