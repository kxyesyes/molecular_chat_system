# 分批合并检查点：后续代码暂缓

日期：2026-09-08。状态：**partial / hold，不是全部合并完成**。

## 已完成与分支

- PR #7（性质报告）经 CI 与独立审查通过，用户明确确认后 squash 合并；main 提交 `32d30c877705b107eb9aae50a5d8f303f359fcb2`。
- PR #8（完整性质/类药性输入）位于 `codex/molecular-input-integration`。修复本轮四项解析审查发现，最新提交 `76dae5076fa7950a0f98a9dd1fb474e72d78c41a`；复审无 P1/P2，CI 与合并状态以 PR 为准。没有将报告提交回退。
- 本分支 `codex/activity-assets-integration` 是本地审查检查点：从 main `32d30c8` 复制 `5f58f79..af55114` 的 56 文件净差异；不包括 source 的 40 个历史提交，不包含数据/权重，不发布、不合并。
- Agent 源代码仍在 `codex/scientific-feedback-clarity` / `eb93c43`，未移入 main。
- 原项目工作树已有修改与未跟踪文件均保持原状；未切换/重启服务、未修改运行时凭据、未训练/启用模型。

## 活性批次阻塞（af55114 等价快照）

### P1：源数据靶点/端点冲突未校验

涉及 `src/activity/dataset_contract.py:874`、`:928`、`:584`，家族入口 `src/activity/family_dataset.py:216`。

合成 CSV 的每一行声明 `target_id=buche-family, endpoint=pKi`，但调用方给出 PDE/pIC50 manifest，仍被接受且丢失行级身份字段。12 行由苯/吡啶/环己烷三个公开简单骨架与空/C/CC/CCC 前缀组合，标签均为字符串 4.8；不使用用户数据。

复现结果：accepted=12、rejected=0、ready_for_training=true，endpoint_key=pde-family:pic50:pic50:regression；split 与 publication precheck 也通过。

修复方向：对存在的源身份列验证与 manifest 一致，或要求明确的受校验映射；保留源身份，冲突不能静默重标。

### P1：仅注册新模型就改变未激活旧推理的选择

涉及 `src/activity/prepared_training.py:303`、`src/activity/trainer.py:35`/`:51`、`src/activity/model_registry.py:718`、`src/activity/predictor.py:98`。

内存 registry 的 active global 为 None，原 list 只有 legacy；新注册的 endpoint_ready 分类模型排在 list 首位后，实际 `_find_checkpoint()` 从 legacy 改选该分类模型，尽管 active_model_id=null、active_family_bundles={}。这是检查点选择复现，不是权重加载或推理。

修复方向：未显式激活的端点/家族 prepared 模型不得进入旧全局发现路径；保留已批准的 legacy 兼容范围，测试注册前后选择不变。

### P2：通用 CSV 自动推断改写标签与证据 ID

涉及 `scripts/prepare_activity_dataset.py:60`、`src/activity/dataset_contract.py:790`/`:992`。

合成输入：`smiles,value,units,relation,compound_id` / `CCO,4.9999999999999994,pIC50,=,000123`。

默认 pandas 解析得到 original_value=5.0、分类 1、ID=123；dtype=str 得到原字符串、分类 0、ID=000123。两种路径都通过源快照验证。家族适配器已经保留文本，该问题属于通用 CLI。

修复方向：保留原始文本到科学转换边界，源快照校验不能以已损失精度/前导零的 DataFrame 为权威。

## Agent 批次阻塞（eb93c43）

### P1：续接敏感赋值可被派发和保存

涉及 `src/agent/harness/decision_continuation.py:106`、`src/web/decision_lab.py:249`。

ScriptedModel 连续 clarify，补充输入含 `password=synthetic-review-marker`，现有 redact_sensitive 字符串检查未拒绝；模型收到原文，SQLite waiting snapshot metadata 保留原文。仅使用合成标记与临时数据库。

修复方向：自由文本敏感赋值检测须在 claim、派发、快照持久化之前生效；不能只脱敏显示，也不能输出被拒绝内容。

### P2：续接与 user 引用丢失靶点

涉及 `src/agent/harness/decision_inputs.py:41`、`src/agent/harness/decision_loop.py:140`。

user 引用不转发服务端 context.metadata.target；初始 BuChE 活性请求在澄清回复 `SMILES: CCO` 后，被整体替换成回复，工具仅收到该 SMILES。后续无靶点 legacy 选择是代码路径推断，未调用真实活性模型。

修复方向：补充输入保留已确认的靶点/任务义务，结构化 target 对 user/evidence 引用一致生效；同时明确用户改变靶点时的冲突处理契约，不能靠拼接旧无效结构解决。

### P2：证据一致性未绑定 artifacts/evidence

涉及 `src/agent/harness/decision_inputs.py:19–29`。

合成工具保留 ToolResult 对象；注册后追加 artifact 与 citation，再 finish，结果 success=true 且呈现未注册文件，ledger 中 artifact 仍为空。

修复方向：使用不可变已登记快照，或校验完整 artifact/evidence 与 ledger 的绑定。不能只校验 data digest。

## 本轮真实运行的验证

均在各自独立 worktree、使用 MedChat Conda Python，不调用外部主模型，不读取真实数据/权重或凭据。集合有重叠，不累计。

| 范围 | 命令概要 | 结果 |
|---|---|---|
| PR8 输入/报告/提示词 | pytest 三个相应文件 | 168 passed |
| PR8 Agent/反幻觉/健康 | pytest tests/agent 与两个根测试文件 | 1688 passed、1 skipped（未启用性能测试）、7 既有警告 |
| PR8 contract | run_agent_acceptance.py --mode contract | 34/34 passed |
| 活性定向 | pytest test_activity*.py、test_family_training_run.py、Agent family tool | 916 passed、5 skipped（Windows symlink 权限）、2 既有警告 |
| 活性分支 Agent | pytest tests/agent 与反幻觉/健康两个文件 | 1683 passed、1 skipped、7 既有警告 |
| 活性分支根测试 | pytest tests --ignore=tests/agent --ignore=tests/sandbox_broker --ignore=tests/task_runtime | 1898 passed、141 skipped、171 subtests passed、6 warnings |
| 静态 | compileall、Node family UI、相关 JS 语法、PR8 的 8 个 Node 脚本、diff check | passed |

根测试跳过包括需 Linux/root/systemd 的集成、Windows symlink 权限及显式未启用的运行验收，不得当成跨平台运行成功。神经网络前向测试使用明确的临时合成未训练权重；不作真实模型性能依据。独立 Agent 审查额外跑了 351 项 Python、27 项 lab UI 测试，仍复现上述缺陷。

## 后续顺序与合并门禁

1. PR8 保持独立；最终 CI 成功、无未解决意见，并按仓库规则取得具体 PR 合并确认后再合并。
2. 不发布本地 56 文件活动快照为大型可合并 PR。按原提交/差异块拆成：截至 `5dadb49` 的数据准备/端点注册/训练，再是家族数据和 runtime。先解决三个活性阻塞。
3. 再集成 Agent 审计修复、决策协议/循环/证据与隔离入口。活动适配/家族服务依赖必须已经集成；先解决三个 Agent 阻塞，不启用生产入口。
4. 合并任务不等于可以重新设计续接语义或改生产权限。本轮只修了移植中直接复现的局部解析回归；其余修复需单独有界实施、测试与审查。
5. 保持真实数据、模型和运行资产在本地，禁止以高测试数量代替科学/安全审查，不绕过 CI 或强制合并。
