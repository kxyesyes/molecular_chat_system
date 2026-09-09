# 家族数据层分批集成交接

日期：2026-09-09。状态：实现、本地验证及独立审查完成，准备独立 draft PR，尚未合并。

## 目标与来源

- 用户同意关闭被替代的旧 PR #1，并继续独立批次整理剩余代码。
  PR #1 已以 superseded 关闭，未合并、未删除分支。
- 本批基于 main `0dc0b8e`（已合并 PR #10）；分支
  `codex/activity-family-data-integration`，使用独立 worktree。
- 受控移植源：`codex/activity-integration-blockers` / `5432968`，
  家族数据原始实现来自 `69b83a5`，包含后续 NUL 拒绝修复。
- 不整体合并历史分支，不覆盖 main 已有 CSV/来源身份、模型卡、特征泄漏隔离、
  legacy 模型发现与旧 RDKit 兼容修复。

## 实施顺序

1. 从 main 新建独立分支；保留原项目混杂工作树及全部运行资产。
2. 先移植家族契约/配对数据/CLI 测试，确认入口缺失的 RED。
3. 仅新增 `family_contract.py`、`family_dataset.py` 和 prepare CLI；
   复用现有 dataset_contract、model_card、model_registry 原子发布与严格 loader。
4. 增加独立来源完整性回归；只修复本批可复现的兼容缺陷。
5. 依次进行独立范围审查、质量审查，运行活性与 Agent 回归、contract 和编译检查。
6. 精确暂存、提交独立 draft PR，CI 通过并取得具体 PR 授权后方可合并。

## 文件范围

- `src/activity/family_contract.py`
- `src/activity/family_dataset.py`
- `scripts/prepare_family_activity_dataset.py`
- `tests/test_activity_family_contract.py`
- `tests/test_activity_family_dataset.py`
- `tests/test_activity_family_prepare_cli.py`
- `tests/test_activity_family_data_integrity.py`
- `docs/activity_family_data.md`
- 本交接文档及 `docs/handoff/latest.md` 的索引入口。

## 验证记录

- RED：契约模块存在断言、CLI validate/publish 用例共 2 failed，
  原因是 main 尚无对应模块/脚本；不是依赖 collection 错误。
- 原有三份家族测试：124 passed。
- 独立新增来源完整性回归：36 passed；包含 CSV/TSV 文本、阈值边界、ID、
  NUL/列宽/重复列拒绝、源身份冲突、重哈希后的子包证据篡改。
- 活性回归：920 passed、5 skipped、2 warnings；该轮收集时新增 36 项尚未落盘，
  不包含它们。跳过均为 Windows symlink 权限/平台限制。
- Agent + 反幻觉/平台健康测试：1688 passed、1 skipped（未启用性能测试）、7 既有警告。
- `python -B scripts/run_agent_acceptance.py --mode contract`：34/34 passed。
  该模式的 legacy REAL 名称不表示执行了真实模型；本轮无外部科研执行。
- `python -B -X pycache_prefix=<temporary-dir> -m compileall -q src scripts`：通过。
- `git diff --check`、所选文件凭据形态扫描通过；prepared 数据目录 Git 忽略检查通过。
- 实际 Python 测试均使用 MedChat Conda 环境、`-B -m pytest -q -p no:cacheprovider --tb=short -rs`。
  范围分别为三份 family 测试、独立 integrity 测试、`tests/test_activity*.py`，以及
  `tests/agent tests/test_agent_anti_hallucination_fallbacks.py tests/test_agent_platform_health_check.py`。
- 修复前根测试回归：`pytest tests --ignore=tests/agent --ignore=tests/sandbox_broker --ignore=tests/task_runtime`，
  2028 passed、141 skipped、6 warnings、171 subtests passed；跳过主要为 Linux/root/systemd、
  Windows symlink 权限、缺少 Docker/promtool 和未启用的真实集成。不是生产验收通过。
- 独立范围审查复现 P2：子包把实际 CSV 适配格式声明为 TSV，并同步重算绑定/manifest 摘要，
  家族 loader 仍接受。新增两种源格式 × 两种任务共 4 项测试，RED 为 4 failed（未抛出异常）。
  `_describe` 现要求两个子包的 `input_format=csv`，不改动 main 的通用 loader。
  文档同步澄清 `--help` 是文本输出。
- 修复后四份家族测试：164 passed；独立范围复审通过，审查者另跑 integrity 文件 40 passed。
- 第一项修复后完整活性回归：960 passed、5 skipped、2 warnings。
- 独立质量审查复现第二项 P2：源字段低于 CSV 限制，但重复聚合证据超过限制时，
  validate-only 返回 ready，prepare 在子包 loader 处拒绝。新增函数与 CLI 两种模式
  共 4 项 RED；现在对六个序列化拆分提前复用严格记录校验，不放宽解析器限制、不写临时包。
- 第二项修复后四份家族测试：168 passed；compileall 再次通过。
- 独立范围审查和质量复审均 APPROVED；质量审查者对最终修复另跑 6 项聚焦检查通过。
- 完整活性最终复验：964 passed、5 skipped、2 既有警告，134.12 秒。
- 8 个 Node 契约脚本通过。远端 CI 以 PR checks 为准，本文不预先标记通过。
- 测试集合有重叠，不相加宣称总覆盖。

## 明确未做

未读取真实数据/密钥，未调用外部 API，未训练、激活或切换模型，未重启在线服务。
不移植 family_models、family_predictor、prediction_service、在线 API/UI 或 Agent 决策入口。
历史真实数据预检数字没有当成本轮结果重新发布。
后续仍需独立整理家族模型运行层，再处理有依赖的 Agent 批次；本批完成不等于全项目合并完毕。
