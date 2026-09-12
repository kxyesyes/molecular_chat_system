# Agent 家族活性接线集成

日期：2026-09-12。分支 `codex/family-activity-agent-integration`。
依赖上一批 API 提交 `3261d7f` / [PR #14](https://github.com/kxyesyes/molecular_chat_system/pull/14)，
用户明确授权后已 squash 合并为 main `370898a`，合并树与 API 提交树一致。
API Linux CI run `34683624419` 已 7/7 通过；root 2283 passed/72 skipped，sandbox-core 1928 passed/4 skipped，Agent 1670 passed/1 skipped。
本批须另行独立 PR、CI 和具体合并授权，不直接提交 main。
本分支是显式依赖栈，不能将包含父批次的全部 diff 当作本批独立修改。

范围为总体计划 Task 2：输入边界、家族活性工具、候选/target 绑定与领域校验；不迁移 UI、训练入口或决策循环。
候选源 `9312bf5`，必须保留 main 更强的 `molecular_input.py` 完整解析，不整体覆盖历史文件。

状态：实施完成，独立规格及质量审查 APPROVED；准备独立 PR/CI，未启用生产模型。

实施者实测：初始 RED 151 failed/5 passed，旧接口/输出契约/靶点列表补充 RED 2/7/4 项；
修复后聚焦与旧接口 192 passed、Agent 1843 passed/1 skipped。仅临时合成数据，不是实际模型性能验收。
父任务另复现 warnings 畸形容器 4 项 RED（None/整数异常、字符串/字典错误汇总），
修复时复用共享 summarize_predictions 的列表警告契约，保留原始观察行；两个新测试模块 177 passed。
独立规格审查再现后置靶点/未知列表被丢弃及单位、阈值未校验的问题，已逐项先补 RED：
18 项补充用例中 15 failed/3 passed；制表符后置靶点另 1 failed。
后置 Predict/Assess/Evaluate 与中文预测/评估形式再补 15 项，均先失败；
修复复用同一个 labelled-target 模式进行上下文保留和实际解析，避免位置规则再次分叉。
保留 main 完整 SMILES 校验；未知/冲突靶点不能进入 legacy；有数值的家族结果必须
具有 units=pIC50、label_threshold=5.0，不能将其他单位或阈值渲染成已约定的结果。

后置 for/against 的冒号/等号分隔再现 12 failed/8 passed；上下文保留和位置解析
也统一复用 `_TARGET_LABEL`，不再维护另一份缩减标签语法。
最新父任务实测：两个新测试模块 231 passed；全 `tests/agent` 1901 passed、1 skipped、
7 项既有弃用 warnings，19.05 秒。反幻觉/平台健康补充 18 passed；contract 通过。
独立规格重审 APPROVED：327 项测试（231 本批 + 96 共享分子输入）及 160 项额外
位置/分隔/冲突/完整结构检查通过。全部 8 个 Node、compileall、diff check 通过。
质量审查、CI 与本批具体合并授权为独立后续门槛。

质量审查随后发现中文/带引号的未知靶点及背景说明后的明确靶点会被忽略，
父任务补 20 项 RED 复现；另补 4 项已知靶点前缀截断 RED。修复为先识别明确
靶点位置，再验证完整候选值；背景仅忽略明确声明之前的文本，不忽略之后的声明。
最新聚焦 255 passed，全 Agent 1925 passed/1 skipped/7 warnings（18.81 秒），
compileall、contract、diff check 通过。质量重审中，先前 SPEC 计数不覆盖这次新修复。

进一步重审的 labelled 未知靶点与已支持指标后缀回归新增 5 项 RED；
修复后主动补充普通中文“分子/这个分子/该分子”无靶点请求兼容性，3 项 RED 后修复。
最新结果为聚焦 263 passed，全 Agent 1933 passed/1 skipped/7 warnings（18.96 秒）。
这些均为输入契约测试，不代表真实模型性能；最终独立质量意见及 PR 状态另行补录。

最终质量审查批准代码提交 `10b2ad5`：独立 263 项聚焦测试及 58 项内存补充断言通过，
未余留范围内 blocker。父任务 1933 项全 Agent 通过覆盖该代码树，随后仅更新此交接文档。
审查不等于合并授权；CI 及具体 PR 的用户授权仍待完成。

实施者生成的五个 temp_task2_* 根目录临时产物未暂存，后续测试改用忽略目录或系统临时目录。
清理尝试受执行策略阻止，暂留原处，不以 git clean 或覆盖操作绕过。

API 批次的全仓扩展回归曾有一次未稳定复现的沙盒 artifact_failed；失败证据及重跑结果仍保留在
[API 交接](family-activity-api-integration.md)，不能据重跑通过删除记录。服务器参数暂不阻塞代码工作。

独立只读诊断进一步定位到第一次 pose 产物发布前后：测试临时 validating → failed 约 52ms，
published 目录空；留存输出的 100 次只读科学校验/快照均通过。文件系统/身份检查是候选原因，
尚无原始异常操作证据，不能视为已修复。后续应仅对合成 fixture 做限时、内存内异常定位。
父任务完成 100 次限时合成 fixture 复跑（21.797 秒），带内存内发布异常跟踪，未再次触发；
这仍不是原始失败根因的确认或修复。诊断脚本只在上一批忽略的 scratch/，未提交。
