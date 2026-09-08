# A2：显式分子输入完整性修复

基线：`ee161d8`；实施分支 `codex/agent-explicit-smiles-input`。
承接已批准 model-driven-agent 设计及 agent-task-requirements 交接的下一步。

## 边界与取舍

只迁移 property_calculator、drug_likeness_assessment 的输入解析，不全局替换
BaseMolecularTool（其他生成/对接接口依赖旧行为），不切换网站，不启用生产模型。
复用 activity_input 的保守分隔原则，但不依赖活性靶点解析或复制其业务逻辑。
工具名、execute(query) 及成功结果结构保持兼容。

输入支持显式 SMILES 标签、完整独立结构及换行/分号分隔批量。
大小写、盐的点号、括号、立体符号和无效后缀均原样进入真实 RDKit 校验；
不从无效字段提取有效片段，不将小写 cco 改成 CCO。
显式字段有空值、损坏值或含不明确空格时拒绝整个批次，请用户修正；
不悄悄漏项再声称成功。无 RDKit 不可用启发式验证冒充真实校验。
自然语言仅支持保守的完整 token 提取，不实现化学名称推断或任意文章解析。

## TDD 步骤

1. 新增两工具输入回归：短串、混合长短批量、标签/引号、立体/盐、无效后缀、缺依赖。
2. 将已有 CCO+CCN 漏项负例升级为预期完整成功；保留独立的缺项验收负例。
3. 观察 RED 后新增小型共享解析 helper；两工具接入并输出明确错误。
4. 验证真实 RDKit property → evidence ref → drug-likeness 批量闭环。
5. 运行聚焦、全 Agent/模型回归、legacy contract、compileall、diff check；独立审查。
6. 写交接，显式暂存本任务文件并提交；不 push、不创建历史巨型 PR、不合并 main。

## 验证

使用既有 MedChat Conda Python，不安装新依赖。外部模型使用明确测试替身，
工具计算使用真实本地 RDKit；本轮不是实网 LLM 或整站切换验收。

## 执行状态

- [x] 分支隔离，原工作树状态保留。
- [x] 首轮 RED → GREEN；两轮审查边界问题均先补 RED 再修复。
- [x] 真实 RDKit 批量与下游证据消费验证。
- [x] 聚焦 129 passed；全量 1942 passed / 1 skipped；反幻觉/健康检查 53 passed。
- [x] legacy contract 34/34、compileall、diff check 通过。
- [x] 独立复审 348 项零失败；交接记录兼容收紧、限制及下一步。

精确文件、命令和结果见 `docs/handoff/agent-explicit-smiles-input.md`。
